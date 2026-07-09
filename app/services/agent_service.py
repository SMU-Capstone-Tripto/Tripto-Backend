
import sys
import os
import asyncio
import json
from typing import AsyncGenerator, Optional
from concurrent.futures import ThreadPoolExecutor
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from sqlalchemy.ext.asyncio import AsyncSession

# app/agent/ 디렉토리를 sys.path에 추가 (graph.py의 상대 임포트 해결)
_AGENT_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "agent")
)
if _AGENT_DIR not in sys.path:
    sys.path.insert(0, _AGENT_DIR)

from graph import app as _graph  # noqa: E402
from app.services import vote_service  # noqa: E402
from app.infra.redis_client import get_redis  # noqa: E402

_VOTE_TRIGGERS = [
    "투표할게", "투표 시작", "투표하자", "투표 만들어", "투표할래",
    "투표 해줘", "이제 투표", "투표 시작해", "투표 열어", "투표 개설",
]

def _is_vote_trigger(message: str) -> bool:
    return any(trigger in message for trigger in _VOTE_TRIGGERS)


_STATUS = {
    "intent_analyzer":     "사용자 의도 파악 중...",
    "info_gatherer":       "필요 정보 확인 중...",
    "confirmer":           "수집된 정보 정리 중...",
    "travel_searcher":     "숙소 검색 중...",
    "tourist_searcher":    "관광지 검색 중...",
    "spot_enhancer":       "유명 관광지 보완 중...",
    "restaurant_searcher": "식당 검색 중...",
    "transport_searcher":  "교통편 검색 중...",
    "optimizer":           "일정 최적화 중...",
    "revision_manager":    "일정 수정 중...",
}

SESSION_TTL = 60 * 60 * 24 * 7  # 7일

_MSG_TYPE_MAP = {
    "AIMessage":     AIMessage,
    "HumanMessage":  HumanMessage,
    "SystemMessage": SystemMessage,
}

# 유저별 인메모리 캐시 (Redis의 write-through 캐시)
_sessions: dict[int, dict] = {}
_executor = ThreadPoolExecutor(max_workers=4)


def _default_session() -> dict:
    return {
        "graph_state": {
            "question": "",
            "messages": [],
            "preferences": [],
            "itinerary": [],
            "itinerary_history": [],
            "current_step": "start",
        },
        "snapshot_ids": [],
        "result": None,
    }


def _serialize_session(session: dict) -> str:
    """세션 dict → Redis에 저장할 JSON 문자열 변환."""
    graph_state = dict(session.get("graph_state") or {})
    messages = graph_state.pop("messages", [])
    graph_state["messages"] = [
        {"type": m.__class__.__name__, "content": m.content}
        for m in messages
    ]
    return json.dumps(
        {
            "graph_state": graph_state,
            "snapshot_ids": session.get("snapshot_ids", []),
        },
        ensure_ascii=False,
        default=str,  # datetime 등 직렬화 안 되는 타입 fallback
    )


def _deserialize_session(data: str) -> dict:
    """Redis JSON 문자열 → 세션 dict 복원."""
    raw = json.loads(data)
    graph_state = raw.get("graph_state", {})
    msgs_raw = graph_state.pop("messages", [])
    graph_state["messages"] = [
        _MSG_TYPE_MAP.get(m["type"], HumanMessage)(content=m["content"])
        for m in msgs_raw
    ]
    return {
        "graph_state": graph_state,
        "snapshot_ids": raw.get("snapshot_ids", []),
        "result": graph_state,  # result는 graph_state와 동일 참조
    }


async def get_session(user_id: int) -> dict:
    """세션 반환. 인메모리 캐시 미스 시 Redis에서 로드."""
    if user_id in _sessions:
        return _sessions[user_id]

    try:
        redis = await get_redis()
        if redis:
            data = await redis.get(f"agent_session:{user_id}")
            if data:
                session = _deserialize_session(data)
                _sessions[user_id] = session
                return session
    except Exception:
        pass

    session = _default_session()
    _sessions[user_id] = session
    return session


async def _save_session(user_id: int) -> None:
    """현재 인메모리 세션을 Redis에 동기화."""
    session = _sessions.get(user_id)
    if not session:
        return
    try:
        redis = await get_redis()
        if redis:
            await redis.setex(
                f"agent_session:{user_id}",
                SESSION_TTL,
                _serialize_session(session),
            )
    except Exception:
        pass  # Redis 장애가 채팅을 멈추면 안 됨


async def reset_session(user_id: int) -> None:
    """인메모리 캐시 + Redis에서 세션 삭제."""
    _sessions.pop(user_id, None)
    try:
        redis = await get_redis()
        if redis:
            await redis.delete(f"agent_session:{user_id}")
    except Exception:
        pass


