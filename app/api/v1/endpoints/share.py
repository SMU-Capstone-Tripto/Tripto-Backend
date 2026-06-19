from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_db
from app.core.dependencies import get_current_user
from app.models.user_model import User
from app.services import share_service

router = APIRouter(tags=["공유 링크"])

@router.post("/travels/{travel_id}/share", summary="공유 링크 생성")
async def create_share_link(
    travel_id: int,
    request: Request,
    db: AsyncSession = Depends(get_async_db),
    current_user: User = Depends(get_current_user),
):
    token = await share_service.create_share_token(db, travel_id, current_user.user_id)
    base_url = str(request.base_url).rstrip("/")
    return {"share_url": f"{base_url}/api/v1/share/{token}"}

@router.get("/share/{token}", summary="공유 링크로 여행 조회 (공개)")
async def get_shared_travel(
    token: str,
    db: AsyncSession = Depends(get_async_db),
):
    return await share_service.get_shared_travel(db, token)