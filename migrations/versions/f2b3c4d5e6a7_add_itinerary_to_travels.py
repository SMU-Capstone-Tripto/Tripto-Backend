"""add itinerary text to travels

투표로 확정된 여행에 AI 일정 원문(일자별 텍스트)을 보존하기 위한 컬럼.
이전에는 Travel + '1일차 일정' 껍데기 Schedule만 저장돼 실제 일정 내용이 유실됐다.

Revision ID: f2b3c4d5e6a7
Revises: e1f2a3b4c5d6
Create Date: 2026-09-02

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'f2b3c4d5e6a7'
down_revision: Union[str, Sequence[str], None] = 'e1f2a3b4c5d6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('travels', sa.Column('itinerary', sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column('travels', 'itinerary')
