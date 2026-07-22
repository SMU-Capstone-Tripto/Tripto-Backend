from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime
from app.models.friendship_model import FriendshipStatus


class FriendSearchResponse(BaseModel):
    friend_id: int
    friend_unique_id: str
    nickname: str
    tags: List[str]
    status_message: Optional[str] = None
    profile_image: Optional[str] = None
    avatar_color: Optional[str] = None

    model_config = {"from_attributes": True}

    @classmethod
    def from_user(cls, user):
        return cls(
            friend_id=user.user_id,
            friend_unique_id=user.unique_id,
            nickname=user.nickname,
            tags=user.tags,
            status_message=user.status_message,
            profile_image=user.profile_image,
            avatar_color=user.avatar_color,
        )


class FriendRequestCreate(BaseModel):
    friend_unique_id: str 


class FriendRequestResponse(BaseModel):
    friendship_id: int
    requester_id: int
    addressee_id: int
    status: FriendshipStatus
    created_at: datetime
    requester: FriendSearchResponse
    addressee: FriendSearchResponse

    model_config = {"from_attributes": True}

    @classmethod
    def from_friendship(cls, friendship):
        return cls(
            friendship_id=friendship.friendship_id,
            requester_id=friendship.requester_id,
            addressee_id=friendship.addressee_id,
            status=friendship.status,
            created_at=friendship.created_at,
            requester=FriendSearchResponse.from_user(friendship.requester),
            addressee=FriendSearchResponse.from_user(friendship.addressee),
        )

class FriendRequestAction(BaseModel):
    friendship_id: int
    is_accept: bool


class FriendListItem(BaseModel):
    friendship_id: int
    user: FriendSearchResponse
    since: datetime

    model_config = {"from_attributes": True}
