from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from fastapi import HTTPException

from app.models.user_model import User
from app.models.travel_model import Travel
from app.models.schedule_model import Schedule
from app.services.friendship_service import get_accepted_friend_ids # 최적화: 공용 함수 import

# 일정 전체 예산 합산
def _calc_budget(schedules) -> int | None:
    costs = [s.cost for s in schedules if s.cost is not None]
    return sum(costs) if costs else None


# ── 친구 여행 소식 리스트 ──────────────────────────────────
async def get_friend_travel_feed(
    current_user: User,
    db: AsyncSession,
    skip: int = 0,
    limit: int = 20,
) -> list[Travel]:
    friend_ids = await get_accepted_friend_ids(current_user.user_id, db)
    
    if not friend_ids:
        return []

    result = await db.execute(
        select(Travel)
        .options(
            selectinload(Travel.schedules).selectinload(Schedule.memos),
        )
        .where(Travel.owner_id.in_(friend_ids))
        .order_by(Travel.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    return list(result.unique().scalars().all())


# ── 친구 여행 소식 상세 ────────────────────────────────────
async def get_friend_travel_detail(
    travel_id: int,
    current_user: User,
    db: AsyncSession,
) -> Travel:
    friend_ids = await get_accepted_friend_ids(current_user.user_id, db)
    
    if not friend_ids:
        raise HTTPException(status_code=404, detail="여행 정보를 찾을 수 없습니다.")

    result = await db.execute(
        select(Travel)
        .options(
            selectinload(Travel.schedules).selectinload(Schedule.memos),
        )
        .where(
            Travel.travel_id == travel_id,
            Travel.owner_id.in_(friend_ids),
        )
    )
    travel = result.unique().scalar_one_or_none()

    if not travel:
        raise HTTPException(status_code=404, detail="여행 정보를 찾을 수 없습니다.")

    return travel