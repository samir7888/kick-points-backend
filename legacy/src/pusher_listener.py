"""Kick.com Pusher WebSocket listener for real-time prediction events.

Kick broadcasts predictions through:
  1. `predictions-channel-{channel_id}` (dedicated prediction channel, public)
     Events: PredictionCreated, PredictionUpdated
  2. `private-chatrooms.{chatroom_id}.v2` (authenticated chatroom channel)
  3. `chatrooms.{chatroom_id}.v2` (public chatroom channel fallback)
"""

import json
import threading
import time
from typing import Callable, Optional

import cloudscraper
import websocket
from loguru import logger

# Kick.com's Pusher app key (public, embedded in their frontend JS)
PUSHER_APP_KEY = "32cbd69e4b950bf97679"
PUSHER_WS_URL = (
    f"wss://ws-us2.pusher.com/app/{PUSHER_APP_KEY}"
    "?protocol=7&client=js&version=7.6.0&flash=false"
)

# Kick Pusher auth endpoint
KICK_BROADCASTING_AUTH = "https://kick.com/broadcasting/auth"

# Pusher event names Kick uses for predictions
PREDICTION_EVENTS = {
    "PredictionCreated",
    "PredictionUpdated",
    "App\\Events\\PredictionCreated",
    "App\\Events\\PredictionUpdated",
    r"App\Events\PredictionCreated",
    r"App\Events\PredictionUpdated",
}

# Prediction states/statuses that mean the prediction is closed / not voteable
CLOSED_STATES = {
    "LOCKED",
    "RESOLVED",
    "CANCELLED",
    "ENDED",
    "finished",
    "cancelled",
    "canceled",
    "refunded",
    "resolved",
    "paid",
    "ended",
    "locked",
}


