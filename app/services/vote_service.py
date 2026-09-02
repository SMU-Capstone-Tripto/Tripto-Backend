import logging
import re
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.chat_model import ChatRoomMember
from app.models.itinerary_vote_model import (
    ItinerarySnapshot, VoteRecord, VoteSession, VoteStatus, VoteType,
)
from app.models.schedule_model import Schedule, ScheduleCategoryEnum
from app.models.travel_model import Travel
from app.services import notification_service

logger = logging.getLogger(__name__)

# 그룹 투표 자동 마감까지의 시간 (이 시간이 지나면 조회/투표/백그라운드 루프가 자동으로 마감한다)
VOTE_AUTO_CLOSE_HOURS = 24

_DATE_RE = re.compile(r'(\d{4})\D+(\d{1,2})\D+(\d{1,2})')


def _parse_travel_dates(traveldates: Optional[str]) -> tuple[date, date]:
    """'2026-05-20 ~ 2026-05-23' / '2026.05.20~2026.05.23' 등 → (start, end).
    파싱 실패 시 (오늘, 오늘)."""
    today = date.today()
    if not traveldates:
        return today, today
    found = _DATE_RE.findall(str(traveldates))
    if not found:
        return today, today
    try:
        start = date(int(found[0][0]), int(found[0][1]), int(found[0][2]))
        end = (date(int(found[1][0]), int(found[1][1]), int(found[1][2]))
               if len(found) > 1 else start)
    except (ValueError, IndexError):
        return today, today
    return (start, end) if end >= start else (end, start)


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

    expires_at = (datetime.now(timezone.utc) + timedelta(hours=VOTE_AUTO_CLOSE_HOURS)
                  if vote_type == "group" else None)

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
    # 조회 시점에 만료된 그룹 투표면 먼저 자동 마감한다 (별도 finalize 호출 없이도 등록되도록)
    await _close_if_expired(db, vote_id)

    result = await db.execute(
        select(VoteSession)
        .options(selectinload(VoteSession.records))
        .where(VoteSession.vote_id == vote_id)
    )
    session = result.scalar_one_or_none()
    if not session:
        raise HTTPException(status_code=404, detail="투표를 찾을 수 없습니다.")
    return session


# ── 자동 마감 / 여행 등록 ─────────────────────────────────────────────────

async def _ensure_travel(db: AsyncSession, vote_id: int) -> Optional[Travel]:
    """당선 스냅샷으로 Travel + 일별 Schedule을 생성한다. **멱등** —
    이미 만들었으면 그 Travel을 반환한다. vote_sessions 행을 잠가
    동시 호출(수동 finalize / 자동 마감 / 조회 시 정리)이 Travel을 중복 생성하지 않게 한다.
    호출자가 커밋해야 한다."""
    locked = (await db.execute(
        select(VoteSession).where(VoteSession.vote_id == vote_id).with_for_update()
    )).scalar_one_or_none()
    if not locked:
        return None

    if locked.winner_travel_id:
        return (await db.execute(
            select(Travel).where(Travel.travel_id == locked.winner_travel_id)
        )).scalar_one_or_none()

    if not locked.winner_snapshot_id:
        return None

    snap = (await db.execute(
        select(ItinerarySnapshot).where(ItinerarySnapshot.snapshot_id == locked.winner_snapshot_id)
    )).scalar_one_or_none()
    if not snap:
        return None

    start_date, end_date = _parse_travel_dates(snap.traveldates)
    itinerary_text = list(snap.itinerary) if snap.itinerary else None
    travel = Travel(
        owner_id=locked.creator_id,
        title=snap.plan_title or "여행 계획",
        destination=snap.city or "",
        start_date=start_date,
        end_date=end_date,
        itinerary=itinerary_text,   # AI 일정 원문 보존 (여행 상세에서 그대로 표시)
    )
    db.add(travel)
    await db.flush()

    # 일자별 껍데기 Schedule (사용자가 이후 편집). 실제 내용은 travel.itinerary 에 있음.
    for day_idx, _ in enumerate(snap.itinerary or []):
        db.add(Schedule(
            travel_id=travel.travel_id,
            day_number=day_idx + 1,
            date=start_date + timedelta(days=day_idx),
            order_index=0,
            place_name=f"{day_idx + 1}일차 일정",
            category=ScheduleCategoryEnum.ETC,
        ))

    locked.winner_travel_id = travel.travel_id
    await db.flush()
    return travel


