from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.schedule_model import Schedule
from app.schemas.schedule_schema import ScheduleCreate, ScheduleUpdate


async def create_schedule(db: AsyncSession, data: ScheduleCreate) -> Schedule:
    schedule_data = data.model_dump(exclude={"schedule_id"})  # schedule_id 제외
    schedule = Schedule(**schedule_data)
    db.add(schedule)
    await db.flush()
    await db.refresh(schedule)
    return schedule


async def get_schedule(db: AsyncSession, schedule_id: int) -> Optional[Schedule]:
    result = await db.execute(
        select(Schedule)
        .where(Schedule.schedule_id == schedule_id)
        .options(selectinload(Schedule.memos))
    )
    return result.scalar_one_or_none()


async def get_schedules_by_travel(db: AsyncSession, travel_id: int) -> List[Schedule]:
    result = await db.execute(
        select(Schedule)
        .where(Schedule.travel_id == travel_id)
        .order_by(Schedule.day_number, Schedule.order_index)
    )
    return list(result.scalars().all())


async def update_schedule(
    db: AsyncSession, schedule_id: int, data: ScheduleUpdate
) -> Optional[Schedule]:
    schedule = await get_schedule(db, schedule_id)
    if not schedule:
        return None
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(schedule, field, value)
    await db.flush()
    await db.refresh(schedule)
    return schedule


async def delete_schedule(db: AsyncSession, schedule_id: int) -> bool:
    schedule = await get_schedule(db, schedule_id)
    if not schedule:
        return False
    await db.delete(schedule)
    await db.flush()
    return True


async def get_map_pins(db: AsyncSession, travel_id: int) -> List[Schedule]:
    result = await db.execute(
        select(Schedule)
        .where(
            Schedule.travel_id == travel_id,
            Schedule.latitude.is_not(None),
            Schedule.longitude.is_not(None),
        )
        .order_by(Schedule.day_number, Schedule.order_index)
    )
    return list(result.scalars().all())