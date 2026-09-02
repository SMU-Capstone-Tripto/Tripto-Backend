import json
from typing import Dict, List, Optional

from fastapi import WebSocket
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload
from firebase_admin import messaging

from app.models.notification_model import Notification, NotificationType
from app.models.user_model import User
from app.models.chat_model import ChatRoomMember
from app.schemas.notification_schema import NotificationResponse
from app.core.firebase import get_firebase_messaging


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


# AI 여행 일정 생성 완료 시 본인에게 알림 (앱을 꺼둔 사이 끝나도 알 수 있도록 FCM 푸시 포함)
async def notify_itinerary_ready(
    db: AsyncSession,
    user_id: int,
    plan_title: str = "",
    room_id: Optional[int] = None,
):
    body = f"'{plan_title}' 일정이 완성됐어요! 확인해 보세요." if plan_title else "AI가 여행 일정을 다 만들었어요!"

    # DB 알림 + WebSocket 실시간 push (앱이 열려 있으면 바로 뜸). 시스템 알림이라 actor=본인.
    try:
        await _create_notification(
            db=db,
            recipient_id=user_id,
            actor_id=user_id,
            notif_type="itinerary_ready",
            content=body,
        )
    except Exception as e:
        print(f"notify_itinerary_ready: DB 알림 실패 - {e}")

    # FCM 푸시 (앱이 꺼져 있어도 폰에 뜸)
    try:
        result = await db.execute(
            select(User.fcm_token).where(
                User.user_id == user_id,
                User.fcm_token.isnot(None),
                User.is_active == True,
            )
        )
        token = result.scalar_one_or_none()
        if token:
            await send_push_notification(
                token=token,
                title="여행 일정이 완성됐어요",
                body=body,
                data={"type": "itinerary_ready", "room_id": str(room_id) if room_id is not None else ""},
            )
    except Exception as e:
        print(f"notify_itinerary_ready: FCM 푸시 실패 - {e}")


# 투표가 동점으로 마감됐을 때 방장(생성자)에게 "직접 골라주세요" 알림 + FCM.
async def notify_vote_tie(db: AsyncSession, vote_session):
    body = "여행 일정 투표가 동점으로 마감됐어요. 방장이 최종 일정을 선택해 주세요."
    try:
        await _create_notification(
            db=db,
            recipient_id=vote_session.creator_id,
            actor_id=vote_session.creator_id,
            notif_type="vote_tie",
            content=body,
        )
        await db.commit()
    except Exception as e:
        print(f"notify_vote_tie: DB 알림 실패 - {e}")

    try:
        token = (await db.execute(
            select(User.fcm_token).where(
                User.user_id == vote_session.creator_id,
                User.fcm_token.isnot(None),
                User.is_active == True,
            )
        )).scalar_one_or_none()
        if token:
            await send_push_notification(
                token=token,
                title="여행 일정 투표 결과",
                body=body,
                data={"type": "vote_tie", "vote_id": str(vote_session.vote_id)},
            )
    except Exception as e:
        print(f"notify_vote_tie: FCM 푸시 실패 - {e}")


