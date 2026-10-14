"""Add explicit premium renewal grace expiry.

Revision ID: 20261011_2300
Revises: 9451d9e82394
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20261011_2300"
down_revision: str | None = "9451d9e82394"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user",
        sa.Column(
            "premium_grace_until",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="End of temporary premium access after a failed renewal",
        ),
    )


def downgrade() -> None:
    op.drop_column("user", "premium_grace_until")
