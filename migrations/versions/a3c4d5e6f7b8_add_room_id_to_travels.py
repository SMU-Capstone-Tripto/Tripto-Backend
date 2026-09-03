"""add room_id to travels

그룹 투표로 확정된 여행을 채팅방 멤버 전원의 '내 여행' 목록에 노출하기 위한 컬럼.

Revision ID: a3c4d5e6f7b8
Revises: f2b3c4d5e6a7
Create Date: 2026-09-03

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'a3c4d5e6f7b8'
down_revision: Union[str, Sequence[str], None] = 'f2b3c4d5e6a7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('travels', sa.Column('room_id', sa.Integer(), nullable=True))
    op.create_index('ix_travels_room_id', 'travels', ['room_id'])
    op.create_foreign_key(
        'fk_travels_room_id',
        'travels', 'chat_rooms',
        ['room_id'], ['room_id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    op.drop_constraint('fk_travels_room_id', 'travels', type_='foreignkey')
    op.drop_index('ix_travels_room_id', table_name='travels')
    op.drop_column('travels', 'room_id')
