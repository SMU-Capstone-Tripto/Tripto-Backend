from fastapi import HTTPException, WebSocket
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.chat_model import ChatRoom, ChatRoomMember, ChatMessage
from app.schemas.chat_schema import ChatMessageCreate
from typing import Dict, List, Optional
from app.services import agent_service
from app.core.database import AsyncSessionLocal
from app.services.notification_service import send_chat_notification
import json 

# 채팅방 생성
async def create_room(db: AsyncSession, owner_id: int, invited_ids: List[int], room_name: Optional[str] = None) -> int:
    BOT_USER_ID=-1

    all_member_ids = list(set(invited_ids + [owner_id, BOT_USER_ID]))
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
    new_message = ChatMessage(
        room_id=room_id,
        sender_id=sender_id,
        content=content,
        message_type= message_type
    )
    db.add(new_message)
    await db.commit()
    await db.refresh(new_message)

    # 푸시알림 전송
    result = await db.execute(select(ChatRoom).where(ChatRoom.room_id == room_id))
    room = result.scalar_one_or_none()
    if room:
        # 발신자 정보 조회
        from app.models.user_model import User
        sender_result = await db.execute(select(User).where(User.user_id == sender_id))
        sender = sender_result.scalar_one_or_none()

        if sender:
            await send_chat_notification(
                db=db,
                sender_nickname=sender.nickname,
                room_id=room_id,
                message_content=content,
                recipient_user_ids=room.member_ids,
                exclude_user_id=sender_id
            )

    return new_message

async def delete_message(db: AsyncSession, room_id: int, user_id: int, message_id: int):
    # 1. 삭제할 메시지가 존재하는지, 그리고 본인이 작성한 메시지가 맞는지 확인
    result = await db.execute(
        select(ChatMessage).where(
            ChatMessage.message_id == message_id, 
            ChatMessage.room_id == room_id
        )
    )
    message = result.scalar_one_or_none()
    
    if not message:
        raise ValueError("메시지를 찾을 수 없습니다.")
    if message.sender_id != user_id:
        raise ValueError("본인이 작성한 메시지만 삭제할 수 있습니다.")

    # 2. 삭제할 메시지 '바로 직전'의 메시지 ID 찾기
    prev_msg_result = await db.execute(
        select(ChatMessage.message_id)
        .where(
            ChatMessage.room_id == room_id,
            ChatMessage.message_id < message_id
        )
        .order_by(ChatMessage.message_id.desc())
        .limit(1)
    )
    prev_msg_id = prev_msg_result.scalar_one_or_none() # 이전 메시지가 없으면 None 반환

    # 3. 누군가의 마지막 읽은 메시지가 '삭제할 메시지'라면, 직전 메시지 ID로 업데이트
    await db.execute(
        update(ChatRoomMember)
        .where(
            ChatRoomMember.room_id == room_id,
            ChatRoomMember.last_read_message_id == message_id
        )
        .values(last_read_message_id=prev_msg_id)
    )

    # 4. 메시지 영구 삭제 (DELETE)
    await db.execute(
        delete(ChatMessage).where(ChatMessage.message_id == message_id)
    )
    
    await db.commit()
    return True

# 사용자가 속한 채팅방 목록, 새로운 메시지 여부 조회
async def get_user_rooms(db: AsyncSession, user_id: int):
    subq = (
        select(
            ChatMessage.room_id,
            func.max(ChatMessage.created_at).label("latest_message_at"),
            func.max(ChatMessage.message_id).label("latest_message_id")
        )
        .group_by(ChatMessage.room_id)
        .subquery()
    )
    stmt = (
        select(ChatRoom, ChatRoomMember.last_read_message_id)
        .join(ChatRoomMember, ChatRoom.room_id == ChatRoomMember.room_id)
        .outerjoin(subq, ChatRoom.room_id == subq.c.room_id)
        .where(ChatRoomMember.user_id == user_id)
        .order_by(func.coalesce(subq.c.latest_message_at, ChatRoom.created_at).desc())
    )
    result = await db.execute(stmt)
    rows = result.all() 

    room_list_with_status = []
    
    for room, last_read_id in rows:
        latest_msg_result = await db.execute(
            select(func.max(ChatMessage.message_id))
            .where(ChatMessage.room_id == room.room_id)
        )
        latest_message_id = latest_msg_result.scalar() or 0
        has_new_message = latest_message_id > (last_read_id or 0)
        
        room_list_with_status.append({
            "room_id": room.room_id,
            "room_name": room.room_name,
            "owner_id": room.owner_id,
            "member_ids": room.member_ids,
            "created_at": room.created_at,
            "has_new_message": has_new_message, 
        })
    return room_list_with_status

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

