"""add fcm_token to users

Revision ID: fcm_token_001
Revises: 5948fa9d3533
Create Date: 2026-08-07

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'fcm_token_001'
down_revision = '5948fa9d3533'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('users', sa.Column('fcm_token', sa.String(length=255), nullable=True))


def downgrade():
    op.drop_column('users', 'fcm_token')