async def chat_stream(
    user_id: int,
    message: str,
    db: AsyncSession,
    user_nickname: str = "",
) -> AsyncGenerator[str, None]:
    """
    SSE 제너레이터.
    LangGraph를 백그라운드 스레드에서 stream()으로 실행하고
    노드가 완료될 때마다 진행 상황을 yield, 마지막에 결과를 yield.
    step == 'optimized'이면 최신 itinerary를 DB에 snapshot으로 저장.
    """
    session = await get_session(user_id)

    # ── 투표 트리거 감지 ──────────────────────────────────────────────
    if _is_vote_trigger(message):
        snapshot_ids: list[int] = session.get("snapshot_ids", [])
        if not snapshot_ids:
            msg = json.dumps({
                "type": "result",
                "content": "아직 확정된 일정 버전이 없어요. AI와 대화해서 일정을 먼저 만들어 주세요!",
                "step": "no_snapshot",
            }, ensure_ascii=False)
            yield f"data: {msg}\n\n"
            yield "data: [DONE]\n\n"
            return

        try:
            vote_session = await vote_service.create_vote_session(
                db=db,
                creator_id=user_id,
                creator_nickname=user_nickname,
                vote_type="solo",
                snapshot_ids=snapshot_ids,
                room_id=None,
            )
            msg = json.dumps({
                "type": "vote_created",
                "vote_id": vote_session.vote_id,
                "snapshot_count": len(snapshot_ids),
                "content": f"투표가 시작됐어요! 최근 {len(snapshot_ids)}개 버전 중 마음에 드는 일정을 선택해 주세요.",
                "step": "vote_created",
            }, ensure_ascii=False)
            yield f"data: {msg}\n\n"
        except Exception as e:
            msg = json.dumps({"type": "error", "message": str(e)}, ensure_ascii=False)
            yield f"data: {msg}\n\n"
        yield "data: [DONE]\n\n"
        return

    graph_state = {**session["graph_state"], "question": message}

    loop: asyncio.AbstractEventLoop = asyncio.get_event_loop()
    queue: asyncio.Queue            = asyncio.Queue()

    def _run_stream():
        try:
            final_state = dict(graph_state)
            for chunk in _graph.stream(graph_state):
                node_name    = list(chunk.keys())[0]
                state_update = chunk[node_name] or {}
                final_state.update(state_update)

                asyncio.run_coroutine_threadsafe(
                    queue.put({"type": "status", "message": _STATUS.get(node_name, "처리 중...")}),
                    loop,
                )

            asyncio.run_coroutine_threadsafe(
                queue.put({"type": "done", "state": final_state}),
                loop,
            )
        except Exception as e:
            asyncio.run_coroutine_threadsafe(
                queue.put({"type": "error", "message": str(e)}),
                loop,
            )

    loop.run_in_executor(_executor, _run_stream)

    while True:
        item = await queue.get()

        if item["type"] == "status":
            yield f"data: {json.dumps({'type': 'status', 'message': item['message']}, ensure_ascii=False)}\n\n"

        elif item["type"] == "error":
            yield f"data: {json.dumps({'type': 'error', 'message': item['message']}, ensure_ascii=False)}\n\n"
            break

        elif item["type"] == "done":
            final_state            = item["state"]
            session["graph_state"] = final_state
            session["result"]      = final_state

            messages = final_state.get("messages", [])
            bot_msg  = next(
                (m.content for m in reversed(messages) if isinstance(m, AIMessage)),
                None,
            )

            step     = final_state.get("current_step", "")
            response: dict = {
                "type":    "result",
                "content": bot_msg or "",
                "step":    step,
            }

            snapshot_id: Optional[int] = None

            if step == "optimized":
                response["plan_title"]     = final_state.get("plan_title", "")
                response["itinerary"]      = final_state.get("itinerary", [])
                response["estimated_cost"] = final_state.get("estimated_cost", {})
                response["selected_acc"]   = final_state.get("selected_acc")

                # 최신 버전을 DB에 snapshot으로 저장
                history = final_state.get("itinerary_history") or []
                if history:
                    latest = history[0]
                    try:
                        snap = await vote_service.save_snapshot(
                            db=db,
                            user_id=user_id,
                            version_num=latest.get("version", 1),
                            plan_title=latest.get("plan_title"),
                            itinerary=latest.get("itinerary", []),
                            estimated_cost=latest.get("estimated_cost"),
                            selected_acc=latest.get("selected_acc"),
                            traveldates=latest.get("traveldates"),
                            city=latest.get("city"),
                        )
                        snapshot_id = snap.snapshot_id
                        session.setdefault("snapshot_ids", [])
                        session["snapshot_ids"] = ([snapshot_id] + session["snapshot_ids"])[:3]
                        await db.commit()
                    except Exception:
                        pass  # snapshot 저장 실패가 채팅을 멈추면 안 됨

                response["snapshot_id"] = snapshot_id

            # ── 세션 Redis 동기화 ─────────────────────────────────────
            await _save_session(user_id)

            yield f"data: {json.dumps(response, ensure_ascii=False)}\n\n"
            break

    yield "data: [DONE]\n\n"