import json

async def generate_and_send_bot_reply(room_id: int, user_id: int, user_content: str, nickname: str):
    bot_nickname = "Tripto"
    BOT_USER_ID = -1  # 상수 값이 선언되어 있지 않다면 추가 확인 필요
    
    async with AsyncSessionLocal() as db:
        stream = agent_service.chat_stream(user_id, user_content, db, nickname, room_id=room_id)
        
        async for chunk in stream:
            try:
                if isinstance(chunk, str):
                    chunk = chunk.strip()
                    if chunk == "data: [DONE]":
                        break
                    if chunk.startswith("data: "):
                        json_str = chunk[6:]
                        data = json.loads(json_str)
                    else:
                        data = json.loads(chunk)
                else:
                    data = chunk
                
                event_type = data.get("type")
                
                if event_type == "status":
                    await manager.broadcast(room_id, json.dumps({
                        "type": "bot_status",
                        "sender_id": BOT_USER_ID,
                        "content": data.get("message")
                    }, ensure_ascii=False))
                    
                elif event_type == "result":
                    final_content = data.get("content")
                    step = data.get("step")
                    
                    # 최적화 완료 시 전체 JSON 데이터를 DB에 저장
                    msg_type = "text"
                    if step == "optimized":
                        plan_data = {
                            "plan_title": data.get("plan_title", "여행 계획"),
                            "itinerary": data.get("itinerary"),
                            "estimated_cost": data.get("estimated_cost"),
                            "selected_acc": data.get("selected_acc")
                        }
                        # DB content 컬럼에 넣기 위해 JSON을 문자열(str)로 변환
                        final_content = json.dumps(plan_data, ensure_ascii=False)
                        msg_type = "plan"  # 프론트가 파싱을 판단할 수 있도록 별도 타입 지정
                        
                    elif not final_content and step == "optimized":
                        plan_title = data.get("plan_title", "여행 계획")
                        final_content = f"🎉 '{plan_title}' 일정이 완성되었습니다! 투표를 통해 일정을 확정해 보세요."

                    # DB 저장 (위에서 수정한 save_message 함수에 message_type 인자 전달)
                    new_bot_msg = await save_message(db, room_id, BOT_USER_ID, final_content, message_type=msg_type)
                    
                    broadcast_data = {
                        "type": "new_message",
                        "message_id": new_bot_msg.message_id,
                        "sender_id": BOT_USER_ID,
                        "sender_nickname": bot_nickname,
                        "content": final_content,
                        "message_type": msg_type, # 웹소켓에도 message_type 전달
                        "step": step,
                        "snapshot_id": data.get("snapshot_id")
                    }
                    
                    # 최적화 완료 시 관련 UI 데이터 추가 탑재 (웹소켓 데이터용)
                    if step == "optimized":
                        broadcast_data["plan_title"] = data.get("plan_title")
                        broadcast_data["itinerary"] = data.get("itinerary")
                        broadcast_data["estimated_cost"] = data.get("estimated_cost")
                        broadcast_data["selected_acc"] = data.get("selected_acc")
                        
                    await manager.broadcast(room_id, json.dumps(broadcast_data, ensure_ascii=False))
                    
                elif event_type == "error":
                    await manager.broadcast(room_id, json.dumps({
                        "type": "bot_error",
                        "content": "앗, 에이전트 처리 중 문제가 발생했어요. 다시 시도해 주세요!"
                    }, ensure_ascii=False))
                    
            except Exception as e:
                print(f"챗봇 스트림 처리 중 에러: {e}")

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

