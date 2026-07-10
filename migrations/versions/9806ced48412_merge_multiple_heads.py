"""merge multiple heads

Revision ID: 9806ced48412
Revises: c3d4e5f6g7h8, fcc11c2cc501
Create Date: 2026-07-10 15:31:29.907096

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9806ced48412'
down_revision: Union[str, Sequence[str], None] = ('c3d4e5f6g7h8', 'fcc11c2cc501')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    pass


def downgrade() -> None:
    """Downgrade schema."""
    pass
