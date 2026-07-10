"""add vote tables

Revision ID: c3d4e5f6g7h8
Revises: a1b2c3d4e5f6
Create Date: 2026-07-08

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'c3d4e5f6g7h8'
down_revision: Union[str, Sequence[str], None] = 'a1b2c3d4e5f6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. itinerary_snapshots
    op.create_table(
        'itinerary_snapshots',
        sa.Column('snapshot_id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('user_id', sa.Integer(), nullable=False),
        sa.Column('version_num', sa.Integer(), nullable=False),
        sa.Column('plan_title', sa.String(length=100), nullable=True),
        sa.Column('itinerary', sa.JSON(), nullable=False),
        sa.Column('estimated_cost', sa.JSON(), nullable=True),
        sa.Column('selected_acc', sa.JSON(), nullable=True),
        sa.Column('traveldates', sa.String(length=50), nullable=True),
        sa.Column('city', sa.String(length=50), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['user_id'], ['users.user_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('snapshot_id'),
    )
    op.create_index('ix_itinerary_snapshots_user_id', 'itinerary_snapshots', ['user_id'])

    # 2. vote_sessions
    op.create_table(
        'vote_sessions',
        sa.Column('vote_id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('creator_id', sa.Integer(), nullable=False),
        sa.Column('vote_type', sa.Enum('solo', 'group', name='votetype'), nullable=False),
        sa.Column('room_id', sa.Integer(), nullable=True),
        sa.Column('snapshot_ids', sa.JSON(), nullable=False),
        sa.Column('status', sa.Enum('active', 'finalized', name='votestatus'), nullable=False, server_default='active'),
        sa.Column('winner_snapshot_id', sa.Integer(), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['creator_id'], ['users.user_id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['room_id'], ['chat_rooms.room_id'], ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['winner_snapshot_id'], ['itinerary_snapshots.snapshot_id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('vote_id'),
    )
    op.create_index('ix_vote_sessions_creator_id', 'vote_sessions', ['creator_id'])

    # 3. vote_records
    op.create_table(
        'vote_records',
        sa.Column('record_id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('vote_session_id', sa.Integer(), nullable=False),
        sa.Column('voter_id', sa.Integer(), nullable=False),
        sa.Column('snapshot_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=True),
        sa.ForeignKeyConstraint(['snapshot_id'], ['itinerary_snapshots.snapshot_id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['vote_session_id'], ['vote_sessions.vote_id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['voter_id'], ['users.user_id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('record_id'),
        sa.UniqueConstraint('vote_session_id', 'voter_id', name='uq_one_vote_per_session'),
    )
    op.create_index('ix_vote_records_vote_session_id', 'vote_records', ['vote_session_id'])


def downgrade() -> None:
    op.drop_index('ix_vote_records_vote_session_id', table_name='vote_records')
    op.drop_table('vote_records')

    op.drop_index('ix_vote_sessions_creator_id', table_name='vote_sessions')
    op.drop_table('vote_sessions')
    op.execute("DROP TYPE IF EXISTS votestatus")
    op.execute("DROP TYPE IF EXISTS votetype")

    op.drop_index('ix_itinerary_snapshots_user_id', table_name='itinerary_snapshots')
    op.drop_table('itinerary_snapshots')
