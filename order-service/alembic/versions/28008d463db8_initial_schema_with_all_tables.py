"""Initial schema with all tables

Revision ID: 28008d463db8
Revises:
Create Date: 2025-12-10 20:45:19.377167

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '28008d463db8'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create orders table
    op.create_table(
        'orders',
        sa.Column('order_id', sa.String(), nullable=False),
        sa.Column('customer_id', sa.String(), nullable=False),
        sa.Column('items', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('amount', sa.Float(), nullable=False),
        sa.Column('status', sa.String(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint('order_id')
    )

    # Create outbox table
    op.create_table(
        'outbox',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('event_type', sa.String(), nullable=False),
        sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('published_at', sa.String(), nullable=True),
        sa.Column('retry_count', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('last_retry_at', sa.String(), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )

    # Create processed_inbox table
    op.create_table(
        'processed_inbox',
        sa.Column('event_key', sa.String(), nullable=False),
        sa.PrimaryKeyConstraint('event_key')
    )

    # Create dead_letter_queue table
    op.create_table(
        'dead_letter_queue',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('original_event_type', sa.String(), nullable=False),
        sa.Column('payload', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('retry_count', sa.Integer(), nullable=False),
        sa.Column('last_retry_at', sa.String(), nullable=True),
        sa.Column('failure_reason', sa.String(), nullable=False),
        sa.Column('moved_to_dlq_at', sa.String(), nullable=False),
        sa.PrimaryKeyConstraint('id')
    )


def downgrade() -> None:
    op.drop_table('dead_letter_queue')
    op.drop_table('processed_inbox')
    op.drop_table('outbox')
    op.drop_table('orders')
