from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.chat_model import ChatRoomMember
from app.models.itinerary_vote_model import (
    ItinerarySnapshot, VoteRecord, VoteSession, VoteStatus, VoteType,
)
from app.models.schedule_model import Schedule, ScheduleCategoryEnum
from app.models.travel_model import Travel
from app.services import notification_service


async def save_snapshot(
    db: AsyncSession,
    user_id: int,
    version_num: int,
    plan_title: Optional[str],
    itinerary: List[str],
    estimated_cost: Optional[dict],
    selected_acc: Optional[dict],
    traveldates: Optional[str],
    city: Optional[str],
) -> ItinerarySnapshot:
    snap = ItinerarySnapshot(
        user_id=user_id,
        version_num=version_num,
        plan_title=plan_title,
        itinerary=itinerary,
        estimated_cost=estimated_cost,
        selected_acc=selected_acc,
        traveldates=traveldates,
        city=city,
    )
    db.add(snap)
    await db.flush()
    await db.refresh(snap)
    return snap


async def create_vote_session(
    db: AsyncSession,
    creator_id: int,
    creator_nickname: str,
    vote_type: str,
    snapshot_ids: List[int],
    room_id: Optional[int],
) -> VoteSession:
    if vote_type not in ("solo", "group"):
        raise HTTPException(status_code=400, detail="vote_type은 'solo' 또는 'group'이어야 합니다.")
    if vote_type == "group" and not room_id:
        raise HTTPException(status_code=400, detail="group 투표에는 room_id가 필요합니다.")
    if not snapshot_ids:
        raise HTTPException(status_code=400, detail="투표할 일정 버전이 없습니다.")

    expires_at = datetime.now(timezone.utc) + timedelta(hours=48) if vote_type == "group" else None

    session = VoteSession(
        creator_id=creator_id,
        vote_type=VoteType(vote_type),
        room_id=room_id,
        snapshot_ids=snapshot_ids,
        status=VoteStatus.ACTIVE,
        expires_at=expires_at,
    )
    db.add(session)
    await db.flush()

    # group이면 채팅방 멤버 전원에게 알림 발송
    if vote_type == "group" and room_id:
        members_result = await db.execute(
            select(ChatRoomMember).where(
                ChatRoomMember.room_id == room_id,
                ChatRoomMember.user_id != creator_id,
            )
        )
        for member in members_result.scalars().all():
            await notification_service._create_notification(
                db=db,
                recipient_id=member.user_id,
                actor_id=creator_id,
                notif_type="vote_invite",
                content=f"{creator_nickname}님이 여행 일정 투표를 시작했습니다. 참여해 주세요!",
            )

    await db.commit()
    await db.refresh(session)
    return session


async def get_vote_session(db: AsyncSession, vote_id: int) -> VoteSession:
    result = await db.execute(
        select(VoteSession)
        .options(selectinload(VoteSession.records))
        .where(VoteSession.vote_id == vote_id)
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="투표를 찾을 수 없습니다.")
    return session


async def get_snapshots_by_ids(db: AsyncSession, snapshot_ids: List[int]) -> List[ItinerarySnapshot]:
    result = await db.execute(
        select(ItinerarySnapshot)
        .where(ItinerarySnapshot.snapshot_id.in_(snapshot_ids))
        .order_by(ItinerarySnapshot.version_num.desc())
    )
    return result.scalars().all()


