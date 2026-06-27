from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_db
from app.core.dependencies import get_current_user
from app.models.user_model import User
from app.schemas.notification_schema import NotificationResponse
from app.services import notification_service

router = APIRouter(prefix="/notifications", tags=["알림"])


@router.get("/", response_model=List[NotificationResponse], summary="알림 목록 조회")
async def get_notifications(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    return await notification_service.get_notifications(db, current_user.user_id)


@router.patch("/{notification_id}/read", summary="알림 읽음 처리")
async def mark_notification_read(
    notification_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    success = await notification_service.mark_as_read(db, notification_id, current_user.user_id)
    if not success:
        raise HTTPException(status_code=404, detail="알림을 찾을 수 없습니다.")
    return {"message": "읽음 처리되었습니다."}


@router.patch("/read-all", summary="모든 알림 읽음 처리")
async def mark_all_notifications_read(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    await notification_service.mark_all_as_read(db, current_user.user_id)
    return {"message": "모든 알림이 읽음 처리되었습니다."}


@router.websocket("/ws")
async def notification_websocket(
    websocket: WebSocket,
    user_id: int = Query(...),
):
    await notification_service.manager.connect(user_id, websocket)
    try:
        while True:
            # 클라이언트에서 보내는 메시지는 무시 (연결 유지 목적)
            await websocket.receive_text()
    except WebSocketDisconnect:
        notification_service.manager.disconnect(user_id, websocket)