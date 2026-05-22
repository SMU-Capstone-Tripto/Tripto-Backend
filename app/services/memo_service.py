from typing import List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.memo_model import Memo
from app.schemas.memo_schema import MemoCreate, MemoUpdate


async def create_memo(db: AsyncSession, data: MemoCreate) -> Memo:
    memo = Memo(**data.model_dump())
    db.add(memo)
    await db.flush()
    await db.refresh(memo)
    return memo


async def get_memo(db: AsyncSession, memo_id: int) -> Optional[Memo]:
    result = await db.execute(select(Memo).where(Memo.id == memo_id))
    return result.scalar_one_or_none()


async def update_memo(
    db: AsyncSession, memo_id: int, data: MemoUpdate
) -> Optional[Memo]:
    memo = await get_memo(db, memo_id)
    if not memo:
        return None
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(memo, field, value)
    await db.flush()
    await db.refresh(memo)
    return memo


async def delete_memo(db: AsyncSession, memo_id: int) -> bool:
    memo = await get_memo(db, memo_id)
    if not memo:
        return False
    await db.delete(memo)
    await db.flush()
    return True