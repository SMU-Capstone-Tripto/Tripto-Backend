from fastapi import APIRouter, Depends, File, Query, UploadFile, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.database import get_async_db
from app.core.dependencies import get_current_user
from app.models.user_model import User
from app.models.chat_model import ChatMessage, ChatRoom
from app.schemas.chat_schema import ChatRoomCreate, ChatRoomResponse, ChatRoomInvite, ChatMessageResponse
from app.services import chat_service

router = APIRouter(prefix="/chat", tags=["채팅"])

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

@router.post("/{room_id}/invite", summary="방장이 사용자 초대")
async def invite_to_room(
    room_id: int,
    body: ChatRoomInvite, 
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user)
):
    await chat_service.invite_users(db, room_id, current_user.user_id, body.invited_user_ids)
    return {"message": "사용자가 성공적으로 초대되었습니다."}

@router.post("/{room_id}/image", summary="사진 전송")
async def send_image(
    room_id: int,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db)
):
    # 이미지 저장
    # file_url = await upload_to_s3(file) 
    file_url = "https://example.com/uploaded_image.jpg" 
    
    # DB에 메시지 저장
    message = ChatMessage(
        room_id=room_id,
        sender_id=current_user.user_id,
        content=file_url,
        message_type="image"
    )
    db.add(message)
    await db.commit()
    return {"message": "사진이 전송되었습니다.", "url": file_url}

@router.get("/{room_id}/messages", summary="메시지 조회")
async def get_messages(room_id: int, db: AsyncSession = Depends(get_async_db)):
    # DB에서 해당 방의 대화 기록 조회
    result = await db.execute(
        select(ChatMessage).where(ChatMessage.room_id == room_id).order_by(ChatMessage.created_at.asc())
    )
    return result.scalars().all()

@router.delete("/{room_id}/leave", summary="채팅방 나가기")
async def leave_room(
    room_id: int, 
    db: AsyncSession = Depends(get_async_db),
    # 여기서 user_id를 쿼리로 받거나, 나중에 토큰으로 대체하세요
    user_id: int = Query(...) 
):
    await chat_service.leave_room(db, room_id, user_id)
    return {"message": f"id {user_id}: 채팅방을 나갔습니다."}

@router.websocket("/ws/{room_id}")
async def websocket_endpoint(websocket: WebSocket, room_id: int, user_id: int = Query(...), db: AsyncSession = Depends(get_async_db)):
    # 해당 유저가 이 방의 멤버인지 조회
    result = await db.execute(select(ChatRoom).where(ChatRoom.room_id == room_id))
    room = result.scalar_one_or_none()
    
    if not room or user_id not in room.member_ids:
        await websocket.close(code=1003)
        return
        
    await chat_service.manager.connect(room_id, websocket)
    try:
        while True:
            data = await websocket.receive_text()
            print(f"DEBUG: 방 {room_id} 메시지 - {data}")
            await chat_service.save_message(db, room_id, user_id, data)
            await chat_service.manager.broadcast(room_id, f"User {user_id}: {data}")
    except WebSocketDisconnect:
        chat_service.manager.disconnect(room_id, websocket)