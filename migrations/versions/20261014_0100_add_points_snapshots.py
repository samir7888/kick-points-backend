"""Add append-only channel-points balance snapshots.

Powers the points-growth analytics dashboard: a background task records each
user's balance per channel periodically, and the API aggregates those rows into
per-channel and total growth series. Rows are never updated or deleted by the
app, so the series stays a faithful history.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261014_0100"
down_revision: str | None = "20261013_0100"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "points_snapshot",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "channel_id",
            sa.Integer(),
            sa.ForeignKey("channel.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("channel_slug", sa.String(length=100), nullable=False),
        sa.Column("balance", sa.Integer(), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
            index=True,
        ),
    )
    op.create_index(
        "ix_points_snapshot_user_channel_time",
        "points_snapshot",
        ["user_id", "channel_id", "recorded_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_points_snapshot_user_channel_time", table_name="points_snapshot"
    )
    op.drop_table("points_snapshot")
