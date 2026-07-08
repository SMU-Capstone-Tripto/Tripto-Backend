"""empty message

Revision ID: fcc11c2cc501
Revises: 708c4e67adf5
Create Date: 2026-07-07 01:34:21.800698

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'fcc11c2cc501'
down_revision: Union[str, Sequence[str], None] = '708c4e67adf5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    op.drop_constraint(
        'chat_room_members_last_read_message_id_fkey', 
        'chat_room_members', 
        type_='foreignkey'
    )
    op.create_foreign_key(
        'chat_room_members_last_read_message_id_fkey',
        'chat_room_members',      
        'chat_messages',         
        ['last_read_message_id'],
        ['message_id'],          
        ondelete='SET NULL'       
    )

def downgrade():
    op.drop_constraint(
        'chat_room_members_last_read_message_id_fkey', 
        'chat_room_members', 
        type_='foreignkey'
    )
    op.create_foreign_key(
        'chat_room_members_last_read_message_id_fkey',
        'chat_room_members',
        'chat_messages',
        ['last_read_message_id'],
        ['message_id']
    )