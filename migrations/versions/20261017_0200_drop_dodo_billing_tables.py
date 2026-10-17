"""drop dodo billing tables

Revision ID: 20261017_0200
Revises: 20261017_0100
Create Date: 2026-10-17 02:00:00.000000

Payments are handled manually via the QR / bank-transfer flow, so the
Dodo-specific ``subscription`` and ``webhook_event`` tables are no longer used.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20261017_0200"
down_revision: str | None = "20261017_0100"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index(op.f("ix_subscription_user_id"), table_name="subscription")
    op.drop_table("subscription")
    op.drop_table("webhook_event")


def downgrade() -> None:
    op.create_table(
        "webhook_event",
        sa.Column(
            "id",
            sa.String(length=100),
            nullable=False,
            comment="Provider webhook event ID (ensures idempotency)",
        ),
        sa.Column(
            "type",
            sa.String(length=100),
            nullable=False,
            comment="Event type from provider",
        ),
        sa.Column("payload", sa.JSON(), nullable=False, comment="Raw webhook payload"),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="When webhook was received",
        ),
        sa.Column(
            "processed_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="When webhook was successfully processed",
        ),
        sa.Column(
            "error",
            sa.Text(),
            nullable=True,
            comment="Processing error details (if any)",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_webhook_event")),
    )
    op.create_table(
        "subscription",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "user_id",
            sa.Integer(),
            nullable=False,
            comment="User who owns this subscription",
        ),
        sa.Column(
            "dodo_subscription_id",
            sa.String(length=100),
            nullable=False,
            comment="Dodo Payments subscription ID",
        ),
        sa.Column(
            "dodo_customer_id",
            sa.String(length=100),
            nullable=False,
            comment="Dodo Payments customer ID",
        ),
        sa.Column(
            "status",
            sa.String(length=50),
            nullable=False,
            comment="Subscription status from Dodo",
        ),
        sa.Column(
            "current_period_end",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Current billing period end",
        ),
        sa.Column(
            "cancel_at_period_end",
            sa.Boolean(),
            nullable=False,
            comment="Whether subscription cancels at period end",
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
            comment="Last update from webhook",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["user.id"],
            name=op.f("fk_subscription_user_id_user"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_subscription")),
        sa.UniqueConstraint(
            "dodo_subscription_id", name=op.f("uq_subscription_dodo_subscription_id")
        ),
    )
    op.create_index(
        op.f("ix_subscription_user_id"), "subscription", ["user_id"], unique=False
    )
