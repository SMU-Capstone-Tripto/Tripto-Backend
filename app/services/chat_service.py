from fastapi import HTTPException, WebSocket
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.chat_model import ChatRoom, ChatRoomMember, ChatMessage
from typing import Dict, List, Optional
import json 

# 채팅방 생성
async def create_room(db: AsyncSession, owner_id: int, invited_ids: List[int], room_name: Optional[str] = None) -> int:
    all_member_ids = list(set(invited_ids + [owner_id]))
    new_room = ChatRoom(owner_id=owner_id, room_name=room_name, member_ids=all_member_ids)
    db.add(new_room)
    await db.flush()
    
    for uid in all_member_ids:
        member = ChatRoomMember(room_id=new_room.room_id, user_id=uid)
        db.add(member)
    
    await db.commit()
    await db.refresh(new_room)
    return new_room

# 채팅 기록 조회 
async def get_messages(db: AsyncSession, room_id: int) -> List[ChatMessage]:
    result = await db.execute(
        select(ChatMessage).where(ChatMessage.room_id == room_id).order_by(ChatMessage.created_at)
    )
    return result.scalars().all()

# (방장만 가능) 사용자 초대
async def invite_users(db: AsyncSession, room_id: int, owner_id: int, new_user_ids: List[int]):
    # 방장 확인
    result = await db.execute(select(ChatRoom).where(ChatRoom.room_id == room_id))
    room = result.scalar_one_or_none()
    
    if not room or room.owner_id != owner_id:
        raise HTTPException(status_code=403, detail="방장만 사용자를 초대할 수 있습니다.")
    
    # 사용자 추가
    for uid in new_user_ids:
        # 이미 멤버인지 확인(선택)
        member = ChatRoomMember(room_id=room_id, user_id=uid)
        db.add(member)
        
    await db.commit()

# 채팅방 멤버 여부 확인
async def check_member(db: AsyncSession, room_id: int, user_id: int) -> bool:
    result = await db.execute(
        select(ChatRoomMember).where(
            (ChatRoomMember.room_id == room_id) & (ChatRoomMember.user_id == user_id)
        )
    )
    return result.scalar_one_or_none() is not None

# 채팅방 나가기
async def leave_room(db: AsyncSession, room_id: int, user_id: int):
    result = await db.execute(select(ChatRoom).where(ChatRoom.room_id == room_id))
    room = result.scalar_one_or_none()
    if not room: return

    stmt = delete(ChatRoomMember).where(ChatRoomMember.room_id == room_id, ChatRoomMember.user_id == user_id)
    await db.execute(stmt)
    
    # 방장 위임
    if room.owner_id == user_id:
        remaining_members = [m for m in room.member_ids if m != user_id]
        if remaining_members:
            room.owner_id = remaining_members[0] # 첫 번째 멤버에게 위임
        else:
            # 방에 아무도 없으면 방을 삭제
            await db.execute(delete(ChatRoom).where(ChatRoom.room_id == room_id))
            await db.commit()
            return
        
    room.member_ids = [m for m in room.member_ids if m != user_id]
    await db.commit()
    await manager.broadcast(room_id, json.dumps({
        "type": "user_left",
        "user_id": user_id,
        "content": f"User {user_id}님이 퇴장했습니다.",
    }, ensure_ascii=False))
  
# DB에 메시지 저장 
async def save_message(db: AsyncSession, room_id: int, sender_id: int, content: str, message_type: str = "text"):
    new_message = ChatMessage(room_id=room_id, sender_id=sender_id, content=content, message_type=message_type)
    db.add(new_message)
    await db.commit()
    await db.refresh(new_message)
    return new_message

# 사용자가 속한 채팅방 목록 조회
async def get_user_rooms(db: AsyncSession, user_id: int) -> List[ChatRoom]:
    result = await db.execute(
        select(ChatRoom)
        .join(ChatRoomMember, ChatRoom.room_id == ChatRoomMember.room_id)
        .where(ChatRoomMember.user_id == user_id)
        .order_by(ChatRoom.created_at.desc()) 
    )
    return result.scalars().all()

#사용자가 어디까지 읽었는지 DB에 업데이트
async def update_last_read(db: AsyncSession, room_id: int, user_id: int, message_id: int) -> bool:
    result = await db.execute(
        select(ChatRoomMember).where(
            ChatRoomMember.room_id == room_id,
            ChatRoomMember.user_id == user_id
        )
    )
    member = result.scalar_one_or_none()
    
    # 최신 메시지를 읽었을 때만 덮어씌움 
    if member and (member.last_read_message_id is None or member.last_read_message_id < message_id):
        member.last_read_message_id = message_id
        await db.commit()
        return True
        
    return False

class ConnectionManager:
    def __init__(self):
        self.active_connections: Dict[int, List[WebSocket]] = {}

    async def connect(self, room_id: int, websocket: WebSocket):
        await websocket.accept()
        if room_id not in self.active_connections:
            self.active_connections[room_id] = []
        self.active_connections[room_id].append(websocket)

    def disconnect(self, room_id: int, websocket: WebSocket):
        self.active_connections[room_id].remove(websocket)
        if not self.active_connections[room_id]:
            del self.active_connections[room_id]

    async def broadcast(self, room_id: int, message: str):
        if room_id in self.active_connections:
            for connection in self.active_connections[room_id]:
                await connection.send_text(message)

manager = ConnectionManager()

