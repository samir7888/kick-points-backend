"""Store Cloudinary location for manual QR payment proofs.

When Cloudinary is configured the transfer screenshot is uploaded there and
these columns hold its secure URL and public id, so the disk path is no longer
the only place a proof can live.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261017_0100"
down_revision: str | None = "20261014_0100"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "qr_payment",
        sa.Column("proof_url", sa.String(length=1024), nullable=True),
    )
    op.add_column(
        "qr_payment",
        sa.Column("proof_public_id", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("qr_payment", "proof_public_id")
    op.drop_column("qr_payment", "proof_url")
