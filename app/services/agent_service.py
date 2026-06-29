
import sys
import os
import asyncio
import json
from typing import AsyncGenerator
from concurrent.futures import ThreadPoolExecutor
from langchain_core.messages import AIMessage

# app/agent/ 디렉토리를 sys.path에 추가 (graph.py의 상대 임포트 해결)
_AGENT_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "agent")
)
if _AGENT_DIR not in sys.path:
    sys.path.insert(0, _AGENT_DIR)

from graph import app as _graph  # noqa: E402

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

# 유저별 세션: user_id → {graph_state, result}
_sessions: dict[int, dict] = {}
_executor = ThreadPoolExecutor(max_workers=4)


def get_session(user_id: int) -> dict:
    if user_id not in _sessions:
        _sessions[user_id] = {
            "graph_state": {
                "question": "",
                "messages": [],
                "preferences": [],
                "itinerary": [],
                "current_step": "start",
            },
            "result": None,
        }
    return _sessions[user_id]


def reset_session(user_id: int) -> None:
    _sessions.pop(user_id, None)


async def chat_stream(user_id: int, message: str) -> AsyncGenerator[str, None]:
    """
    SSE 제너레이터.
    LangGraph를 백그라운드 스레드에서 stream()으로 실행하고
    노드가 완료될 때마다 진행 상황을 yield, 마지막에 결과를 yield.
    """
    session     = get_session(user_id)
    graph_state = {**session["graph_state"], "question": message}

    loop: asyncio.AbstractEventLoop = asyncio.get_event_loop()
    queue: asyncio.Queue            = asyncio.Queue()

    def _run_stream():
        """백그라운드 스레드: LangGraph stream() 실행"""
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

            if step == "optimized":
                response["plan_title"]     = final_state.get("plan_title", "")
                response["itinerary"]      = final_state.get("itinerary", [])
                response["estimated_cost"] = final_state.get("estimated_cost", {})
                response["selected_acc"]   = final_state.get("selected_acc")

            yield f"data: {json.dumps(response, ensure_ascii=False)}\n\n"
            break

    yield "data: [DONE]\n\n"
