"""add notifications table

Revision ID: a1b2c3d4e5f6
Revises: 881a41583475
Create Date: 2026-06-27

"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa


revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '881a41583475'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'notifications',
        sa.Column('notification_id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('recipient_id', sa.Integer(), nullable=False),
        sa.Column('actor_id', sa.Integer(), nullable=False),
        sa.Column('type', sa.String(length=50), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('is_read', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['actor_id'], ['users.user_id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['recipient_id'], ['users.user_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('notification_id'),
    )
    op.create_index(op.f('ix_notifications_notification_id'), 'notifications', ['notification_id'], unique=False)
    op.create_index(op.f('ix_notifications_recipient_id'), 'notifications', ['recipient_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_notifications_recipient_id'), table_name='notifications')
    op.drop_index(op.f('ix_notifications_notification_id'), table_name='notifications')
    op.drop_table('notifications')