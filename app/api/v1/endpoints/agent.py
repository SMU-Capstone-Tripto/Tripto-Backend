from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_async_db
from app.core.dependencies import get_current_user
from app.models.user_model import User
from app.services import agent_service

router = APIRouter(prefix="/agent", tags=["AI 여행 에이전트"])


class ChatRequest(BaseModel):
    message: str


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
    - `[DONE]` — 스트림 종료 신호

    step == "optimized"일 때 `snapshot_id`가 포함됩니다. 이 값을 모아두면 `/vote/create` 호출 시 사용됩니다.
    """
    return StreamingResponse(
        agent_service.chat_stream(current_user.user_id, body.message, db, current_user.nickname),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.delete("/session", summary="세션 초기화 (새 여행 시작)")
async def reset_session(current_user: User = Depends(get_current_user)):
    """현재 대화 상태를 초기화합니다. 새 여행 계획을 시작할 때 호출하세요."""
    await agent_service.reset_session(current_user.user_id)
    return {"message": "세션이 초기화되었습니다."}


@router.get("/session", summary="현재 세션 상태 조회")
async def get_session_state(current_user: User = Depends(get_current_user)):
    """앱 재시작 후 이전 세션 복원 시 사용합니다."""
    session = await agent_service.get_session(current_user.user_id)
    result  = session.get("result") or {}
    return {
        "step":           result.get("current_step", "start"),
        "plan_title":     result.get("plan_title", ""),
        "itinerary":      result.get("itinerary", []),
        "estimated_cost": result.get("estimated_cost", {}),
        "selected_acc":   result.get("selected_acc"),
        "snapshot_ids":   session.get("snapshot_ids", []),
    }
