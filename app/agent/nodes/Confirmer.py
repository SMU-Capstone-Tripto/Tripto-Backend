from langchain_core.messages import AIMessage
from state import TravelState


def Confirmer(state: TravelState) -> dict:
    """수집된 정보 요약 후 사용자 확인 요청 (수정 요청이 있으면 수정 사항 표시)"""

    itinerary_feedback = state.get("itinerary_feedback")

    # 일정 수정 요청이 있는 경우: 수정 내용을 보여주고 확인 요청
    if itinerary_feedback:
        lines = ["아래 수정 사항을 반영할까요?\n"]
        for item in itinerary_feedback.split(","):
            item = item.strip()
            if item:
                lines.append(f"• {item}")
        lines.append("\n맞으면 '네'를, 추가 수정이 있으면 알려주세요.")
        return {
            "current_step": "awaiting_confirmation",
            "messages": [AIMessage(content="\n".join(lines))],
        }

    # 초기 여행 정보 확인
    city        = state.get("city", "")
    district    = state.get("district", "")
    traveldates = state.get("traveldates", "")
    num_people  = state.get("num_people", 0)
    budget      = state.get("budget", 0)
    origin_city = state.get("origin_city", "")
    preferences = state.get("preferences") or []
    must_visit  = state.get("must_visit") or []

    destination = f"{city} {district}".strip() if district else city

    lines = ["아래 정보로 여행 계획을 시작할게요! 확인해 주세요.\n"]
    lines.append(f"• 출발지: {origin_city}")
    lines.append(f"• 목적지: {destination}")
    lines.append(f"• 여행 기간: {traveldates}")
    lines.append(f"• 인원: {num_people}명")
    lines.append(f"• 1인당 예산: {budget:,}원")
    if preferences:
        lines.append(f"• 선호 스타일: {', '.join(preferences)}")
    if must_visit:
        lines.append(f"• 필수 방문: {', '.join(must_visit)}")
    lines.append("\n정보가 맞으면 '네'를, 수정이 필요하면 수정 내용을 알려주세요.")

    return {
        "current_step": "awaiting_confirmation",
        "messages": [AIMessage(content="\n".join(lines))],
    }
