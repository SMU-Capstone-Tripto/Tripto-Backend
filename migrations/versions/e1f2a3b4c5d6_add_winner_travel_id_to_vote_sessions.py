"""add winner_travel_id to vote_sessions

투표 마감(수동 finalize / 만료 자동 마감 / 조회 시 정리)이 Travel을 중복 생성하지
않도록 하는 멱등 가드 컬럼.

Revision ID: e1f2a3b4c5d6
Revises: fcm_token_001
Create Date: 2026-09-01

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'e1f2a3b4c5d6'
down_revision: Union[str, Sequence[str], None] = 'fcm_token_001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'vote_sessions',
        sa.Column('winner_travel_id', sa.Integer(), nullable=True),
    )
    op.create_foreign_key(
        'fk_vote_sessions_winner_travel_id',
        'vote_sessions', 'travels',
        ['winner_travel_id'], ['travel_id'],
        ondelete='SET NULL',
    )


def downgrade() -> None:
    op.drop_constraint('fk_vote_sessions_winner_travel_id', 'vote_sessions', type_='foreignkey')
    op.drop_column('vote_sessions', 'winner_travel_id')
