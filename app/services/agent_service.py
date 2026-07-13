
import sys
import os
import re
import asyncio
import json
import logging
from typing import AsyncGenerator, Optional
from concurrent.futures import ThreadPoolExecutor
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from sqlalchemy import select
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
from app.models.chat_model import ChatRoom  # noqa: E402

logger = logging.getLogger(__name__)

_VOTE_TRIGGERS = [
    "투표할게", "투표 시작", "투표하자", "투표 만들어", "투표할래",
    "투표 해줘", "이제 투표", "투표 시작해", "투표 열어", "투표 개설",
]

def _is_vote_trigger(message: str) -> bool:
    return any(trigger in message for trigger in _VOTE_TRIGGERS)


_CONFIRM_WORDS = {
    "네", "예", "응", "그래", "좋아", "좋아요", "시작", "시작해", "시작해줘",
    "ㅇㅇ", "ok", "okay", "오케이", "진행", "진행해", "yes",
}
_DENY_WORDS = {
    "아니", "아니요", "아니오", "취소", "취소해", "그만", "안해", "안할래",
    "노노", "no", "싫어", "아직", "나중에",
}


def _match_word_set(text: str, words: set[str]) -> bool:
    """입력 전체가 주어진 단어 집합의 토큰으로만 구성됐는지 확인 (단답형 응답 판별용)."""
    tokens = set(re.split(r"[\s,!?.~]+", text.strip().lower())) - {""}
    return bool(tokens) and tokens.issubset(words)


def _is_confirm(message: str) -> bool:
    return _match_word_set(message, _CONFIRM_WORDS)


def _is_deny(message: str) -> bool:
    return _match_word_set(message, _DENY_WORDS)


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

# (유저, 채팅방)별 인메모리 캐시 (Redis의 write-through 캐시)
# room_id가 None이면 채팅방과 무관한 "개인 대화" 세션을 의미한다.
_sessions: dict[tuple[int, Optional[int]], dict] = {}
_executor = ThreadPoolExecutor(max_workers=4)


def _redis_key(user_id: int, room_id: Optional[int]) -> str:
    return f"agent_session:{user_id}:{room_id if room_id is not None else 'solo'}"


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
        # 투표 트리거 감지 후 "네/아니오" 확인 대기 중인 투표 정보 ({"vote_type", "room_id"} 또는 None)
        "pending_vote": None,
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
            "pending_vote": session.get("pending_vote"),
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
        "pending_vote": raw.get("pending_vote"),
        "result": graph_state,  # result는 graph_state와 동일 참조
    }


async def get_session(user_id: int, room_id: Optional[int] = None) -> dict:
    """
    세션 반환. 인메모리 캐시 미스 시 Redis에서 로드.
    room_id별로 독립된 대화를 유지한다 (room_id 생략 시 개인 대화).
    """
    cache_key = (user_id, room_id)
    if cache_key in _sessions:
        return _sessions[cache_key]

    try:
        redis = await get_redis()
        if redis:
            data = await redis.get(_redis_key(user_id, room_id))
            if data:
                session = _deserialize_session(data)
                _sessions[cache_key] = session
                return session
    except Exception:
        logger.exception("Redis에서 user_id=%s room_id=%s 세션 로드 실패", user_id, room_id)

    session = _default_session()
    _sessions[cache_key] = session
    return session


async def _save_session(user_id: int, room_id: Optional[int] = None) -> None:
    """현재 인메모리 세션을 Redis에 동기화."""
    cache_key = (user_id, room_id)
    session = _sessions.get(cache_key)
    if not session:
        return
    try:
        redis = await get_redis()
        if redis:
            await redis.setex(
                _redis_key(user_id, room_id),
                SESSION_TTL,
                _serialize_session(session),
            )
    except Exception:
        # Redis 장애가 채팅을 멈추면 안 되므로 흐름은 계속 진행하되, 원인 추적을 위해 기록
        logger.exception("Redis에 user_id=%s room_id=%s 세션 저장 실패", user_id, room_id)


async def reset_session(user_id: int, room_id: Optional[int] = None) -> None:
    """인메모리 캐시 + Redis에서 세션 삭제 (room_id별로 독립적으로 초기화)."""
    _sessions.pop((user_id, room_id), None)
    try:
        redis = await get_redis()
        if redis:
            await redis.delete(_redis_key(user_id, room_id))
    except Exception:
        logger.exception("Redis에서 user_id=%s room_id=%s 세션 삭제 실패", user_id, room_id)


