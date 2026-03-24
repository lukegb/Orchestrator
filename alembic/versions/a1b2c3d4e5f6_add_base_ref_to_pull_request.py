"""Add base_ref to pull_request

Revision ID: a1b2c3d4e5f6
Revises: 0607799fb3ac
Create Date: 2026-03-24 21:59:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "0607799fb3ac"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add base_ref column to pull_request table."""
    op.add_column(
        "pull_request",
        sa.Column("base_ref", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    """Remove base_ref column from pull_request table."""
    op.drop_column("pull_request", "base_ref")
