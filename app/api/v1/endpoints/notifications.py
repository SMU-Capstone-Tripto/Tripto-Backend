from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_db, AsyncSessionLocal
from app.core.dependencies import get_current_user
from app.core.security import verify_access_token
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
    token: str = Query(..., description="액세스 토큰 (쿼리스트링). 예전 user_id 파라미터 대체"),
):
    # 액세스 토큰에서 user_id를 뽑는다 — 예전엔 user_id를 쿼리로 그대로 받아
    # 누구나 남의 알림 채널을 구독할 수 있었다(위조 가능).
    async with AsyncSessionLocal() as db:
        try:
            payload = verify_access_token(token)
            user_id = int(payload.get("sub"))
        except (HTTPException, ValueError, TypeError):
            await websocket.close(code=1008)  # Policy Violation
            return

        user = (
            await db.execute(select(User).where(User.user_id == user_id))
        ).scalar_one_or_none()
        if not user or not user.is_active:
            await websocket.close(code=1008)
            return

    await notification_service.manager.connect(user_id, websocket)
    try:
        while True:
            # 클라이언트에서 보내는 메시지는 무시 (연결 유지 목적)
            await websocket.receive_text()
    except WebSocketDisconnect:
        notification_service.manager.disconnect(user_id, websocket)