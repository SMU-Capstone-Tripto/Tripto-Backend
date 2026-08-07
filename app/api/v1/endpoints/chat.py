import asyncio
import json
from typing import List
from fastapi import HTTPException, APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_async_db, AsyncSessionLocal
from app.core.dependencies import get_current_user
from app.core.security import verify_access_token
from app.models.user_model import User
from app.models.chat_model import ChatMessage, ChatRoom, ChatRoomMember
from app.schemas.chat_schema import ChatRoomCreate, ChatRoomResponse, ChatRoomInvite, ChatImageMessageCreate
from app.services import chat_service

router = APIRouter(prefix="/chat", tags=["채팅"])

active_bot_tasks = set()

# 채팅방 생성 API
@router.post("/rooms", response_model=ChatRoomResponse, summary="채팅방 생성")
async def create_room(
    body: ChatRoomCreate,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user)
):
    room = await chat_service.create_room(
        db, current_user.user_id, body.invited_user_ids, body.room_name
    )
    return room

# 채팅방 목록 조회 API
@router.get("/rooms", response_model=List[ChatRoomResponse], summary="자신이 속한 채팅방 목록 조회")
async def get_my_rooms(
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user)
):
    rooms = await chat_service.get_user_rooms(db, current_user.user_id)
    return rooms

# 멤버 초대 API
@router.post("/{room_id}/invite", summary="방장이 사용자 초대")
async def invite_to_room(
    room_id: int,
    body: ChatRoomInvite, 
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user)
):
    await chat_service.invite_users(db, room_id, current_user.user_id, body.invited_user_ids)
    return {"message": "사용자가 성공적으로 초대되었습니다."}

# 채팅 기록 조회 API
@router.get("/{room_id}/messages", summary="메시지 조회")
async def get_messages(room_id: int, db: AsyncSession = Depends(get_async_db)):
    # DB에서 해당 방의 대화 기록 조회
    msg_result = await db.execute(
        select(ChatMessage).where(ChatMessage.room_id == room_id).order_by(ChatMessage.created_at.asc())
    )
    messages = msg_result.scalars().all()

    # 읽음 위치 + 조인으로 닉네임까지 조회
    member_result = await db.execute(
        select(ChatRoomMember.user_id, ChatRoomMember.last_read_message_id, User.nickname, User.profile_image)
        .join(User, ChatRoomMember.user_id == User.user_id)
        .where(ChatRoomMember.room_id == room_id)
    )
    
    read_statuses = {}
    user_names = {} 
    user_profiles = {}

    for row in member_result.all():
        # 읽음 상태 맵핑
        if row.last_read_message_id is not None:
            read_statuses[str(row.user_id)] = row.last_read_message_id
        
        user_names[str(row.user_id)] = row.nickname
        user_profiles[str(row.user_id)] = row.profile_image

    return {
        "messages": messages,
        "read_statuses": read_statuses,
        "user_names": user_names,
        "user_profiles": user_profiles
    }

# 채팅방 나가기 API
@router.delete("/{room_id}/leave", summary="채팅방 나가기")
async def leave_room(
    room_id: int, 
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user)
):
    await chat_service.leave_room(db, room_id, current_user.user_id)
    return {"message": f"id {current_user.user_id}: 채팅방을 나갔습니다."}

# 사진 전송 API
# 클라이언트는 POST /api/v1/files/presigned-url (category=chat)로 발급받은 upload_url에
# 이미지를 직접 S3로 업로드한 뒤, 응답의 file_url을 아래 API에 전달해 메시지로 저장한다.
@router.post("/{room_id}/image", summary="사진 전송")
async def send_image(
    room_id: int,
    body: ChatImageMessageCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db)
):
    file_url = body.image_url


    # DB에 메시지 저장
    message = await chat_service.save_message(
        db, room_id, current_user.user_id, file_url, message_type="image"
    )
    
    # 사진을 보낸 사람의 마지막 읽은 위치 업데이트
    await chat_service.update_last_read(db, room_id, current_user.user_id, message.message_id)
    
    # 웹소켓으로 알림
    broadcast_data = {
        "type": "new_message",
        "message_id": message.message_id,
        "sender_id": current_user.user_id,
        "sender_nickname": current_user.nickname,
        "sender_profile_image": current_user.profile_image,
        "content": file_url,
        "message_type": "image"
    }
    await chat_service.manager.broadcast(room_id, json.dumps(broadcast_data))
    
    return {"message": "사진이 전송되었습니다.", "url": file_url}

