"""rename reason to fail_reason

Revision ID: 7e3f3f6f2f1c
Revises: 6a2fb58e99b4
Create Date: 2025-12-11
"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "7e3f3f6f2f1c"
down_revision = "6a2fb58e99b4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("orders", "reason", new_column_name="fail_reason")


def downgrade() -> None:
    op.alter_column("orders", "fail_reason", new_column_name="reason")
