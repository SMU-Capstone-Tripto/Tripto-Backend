from langchain_core.messages import AIMessage
from state import TravelState


REQUIRED_FIELDS = {
    "city": "어디로 떠나고 싶으신가요? (예시: 제주도, 경주)",
    "traveldates": "여행 일정은 언제인가요? 구체적인 날짜나 기간을 알려주세요.",
    "budget": "1인당 예산은 얼마 정도 생각하고 계신가요? (예시: 30만원, 50만원)",
    "origin_city": "어디서 출발하시나요? (예시: 서울, 부산)",
    "num_people": "몇 명이서 여행하시나요? (예시: 2명, 4명)",
}

DISTRICT_QUESTION = "여행지의 상세 위치도 알려주실 수 있나요? (읍/면/동 단위, 없으면 '없음'이라고 말씀해주세요)"


def _district_already_asked(state: TravelState) -> bool:
    messages = state.get("messages") or []
    return any(
        isinstance(msg, AIMessage) and DISTRICT_QUESTION in msg.content
        for msg in messages
    )


_CONFIRM_WORDS = [
    "네", "예", "맞아", "맞아요", "확인", "ok", "오케이",
    "좋아", "진행", "맞습니다", "맞음", "ㅇㅇ", "응", "그래",
    "correct", "yes", "괜찮아", "괜찮습니다",
]


def Info_Gatherer(state: TravelState) -> dict:
    """필수 정보 점검 후 부족하면 AIMessage로 질문 반환"""

    # 확인 대기 중: 사용자 응답이 긍정이면 검색 진행, 수정 요청이면 fall-through
    if state.get("current_step") == "awaiting_confirmation":
        user_q = (state.get("question") or "").strip().lower()
        if any(w in user_q for w in _CONFIRM_WORDS):
            return {"current_step": "confirmed"}
        # 수정 요청 → 아래 일반 정보 수집 로직으로 계속 처리

    missing = [
        (field, q_text)
        for field, q_text in REQUIRED_FIELDS.items()
        if not state.get(field) or state.get(field) == "NULL"
    ]

    if missing:
        field_name, question_text = missing[0]
        bot_msg = f"여행 계획을 위해 몇 가지 정보가 더 필요합니다. {question_text}"
        return {
            "current_step": "asking_info",
            "last_asked_field": field_name,
            "messages": [AIMessage(content=bot_msg)],
        }

    # 필수 정보 완료 → district가 없고 아직 안 물어봤으면 한 번만 질문
    if not state.get("district") and not _district_already_asked(state):
        return {
            "current_step": "asking_info",
            "messages": [AIMessage(content=DISTRICT_QUESTION)],
        }

    return {"current_step": "info_gathered"}
