from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_, and_
from sqlalchemy.orm import joinedload
from fastapi import HTTPException, status

from app.models.user_model import User
from app.models.friendship_model import Friendship, FriendshipStatus
from app.schemas.friendship_schema import FriendListItem, FriendSearchResponse, FriendRequestResponse


# ── ID로 사용자 검색 ──────────────────────────────────────
async def search_user_by_unique_id(
    unique_id: str,
    current_user: User,
    db: AsyncSession,
) -> FriendSearchResponse:
    if unique_id == current_user.unique_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="자기 자신을 검색할 수 없습니다.",
        )

    result = await db.execute(select(User).where(User.unique_id == unique_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="해당 ID의 사용자를 찾을 수 없습니다.",
        )

    return FriendSearchResponse.from_user(user)

# ── 친구 요청 보내기 ──────────────────────────────────────
async def send_friend_request(
    target_unique_id: str,
    current_user: User,
    db: AsyncSession,
) -> FriendRequestResponse:
    # 대상 유저 조회
    result = await db.execute(select(User).where(User.unique_id == target_unique_id))
    target = result.scalar_one_or_none()

    if not target:
        raise HTTPException(status_code=404, detail="사용자를 찾을 수 없습니다.")
    if target.user_id == current_user.user_id:
        raise HTTPException(status_code=400, detail="자기 자신에게 친구 요청을 보낼 수 없습니다.")

    # 기존 관계 확인
    existing = await db.execute(
        select(Friendship).where(
            or_(
                and_(
                    Friendship.requester_id == current_user.user_id,
                    Friendship.addressee_id == target.user_id,
                ),
                and_(
                    Friendship.requester_id == target.user_id,
                    Friendship.addressee_id == current_user.user_id,
                ),
            )
        )
    )
    rel = existing.scalar_one_or_none()

    if rel:
        if rel.status == FriendshipStatus.ACCEPTED:
            raise HTTPException(status_code=409, detail="이미 친구입니다.")
        if rel.status == FriendshipStatus.PENDING:
            raise HTTPException(status_code=409, detail="이미 대기 중인 친구 신청이 있습니다.")

    friendship = Friendship(
        requester_id=current_user.user_id,
        addressee_id=target.user_id,
        status=FriendshipStatus.PENDING,
    )
    db.add(friendship)
    await db.flush()

    # joinedload로 관계 명시적 로드
    result = await db.execute(
        select(Friendship)
        .options(joinedload(Friendship.requester), joinedload(Friendship.addressee))
        .where(Friendship.friendship_id == friendship.friendship_id)
    )
    friendship = result.scalar_one()
    return FriendRequestResponse.from_friendship(friendship)


# ── 받은 친구 요청 목록 ──────────────────────────────────
async def get_received_requests(
    current_user: User,
    db: AsyncSession,
) -> list[FriendRequestResponse]:
    result = await db.execute(
        select(Friendship)
        .options(joinedload(Friendship.requester), joinedload(Friendship.addressee))
        .where(
            Friendship.addressee_id == current_user.user_id,
            Friendship.status == FriendshipStatus.PENDING,
        )
    )
    friendships = result.scalars().all()
    return [FriendRequestResponse.from_friendship(f) for f in friendships]


# ── 친구 요청 수락/거절 ───────────────────────────────────
async def respond_to_friend_request(
    db: AsyncSession, 
    friendship_id: int, 
    is_accept: bool, 
    current_user_id: int
):
    # 친구 신청 내역 조회
    result = await db.execute(
        select(Friendship).where(
            Friendship.friendship_id == friendship_id,
            Friendship.addressee_id == current_user_id,
            Friendship.status == FriendshipStatus.PENDING  # 대기 중인 신청만 처리 가능
        )
    )
    friendship = result.scalar_one_or_none()

    if not friendship:
        raise HTTPException(status_code=404, detail="존재하지 않거나 이미 처리된 친구 신청입니다.")

    if is_accept: # 수락
        friendship.status = FriendshipStatus.ACCEPTED
        message = "친구 신청 수락"
    else: # 거절
        await db.delete(friendship)
        message = "친구 신청 거절"
    await db.commit()
    
    if is_accept:
        await db.refresh(friendship)
        
    return {"message": message}


# ── 친구 목록 보기 ────────────────────────────────────────
async def get_friend_list(
    current_user: User,
    db: AsyncSession,
) -> list[FriendListItem]:
    result = await db.execute(
        select(Friendship)
        .options(joinedload(Friendship.requester), joinedload(Friendship.addressee))
        .where(
            or_(
                Friendship.requester_id == current_user.user_id,
                Friendship.addressee_id == current_user.user_id,
            ),
            Friendship.status == FriendshipStatus.ACCEPTED,
        )
    )
    friendships = result.scalars().all()

    items = []
    for f in friendships:
        friend_user = f.addressee if f.requester_id == current_user.user_id else f.requester
        items.append(
            FriendListItem(
                friendship_id=f.friendship_id,
                user=FriendSearchResponse.from_user(friend_user),
                since=f.updated_at or f.created_at,
            )
        )
    return items

# 수락된 친구 목록만 빠르게 추출 
async def get_accepted_friend_ids(user_id: int, db: AsyncSession) -> list[int]:
    result = await db.execute(
        select(Friendship).where(
            or_(
                Friendship.requester_id == user_id,
                Friendship.addressee_id == user_id,
            ),
            Friendship.status == FriendshipStatus.ACCEPTED,
        )
    )
    friendships = result.scalars().all()
    return [
        f.addressee_id if f.requester_id == user_id else f.requester_id
        for f in friendships
    ]

# ── 친구 삭제 ─────────────────────────────────────────────
async def remove_friend(
    friendship_id: int,
    current_user: User,
    db: AsyncSession,
) -> None:
    result = await db.execute(
        select(Friendship).where(
            Friendship.friendship_id == friendship_id,
            or_(
                Friendship.requester_id == current_user.user_id,
                Friendship.addressee_id == current_user.user_id,
            ),
            Friendship.status == FriendshipStatus.ACCEPTED,
        )
    )
    friendship = result.scalar_one_or_none()

    if not friendship:
        raise HTTPException(status_code=404, detail="친구 관계를 찾을 수 없습니다.")

    await db.delete(friendship)
