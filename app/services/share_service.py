import uuid
from collections import defaultdict

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.schedule_model import Schedule
from app.models.travel_model import Travel
from app.schemas.schedule_schema import ScheduleDetailResponse
from app.services import travel_service


async def create_share_token(db: AsyncSession, travel_id: int, owner_id: int) -> str:
    travel = await travel_service.get_travel(db, travel_id)
    if not travel:
        raise HTTPException(status_code=404, detail="여행을 찾을 수 없습니다.")
    if travel.owner_id != owner_id:
        raise HTTPException(status_code=403, detail="권한이 없습니다.")

    if not travel.share_token:
        travel.share_token = str(uuid.uuid4())
        await db.commit()
        await db.refresh(travel)

    return travel.share_token


async def get_shared_travel(db: AsyncSession, token: str) -> dict:
    result = await db.execute(
        select(Travel)
        .options(
            selectinload(Travel.schedules).selectinload(Schedule.memos)
        )
        .where(Travel.share_token == token)
    )
    travel = result.scalar_one_or_none()
    if not travel:
        raise HTTPException(status_code=404, detail="유효하지 않은 공유 링크입니다.")

    days_dict = defaultdict(list)
    for schedule in travel.schedules:
        days_dict[schedule.day_number].append(schedule)

    days = [
        {
            "day_number": day_num,
            "date": str(schedules[0].date),
            "schedules": [
                ScheduleDetailResponse.model_validate(s).model_dump()
                for s in sorted(schedules, key=lambda s: s.order_index)
            ],
        }
        for day_num, schedules in sorted(days_dict.items())
    ]

    return {
        "travel_id": travel.travel_id,
        "title": travel.title,
        "destination": travel.destination,
        "start_date": str(travel.start_date),
        "end_date": str(travel.end_date),
        "days": days,
    }
