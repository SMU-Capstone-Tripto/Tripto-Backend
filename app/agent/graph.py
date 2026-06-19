import sys
sys.stdout.reconfigure(encoding="utf-8")
sys.stdin.reconfigure(encoding="utf-8")

from langgraph.graph import StateGraph, START, END
from langchain_core.messages import AIMessage

from state import TravelState
from nodes.Intent_Analyzer import Intent_Analyzer
from nodes.Info_Gatherer import Info_Gatherer
from nodes.Confirmer import Confirmer
from nodes.Travel_Searcher import Travel_Searcher
from nodes.Tourist_Searcher import Tourist_Searcher
from nodes.Restaurant_Searcher import Restaurant_Searcher
from nodes.Transport_Searcher import Transport_Searcher
from nodes.Spot_Enhancer import Spot_Enhancer
from nodes.Optimizer import Optimizer
from nodes.Revision_Manager import Revision_Manager


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


def _node(name: str, fn):
    def wrapper(state: TravelState):
        print(f"\n[상태] {_STATUS[name]}")
        return fn(state)
    return wrapper


def route_info(state: TravelState):
    """정보 수집 완료 → 확인 요청, 사용자 확인 → 검색 시작, 부족하면 END"""
    step = state.get("current_step")

    if step == "confirmed":
        # 기존 일정이 있고 수정 피드백이 있으면 → 경량 수정 경로
        if state.get("itinerary") and state.get("itinerary_feedback"):
            print("\n[상태] 기존 일정 수정 요청 → Revision_Manager")
            return "revision_manager"
        # 최초 일정 생성 → 전체 검색 + 최적화
        print("\n[상태] 사용자 확인 완료 -> 최적화 프로세스 진행")
        return ["travel_searcher", "tourist_searcher", "restaurant_searcher", "transport_searcher"]

    if step == "info_gathered":
        print("\n[상태] 모든 정보 수집 완료 -> 사용자 확인 요청")
        return "confirmer"

    return END


def build_graph():
    workflow = StateGraph(TravelState)

    workflow.add_node("intent_analyzer",     _node("intent_analyzer",     Intent_Analyzer))
    workflow.add_node("info_gatherer",        _node("info_gatherer",        Info_Gatherer))
    workflow.add_node("confirmer",            _node("confirmer",            Confirmer))
    workflow.add_node("travel_searcher",      _node("travel_searcher",      Travel_Searcher))
    workflow.add_node("tourist_searcher",     _node("tourist_searcher",     Tourist_Searcher))
    workflow.add_node("restaurant_searcher",  _node("restaurant_searcher",  Restaurant_Searcher))
    workflow.add_node("transport_searcher",   _node("transport_searcher",   Transport_Searcher))
    workflow.add_node("spot_enhancer",        _node("spot_enhancer",        Spot_Enhancer))
    workflow.add_node("optimizer",            _node("optimizer",            Optimizer))
    workflow.add_node("revision_manager",     _node("revision_manager",     Revision_Manager))

    workflow.add_edge(START, "intent_analyzer")
    workflow.add_edge("intent_analyzer", "info_gatherer")
    workflow.add_conditional_edges("info_gatherer", route_info)
    workflow.add_edge("confirmer",           END)
    workflow.add_edge("travel_searcher",     "optimizer")
    workflow.add_edge("tourist_searcher",    "spot_enhancer")
    workflow.add_edge("spot_enhancer",       "optimizer")
    workflow.add_edge("restaurant_searcher", "optimizer")
    workflow.add_edge("transport_searcher",  "optimizer")
    workflow.add_edge("optimizer",           END)
    workflow.add_edge("revision_manager",    END)

    return workflow.compile()


app = build_graph()


if __name__ == "__main__":
    print("=== 여행 도우미 챗봇 ===")
    user_input = input("사용자: ")

    state = {
        "question": user_input,
        "messages": [],
        "preferences": [],
        "itinerary": [],
        "current_step": "start",
    }

    MAX_TURNS = 10
    for _ in range(MAX_TURNS):
        result = app.invoke(state)

        # 사용자가 변경 사항 없이 최종 만족하여 '네'라고 했을 때만 루프 탈출
        if result.get("current_step") == "completed":
            print("\n즐거운 여행 되세요! 일정을 종료합니다.")
            break

        messages = result.get("messages", [])
        bot_question = next(
            (msg.content for msg in reversed(messages) if isinstance(msg, AIMessage)),
            None,
        )
        if bot_question:
            print(f"\n봇: {bot_question}")

        user_answer = ""
        while not user_answer.strip():
            user_answer = input("사용자: ")

        state = {**result, "question": user_answer}
    else:
        print("\n[오류] 최대 대화 횟수를 초과했습니다. 처음부터 다시 시도해주세요.")
        exit(1)

    # ── 최종 결과 출력 ────────────────────────────────────────────
    sep = "=" * 50

    print(f"\n{sep}")
    print(f"  {result.get('plan_title', '여행 계획')}")
    print(sep)

    for day_plan in result.get("itinerary", []):
        print(f"\n{day_plan}")

    cost       = result.get("estimated_cost") or {}
    num_people = result.get("num_people") or 1
    if cost:
        print(f"\n{sep}")
        print(f"  예상 총 경비  ({num_people}명 합산)")
        print(sep)
        print(f"  교통비 (왕복·{num_people}명):  {cost.get('transportation', 0):>10,}원")
        print(f"  숙박비 (객실 기준):    {cost.get('accommodation',  0):>10,}원")
        print(f"  식비   ({num_people}명 합산):    {cost.get('meals',          0):>10,}원")
        print(f"  관광/입장 ({num_people}명):     {cost.get('activities',    0):>10,}원")
        print(f"  {'─' * 34}")
        print(f"  합계:                 {cost.get('total',          0):>10,}원")
        total = cost.get('total', 0)
        if num_people > 1:
            print(f"  1인당:                {total // num_people:>10,}원")
        print(sep)
