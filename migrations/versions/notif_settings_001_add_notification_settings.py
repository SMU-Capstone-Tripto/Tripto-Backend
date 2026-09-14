"""add notification settings columns to users (push_enabled, notif_enabled)

Revision ID: notif_settings_001
Revises: a3c4d5e6f7b8
Create Date: 2026-09-09

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'notif_settings_001'
down_revision = 'a3c4d5e6f7b8'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('users', sa.Column('push_enabled', sa.Boolean(), nullable=False, server_default='1'))
    op.add_column('users', sa.Column('notif_enabled', sa.Boolean(), nullable=False, server_default='1'))


def downgrade() -> None:
    op.drop_column('users', 'notif_enabled')
    op.drop_column('users', 'push_enabled')