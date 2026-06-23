from typing import List
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.database import get_async_db
from app.core.dependencies import get_current_user
from app.models.user_model import User
from app.models.travel_model import Travel
from app.schemas import TravelCreate, TravelUpdate, TravelResponse, TravelDetailResponse
from app.schemas.schedule_schema import ScheduleMapPin
from app.services import travel_service, schedule_service

router = APIRouter(prefix="/travels", tags=["나의 여행"])


@router.post("", response_model=TravelResponse, status_code=201, summary="여행 생성")
async def create_travel(
    data: TravelCreate,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user),
):
    return await travel_service.create_travel(db, data, current_user.user_id)


@router.get("/{travel_id}", response_model=TravelDetailResponse, summary="여행 상세 조회")
async def get_travel(travel_id: int, db: AsyncSession = Depends(get_async_db)):
    result = await db.execute(
        select(Travel)
        .options(selectinload(Travel.schedules))
        .where(Travel.travel_id == travel_id)
    )
    travel = result.scalar_one_or_none()
    if not travel:
        raise HTTPException(status_code=404, detail="여행을 찾을 수 없습니다.")
    return travel


@router.patch("/{travel_id}", response_model=TravelResponse, summary="여행 수정")
async def update_travel(
    travel_id: int,
    data: TravelUpdate,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user), 
):
    travel = await travel_service.update_travel(db, travel_id, data, current_user.user_id)
    if not travel:
        raise HTTPException(status_code=404, detail="여행을 찾을 수 없습니다.")
    return travel


@router.delete("/{travel_id}", status_code=204, summary="여행 삭제")
async def delete_travel(
    travel_id: int,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user),
):
    deleted = await travel_service.delete_travel(db, travel_id, current_user.user_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="여행을 찾을 수 없습니다.")


@router.get("", response_model=List[TravelResponse], summary="내 여행 리스트 조회")
async def list_travels(
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user), # 토큰 기반으로 내 여행만 조회
):
    return await travel_service.get_travels_by_owner(db, current_user.user_id)

@router.get("/{travel_id}/map", response_model=List[ScheduleMapPin], summary="여행장소 지도 Pin")
async def get_map_pins(
    travel_id: int, 
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user)
):
    pins = await schedule_service.get_map_pins(db, travel_id)
    if not pins:
        return []
    return pins