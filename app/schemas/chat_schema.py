from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime

class ChatRoomCreate(BaseModel):
    room_name: Optional[str] = Field(None, max_length=100)
    invited_user_ids: List[int]

class ChatMessageResponse(BaseModel):
    message_id: int
    room_id: int
    sender_id: int
    content: str
    created_at: datetime

    model_config = {"from_attributes": True}

class ChatRoomResponse(BaseModel):
    room_id: int
    room_name: Optional[str] = None
    owner_id: int  
    member_ids: List[int] = []  
    created_at: Optional[datetime] = None

    model_config = {"from_attributes": True}

class ChatRoomInvite(BaseModel):
    invited_user_ids: List[int] = Field(..., description="초대할 사용자 ID")