async def _close_if_expired(db: AsyncSession, vote_id: int) -> None:
    """만료된 ACTIVE 그룹 투표 1건을 마감한다 (당선작 결정 + 여행 등록 + 알림).
    이미 마감됐거나 아직 시간이 안 됐으면 아무것도 안 한다. 자체적으로 커밋한다."""
    locked = (await db.execute(
        select(VoteSession)
        .options(selectinload(VoteSession.records))
        .where(VoteSession.vote_id == vote_id)
        .with_for_update()
    )).scalar_one_or_none()

    if (not locked
            or locked.status != VoteStatus.ACTIVE
            or not locked.expires_at
            or locked.expires_at > datetime.now(timezone.utc)):
        await db.rollback()   # 잠금 해제
        return

    winner, tie = _winner_or_tie(locked.records)
    if winner:
        locked.winner_snapshot_id = winner
    locked.status = VoteStatus.FINALIZED
    await db.flush()

    travel = await _ensure_travel(db, vote_id) if winner else None
    await db.commit()

    try:
        if tie:
            # 동점 → 자동 등록하지 않고 방장에게 선택 요청
            await notification_service.notify_vote_tie(db, locked)
        else:
            await notification_service.notify_vote_finalized(db, locked, travel)
    except Exception:
        logger.exception("vote_id=%s 마감 알림 실패", vote_id)


async def sweep_expired_votes(db: AsyncSession) -> int:
    """만료된 모든 ACTIVE 투표를 마감한다. 목록 조회 + 백그라운드 루프에서 호출."""
    now = datetime.now(timezone.utc)
    vote_ids = (await db.execute(
        select(VoteSession.vote_id).where(
            VoteSession.status == VoteStatus.ACTIVE,
            VoteSession.expires_at.isnot(None),
            VoteSession.expires_at < now,
        )
    )).scalars().all()

    closed = 0
    for vid in vote_ids:
        try:
            await _close_if_expired(db, vid)
            closed += 1
        except Exception:
            logger.exception("vote_id=%s 자동 마감 실패", vid)
            await db.rollback()
    return closed


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

    just_finalized = False
    just_tie = False

    # solo는 투표 즉시 확정
    if session.vote_type == VoteType.SOLO:
        session.status = VoteStatus.FINALIZED
        session.winner_snapshot_id = snapshot_id
        just_finalized = True

    # group은 전원 투표 완료 여부 확인 (방금 넣은 표 포함해 실제 카운트로 비교)
    elif session.vote_type == VoteType.GROUP and session.room_id:
        total_members = (await db.execute(
            select(func.count()).select_from(ChatRoomMember)
            .where(ChatRoomMember.room_id == session.room_id)
        )).scalar() or 0
        current_votes = (await db.execute(
            select(func.count()).select_from(VoteRecord)
            .where(VoteRecord.vote_session_id == vote_id)
        )).scalar() or 0

        if total_members and current_votes >= total_members:
            all_records = (await db.execute(
                select(VoteRecord).where(VoteRecord.vote_session_id == vote_id)
            )).scalars().all()
            winner, tie = _winner_or_tie(all_records)
            session.status = VoteStatus.FINALIZED
            if winner:
                session.winner_snapshot_id = winner
                just_finalized = True
            elif tie:
                just_tie = True

    await db.commit()

    # 확정됐으면 여행 자동 등록 + 알림 (실패해도 투표 자체는 이미 확정된 상태)
    if just_finalized:
        try:
            travel = await _ensure_travel(db, vote_id)
            await db.commit()
            await notification_service.notify_vote_finalized(db, session, travel)
        except Exception:
            logger.exception("vote_id=%s 확정 후 여행 등록/알림 실패", vote_id)
            await db.rollback()
    elif just_tie:
        # 동점 → 자동 등록하지 않고 방장에게 선택 요청
        try:
            await notification_service.notify_vote_tie(db, session)
        except Exception:
            logger.exception("vote_id=%s 동점 알림 실패", vote_id)


async def change_vote(
    db: AsyncSession,
    vote_id: int,
    voter_id: int,
    snapshot_id: int,
) -> None:
    """이미 참여한 투표에서 선택한 일정 버전을 변경한다.
    solo는 투표 즉시 확정되므로 변경 불가, group·ACTIVE 상태에서만 허용."""
    session = await get_vote_session(db, vote_id)

    if session.status != VoteStatus.ACTIVE:
        raise HTTPException(status_code=400, detail="이미 종료된 투표입니다.")
    if session.vote_type == VoteType.SOLO:
        raise HTTPException(status_code=400, detail="solo 투표는 투표 즉시 확정되어 변경할 수 없습니다.")
    if snapshot_id not in session.snapshot_ids:
        raise HTTPException(status_code=400, detail="해당 스냅샷은 이 투표에 포함되지 않습니다.")

    existing = await db.execute(
        select(VoteRecord).where(
            VoteRecord.vote_session_id == vote_id,
            VoteRecord.voter_id == voter_id,
        )
    )
    record = existing.scalar_one_or_none()
    if record is None:
        raise HTTPException(status_code=400, detail="아직 투표하지 않았습니다. 먼저 투표해 주세요.")

    if record.snapshot_id != snapshot_id:
        record.snapshot_id = snapshot_id
        await db.commit()


