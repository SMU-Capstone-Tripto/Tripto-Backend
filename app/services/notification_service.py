import json
from typing import Dict, List

from fastapi import WebSocket
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.models.notification_model import Notification, NotificationType
from app.schemas.notification_schema import NotificationResponse


# 유저별 WebSocket 연결을 메모리에서 관리하는 클래스
class NotificationManager:
    def __init__(self):
        self.active_connections: Dict[int, List[WebSocket]] = {}

    # 핸드셰이크 수락 후 해당 유저의 연결 목록에 추가
    async def connect(self, user_id: int, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.setdefault(user_id, []).append(websocket)

    # 연결 목록에서 제거하고 목록이 비면 키도 삭제
    def disconnect(self, user_id: int, websocket: WebSocket):
        connections = self.active_connections.get(user_id, [])
        if websocket in connections:
            connections.remove(websocket)
        if not connections:
            self.active_connections.pop(user_id, None)

    # 특정 유저의 모든 WebSocket 연결에 JSON 메시지 push
    async def send_to_user(self, user_id: int, data: dict):
        for ws in self.active_connections.get(user_id, []):
            try:
                await ws.send_text(json.dumps(data, ensure_ascii=False, default=str))
            except Exception:
                pass


# 앱 전체에서 공유하는 싱글톤 인스턴스
manager = NotificationManager()


# DB에 알림을 저장하고 WebSocket으로 실시간 push하는 내부 공통 함수
async def _create_notification(
    db: AsyncSession,
    recipient_id: int,
    actor_id: int,
    notif_type: NotificationType,
    content: str,
) -> Notification:
    notification = Notification(
        recipient_id=recipient_id,
        actor_id=actor_id,
        type=notif_type,
        content=content,
    )
    db.add(notification)
    await db.flush()

    result = await db.execute(
        select(Notification)
        .options(joinedload(Notification.actor), joinedload(Notification.recipient))
        .where(Notification.notification_id == notification.notification_id)
    )
    notification = result.scalar_one()

    await manager.send_to_user(
        recipient_id,
        {
            "type": "notification",
            "data": NotificationResponse.from_notification(notification).model_dump(),
        },
    )
    return notification


# 친구 요청 수신자에게 알림 생성
async def notify_friend_request(db: AsyncSession, recipient_id: int, actor_id: int, actor_nickname: str):
    await _create_notification(
        db=db,
        recipient_id=recipient_id,
        actor_id=actor_id,
        notif_type=NotificationType.FRIEND_REQUEST,
        content=f"{actor_nickname}님이 친구 요청을 보냈습니다.",
    )


# 친구 수락 시 요청자에게 알림 생성
async def notify_friend_accepted(db: AsyncSession, recipient_id: int, actor_id: int, actor_nickname: str):
    await _create_notification(
        db=db,
        recipient_id=recipient_id,
        actor_id=actor_id,
        notif_type=NotificationType.FRIEND_ACCEPTED,
        content=f"{actor_nickname}님이 친구 요청을 수락했습니다.",
    )


# 유저의 전체 알림 목록을 최신순으로 조회
async def get_notifications(db: AsyncSession, user_id: int) -> list[NotificationResponse]:
    result = await db.execute(
        select(Notification)
        .options(joinedload(Notification.actor))
        .where(Notification.recipient_id == user_id)
        .order_by(Notification.created_at.desc())
    )
    notifications = result.scalars().all()
    return [NotificationResponse.from_notification(n) for n in notifications]


# 특정 알림 하나를 읽음 처리 (본인 알림이 아니면 False 반환)
async def mark_as_read(db: AsyncSession, notification_id: int, user_id: int) -> bool:
    result = await db.execute(
        select(Notification).where(
            Notification.notification_id == notification_id,
            Notification.recipient_id == user_id,
        )
    )
    notification = result.scalar_one_or_none()
    if not notification:
        return False
    notification.is_read = True
    await db.commit()
    return True


# 읽지 않은 알림 전체를 일괄 읽음 처리
async def mark_all_as_read(db: AsyncSession, user_id: int):
    result = await db.execute(
        select(Notification).where(
            Notification.recipient_id == user_id,
            Notification.is_read == False,
        )
    )
    for notification in result.scalars().all():
        notification.is_read = True
    await db.commit()
