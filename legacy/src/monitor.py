"""Channel monitoring."""

import random
import threading
import time
from typing import Optional

from loguru import logger

from . import web_server
from .client import KickClient
from .prediction_logger import prediction_logger
from .pusher_listener import ChannelPusherListener


class ChannelMonitor:
    """Monitors a single Kick.com channel and sends messages / votes.

    Predictions are detected via a persistent Pusher WebSocket connection
    (the only channel Kick.com uses to broadcast them). Voting still uses
    the existing REST API endpoint.
    """

    def __init__(
        self,
        username: str,
        client: KickClient,
        messages: list[str],
        wait_times: dict[str, int | dict[str, int]],
        prediction_amount: int = 50,
    ):
        self.username = username
        self.client = client
        self.messages = messages
        self.wait_times = wait_times
        self.prediction_amount = prediction_amount

        # Predictions received from Pusher are placed here; the monitor loop
        # drains them and votes.
        self._pending_predictions: list[dict] = []
        self._pending_lock = threading.Lock()
        self._vote_lock = threading.Lock()
        self.voted_prediction_ids: set[str] = set()
        self.resolved_prediction_ids: set[str] = set()

        # Initialize already resolved prediction IDs from recent history
        try:
            for ev in prediction_logger.get_recent_predictions(
                self.username, hours=168
            ):
                if ev.get("event_type") in ("prediction_result", "resolved"):
                    pid = ev.get("prediction", {}).get("id")
                    if pid:
                        self.resolved_prediction_ids.add(str(pid))
        except Exception:
            pass

        self._pusher: Optional[ChannelPusherListener] = None

        web_server.register_channel(username)

    # ------------------------------------------------------------------
    # Pusher initialisation
    # ------------------------------------------------------------------

    def _start_pusher(self, chatroom_id: int, channel_id: Optional[int] = None) -> None:
        """Spin up (or restart) the Pusher WebSocket listener for this channel."""
        if self._pusher is not None:
            self._pusher.stop()

        self._pusher = ChannelPusherListener(
            username=self.username,
            chatroom_id=chatroom_id,
            channel_id=channel_id,
            authorization=self.client.authorization,
            on_prediction=self._on_pusher_prediction,
            on_prediction_resolved=self._on_pusher_prediction_resolved,
        )
        self._pusher.start()

    def _on_pusher_prediction(self, prediction: dict) -> None:
        """Callback invoked immediately from the Pusher listener thread when a prediction arrives."""
        logger.info(f"{self.username}: Immediate prediction event from Pusher")
        threading.Thread(
            target=self._handle_prediction,
            args=(prediction,),
            daemon=True,
            name=f"vote-{self.username}",
        ).start()

    def _on_pusher_prediction_resolved(self, prediction: dict) -> None:
        """Callback invoked when a prediction resolution arrives from Pusher."""
        threading.Thread(
            target=self._check_prediction_resolution,
            args=(prediction,),
            daemon=True,
            name=f"resolve-{self.username}",
        ).start()

    # ------------------------------------------------------------------
    # Chat message sending
    # ------------------------------------------------------------------

    def check_and_send(self) -> tuple[bool, Optional[str], str]:
        """Check if channel is live and send a message if so.

        Returns (sent, message, status) where status is one of
        "online", "offline" or "error".
        """
        channel_data = self.client.get_channel(self.username)

        if not channel_data:
            return False, None, "error"

        if channel_data.get("livestream") is None:
            return False, None, "offline"

        channel_id = channel_data.get("id")
        chatroom_id = channel_data.get("chatroom", {}).get("id")
        if not chatroom_id:
            return False, None, "error"

        # Lazily start the Pusher listener the first time we see the channel live
        # (and restart it whenever we reconnect after an offline period).
        if (
            self._pusher is None
            or not self._pusher._thread
            or not self._pusher._thread.is_alive()
        ):
            self._start_pusher(chatroom_id, channel_id)

        # Check for active predictions via REST API right away (e.g. if started before connection)
        try:
            active_pred = self.client.get_active_prediction(self.username)
            if active_pred:
                threading.Thread(
                    target=self._handle_prediction,
                    args=(active_pred,),
                    daemon=True,
                    name=f"vote-{self.username}",
                ).start()
        except Exception as e:
            logger.debug(
                f"{self.username}: Error checking active prediction via REST: {e}"
            )

        message = random.choice(self.messages)
        success = self.client.send_message(chatroom_id, message)

        if not success:
            return False, None, "error"

        return True, message, "online"

    # ------------------------------------------------------------------
    # Prediction voting
    # ------------------------------------------------------------------

    def process_pending_predictions(self) -> None:
        """Process active predictions and check for resolutions."""
        try:
            # 1. Check if an existing voted prediction has resolved
            self._check_prediction_resolution()

            # 2. Check for active predictions to vote on
            active_pred = self.client.get_active_prediction(self.username)
            if active_pred:
                self._handle_prediction(active_pred)
        except Exception as e:
            logger.debug(f"{self.username}: Error in process_pending_predictions: {e}")

    def _check_prediction_resolution(self, pusher_pred: Optional[dict] = None) -> None:
        """Check if a prediction has resolved or ended, and record the win/loss outcome."""
        try:
            data = self.client.get_latest_prediction_data(self.username)
            if not data:
                return

            pred = data.get("prediction") or pusher_pred
            if not pred or not isinstance(pred, dict):
                return

            pred_id = str(pred.get("id") or "")
            if not pred_id or pred_id in self.resolved_prediction_ids:
                return

            state = str(pred.get("state") or pred.get("status") or "").upper()
            if state not in ("RESOLVED", "CANCELLED", "ENDED"):
                return

            user_vote = data.get("user_vote")
            if not user_vote and pred_id not in self.voted_prediction_ids:
                return

            outcomes = pred.get("outcomes") or []
            voted_outcome_id = str((user_vote or {}).get("outcome_id") or "")
            voted_amount = int(
                (user_vote or {}).get("total_vote_amount") or self.prediction_amount
            )

            winning_outcome_id = str(
                pred.get("winning_outcome_id") or pred.get("winningOutcomeId") or ""
            )
            if not winning_outcome_id:
                for o in outcomes:
                    if o.get("is_winner") or o.get("winner"):
                        winning_outcome_id = str(o.get("id") or "")
                        break

            winning_title = next(
                (
                    o.get("title", "Unknown")
                    for o in outcomes
                    if str(o.get("id")) == winning_outcome_id
                ),
                "Unknown",
            )
            voted_title = next(
                (
                    o.get("title", "Unknown")
                    for o in outcomes
                    if str(o.get("id")) == voted_outcome_id
                ),
                "Unknown",
            )
            return_rate = next(
                (
                    float(o.get("return_rate", 1.0))
                    for o in outcomes
                    if str(o.get("id")) == winning_outcome_id
                ),
                1.0,
            )

            # Determine win / loss / refund
            if state == "CANCELLED":
                result = "refunded"
                points_won = 0
            elif winning_outcome_id and voted_outcome_id == winning_outcome_id:
                result = "won"
                points_won = int(voted_amount * return_rate)
            elif winning_outcome_id and voted_outcome_id != winning_outcome_id:
                result = "lost"
                points_won = 0
            else:
                result = "ended"
                points_won = 0

            self.resolved_prediction_ids.add(pred_id)

            pred_title = pred.get("title", "Prediction")
            if result == "won":
                logger.success(
                    f"🎉 {self.username}: Prediction '{pred_title}' WON! "
                    f"Outcome: '{voted_title}' (+{points_won} pts)"
                )
            elif result == "lost":
                logger.warning(
                    f"❌ {self.username}: Prediction '{pred_title}' LOST. "
                    f"Voted: '{voted_title}', Winner: '{winning_title}'"
                )
            elif result == "refunded":
                logger.info(
                    f"🔄 {self.username}: Prediction '{pred_title}' CANCELLED (Refunded {voted_amount} pts)"
                )

            result_info = {
                "id": pred_id,
                "result": result,
                "title": pred_title,
                "voted_outcome": voted_title,
                "winning_outcome": winning_title,
                "amount": voted_amount,
                "return_rate": return_rate,
                "points_won": points_won,
            }

            prediction_logger.log_prediction_event(
                self.username,
                "prediction_result",
                {
                    "id": pred_id,
                    "title": pred_title,
                    "outcomes": [
                        {"id": o.get("id"), "title": o.get("title")} for o in outcomes
                    ],
                    "state": state,
                    "winning_outcome_id": winning_outcome_id,
                },
                result_info,
            )

            web_server.record_prediction_result(self.username, result_info)

        except Exception as e:
            logger.error(f"{self.username}: Error checking prediction resolution: {e}")

    def _handle_prediction(self, prediction: dict) -> None:
        """Vote on a single prediction dict."""
        if self.prediction_amount <= 0:
            return

        with self._vote_lock:
            prediction_id = str(prediction.get("id") or "")
            outcomes = prediction.get("outcomes") or []
            prediction_title = prediction.get("title", "Unknown Prediction")

            if not prediction_id or not outcomes:
                logger.warning(
                    f"{self.username}: Invalid prediction data — "
                    f"ID: {prediction_id}, Outcomes: {len(outcomes)}"
                )
                return

            prediction_data = {
                "id": prediction_id,
                "title": prediction_title,
                "outcomes": [
                    {"id": str(o.get("id", "")), "title": o.get("title", "")}
                    for o in outcomes
                ],
                "status": prediction.get("state") or prediction.get("status", "active"),
            }

            if prediction_id in self.voted_prediction_ids:
                logger.debug(
                    f"{self.username}: Already voted on prediction "
                    f"'{prediction_title}' (ID: {prediction_id})"
                )
                return

            # Log discovery
            prediction_logger.log_prediction_event(
                self.username,
                "discovered",
                prediction_data,
                {"total_outcomes": len(outcomes)},
            )
            web_server.update_channel_prediction_availability(self.username, True)

            # ✨ SMART PREDICTION SELECTION ✨
            # Analyze outcomes and make more intelligent choices

            # Strategy 1: Favor outcomes with better odds (higher multipliers)
            outcomes_with_odds = []
            for outcome in outcomes:
                # Look for return_rate or odds data
                return_rate = outcome.get("return_rate", 1.0)
                odds = outcome.get("odds", 1.0)
                multiplier = max(return_rate, odds)
                outcomes_with_odds.append(
                    {
                        "outcome": outcome,
                        "multiplier": multiplier,
                        "title": outcome.get("title", ""),
                    }
                )

            # Strategy 2: Weight selection based on potential returns
            # Higher odds = higher potential return but lower probability
            # We'll use a balanced approach: 70% random, 30% weighted by odds
            if random.random() < 0.3 and outcomes_with_odds:
                # 30% chance: Pick based on favorable odds
                # Sort by multiplier descending, but still add some randomness
                sorted_outcomes = sorted(
                    outcomes_with_odds, key=lambda x: x["multiplier"], reverse=True
                )
                # Pick from top 50% of outcomes by odds
                top_half = sorted_outcomes[: max(1, len(sorted_outcomes) // 2)]
                selected_data = random.choice(top_half)
                selected_outcome = selected_data["outcome"]
                strategy = f"Smart (odds: {selected_data['multiplier']:.2f}x)"
            else:
                # 70% chance: Pure random selection
                selected_outcome = random.choice(outcomes)
                strategy = "Random"

            outcome_id = str(selected_outcome.get("id") or "")
            outcome_title = selected_outcome.get("title", "Unknown Outcome")

            logger.info(
                f"{self.username}: Selection strategy: {strategy} → '{outcome_title}'"
            )

            if not outcome_id:
                logger.warning(
                    f"{self.username}: No valid outcome ID for prediction '{prediction_title}'"
                )
                prediction_logger.log_prediction_event(
                    self.username,
                    "vote_failed",
                    prediction_data,
                    {"reason": "no_valid_outcome_id"},
                )
                return

            logger.info(
                f"{self.username}: Active prediction detected — '{prediction_title}'. "
                f"Voting {self.prediction_amount} pts on '{outcome_title}'"
            )

            ok = self.client.vote_prediction(
                self.username, prediction_id, outcome_id, self.prediction_amount
            )

            if ok:
                self.voted_prediction_ids.add(prediction_id)
                logger.success(
                    f"{self.username}: Voted {self.prediction_amount} pts on "
                    f"'{prediction_title}' → '{outcome_title}'"
                )
                prediction_logger.log_prediction_event(
                    self.username,
                    "voted",
                    prediction_data,
                    {
                        "voted_outcome": outcome_title,
                        "outcome_id": outcome_id,
                        "amount": self.prediction_amount,
                    },
                )
                prediction_info = {
                    "id": prediction_id,
                    "title": prediction_title,
                    "outcomes": [
                        {"id": o.get("id"), "title": o.get("title")} for o in outcomes
                    ],
                    "voted_outcome": outcome_title,
                    "amount": self.prediction_amount,
                    "status": "voted",
                }
                web_server.update_channel(
                    self.username, "prediction_vote", prediction_info=prediction_info
                )
            else:
                logger.error(
                    f"{self.username}: Failed to vote on prediction '{prediction_title}'"
                )
                prediction_logger.log_prediction_event(
                    self.username,
                    "vote_failed",
                    prediction_data,
                    {
                        "attempted_outcome": outcome_title,
                        "outcome_id": outcome_id,
                        "amount": self.prediction_amount,
                        "reason": "api_error",
                    },
                )

    # ------------------------------------------------------------------
    # Main monitoring loop
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Run the monitoring loop."""
        # Check prediction resolution immediately at startup
        try:
            self._check_prediction_resolution()
        except Exception as e:
            logger.debug(f"{self.username}: Startup prediction check error: {e}")

        while True:
            try:
                sent, message, status = self.check_and_send()

                if status == "error":
                    web_server.update_channel(self.username, "error")
                    wait_time = self.wait_times.get("error_wait", 60)
                    logger.error(
                        f"{self.username}: check/send failed. Waiting {wait_time}s."
                    )
                else:
                    if status == "online":
                        web_server.update_channel(self.username, "online", message)
                    else:
                        web_server.update_channel(self.username, "offline")

                    # Process any predictions that arrived via Pusher WebSocket
                    try:
                        self.process_pending_predictions()
                    except Exception as e:
                        logger.error(
                            f"Prediction processing error on {self.username}: {e}"
                        )

                    if sent:
                        wait_time = random.randint(
                            self.wait_times["livestream_active"]["min"],
                            self.wait_times["livestream_active"]["max"],
                        )
                        logger.info(
                            f"Sent to {self.username}: {message}. Waiting {wait_time}s."
                        )
                    else:
                        wait_time = self.wait_times["livestream_inactive"]
                        logger.info(
                            f"{self.username} is offline. Waiting {wait_time}s."
                        )

                time.sleep(wait_time)
            except Exception as e:
                logger.error(f"Error monitoring {self.username}: {e}")
                web_server.update_channel(self.username, "error")
                time.sleep(self.wait_times.get("error_wait", 60))
