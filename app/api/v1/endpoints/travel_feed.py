from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List

from app.core.database import get_async_db
from app.core.dependencies import get_current_user
from app.models.user_model import User
from app.schemas.travel_schema import TravelResponse, TravelDetailResponse
from app.services import travel_feed_service

router = APIRouter(prefix="/feed", tags=["친구 여행 피드"])


@router.get("", response_model=List[TravelResponse], summary="친구 여행 소식 리스트", description="친구들의 전체 여행 목록을 최신순으로 반환합니다.")
async def get_feed(
    skip: int = Query(default=0, ge=0, description="건너뛸 항목 수"),
    limit: int = Query(default=20, ge=1, le=100, description="최대 반환 수"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    return await travel_feed_service.get_friend_travel_feed(current_user, db, skip, limit)


@router.get("/{travel_id}", response_model=TravelDetailResponse, summary="친구 여행 소식 상세", description="특정 여행의 전체 일정, 장소, 메모, 예산 상세 정보를 반환합니다.")
async def get_feed_detail(
    travel_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    return await travel_feed_service.get_friend_travel_detail(travel_id, current_user, db)