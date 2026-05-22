from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class MemoBase(BaseModel):
    content: str = Field(..., min_length=1, description="메모 내용")


class MemoCreate(MemoBase):
    schedule_id: int = Field(..., description="메모를 추가할 일정 ID")


class MemoUpdate(BaseModel):
    content: Optional[str] = Field(None, min_length=1, description="수정할 메모 내용")


class MemoResponse(MemoBase):
    model_config = ConfigDict(from_attributes=True)
    id: int
    schedule_id: int
    created_at: datetime
    updated_at: datetime