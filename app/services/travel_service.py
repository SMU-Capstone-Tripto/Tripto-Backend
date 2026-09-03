from typing import List, Optional

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.travel_model import Travel
from app.models.schedule_model import Schedule
from app.models.memo_model import Memo
from app.models.chat_model import ChatRoomMember
from app.schemas.travel_schema import TravelCreate, TravelUpdate


async def create_travel(db: AsyncSession, data: TravelCreate, owner_id: int) -> Travel:
    travel_data = data.model_dump() 
    travel_data["owner_id"] = owner_id
    travel = Travel(**travel_data)
    db.add(travel)
    await db.flush()
    await db.refresh(travel)
    return travel


async def get_travel(db: AsyncSession, travel_id: int) -> Optional[Travel]:
    result = await db.execute(
        select(Travel)
        .options(selectinload(Travel.schedules).selectinload(Schedule.memos))
        .where(Travel.travel_id == travel_id)
    )
    return result.scalar_one_or_none()


async def get_travels_by_owner(db: AsyncSession, owner_id: int) -> List[Travel]:
    """내가 만든 여행 + 내가 속한 채팅방의 그룹 투표로 확정된 여행."""
    my_room_ids = (await db.execute(
        select(ChatRoomMember.room_id).where(ChatRoomMember.user_id == owner_id)
    )).scalars().all()

    conds = [Travel.owner_id == owner_id]
    if my_room_ids:
        conds.append(Travel.room_id.in_(my_room_ids))

    result = await db.execute(
        select(Travel).where(or_(*conds)).order_by(Travel.start_date.desc())
    )
    return list(result.scalars().unique().all())


async def update_travel(db: AsyncSession, travel_id: int, data: TravelUpdate, owner_id: int):
    result = await db.execute(
        select(Travel).where(Travel.travel_id == travel_id, Travel.owner_id == owner_id)
    )
    travel = result.scalar_one_or_none()
    
    if not travel: # 여행이 없거나 내 여행이 아닌 경우 
        raise HTTPException(status_code=404, detail="여행을 찾을 수 없거나 권한이 없습니다.")
        
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(travel, field, value)
    await db.flush()
    await db.refresh(travel)
    return travel


async def delete_travel(db: AsyncSession, travel_id: int, owner_id: int) -> bool:
    travel = await get_travel(db, travel_id)
    if not travel:
        return False
    if travel.owner_id != owner_id:
        raise HTTPException(status_code=403, detail="자신의 여행/일정만 수정할 수 있습니다.")
    await db.delete(travel)
    await db.flush()
    return True