from typing import Optional

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_db
from app.core.dependencies import get_current_user
from app.models.user_model import User
from app.services import agent_service

router = APIRouter(prefix="/agent", tags=["AI 여행 에이전트"])


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, description="사용자 메시지")
    room_id: Optional[int] = Field(
        None,
        description="이 대화를 특정 채팅방에 묶어서 진행할 때 지정합니다. "
                     "채팅방마다 서로 다른 room_id를 넘기면 독립된 대화(여행 계획)를 동시에 진행할 수 있습니다. "
                     "생략하면 채팅방과 무관한 개인 대화 1개로 처리됩니다.",
    )

    @field_validator("message")
    @classmethod
    def message_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("메시지는 공백일 수 없습니다.")
        return v


@router.post("/chat", summary="에이전트 채팅 (SSE 스트리밍)")
async def agent_chat(
    body: ChatRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    """
    메시지를 보내면 SSE로 실시간 진행 상황과 결과를 수신합니다.

    **이벤트 종류:**
    - `{"type": "status", "message": "관광지 검색 중..."}` — 노드 진행 상황
    - `{"type": "result", "content": "...", "step": "optimized", "snapshot_id": 1, ...}` — 최종 결과
    - `{"type": "error", "message": "..."}` — 오류
    - `{"type": "vote_created", "vote_id": 1, "vote_type": "solo|group", "snapshot_count": 2, "content": "..."}` — 대화 중 투표 트리거 감지 시
    - `[DONE]` — 스트림 종료 신호

    step == "optimized"일 때 `snapshot_id`가 포함됩니다. 이 값을 모아두면 `/vote/create` 호출 시 사용됩니다.
    일정 저장에 실패하면 `snapshot_id`가 `null`이고 `snapshot_error` 필드가 대신 포함됩니다 — 이 경우
    `/vote/create`를 호출해도 실패하니, 사용자에게 재시도를 안내하세요.

    HTTP 상태 코드는 스트림 시작 여부만 나타내며 (SSE 특성상) 항상 200입니다.
    실패 여부는 반드시 스트림 바디의 `type == "error"` 이벤트로 판단하세요.

    `room_id`를 지정하면 그 채팅방 전용 대화로 진행되며, 다른 room_id(또는 room_id 없음)의 대화와
    서로 섞이지 않습니다. 여러 그룹에서 동시에 각자 여행을 계획하려면 room_id를 다르게 넘기세요.

    **"투표해" 등 투표 트리거 문구를 보냈을 때 (즉시 생성되지 않고 먼저 확인을 받습니다):**
    1. 트리거 문구 감지 시 바로 투표를 만들지 않고 `{"type": "result", "step": "vote_confirm", ...}`로
       "투표를 시작할까요?"라고 되묻습니다. 이때 `room_id`가 있고 발신자가 방장이면 그룹 투표로,
       `room_id`가 없으면 solo 투표로 진행될 예정임을 이미 내부적으로 정해둔 상태입니다.
       (단, `room_id`가 있는데 발신자가 방장이 아니면 확인 단계 없이 바로
       `{"type": "result", "step": "vote_denied", ...}`로 거부됩니다.)
    2. 다음 메시지로 "네"/"좋아"/"시작" 등 긍정 답변을 보내면 그제서야 투표가 실제로 생성되고
       `{"type": "vote_created", ...}`가 옵니다.
    3. "아니"/"취소"/"그만" 등 부정 답변을 보내면 `{"type": "result", "step": "vote_cancelled", ...}`로
       취소되고 투표는 만들어지지 않습니다.
    4. 긍정도 부정도 아닌 애매한 답변을 보내면 투표 요청은 자동 취소되고, 그 메시지는 평소처럼
       AI 대화로 처리됩니다 — "투표 시작하면 안 돼" 같은 부정문이 트리거 문구를 우연히 포함해도
       확인 단계에서 걸러지므로 실수로 투표가 생성되지 않습니다.
    """
    return StreamingResponse(
        agent_service.chat_stream(
            current_user.user_id, body.message, db, current_user.nickname, body.room_id
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.delete("/session", summary="세션 초기화 (새 여행 시작)")
async def reset_session(
    room_id: Optional[int] = Query(None, description="초기화할 대화의 채팅방 ID. 생략하면 개인 대화를 초기화합니다."),
    current_user: User = Depends(get_current_user),
):
    """현재 대화 상태를 초기화합니다. 새 여행 계획을 시작할 때 호출하세요. room_id별로 독립적으로 초기화됩니다."""
    await agent_service.reset_session(current_user.user_id, room_id)
    return {"message": "세션이 초기화되었습니다."}


@router.get("/session", summary="현재 세션 상태 조회")
async def get_session_state(
    room_id: Optional[int] = Query(None, description="조회할 대화의 채팅방 ID. 생략하면 개인 대화를 조회합니다."),
    current_user: User = Depends(get_current_user),
):
    """앱 재시작 후 이전 세션 복원 시 사용합니다. room_id별로 독립적으로 조회됩니다."""
    session = await agent_service.get_session(current_user.user_id, room_id)
    result  = session.get("result") or {}
    return {
        "step":           result.get("current_step", "start"),
        "plan_title":     result.get("plan_title", ""),
        "itinerary":      result.get("itinerary", []),
        "estimated_cost": result.get("estimated_cost", {}),
        "selected_acc":   result.get("selected_acc"),
        "snapshot_ids":   session.get("snapshot_ids", []),
    }
