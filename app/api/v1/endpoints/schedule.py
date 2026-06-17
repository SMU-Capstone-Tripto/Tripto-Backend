from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_db
from app.schemas.schedule_schema import ScheduleCreate, ScheduleUpdate, ScheduleSummaryResponse, ScheduleDetailResponse, ScheduleMapPin
from app.services import schedule_service
from app.models.user_model import User
from app.core.dependencies import get_current_user

router = APIRouter(prefix="/schedules", tags=["스케줄"])


@router.post("", response_model=ScheduleSummaryResponse, status_code=201, summary="스케줄 생성")
async def create_schedule(
    data: ScheduleCreate, db: AsyncSession = Depends(get_async_db)
):
    return await schedule_service.create_schedule(db, data)


@router.get("/{schedule_id}", response_model=ScheduleDetailResponse, summary="스케줄 상세 조회")
async def get_schedule(schedule_id: int, db: AsyncSession = Depends(get_async_db)):
    schedule = await schedule_service.get_schedule(db, schedule_id)
    if not schedule:
        raise HTTPException(status_code=404, detail="일정을 찾을 수 없습니다.")
    return schedule


@router.patch("/{schedule_id}", response_model=ScheduleSummaryResponse, summary="스케줄 수정")
async def update_schedule(
    schedule_id: int,
    data: ScheduleUpdate,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user),
):
    schedule = await schedule_service.update_schedule(db, schedule_id, data, current_user.user_id)
    if not schedule:
        raise HTTPException(status_code=404, detail="일정을 찾을 수 없습니다.")
    return schedule


@router.delete("/{schedule_id}", status_code=204, summary="스케줄 삭제")
async def delete_schedule(
    schedule_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user),
):
    deleted = await schedule_service.delete_schedule(db, schedule_id, current_user.user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="일정을 찾을 수 없습니다.")


@router.get("/travel/{travel_id}", response_model=List[ScheduleSummaryResponse], summary="일정 리스트 조회")
async def list_schedules(travel_id: int, db: AsyncSession = Depends(get_async_db)):
    return await schedule_service.get_schedules_by_travel(db, travel_id)


@router.get("/travel/{travel_id}/map-pins", response_model=List[ScheduleMapPin], summary="지도 위치 Pin")
async def get_map_pins(travel_id: int, db: AsyncSession = Depends(get_async_db)):
    return await schedule_service.get_map_pins(db, travel_id)