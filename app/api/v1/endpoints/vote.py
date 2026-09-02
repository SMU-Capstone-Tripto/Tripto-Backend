from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_db
from app.core.dependencies import get_current_user
from app.models.user_model import User
from app.schemas.vote_schema import (
    FinalizeResponse,
    SnapshotOut,
    VoteCastRequest,
    VoteCreateRequest,
    VoteResultItem,
    VoteSessionResponse,
)
from app.services import vote_service

router = APIRouter(prefix="/vote", tags=["투표"])


@router.post("/create", response_model=VoteSessionResponse, summary="투표 세션 생성")
async def create_vote(
    body: VoteCreateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    현재 세션의 최근 itinerary 스냅샷으로 투표를 시작합니다.

    - **solo**: room_id 불필요. 본인이 선택하면 즉시 확정.
    - **group**: room_id 필수. 채팅방 멤버 전원에게 알림 발송.

    스냅샷은 `POST /agent/chat` 호출 시 사용한 것과 **동일한 room_id**의 대화 세션에서 가져옵니다.
    즉 room_id가 있는 채팅방 안에서 AI와 나눈 대화의 결과로 투표를 만들려면, 이 요청의 room_id도
    그 채팅방과 같은 값으로 보내야 합니다. solo 투표(room_id 없음)는 개인 대화 세션을 사용합니다.

    투표할 스냅샷이 없으면 먼저 AI와 대화해 일정을 생성하세요.
    """
    from app.services.agent_service import get_session

    session_data = await get_session(current_user.user_id, body.room_id)
    snapshot_ids: List[int] = session_data.get("snapshot_ids", [])

    if not snapshot_ids:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="투표할 일정 버전이 없습니다. AI와 대화해 일정을 먼저 생성하세요.")

    vote_session = await vote_service.create_vote_session(
        db=db,
        creator_id=current_user.user_id,
        creator_nickname=current_user.nickname,
        vote_type=body.vote_type,
        snapshot_ids=snapshot_ids,
        room_id=body.room_id,
    )

    return await _build_response(db, vote_session, current_user.user_id)


# ── 정적 경로를 동적 경로보다 먼저 등록 (Fix 1) ──────────────────────────
@router.get("/active", response_model=List[VoteSessionResponse], summary="참여 대기 중인 투표 목록")
async def list_active_votes(
    room_id: Optional[int] = Query(None, description="지정하면 해당 채팅방의 투표만 반환합니다."),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    활성 투표 목록을 반환합니다.

    - room_id 미지정: 내가 만들었거나 내가 속한 모든 채팅방의 활성 투표를 합쳐서 반환합니다.
    - room_id 지정: 해당 채팅방의 활성 투표만 반환합니다(해당 방 멤버만 조회 가능).
    """
    sessions = await vote_service.get_active_votes(db, current_user.user_id, room_id=room_id)
    return [await _build_response(db, s, current_user.user_id) for s in sessions]


@router.get("/finalized", response_model=List[VoteSessionResponse], summary="완료된 투표 목록")
async def list_finalized_votes(
    room_id: Optional[int] = Query(None, description="지정하면 해당 채팅방의 투표만 반환합니다."),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    완료(확정)된 투표 목록을 최신순으로 반환합니다.

    - room_id 미지정: 내가 만들었거나 내가 속한 모든 채팅방의 완료된 투표를 합쳐서 반환합니다.
    - room_id 지정: 해당 채팅방의 완료된 투표만 반환합니다(해당 방 멤버만 조회 가능).
    """
    sessions = await vote_service.get_finalized_votes(db, current_user.user_id, room_id=room_id)
    return [await _build_response(db, s, current_user.user_id) for s in sessions]


@router.get("/{vote_id}", response_model=VoteSessionResponse, summary="투표 현황 조회")
async def get_vote(
    vote_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """투표 현황(각 버전 득표수, 내 투표 여부, 만료 시간)을 조회합니다."""
    vote_session = await vote_service.get_vote_session(db, vote_id)
    return await _build_response(db, vote_session, current_user.user_id)


@router.post("/{vote_id}/cast", summary="투표 참여")
async def cast_vote(
    vote_id: int,
    body: VoteCastRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    선택한 일정 버전에 투표합니다. 1인 1표만 허용됩니다.

    - **solo**: 투표 즉시 해당 버전으로 확정됩니다.
    - **group**: 전원 투표 완료 시 자동 확정됩니다.
    """
    await vote_service.cast_vote(
        db=db,
        vote_id=vote_id,
        voter_id=current_user.user_id,
        snapshot_id=body.snapshot_id,
    )
    return {"message": "투표가 완료되었습니다."}


@router.put("/{vote_id}/cast", summary="투표 변경")
async def change_vote(
    vote_id: int,
    body: VoteCastRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    이미 참여한 투표에서 다른 일정 버전으로 선택을 변경합니다.

    - 진행 중(active)인 **group** 투표에서만 가능합니다.
    - solo 투표는 투표 즉시 확정되므로 변경할 수 없습니다.
    - 아직 투표하지 않았다면 `POST /vote/{vote_id}/cast` 를 먼저 호출하세요.
    """
    await vote_service.change_vote(
        db=db,
        vote_id=vote_id,
        voter_id=current_user.user_id,
        snapshot_id=body.snapshot_id,
    )
    return {"message": "투표가 변경되었습니다."}


@router.delete("/{vote_id}", summary="활성 투표 취소")
async def cancel_vote(
    vote_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    진행 중인 투표를 취소(삭제)합니다.

    - 투표 **생성자만** 호출할 수 있습니다.
    - 아직 확정되지 않은(active) 투표만 취소할 수 있습니다.
    - group 투표는 채팅방 멤버 전원에게 취소 알림이 발송됩니다.
    - 투표 기록도 함께 삭제됩니다.
    """
    await vote_service.delete_vote_session(
        db=db,
        vote_id=vote_id,
        current_user_id=current_user.user_id,
    )
    return {"message": "투표가 취소되었습니다."}


class FinalizeRequest(BaseModel):
    winner_snapshot_id: Optional[int] = Field(
        None,
        description="동점일 때 방장이 최종으로 고를 일정 스냅샷 id. 단독 1위면 생략 가능.",
    )


@router.post("/{vote_id}/finalize", response_model=FinalizeResponse, summary="투표 강제 확정 및 여행 등록")
async def finalize_vote(
    vote_id: int,
    body: Optional[FinalizeRequest] = None,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    투표를 마감하고 당선 일정을 최종 여행으로 등록합니다. 투표 생성자만 호출할 수 있습니다.

    - 그룹 투표는 24시간이 지나면 조회/투표/서버 루프가 **자동으로 마감**하고 여행을 등록합니다.
      이 API는 그 전에 즉시 마감하거나, 아래 동점 상황을 해소할 때 씁니다.
    - **동점**(최다 득표 일정이 2개 이상)이면 자동 등록되지 않고 409를 반환합니다.
      이때 `winner_snapshot_id` 로 최종 일정을 지정해 다시 호출하세요.
    """
    travel = await vote_service.finalize_vote(
        db=db,
        vote_id=vote_id,
        current_user_id=current_user.user_id,
        winner_snapshot_id=body.winner_snapshot_id if body else None,
    )
    return FinalizeResponse(
        travel_id=travel.travel_id,
        message="일정이 최종 확정되었습니다. 여행 탭에서 확인하세요!",
    )


# ── 헬퍼 ──────────────────────────────────────────────────────────────────

async def _build_response(db: AsyncSession, vote_session, user_id: int) -> VoteSessionResponse:
    """VoteSession ORM → VoteSessionResponse 변환"""
    from sqlalchemy import select
    from app.models.itinerary_vote_model import ItinerarySnapshot

    snapshots = await vote_service.get_snapshots_by_ids(db, vote_session.snapshot_ids)
    snap_map = {s.snapshot_id: s for s in snapshots}

    # 득표 집계
    counts: dict[int, int] = {sid: 0 for sid in vote_session.snapshot_ids}
    my_vote: int | None = None
    for record in vote_session.records:
        counts[record.snapshot_id] = counts.get(record.snapshot_id, 0) + 1
        if record.voter_id == user_id:
            my_vote = record.snapshot_id

    results = [
        VoteResultItem(
            snapshot_id=sid,
            plan_title=snap_map[sid].plan_title if sid in snap_map else None,
            vote_count=counts.get(sid, 0),
        )
        for sid in vote_session.snapshot_ids
    ]

    # 마감됐는데 당선작이 없고 표는 있음 = 동점 → 방장이 finalize(winner_snapshot_id)로 골라야 함
    top = max(counts.values()) if counts else 0
    needs_tiebreak = (
        vote_session.status.value == "finalized"
        and vote_session.winner_snapshot_id is None
        and top > 0
    )
    tied_snapshot_ids = (
        [sid for sid, c in counts.items() if c == top] if needs_tiebreak else []
    )

    snapshots_out = [
        SnapshotOut(
            snapshot_id=s.snapshot_id,
            version_num=s.version_num,
            plan_title=s.plan_title,
            itinerary=s.itinerary,
            estimated_cost=s.estimated_cost,
            city=s.city,
            traveldates=s.traveldates,
            created_at=s.created_at,
        )
        for s in sorted(snapshots, key=lambda x: x.version_num, reverse=True)
    ]

    return VoteSessionResponse(
        vote_id=vote_session.vote_id,
        vote_type=vote_session.vote_type.value,
        status=vote_session.status.value,
        room_id=vote_session.room_id,
        snapshots=snapshots_out,
        results=results,
        my_vote=my_vote,
        winner_snapshot_id=vote_session.winner_snapshot_id,
        winner_travel_id=vote_session.winner_travel_id,
        needs_tiebreak=needs_tiebreak,
        tied_snapshot_ids=tied_snapshot_ids,
        expires_at=vote_session.expires_at,
        created_at=vote_session.created_at,
    )
