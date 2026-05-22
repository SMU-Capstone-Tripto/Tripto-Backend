from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.schedule_schema import ScheduleSummaryResponse


class Travel(BaseModel):
    travel_id: int
    owner_id: int
    title: str = Field(..., max_length=100, examples=["제주도 여행"])
    destination: str = Field(..., max_length=100, examples=["제주도"])
    start_date: date = Field(..., examples=["2026-05-01"])
    end_date: date = Field(..., examples=["2026-05-05"])

    @model_validator(mode="after")
    def validate_date_range(self) -> "Travel":
        if self.end_date < self.start_date:
            raise ValueError("end_date 는 start_date 와 같거나 이후여야 합니다.")
        return self


class TravelCreate(Travel):
    pass


class TravelUpdate(BaseModel):
    title: Optional[str] = Field(None, max_length=100)
    destination: Optional[str] = Field(None, max_length=100)
    start_date: Optional[date] = None
    end_date: Optional[date] = None
    cover_image_url: Optional[str] = Field(None, max_length=512)

    @model_validator(mode="after")
    def validate_date_range(self) -> "TravelUpdate":
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date 는 start_date 와 같거나 이후여야 합니다.")
        return self


class TravelResponse(Travel):
    model_config = ConfigDict(from_attributes=True)

    travel_id: int
    owner_id: int
    title: str
    destination: str
    created_at: datetime
    updated_at: datetime


class TravelDetailResponse(TravelResponse):
    """여행 상세 조회 응답 — 일차별 일정 요약 목록 포함"""
    travel_id: int
    owner_id: int
    title: str
    destination: str
    schedules: List[ScheduleSummaryResponse] = []