"""Prediction logging system for tracking all prediction activities."""

import json
import os
from datetime import datetime, timedelta
from typing import Dict, Any, Optional

from loguru import logger


class PredictionLogger:
    """Handles logging of all prediction-related activities to files."""

    def __init__(self, log_dir: str = "logs"):
        self.log_dir = log_dir
        self.ensure_log_directory()

    def ensure_log_directory(self) -> None:
        """Create logs directory if it doesn't exist."""
        if not os.path.exists(self.log_dir):
            os.makedirs(self.log_dir)

    def log_prediction_event(
        self,
        username: str,
        event_type: str,
        prediction_data: Dict[str, Any],
        additional_info: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Log a prediction event to both file and console.

        Args:
            username: Channel username
            event_type: Type of event (discovered, voted, failed, etc.)
            prediction_data: Prediction information
            additional_info: Additional context information
        """
        timestamp = datetime.now().isoformat()

        log_entry = {
            "timestamp": timestamp,
            "username": username,
            "event_type": event_type,
            "prediction": prediction_data,
            "additional_info": additional_info or {},
        }

        # Log to daily prediction file
        daily_file = os.path.join(
            self.log_dir, f"predictions_{datetime.now().strftime('%Y%m%d')}.jsonl"
        )

        try:
            with open(daily_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(log_entry) + "\n")
        except Exception as e:
            logger.error(f"Failed to write prediction log: {e}")

        # Also log to channel-specific file
        channel_file = os.path.join(self.log_dir, f"{username}_predictions.jsonl")

        try:
            with open(channel_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(log_entry) + "\n")
        except Exception as e:
            logger.error(f"Failed to write channel prediction log: {e}")

    def get_recent_predictions(
        self, username: Optional[str] = None, hours: int = 24
    ) -> list[Dict[str, Any]]:
        """Get recent prediction events.

        Args:
            username: Filter by username (None for all)
            hours: Number of hours to look back

        Returns:
            List of prediction events
        """
        events = []
        cutoff = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)

        # Read from daily files
        for i in range(hours // 24 + 1):
            date = (cutoff - timedelta(days=i)).strftime("%Y%m%d")
            daily_file = os.path.join(self.log_dir, f"predictions_{date}.jsonl")

            if os.path.exists(daily_file):
                try:
                    with open(daily_file, "r", encoding="utf-8") as f:
                        for line in f:
                            try:
                                event = json.loads(line.strip())
                                event_time = datetime.fromisoformat(event["timestamp"])
                                if event_time >= (
                                    datetime.now() - timedelta(hours=hours)
                                ):
                                    if (
                                        not username
                                        or event.get("username") == username
                                    ):
                                        events.append(event)
                            except (json.JSONDecodeError, KeyError, ValueError):
                                continue
                except Exception as e:
                    logger.error(f"Error reading prediction log {daily_file}: {e}")

        return sorted(events, key=lambda x: x["timestamp"], reverse=True)

    def get_prediction_stats(self, username: Optional[str] = None) -> Dict[str, Any]:
        """Get prediction statistics for a channel or all channels."""
        recent_events = self.get_recent_predictions(username, hours=168)  # Last week

        stats = {
            "total_predictions": 0,
            "votes_placed": 0,
            "votes_won": 0,
            "votes_lost": 0,
            "votes_refunded": 0,
            "win_rate": 0.0,
            "successful_votes": 0,  # backward compatibility alias for votes_placed
            "failed_votes": 0,
            "total_points_bet": 0,
            "total_points_won": 0,
            "channels": set() if not username else {username},
            "last_prediction": None,
            "recent_results": [],
        }

        # Keep track of handled prediction IDs to avoid duplicate counting
        seen_resolved = set()

        for event in recent_events:
            ev_type = event.get("event_type")
            pred = event.get("prediction", {})
            pred_id = pred.get("id")
            info = event.get("additional_info", {})

            if ev_type == "discovered":
                stats["total_predictions"] += 1
                stats["channels"].add(event.get("username", ""))
                if not stats["last_prediction"]:
                    stats["last_prediction"] = event

            elif ev_type == "voted":
                stats["votes_placed"] += 1
                stats["successful_votes"] += 1
                stats["total_points_bet"] += info.get("amount", 0)

            elif ev_type == "vote_failed":
                stats["failed_votes"] += 1

            elif ev_type in ("prediction_result", "resolved"):
                if pred_id and pred_id in seen_resolved:
                    continue
                if pred_id:
                    seen_resolved.add(pred_id)

                res = info.get("result", "").lower()
                if res in ("won", "win"):
                    stats["votes_won"] += 1
                    stats["total_points_won"] += info.get("points_won", 0)
                elif res in ("lost", "lose"):
                    stats["votes_lost"] += 1
                elif res in ("refunded", "cancelled", "canceled"):
                    stats["votes_refunded"] += 1

                stats["recent_results"].append(event)

        # Calculate win rate
        decided_votes = stats["votes_won"] + stats["votes_lost"]
        if decided_votes > 0:
            stats["win_rate"] = round((stats["votes_won"] / decided_votes) * 100, 1)

        stats["channels"] = list(stats["channels"])
        return stats


# Global prediction logger instance
prediction_logger = PredictionLogger()
