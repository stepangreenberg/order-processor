"""add reason column to orders

Revision ID: 6a2fb58e99b4
Revises: 28008d463db8
Create Date: 2025-12-11
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "6a2fb58e99b4"
down_revision = "28008d463db8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("orders", sa.Column("reason", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("orders", "reason")
