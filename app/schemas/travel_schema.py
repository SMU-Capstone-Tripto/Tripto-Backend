from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.schemas.schedule_schema import ScheduleSummaryResponse

class TravelBase(BaseModel):
    title: str = Field(..., max_length=100, examples=["제주도 여행"])
    destination: str = Field(..., max_length=100, examples=["제주도"])
    start_date: date = Field(..., examples=["2026-05-01"])
    end_date: date = Field(..., examples=["2026-05-05"])

    @model_validator(mode="after")
    def validate_date_range(self) -> "TravelBase":
        if self.end_date < self.start_date:
            raise ValueError("end_date 는 start_date 와 같거나 이후여야 합니다.")
        return self

class TravelCreate(TravelBase):
    pass


class TravelUpdate(BaseModel):
    title: Optional[str] = Field(None, max_length=100)
    destination: Optional[str] = Field(None, max_length=100)
    start_date: Optional[date] = None
    end_date: Optional[date] = None

    @model_validator(mode="after")
    def validate_date_range(self) -> "TravelUpdate":
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date 는 start_date 와 같거나 이후여야 합니다.")
        return self


class TravelResponse(TravelBase):
    model_config = ConfigDict(from_attributes=True)
    travel_id: int
    owner_id: int
    created_at: datetime
    updated_at: datetime


class TravelDetailResponse(TravelResponse): # 여행 상세 조회
    schedules: List[ScheduleSummaryResponse] = []

TravelDetailResponse.model_rebuild()