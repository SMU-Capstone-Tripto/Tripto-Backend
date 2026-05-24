from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List

from app.core.database import get_async_db
from app.core.dependencies import get_current_user
from app.models.user_model import User
from app.schemas.friendship_schema import (
    FriendSearchResponse,
    FriendRequestCreate,
    FriendRequestResponse,
    FriendRequestAction,
    FriendListItem,
)
from app.services import friendship_service

router = APIRouter(prefix="/friends", tags=["친구"])


# ── ID로 사용자 검색 ─────────────────────────────────────
@router.get("/search/{unique_id}", response_model=FriendSearchResponse, summary="고유 ID로 사용자 검색")
async def search_user(
    unique_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    return await friendship_service.search_user_by_unique_id(unique_id, current_user, db)


# ── 친구 요청 보내기 ─────────────────────────────────────
@router.post("/request", response_model=FriendRequestResponse, status_code=201, summary="친구 요청 보내기")
async def send_request(
    body: FriendRequestCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    return await friendship_service.send_friend_request(body.friend_unique_id, current_user, db)


# ── 받은 친구 요청 목록 ──────────────────────────────────
@router.get("/requests/received", response_model=List[FriendRequestResponse], summary="받은 친구 요청 목록")
async def received_requests(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    return await friendship_service.get_received_requests(current_user, db)


# ── 친구 요청 수락/거절 ───────────────────────────────────
@router.patch("/request/respond", response_model=FriendRequestResponse, summary="친구 요청 수락/거절")
async def respond_request(
    body: FriendRequestAction,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    return await friendship_service.respond_to_friend_request(
        body.friendship_id, body.action, current_user, db
    )


# ── 친구 목록 ─────────────────────────────────────────────
@router.get("/list", response_model=List[FriendListItem], summary="친구 목록")
async def friend_list(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    return await friendship_service.get_friend_list(current_user, db)


# ── 친구 삭제 ─────────────────────────────────────────────
@router.delete("/{friendship_id}", status_code=204, summary="친구 삭제")
async def remove_friend(
    friendship_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    await friendship_service.remove_friend(friendship_id, current_user, db)
