"""Database models package."""

# Import all models here to ensure they are registered with SQLAlchemy
from .audit import AuditLog
from .billing import Subscription
from .channel import Channel, PausedReason
from .oauth import KickToken, OAuthState, TokenHealth
from .prediction import Prediction, PredictionState, Vote, VoteStatus
from .qr_payment import QrPayment
from .session import Session
from .settings import UserSettings
from .system_setting import SystemSetting
from .user import User
from .webhook import WebhookEvent

PauseReason = PausedReason

__all__ = [
    "AuditLog",
    "Channel",
    "KickToken",
    "OAuthState",
    "PausedReason",
    "PauseReason",
    "Prediction",
    "PredictionState",
    "QrPayment",
    "Session",
    "SystemSetting",
    "Subscription",
    "TokenHealth",
    "User",
    "UserSettings",
    "Vote",
    "VoteStatus",
    "WebhookEvent",
]
