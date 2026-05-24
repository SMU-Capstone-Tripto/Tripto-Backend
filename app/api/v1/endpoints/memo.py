from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_db
from app.schemas.memo_schema import MemoCreate, MemoUpdate, MemoResponse
from app.services import memo_service

router = APIRouter(prefix="/memos", tags=["메모"])


@router.post("", response_model=MemoResponse, status_code=201, summary="메모 생성")
async def create_memo(data: MemoCreate, db: AsyncSession = Depends(get_async_db)):
    return await memo_service.create_memo(db, data)


@router.patch("/{memo_id}", response_model=MemoResponse, summary="메모 수정")
async def update_memo(
    memo_id: int, data: MemoUpdate, db: AsyncSession = Depends(get_async_db)
):
    memo = await memo_service.update_memo(db, memo_id, data)
    if not memo:
        raise HTTPException(status_code=404, detail="메모를 찾을 수 없습니다.")
    return memo


@router.delete("/{memo_id}", status_code=204, summary="메모 삭제")
async def delete_memo(memo_id: int, db: AsyncSession = Depends(get_async_db)):
    deleted = await memo_service.delete_memo(db, memo_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="메모를 찾을 수 없습니다.")