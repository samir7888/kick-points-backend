"""Database models package."""

# Import all models here to ensure they are registered with SQLAlchemy
from .audit import AuditLog
from .channel import Channel, PausedReason
from .oauth import KickToken, OAuthState, TokenHealth
from .points import PointsSnapshot
from .prediction import Prediction, PredictionState, Vote, VoteStatus
from .qr_payment import QrPayment
from .session import Session
from .settings import UserSettings
from .system_setting import SystemSetting
from .user import User

PauseReason = PausedReason

__all__ = [
    "AuditLog",
    "Channel",
    "KickToken",
    "OAuthState",
    "PausedReason",
    "PauseReason",
    "PointsSnapshot",
    "Prediction",
    "PredictionState",
    "QrPayment",
    "Session",
    "SystemSetting",
    "TokenHealth",
    "User",
    "UserSettings",
    "Vote",
    "VoteStatus",
]
