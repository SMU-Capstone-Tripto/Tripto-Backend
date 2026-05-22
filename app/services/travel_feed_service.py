from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_
from sqlalchemy.orm import joinedload, selectinload
from fastapi import HTTPException

from app.models.user_model import User
from app.models.friendship_model import Friendship, FriendshipStatus
from app.models.travel_model import Travel
from app.models.schedule_model import Schedule
from app.schemas.travel_schema import TravelResponse, TravelDetailResponse
from app.schemas.friendship_schema import FriendSearchResponse

def _calc_budget(schedules) -> int | None:
    """일정 전체 예산 합산. 단 하나도 입력 안 됐으면 None 반환"""
    costs = [s.cost for s in schedules if s.cost is not None]
    return sum(costs) if costs else None

# 씁 근데 이거 이 함수 없애고 그냥 friend쪽에 있는 친구조회 기능 함수로 수정할까 생각 중. 
async def _get_friend_ids(current_user: User, db: AsyncSession) -> list[int]:
    """현재 유저의 친구 ID 목록 조회"""
    result = await db.execute(
        select(Friendship).where(
            or_(
                Friendship.requester_id == current_user.user_id,
                Friendship.addressee_id == current_user.user_id,
            ),
            Friendship.status == FriendshipStatus.ACCEPTED,
        )
    )
    friendships = result.scalars().all()

    friend_ids = []
    for f in friendships:
        fid = f.addressee_id if f.requester_id == current_user.user_id else f.requester_id
        friend_ids.append(fid)
    return friend_ids


# ── 친구 여행 소식 리스트 ──────────────────────────────────
async def get_friend_travel_feed(
    current_user: User,
    db: AsyncSession,
    skip: int = 0,
    limit: int = 20,
) -> list[Travel]:
    friend_ids = await _get_friend_ids(current_user, db)
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
    return result.unique().scalars().all()


# ── 친구 여행 소식 상세 ────────────────────────────────────
async def get_friend_travel_detail(
    travel_id: int,
    current_user: User,
    db: AsyncSession,
) -> Travel:
    friend_ids = await _get_friend_ids(current_user, db)
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