@router.delete("/{room_id}/messages/{message_id}", summary="메시지 삭제")
async def delete_chat_message(
    room_id: int,
    message_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user)
):
    try:
        # DB에서 메시지 삭제 및 last_read 갱신 처리
        await chat_service.delete_message(db, room_id, current_user.user_id, message_id)
        
        # 채팅방에 접속 중인 모든 클라이언트에게 실시간 삭제 알림 브로드캐스트
        await chat_service.manager.broadcast(room_id, json.dumps({
            "type": "delete_message",    
            "message_id": message_id,
            "room_id": room_id
        }))
        
        return {"message": "메시지가 삭제되었습니다."}
        
    except ValueError as e:
        raise HTTPException(status_code=403, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail="메시지 삭제 중 오류가 발생했습니다.")

@router.websocket("/ws/{room_id}")
async def websocket_endpoint(websocket: WebSocket, room_id: int, token: str = Query(...)):

    async with AsyncSessionLocal() as db:
        try:
            # 토큰 해독 및 유저 ID 추출
            payload = verify_access_token(token)
            user_id = int(payload.get("sub"))
        except HTTPException:
            await websocket.close(code=1008) # Policy Violation
            return

        # 유저 활성화 상태 및 닉네임 조회
        result = await db.execute(select(User).where(User.user_id == user_id))
        user = result.scalar_one_or_none()
        if not user or not user.is_active:
            await websocket.close(code=1008)
            return
            
        my_nickname = user.nickname
        my_profile_image = user.profile_image

        # 채팅방 멤버 권한 확인
        result = await db.execute(select(ChatRoom).where(ChatRoom.room_id == room_id))
        room = result.scalar_one_or_none()
        if not room or user_id not in room.member_ids:
            await websocket.close(code=1003) # Unsupported Data
            return
        
    await chat_service.manager.connect(room_id, websocket)

    try:
        while True:
            data = await websocket.receive_text()
            
            try:
                payload = json.loads(data)
                action = payload.get("action")

                async with AsyncSessionLocal() as db:
                    if action == "send_message":
                        
                        content = payload.get("content")
                        BOT_USER_ID = -1
                        
                        # DB에 메시지 저장 
                        new_msg = await chat_service.save_message(db, room_id, user_id, content)
                        await chat_service.update_last_read(db, room_id, user_id, new_msg.message_id)
                        
                        await chat_service.manager.broadcast(room_id, json.dumps({
                            "type": "new_message",
                            "message_id": new_msg.message_id,
                            "sender_id": user_id,
                            "sender_nickname": my_nickname, 
                            "sender_profile_image": my_profile_image,
                            "content": content,
                            "message_type": "text"
                        }))
                        if BOT_USER_ID in room.member_ids and user_id != BOT_USER_ID:
                            is_group_chat = len(room.member_ids) > 2
                            bot_triggered = ("@트립토" in content) or ("@Tripto" in content)

                            if (is_group_chat and bot_triggered) or not is_group_chat:
                                
                                clean_content = content
                                if is_group_chat:
                                    clean_content = clean_content.replace("@트립토", "").replace("@Tripto", "").strip() 
                                
                                task = asyncio.create_task(
                                    chat_service.generate_and_send_bot_reply(room_id, user_id, clean_content, my_nickname)
                                )
                                active_bot_tasks.add(task)
                                task.add_done_callback(active_bot_tasks.discard)
                    
                    elif action == "read_message":
                        message_id = payload.get("message_id")
                        
                        updated = await chat_service.update_last_read(db, room_id, user_id, message_id)
                        if updated:
                            await chat_service.manager.broadcast(room_id, json.dumps({
                                "type": "read_update",
                                "user_id": user_id,
                                "last_read_message_id": message_id
                            }))

            except json.JSONDecodeError:
                print("잘못된 JSON 형식의 데이터가 수신됨")
            except Exception as e:
                print(f"웹소켓 메시지 처리 중 에러 발생: {e}")

    except WebSocketDisconnect:
        chat_service.manager.disconnect(room_id, websocket)
