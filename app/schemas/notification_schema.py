from pydantic import BaseModel
from datetime import datetime


class NotificationResponse(BaseModel):
    notification_id: int
    type: str
    content: str
    actor_id: int
    actor_nickname: str
    actor_profile_image_url: str | None = None
    is_read: bool
    created_at: datetime

    model_config = {"from_attributes": True}

    @classmethod
    def from_notification(cls, notification):
        return cls(
            notification_id=notification.notification_id,
            type=notification.type,
            content=notification.content,
            actor_id=notification.actor_id,
            actor_nickname=notification.actor.nickname,
            actor_profile_image_url=notification.actor.profile_image_url,
            is_read=notification.is_read,
            created_at=notification.created_at,
        )