async def cast_vote(
    db: AsyncSession,
    vote_id: int,
    voter_id: int,
    snapshot_id: int,
) -> None:
    session = await get_vote_session(db, vote_id)

    if session.status != VoteStatus.ACTIVE:
        raise HTTPException(status_code=400, detail="이미 종료된 투표입니다.")
    if snapshot_id not in session.snapshot_ids:
        raise HTTPException(status_code=400, detail="해당 스냅샷은 이 투표에 포함되지 않습니다.")

    existing = await db.execute(
        select(VoteRecord).where(
            VoteRecord.vote_session_id == vote_id,
            VoteRecord.voter_id == voter_id,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="이미 투표하셨습니다.")

    record = VoteRecord(
        vote_session_id=vote_id,
        voter_id=voter_id,
        snapshot_id=snapshot_id,
    )
    db.add(record)
    await db.flush()

    # solo는 투표 즉시 확정
    if session.vote_type == VoteType.SOLO:
        session.status = VoteStatus.FINALIZED
        session.winner_snapshot_id = snapshot_id

    # group은 전원 투표 완료 여부 확인
    elif session.vote_type == VoteType.GROUP and session.room_id:
        members_result = await db.execute(
            select(ChatRoomMember).where(ChatRoomMember.room_id == session.room_id)
        )
        total_members = len(members_result.scalars().all())
        current_votes = len(session.records)  # flush 이후 records가 갱신됨

        if current_votes >= total_members:
            session.winner_snapshot_id = _pick_winner(session.records)
            session.status = VoteStatus.FINALIZED

    await db.commit()


def _pick_winner(records: List[VoteRecord]) -> int:
    counts: dict[int, int] = {}
    for r in records:
        counts[r.snapshot_id] = counts.get(r.snapshot_id, 0) + 1
    return max(counts, key=lambda k: counts[k])


async def finalize_vote(
    db: AsyncSession,
    vote_id: int,
    current_user_id: int,
) -> Travel:
    """투표를 강제 마감하고 최다 득표 일정으로 Travel + Schedule을 생성합니다."""
    session = await get_vote_session(db, vote_id)

    if session.creator_id != current_user_id:
        raise HTTPException(status_code=403, detail="투표 생성자만 확정할 수 있습니다.")

    # 아직 자동 확정되지 않았다면 현재 기준으로 winner 결정
    if session.status == VoteStatus.ACTIVE:
        if not session.records:
            raise HTTPException(status_code=400, detail="아직 투표한 사람이 없습니다.")
        session.winner_snapshot_id = _pick_winner(session.records)
        session.status = VoteStatus.FINALIZED
        await db.flush()

    snap_result = await db.execute(
        select(ItinerarySnapshot).where(ItinerarySnapshot.snapshot_id == session.winner_snapshot_id)
    )
    snap = snap_result.scalar_one_or_none()
    if not snap:
        raise HTTPException(status_code=404, detail="당선 스냅샷을 찾을 수 없습니다.")

    # traveldates 파싱: "YYYY-MM-DD ~ YYYY-MM-DD"
    start_date = date.today()
    end_date = date.today()
    if snap.traveldates:
        try:
            parts = snap.traveldates.split("~")
            start_date = date.fromisoformat(parts[0].strip())
            end_date = date.fromisoformat(parts[1].strip())
        except Exception:
            pass

    travel = Travel(
        owner_id=session.creator_id,
        title=snap.plan_title or "여행 계획",
        destination=snap.city or "",
        start_date=start_date,
        end_date=end_date,
    )
    db.add(travel)
    await db.flush()

    # 일별 Schedule 생성 (place_name은 "N일차 일정", 사용자가 이후 편집)
    for day_idx, _ in enumerate(snap.itinerary or []):
        sched = Schedule(
            travel_id=travel.travel_id,
            day_number=day_idx + 1,
            date=start_date + timedelta(days=day_idx),
            order_index=0,
            place_name=f"{day_idx + 1}일차 일정",
            category=ScheduleCategoryEnum.ETC,
        )
        db.add(sched)

    await db.commit()
    await db.refresh(travel)
    return travel


async def _assert_room_member(db: AsyncSession, room_id: int, user_id: int) -> None:
    result = await db.execute(
        select(ChatRoomMember).where(
            ChatRoomMember.room_id == room_id,
            ChatRoomMember.user_id == user_id,
        )
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=403, detail="해당 채팅방의 멤버가 아닙니다.")


async def get_active_votes(
    db: AsyncSession, user_id: int, room_id: Optional[int] = None
) -> List[VoteSession]:
    """활성 투표 목록. room_id 지정 시 해당 채팅방의 투표만 반환."""
    if room_id is not None:
        await _assert_room_member(db, room_id, user_id)
        result = await db.execute(
            select(VoteSession)
            .options(selectinload(VoteSession.records))
            .where(
                VoteSession.room_id == room_id,
                VoteSession.status == VoteStatus.ACTIVE,
            )
        )
        return list(result.scalars().all())

    my_result = await db.execute(
        select(VoteSession)
        .options(selectinload(VoteSession.records))
        .where(
            VoteSession.creator_id == user_id,
            VoteSession.status == VoteStatus.ACTIVE,
        )
    )
    my_sessions = list(my_result.scalars().all())

    my_rooms_result = await db.execute(
        select(ChatRoomMember.room_id).where(ChatRoomMember.user_id == user_id)
    )
    my_room_ids = [r for r in my_rooms_result.scalars().all()]

    if my_room_ids:
        group_result = await db.execute(
            select(VoteSession)
            .options(selectinload(VoteSession.records))
            .where(
                VoteSession.room_id.in_(my_room_ids),
                VoteSession.status == VoteStatus.ACTIVE,
                VoteSession.creator_id != user_id,
            )
        )
        my_sessions += list(group_result.scalars().all())

    return my_sessions


async def get_finalized_votes(
    db: AsyncSession, user_id: int, room_id: Optional[int] = None
) -> List[VoteSession]:
    """완료된(확정) 투표 목록, 최신순. room_id 지정 시 해당 채팅방의 투표만 반환."""
    if room_id is not None:
        await _assert_room_member(db, room_id, user_id)
        result = await db.execute(
            select(VoteSession)
            .options(selectinload(VoteSession.records))
            .where(
                VoteSession.room_id == room_id,
                VoteSession.status == VoteStatus.FINALIZED,
            )
            .order_by(VoteSession.updated_at.desc())
        )
        return list(result.scalars().all())

    my_result = await db.execute(
        select(VoteSession)
        .options(selectinload(VoteSession.records))
        .where(
            VoteSession.creator_id == user_id,
            VoteSession.status == VoteStatus.FINALIZED,
        )
    )
    my_sessions = list(my_result.scalars().all())

    my_rooms_result = await db.execute(
        select(ChatRoomMember.room_id).where(ChatRoomMember.user_id == user_id)
    )
    my_room_ids = [r for r in my_rooms_result.scalars().all()]

    if my_room_ids:
        group_result = await db.execute(
            select(VoteSession)
            .options(selectinload(VoteSession.records))
            .where(
                VoteSession.room_id.in_(my_room_ids),
                VoteSession.status == VoteStatus.FINALIZED,
                VoteSession.creator_id != user_id,
            )
        )
        my_sessions += list(group_result.scalars().all())

    my_sessions.sort(key=lambda s: s.updated_at, reverse=True)
    return my_sessions
