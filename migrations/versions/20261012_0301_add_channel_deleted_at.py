"""Add a soft-delete marker to channels.

Removing a channel hides it from every user-facing view while keeping its
history (votes and predictions still resolve the channel row). A removed row
can be re-activated when the same slug is added again — it is resurrected
instead of hitting the (user_id, channel_slug) unique constraint.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261012_0301"
down_revision: str | None = "20261012_0300"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "channel",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("channel", "deleted_at")