"""Add encrypted kick.com site session token for voting."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261012_0200"
down_revision: str | None = "20261012_0100"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "kick_token",
        sa.Column("session_token_enc", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("kick_token", "session_token_enc")