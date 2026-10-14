"""Kick.com API client."""

import random
from typing import Optional

import cloudscraper
from loguru import logger


class KickClient:
    """Client for interacting with Kick.com API."""

    BASE_URL = "https://kick.com/api/v2"

    def __init__(self, authorization: str):
        self.authorization = authorization
        self.scraper = cloudscraper.create_scraper()

    def _headers(self) -> dict:
        return {"Authorization": self.authorization}

    def get_channel(self, username: str) -> Optional[dict]:
        """Fetch channel information."""
        try:
            url = f"{self.BASE_URL}/channels/{username}"
            response = self.scraper.get(url)
            return response.json()
        except Exception as e:
            logger.error(f"Error fetching channel {username}: {e}")
            return None

    def get_channel_points(self, username: str) -> Optional[dict]:
        """Return the authenticated user's channel-points balance for a channel.

        Endpoint: GET https://kick.com/api/v2/channels/{username}/points
        Response: {"data": {"points": 4218}, "message": "OK"}
        Requires a kick.com site session token, not an OAuth bearer token.
        """
        try:
            url = f"{self.BASE_URL}/channels/{username}/points"
            headers = self._headers()
            headers.update({"Accept": "application/json"})
            response = self.scraper.get(url, headers=headers, timeout=10)
            if response.status_code == 200:
                return response.json()
            logger.warning(
                f"Error fetching points balance for {username}: HTTP {response.status_code}"
            )
        except Exception as e:
            logger.error(f"Error fetching points balance for {username}: {e}")
        return None

    def send_message(self, chatroom_id: int, content: str) -> bool:
        """Send a message to a chatroom."""
        try:
            url = f"{self.BASE_URL}/messages/send/{chatroom_id}"
            payload = {
                "content": content,
                "type": "message",
                "message_ref": str(random.randint(1000000000000, 9999999999999)),
            }
            self.scraper.post(url, json=payload, headers=self._headers())
            return True
        except Exception as e:
            logger.error(f"Error sending message: {e}")
            return False

    def get_active_prediction(self, username: str) -> Optional[dict]:
        """Return the currently active/open prediction for a channel, if any.

        Kick.com provides a dedicated endpoint:
          GET https://kick.com/api/v2/channels/{username}/predictions/latest
        """
        try:
            url = f"{self.BASE_URL}/channels/{username}/predictions/latest"
            headers = self._headers()
            headers.update(
                {
                    "Accept": "application/json",
                    "Referer": f"https://kick.com/{username}",
                }
            )
            response = self.scraper.get(url, headers=headers, timeout=10)
            if response.status_code == 200:
                body = response.json()
                data = body.get("data", {})
                pred = data.get("prediction")
                user_vote = data.get("user_vote")

                if pred and isinstance(pred, dict):
                    state = str(pred.get("state", "")).upper()
                    outcomes = pred.get("outcomes") or []
                    title = pred.get("title", "Unknown")
                    pred_id = pred.get("id")

                    if state == "ACTIVE" and outcomes:
                        if user_vote:
                            logger.info(
                                f"{username}: Prediction '{title}' (ID: {pred_id}) is ACTIVE but user already voted: {user_vote}"
                            )
                            return None
                        logger.info(
                            f"{username}: Found active prediction '{title}' (ID: {pred_id}) with {len(outcomes)} outcomes"
                        )
                        pred["user_vote"] = None
                        return pred
                    else:
                        logger.debug(
                            f"{username}: Latest prediction '{title}' state is {state} (not ACTIVE)"
                        )
        except Exception as e:
            logger.error(f"Error fetching latest prediction for {username}: {e}")

        return None

    def get_latest_prediction_data(self, username: str) -> Optional[dict]:
        """Fetch raw latest prediction and user_vote data for a channel."""
        try:
            url = f"{self.BASE_URL}/channels/{username}/predictions/latest"
            headers = self._headers()
            headers.update(
                {
                    "Accept": "application/json",
                    "Referer": f"https://kick.com/{username}",
                }
            )
            response = self.scraper.get(url, headers=headers, timeout=10)
            if response.status_code == 200:
                body = response.json()
                data = body.get("data", {})
                pred = data.get("prediction")
                user_vote = data.get("user_vote")
                if pred and isinstance(pred, dict):
                    return {
                        "prediction": pred,
                        "user_vote": user_vote,
                    }
        except Exception as e:
            logger.error(f"Error fetching latest prediction data for {username}: {e}")

        return None

    def vote_prediction(
        self, username: str, prediction_id: str, outcome_id: str, amount: int = 50
    ) -> bool:
        """Vote channel points on a prediction outcome."""
        try:
            url = f"{self.BASE_URL}/channels/{username}/predictions/vote"
            headers = self._headers()
            headers.update(
                {
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "Referer": f"https://kick.com/{username}",
                    "Origin": "https://kick.com",
                }
            )

            payload = {
                "amount": amount,
                "outcome_id": outcome_id,
            }
            response = self.scraper.post(url, json=payload, headers=headers, timeout=10)
            if response.status_code == 200:
                return True

            # If 400 (e.g. insufficient points for configured amount), retry with platform min (10)
            if amount > 10 and response.status_code == 400:
                logger.info(
                    f"{username}: Vote with {amount} pts failed (likely balance). Retrying with min (10 pts)..."
                )
                payload["amount"] = 10
                retry_resp = self.scraper.post(
                    url, json=payload, headers=headers, timeout=10
                )
                if retry_resp.status_code == 200:
                    return True

            logger.warning(
                f"Vote on {username} failed: HTTP {response.status_code} - {response.text[:200]}"
            )
            return False
        except Exception as e:
            logger.error(f"Error voting prediction on {username}: {e}")
            return False