async def chat_stream(
    user_id: int,
    message: str,
    db: AsyncSession,
    user_nickname: str = "",
    room_id: Optional[int] = None,
) -> AsyncGenerator[str, None]:
    """
    SSE 제너레이터.
    LangGraph를 백그라운드 스레드에서 stream()으로 실행하고
    노드가 완료될 때마다 진행 상황을 yield, 마지막에 결과를 yield.
    step == 'optimized'이면 최신 itinerary를 DB에 snapshot으로 저장.
    room_id별로 독립된 대화 세션을 사용한다 (room_id 생략 시 개인 대화).
    """
    session = await get_session(user_id, room_id)

    # ── 투표 확인 대기 중이면, 이번 메시지를 "네/아니오" 응답으로 우선 해석 ──────
    pending_vote = session.get("pending_vote")
    if pending_vote:
        if _is_confirm(message):
            session["pending_vote"] = None
            snapshot_ids: list[int] = session.get("snapshot_ids", [])

            if not snapshot_ids:
                msg = json.dumps({
                    "type": "result",
                    "content": "아직 확정된 일정 버전이 없어요. AI와 대화해서 일정을 먼저 만들어 주세요!",
                    "step": "no_snapshot",
                }, ensure_ascii=False)
                yield f"data: {msg}\n\n"
                await _save_session(user_id, room_id)
                yield "data: [DONE]\n\n"
                return

            vote_type   = pending_vote["vote_type"]
            vote_room_id = pending_vote["room_id"]
            try:
                vote_session = await vote_service.create_vote_session(
                    db=db,
                    creator_id=user_id,
                    creator_nickname=user_nickname,
                    vote_type=vote_type,
                    snapshot_ids=snapshot_ids,
                    room_id=vote_room_id,
                )
                content = (
                    f"그룹 투표가 시작됐어요! 최근 {len(snapshot_ids)}개 버전 중 채팅방 멤버들이 투표할 수 있어요."
                    if vote_type == "group"
                    else f"투표가 시작됐어요! 최근 {len(snapshot_ids)}개 버전 중 마음에 드는 일정을 선택해 주세요."
                )
                msg = json.dumps({
                    "type": "vote_created",
                    "vote_id": vote_session.vote_id,
                    "vote_type": vote_type,
                    "snapshot_count": len(snapshot_ids),
                    "content": content,
                    "step": "vote_created",
                }, ensure_ascii=False)
                yield f"data: {msg}\n\n"
            except Exception as e:
                msg = json.dumps({"type": "error", "message": str(e)}, ensure_ascii=False)
                yield f"data: {msg}\n\n"
            await _save_session(user_id, room_id)
            yield "data: [DONE]\n\n"
            return

        if _is_deny(message):
            session["pending_vote"] = None
            await _save_session(user_id, room_id)
            msg = json.dumps({
                "type": "result",
                "content": "투표 시작을 취소했어요.",
                "step": "vote_cancelled",
            }, ensure_ascii=False)
            yield f"data: {msg}\n\n"
            yield "data: [DONE]\n\n"
            return

        # "네"도 "아니오"도 아닌 애매한 응답 → 투표 요청은 자동 취소하고 평소 대화로 계속 진행
        session["pending_vote"] = None

    # ── 투표 트리거 감지 → 즉시 생성하지 않고 먼저 확인을 받는다 ──────────────
    elif _is_vote_trigger(message):
        # room_id가 있으면 그 방의 방장인지에 따라 group/solo를 판단한다.
        # (방장만 그룹 투표를 시작할 수 있고, 일반 멤버는 안내만 받는다)
        vote_type = "solo"
        vote_room_id: Optional[int] = None

        if room_id is not None:
            room_result = await db.execute(select(ChatRoom).where(ChatRoom.room_id == room_id))
            room = room_result.scalar_one_or_none()

            if not room:
                msg = json.dumps({"type": "error", "message": "채팅방을 찾을 수 없습니다."}, ensure_ascii=False)
                yield f"data: {msg}\n\n"
                yield "data: [DONE]\n\n"
                return

            if room.owner_id != user_id:
                msg = json.dumps({
                    "type": "result",
                    "content": "그룹 투표는 방장만 시작할 수 있어요. 방장에게 투표를 요청해 주세요.",
                    "step": "vote_denied",
                }, ensure_ascii=False)
                yield f"data: {msg}\n\n"
                yield "data: [DONE]\n\n"
                return

            vote_type = "group"
            vote_room_id = room_id

        snapshot_ids = session.get("snapshot_ids", [])
        if not snapshot_ids:
            msg = json.dumps({
                "type": "result",
                "content": "아직 확정된 일정 버전이 없어요. AI와 대화해서 일정을 먼저 만들어 주세요!",
                "step": "no_snapshot",
            }, ensure_ascii=False)
            yield f"data: {msg}\n\n"
            yield "data: [DONE]\n\n"
            return

        session["pending_vote"] = {"vote_type": vote_type, "room_id": vote_room_id}
        await _save_session(user_id, room_id)

        content = (
            "채팅방 멤버들과 함께할 그룹 투표를 시작할까요? ('네'라고 답하면 시작해요)"
            if vote_type == "group"
            else "투표를 시작할까요? ('네'라고 답하면 시작해요)"
        )
        msg = json.dumps({
            "type": "result",
            "content": content,
            "step": "vote_confirm",
        }, ensure_ascii=False)
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
            snapshot_error: Optional[str] = None

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
                        # snapshot 저장 실패가 채팅을 멈추면 안 되므로 흐름은 계속 진행하되,
                        # 원인 추적 로깅 + 프론트가 알 수 있도록 응답에 경고를 남김
                        logger.exception("user_id=%s 일정 스냅샷 저장 실패", user_id)
                        snapshot_error = "일정 저장에 실패해 투표를 시작할 수 없습니다. 다시 시도해 주세요."

                response["snapshot_id"] = snapshot_id
                if snapshot_error:
                    response["snapshot_error"] = snapshot_error

            # ── 세션 Redis 동기화 ─────────────────────────────────────
            await _save_session(user_id, room_id)

            yield f"data: {json.dumps(response, ensure_ascii=False)}\n\n"
            break

    yield "data: [DONE]\n\n"