async def delete_vote_session(
    db: AsyncSession,
    vote_id: int,
    current_user_id: int,
) -> None:
    """진행 중인 투표를 취소(삭제)한다. 생성자만, ACTIVE 상태만 가능.
    VoteRecord는 cascade로 함께 삭제된다."""
    session = await get_vote_session(db, vote_id)

    if session.creator_id != current_user_id:
        raise HTTPException(status_code=403, detail="투표 생성자만 취소할 수 있습니다.")
    if session.status != VoteStatus.ACTIVE:
        raise HTTPException(status_code=400, detail="이미 종료된 투표는 취소할 수 없습니다.")

    # group이면 채팅방 멤버에게 취소 알림
    if session.vote_type == VoteType.GROUP and session.room_id:
        members_result = await db.execute(
            select(ChatRoomMember).where(
                ChatRoomMember.room_id == session.room_id,
                ChatRoomMember.user_id != current_user_id,
            )
        )
        for member in members_result.scalars().all():
            await notification_service._create_notification(
                db=db,
                recipient_id=member.user_id,
                actor_id=current_user_id,
                notif_type="vote_cancelled",
                content="진행 중이던 여행 일정 투표가 취소되었습니다.",
            )

    await db.delete(session)
    await db.commit()


def _winner_or_tie(records: List[VoteRecord]) -> tuple[Optional[int], bool]:
    """(당선 snapshot_id, 동점여부).
    - 단독 1위 → (그 snapshot_id, False)
    - 최다 득표가 2개 이상 → (None, True)  → 방장이 직접 선택해야 함
    - 표 없음 → (None, False)
    """
    counts: dict[int, int] = {}
    for r in records:
        counts[r.snapshot_id] = counts.get(r.snapshot_id, 0) + 1
    if not counts:
        return None, False
    top = max(counts.values())
    leaders = [sid for sid, c in counts.items() if c == top]
    if len(leaders) > 1:
        return None, True
    return leaders[0], False


async def finalize_vote(
    db: AsyncSession,
    vote_id: int,
    current_user_id: int,
    winner_snapshot_id: Optional[int] = None,
) -> Travel:
    """투표를 마감하고 당선 일정으로 Travel + Schedule을 생성합니다.
    동점이면 방장이 winner_snapshot_id 로 최종 일정을 직접 지정해야 합니다."""
    # get_vote_session 안에서 이미 만료 자동 마감됐을 수 있다
    session = await get_vote_session(db, vote_id)
    already_registered = session.winner_travel_id is not None

    if session.creator_id != current_user_id:
        raise HTTPException(status_code=403, detail="투표 생성자만 확정할 수 있습니다.")

    # 방장이 특정 일정을 지정했으면 그게 이 투표의 후보인지 검증
    if winner_snapshot_id is not None and winner_snapshot_id not in session.snapshot_ids:
        raise HTTPException(status_code=400, detail="해당 스냅샷은 이 투표에 포함되지 않습니다.")

    if session.status == VoteStatus.ACTIVE:
        if not session.records:
            raise HTTPException(status_code=400, detail="아직 투표한 사람이 없습니다.")
        auto_winner, tie = _winner_or_tie(session.records)
        session.status = VoteStatus.FINALIZED
        # 단독 1위는 그대로, 동점이면 방장이 지정한 것만 인정
        session.winner_snapshot_id = winner_snapshot_id if tie else auto_winner
        await db.flush()
        if session.winner_snapshot_id is None:  # 동점인데 방장이 안 골랐음
            await db.commit()
            raise HTTPException(status_code=409, detail="동점입니다. winner_snapshot_id로 최종 일정을 지정해 주세요.")
    elif session.winner_snapshot_id is None:
        # 자동 마감됐지만 동점이라 당선작 미결정 상태
        if winner_snapshot_id is None:
            raise HTTPException(status_code=409, detail="동점으로 마감됐습니다. winner_snapshot_id로 최종 일정을 지정해 주세요.")
        session.winner_snapshot_id = winner_snapshot_id
        await db.flush()

    if not session.winner_snapshot_id:
        raise HTTPException(status_code=400, detail="당선된 일정이 없습니다.")

    # 여행 등록은 멱등 헬퍼로 (이미 자동 등록됐으면 그걸 반환 — 중복 생성 없음)
    travel = await _ensure_travel(db, vote_id)
    await db.commit()
    if not travel:
        raise HTTPException(status_code=404, detail="당선 스냅샷을 찾을 수 없습니다.")

    # 자동 마감 때 이미 알림을 보냈으면 중복 발송하지 않는다
    if not already_registered:
        try:
            await notification_service.notify_vote_finalized(db, session, travel)
        except Exception:
            logger.exception("vote_id=%s 확정 알림 실패", vote_id)

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
    await sweep_expired_votes(db)   # 만료된 투표는 목록에서 빼기 전에 자동 마감

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
    await sweep_expired_votes(db)   # 만료된 투표를 완료 목록에 포함시키기 위해 먼저 마감

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
