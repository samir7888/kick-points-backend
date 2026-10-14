"""Manual bank-transfer (QR) payment submissions."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from ..base import BaseModel


class QrPayment(BaseModel):
    """A user's proof of a manual QR/bank payment awaiting admin review.

    Users without an international card pay by scanning the dashboard QR and
    uploading their transfer screenshot. An admin reviews the proof on the
    `/payments` page and either confirms it (which grants one month of
    Premium) or rejects it.
    """

    PENDING = "pending"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="User who submitted the proof",
    )
    username: Mapped[str] = mapped_column(
        String(100), nullable=False, comment="Kick username snapshot at submission"
    )
    email: Mapped[str | None] = mapped_column(
        String(255), nullable=True, comment="Email snapshot at submission"
    )
    reference: Mapped[str | None] = mapped_column(
        String(255), nullable=True, comment="Optional transfer reference / sender name"
    )
    amount: Mapped[float | None] = mapped_column(
        Numeric(10, 2), nullable=True, comment="Optional amount the user reports sending"
    )
    proof_filename: Mapped[str] = mapped_column(
        String(255), nullable=False, comment="Stored screenshot of the transfer"
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=PENDING,
        comment="pending, confirmed or rejected",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="Submission timestamp",
    )
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="When an admin confirmed or rejected this submission",
    )
    decided_by: Mapped[str | None] = mapped_column(
        String(100), nullable=True, comment="Admin username that reviewed it"
    )

    def __repr__(self) -> str:
        return (
            f"<QrPayment(id={self.id}, user_id={self.user_id}, "
            f"status='{self.status}')>"
        )