class ChannelPusherListener:
    """Maintains a WebSocket connection to Kick's Pusher and fires callbacks
    immediately whenever a new prediction is detected.

    Subscribes to:
      - predictions-channel-{channel_id}  (dedicated realtime predictions channel)
      - private-chatrooms.{chatroom_id}.v2 (authenticated chatroom events)
      - chatrooms.{chatroom_id}.v2         (public chatroom fallback)
    """

    def __init__(
        self,
        username: str,
        chatroom_id: int,
        channel_id: Optional[int] = None,
        authorization: str = "",
        on_prediction: Optional[Callable[[dict], None]] = None,
        on_prediction_resolved: Optional[Callable[[dict], None]] = None,
        reconnect_delay: int = 10,
    ):
        self.username = username
        self.chatroom_id = chatroom_id
        self.channel_id = channel_id
        self.authorization = authorization
        self.on_prediction = on_prediction
        self.on_prediction_resolved = on_prediction_resolved
        self.reconnect_delay = reconnect_delay

        self._ws: Optional[websocket.WebSocketApp] = None
        self._socket_id: Optional[str] = None
        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._scraper = cloudscraper.create_scraper()

    def start(self) -> None:
        """Spawn the WebSocket listener in a daemon thread."""
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name=f"pusher-{self.username}",
        )
        self._thread.start()
        logger.info(
            f"{self.username}: Pusher listener started (channel_id={self.channel_id}, chatroom_id={self.chatroom_id})"
        )

    def stop(self) -> None:
        """Signal the listener to stop and close the WebSocket."""
        self._stop_event.set()
        if self._ws:
            try:
                self._ws.close()
            except Exception:
                pass

    def _run_loop(self) -> None:
        """Reconnect loop — keeps the WebSocket alive indefinitely."""
        while not self._stop_event.is_set():
            try:
                self._connect()
            except Exception as exc:
                logger.error(f"{self.username}: Pusher connection error: {exc}")
            if not self._stop_event.is_set():
                logger.info(
                    f"{self.username}: Reconnecting Pusher in {self.reconnect_delay}s…"
                )
                time.sleep(self.reconnect_delay)

    def _connect(self) -> None:
        self._socket_id = None
        self._ws = websocket.WebSocketApp(
            PUSHER_WS_URL,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )
        self._ws.run_forever(ping_interval=30, ping_timeout=10)

    def _on_open(self, ws) -> None:
        logger.debug(f"{self.username}: Pusher WebSocket opened")

    def _on_message(self, ws, raw: str) -> None:
        try:
            frame = json.loads(raw)
        except json.JSONDecodeError:
            return

        event = frame.get("event", "")

        if event == "pusher:connection_established":
            conn_data = frame.get("data", "{}")
            if isinstance(conn_data, str):
                try:
                    conn_data = json.loads(conn_data)
                except Exception:
                    conn_data = {}
            self._socket_id = conn_data.get("socket_id")
            logger.debug(
                f"{self.username}: Pusher connected (socket_id={self._socket_id})"
            )
            self._subscribe_all(ws)
            return

        if event == "pusher_internal:subscription_succeeded":
            channel = frame.get("channel", "")
            logger.info(f"{self.username}: Pusher subscribed to {channel}")
            return

        if event == "pusher:error":
            logger.warning(f"{self.username}: Pusher error frame: {frame.get('data')}")
            return

        if event in PREDICTION_EVENTS or "Prediction" in event:
            self._handle_prediction_event(frame)

    def _on_error(self, ws, error) -> None:
        logger.warning(f"{self.username}: Pusher WebSocket error: {error}")

    def _on_close(self, ws, close_status_code, close_msg) -> None:
        logger.debug(
            f"{self.username}: Pusher WebSocket closed (code={close_status_code})"
        )

    def _subscribe_all(self, ws) -> None:
        """Subscribe to all channels associated with this stream."""
        # 1. predictions-channel-{channel_id} (Kick's dedicated prediction channel)
        if self.channel_id:
            pred_channel = f"predictions-channel-{self.channel_id}"
            ws.send(
                json.dumps(
                    {
                        "event": "pusher:subscribe",
                        "data": {"channel": pred_channel},
                    }
                )
            )
            logger.debug(f"{self.username}: Subscribed to {pred_channel}")

        # 2. chatrooms.{chatroom_id}.v2 (public chatroom channel)
        public_channel = f"chatrooms.{self.chatroom_id}.v2"
        ws.send(
            json.dumps(
                {
                    "event": "pusher:subscribe",
                    "data": {"channel": public_channel},
                }
            )
        )
        logger.debug(f"{self.username}: Subscribed to {public_channel}")

        # 3. private-chatrooms.{chatroom_id}.v2 (authenticated chatroom channel)
        if self.authorization:
            private_channel = f"private-chatrooms.{self.chatroom_id}.v2"
            auth = self._get_pusher_auth(private_channel)
            if auth:
                ws.send(
                    json.dumps(
                        {
                            "event": "pusher:subscribe",
                            "data": {
                                "channel": private_channel,
                                "auth": auth,
                            },
                        }
                    )
                )
                logger.debug(f"{self.username}: Subscribed to {private_channel}")

    def _get_pusher_auth(self, channel_name: str) -> Optional[str]:
        """Obtain a Pusher auth signature for a private channel."""
        if not self._socket_id:
            return None

        try:
            resp = self._scraper.post(
                KICK_BROADCASTING_AUTH,
                json={
                    "socket_id": self._socket_id,
                    "channel_name": channel_name,
                },
                headers={
                    "Authorization": self.authorization,
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "Referer": "https://kick.com/",
                    "Origin": "https://kick.com",
                },
                timeout=8,
            )

            if resp.status_code == 200:
                data = resp.json()
                return data.get("auth")
        except Exception as exc:
            logger.error(f"{self.username}: Pusher auth error: {exc}")

        return None

    def _handle_prediction_event(self, frame: dict) -> None:
        """Parse a prediction event frame and invoke the callback."""
        raw_data = frame.get("data", {})

        if isinstance(raw_data, str):
            try:
                raw_data = json.loads(raw_data)
            except json.JSONDecodeError:
                logger.warning(
                    f"{self.username}: Could not parse prediction event data"
                )
                return

        prediction = raw_data.get("prediction") or raw_data
        if not isinstance(prediction, dict):
            logger.warning(
                f"{self.username}: Unexpected prediction data: {type(prediction)}"
            )
            return

        state = str(prediction.get("state") or prediction.get("status") or "").upper()
        title = prediction.get("title", "Unknown")
        pred_id = prediction.get("id", "")

        logger.info(
            f"{self.username}: Pusher prediction event '{title}' (ID: {pred_id}, State: {state})"
        )

        if state not in CLOSED_STATES:
            if self.on_prediction:
                self.on_prediction(prediction)
        else:
            if self.on_prediction_resolved:
                self.on_prediction_resolved(prediction)