# 투표가 마감(확정)됐을 때 생성자 + (그룹이면) 채팅방 멤버 전원에게 알림 + FCM 푸시.
# db는 이 함수 안에서 flush + commit 한다. 호출자는 그 전에 트랜잭션을 정리해 둘 것.
async def notify_vote_finalized(db: AsyncSession, vote_session, travel=None):
    if travel is not None:
        body = f"여행 일정 투표가 마감돼 '{travel.title}' 일정이 여행 탭에 등록됐어요!"
    else:
        body = "여행 일정 투표가 마감됐어요. (투표한 사람이 없어 일정은 등록되지 않았어요)"

    recipients: set[int] = {vote_session.creator_id}
    try:
        if getattr(vote_session, "vote_type", None) and vote_session.vote_type.value == "group" and vote_session.room_id:
            rows = await db.execute(
                select(ChatRoomMember.user_id).where(ChatRoomMember.room_id == vote_session.room_id)
            )
            recipients |= {uid for uid in rows.scalars().all()}
    except Exception as e:
        print(f"notify_vote_finalized: 멤버 조회 실패 - {e}")

    for uid in recipients:
        try:
            await _create_notification(
                db=db,
                recipient_id=uid,
                actor_id=vote_session.creator_id,
                notif_type="vote_finalized",
                content=body,
            )
        except Exception as e:
            print(f"notify_vote_finalized: DB 알림 실패 (user {uid}) - {e}")
    await db.commit()

    try:
        tokens_result = await db.execute(
            select(User.fcm_token).where(
                User.user_id.in_(recipients),
                User.fcm_token.isnot(None),
                User.is_active == True,
            )
        )
        tokens = [t for t in tokens_result.scalars().all() if t]
        if tokens:
            await send_multicast_notification(
                tokens=tokens,
                title="여행 일정 투표 결과",
                body=body,
                data={
                    "type": "vote_finalized",
                    "vote_id": str(vote_session.vote_id),
                    "travel_id": str(travel.travel_id) if travel is not None else "",
                },
            )
    except Exception as e:
        print(f"notify_vote_finalized: FCM 푸시 실패 - {e}")


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


# FCM 푸시알림 전송 함수들
async def send_push_notification(
    token: str,
    title: str,
    body: str,
    data: Optional[dict] = None
) -> bool:
    """
    단일 FCM 토큰으로 푸시알림 전송
    """
    fcm = get_firebase_messaging()
    if not fcm:
        print("Firebase not initialized. Skipping notification.")
        return False

    try:
        message = messaging.Message(
            notification=messaging.Notification(
                title=title,
                body=body,
            ),
            data=data or {},
            token=token,
        )

        response = fcm.send(message)
        print(f"Successfully sent message: {response}")
        return True

    except Exception as e:
        print(f"Error sending FCM notification: {e}")
        return False


async def send_multicast_notification(
    tokens: List[str],
    title: str,
    body: str,
    data: Optional[dict] = None
) -> int:
    """
    여러 FCM 토큰으로 푸시알림 일괄 전송
    """
    fcm = get_firebase_messaging()
    if not fcm or not tokens:
        return 0

    try:
        message = messaging.MulticastMessage(
            notification=messaging.Notification(
                title=title,
                body=body,
            ),
            data=data or {},
            tokens=tokens,
        )

        response = fcm.send_multicast(message)
        print(f"Successfully sent {response.success_count} messages out of {len(tokens)}")

        if response.failure_count > 0:
            for idx, resp in enumerate(response.responses):
                if not resp.success:
                    print(f"Failed to send to token {tokens[idx]}: {resp.exception}")

        return response.success_count

    except Exception as e:
        print(f"Error sending multicast FCM notification: {e}")
        return 0


async def send_chat_notification(
    db: AsyncSession,
    sender_nickname: str,
    room_id: int,
    message_content: str,
    recipient_user_ids: List[int],
    exclude_user_id: Optional[int] = None
):
    """
    채팅 메시지에 대한 푸시알림 전송
    """
    if exclude_user_id:
        recipient_user_ids = [uid for uid in recipient_user_ids if uid != exclude_user_id]

    if not recipient_user_ids:
        return

    result = await db.execute(
        select(User.fcm_token)
        .where(User.user_id.in_(recipient_user_ids))
        .where(User.fcm_token.isnot(None))
        .where(User.is_active == True)
    )

    tokens = [row[0] for row in result.all() if row[0]]

    if not tokens:
        return

    preview = message_content[:50] + "..." if len(message_content) > 50 else message_content

    await send_multicast_notification(
        tokens=tokens,
        title=f"{sender_nickname}",
        body=preview,
        data={
            "type": "chat_message",
            "room_id": str(room_id),
            "sender_nickname": sender_nickname,
        }
    )
