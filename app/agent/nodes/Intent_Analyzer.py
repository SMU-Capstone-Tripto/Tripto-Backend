import os
import re
from dotenv import load_dotenv

from typing import Optional, Union, List
from pydantic import BaseModel
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_groq import ChatGroq
from datetime import datetime

from state import TravelState

load_dotenv()


_RELATIVE_DATE_PATTERNS = [
    "다음 주", "다음주", "이번 주", "이번주", "이번 달", "이번달",
    "다음 달", "다음달", "주말", "이번 주말", "내일", "모레", "곧", "얼마 후",
]


_SEP = r"\s*(?:~|부터|에서|-)\s*"


def _extract_date_by_regex(text: str, year: str) -> Optional[str]:
    """명시적 날짜 패턴만 추출. 실패하면 None."""
    # yyyy-mm-dd ~ yyyy-mm-dd
    m = re.search(r"(\d{4}-\d{2}-\d{2})\s*(?:~|-)\s*(\d{4}-\d{2}-\d{2})", text)
    if m:
        return f"{m.group(1)} ~ {m.group(2)}"

    # 5월 19일 ~ 5월 23일 (끝 날짜에 월 명시)
    m = re.search(
        r"(\d{1,2})월\s*(\d{1,2})일?" + _SEP + r"(\d{1,2})월\s*(\d{1,2})일", text
    )
    if m:
        start = f"{year}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
        end   = f"{year}-{int(m.group(3)):02d}-{int(m.group(4)):02d}"
        return f"{start} ~ {end}"

    # 5월 19일 ~ 23일 (끝 날짜에 월 생략 → 시작 월 재사용)
    m = re.search(
        r"(\d{1,2})월\s*(\d{1,2})일" + _SEP + r"(\d{1,2})일", text
    )
    if m:
        start = f"{year}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
        end   = f"{year}-{int(m.group(1)):02d}-{int(m.group(3)):02d}"
        return f"{start} ~ {end}"

    return None


_NONE_DISTRICT = {"없음", "없어요", "없어", "없습니다", "모름", "딱히없음", "특별히없음"}

_MUST_VISIT_KEYWORDS = ["꼭", "반드시", "무조건", "필수", "꼭 가고", "꼭 가야", "꼭 들러"]

_CONFIRM_ONLY_WORDS = {
    "네", "예", "맞아", "맞아요", "확인", "ok", "오케이",
    "좋아", "진행", "맞습니다", "맞음", "ㅇㅇ", "응", "그래",
    "correct", "yes", "괜찮아", "괜찮습니다",
}


def _is_only_confirmation(text: str) -> bool:
    """입력이 확인 단어만으로 구성된 경우 True — LLM 호출 불필요."""
    tokens = set(re.split(r"[\s,!?.~]+", text.strip().lower())) - {""}
    return bool(tokens) and tokens.issubset(_CONFIRM_ONLY_WORDS)


def _needs_llm(state: TravelState, user_input: str, year: str) -> tuple[bool, Optional[str]]:
    """LLM 호출이 필요한지 판단. (필요 여부, regex로 추출한 날짜) 반환."""
    has_relative_date = any(p in user_input for p in _RELATIVE_DATE_PATTERNS)
    city_in_state    = bool(state.get("city")        and state.get("city")        != "NULL")
    date_in_state    = bool(state.get("traveldates")  and state.get("traveldates") != "NULL")
    budget_in_state  = bool(state.get("budget"))
    origin_in_state  = bool(state.get("origin_city"))
    people_in_state  = bool(state.get("num_people"))
    prefs_exist      = bool(state.get("preferences"))

    has_must_visit_hint = any(k in user_input for k in _MUST_VISIT_KEYWORDS)

    regex_date = None
    if not date_in_state:
        regex_date = _extract_date_by_regex(user_input, year)
        if has_relative_date or not regex_date:
            return True, None

    if (not city_in_state or not budget_in_state or not origin_in_state
            or not people_in_state or not prefs_exist or has_must_visit_hint):
        return True, regex_date

    return False, regex_date



_DISTRICT_SPLIT = re.compile(r"[,、/]|이랑|랑|하고|와|과(?=\s|$)")


def _split_districts(text: str) -> list[str]:
    """'남포동이랑 송도' 같은 단일 문자열을 지역 리스트로 분리"""
    parts = [p.strip() for p in _DISTRICT_SPLIT.split(text)]
    return [p for p in parts if p]


class TravelInfo(BaseModel):
    city: Optional[str] = None
    districts: Optional[List[str]] = None
    traveldates: Optional[str] = None
    budget: Optional[int] = None
    preferences: Optional[list[str]] = None
    origin_city: Optional[str] = None
    num_people: Optional[int] = None
    must_visit: Optional[Union[str, list[str]]] = None
    # 세부 일정 수정 피드백을 담을 필드
    itinerary_feedback: Optional[str] = None

    # 2. 데이터가 주입되어 객체가 생성되는 시점(__init__)에 체크합니다.
    def __init__(self, **data):
        # must_visit이 존재하고, 그 값이 문자열(str) 타입이라면 리스트로 감싸줍니다.
        if "must_visit" in data and isinstance(data["must_visit"], str):
            data["must_visit"] = [data["must_visit"]]

        # (선택 사항) preferences 필드도 혹시 모를 문자열 오작동을 방지하려면 함께 넣어줍니다.
        if "preferences" in data and isinstance(data["preferences"], str):
            data["preferences"] = [data["preferences"]]

        # districts가 문자열("남포동이랑 송도")로 오면 리스트로 분리
        if "districts" in data and isinstance(data["districts"], str):
            data["districts"] = _split_districts(data["districts"])

        # 부모 클래스(BaseModel)의 초기화 메서드를 호출하여 최종 검증 및 저장을 완료합니다.
        super().__init__(**data)


def Intent_Analyzer(state: TravelState) -> dict:
    """사용자 입력에서 여행 정보를 추출하고 기존 state와 병합"""

    today = datetime.now().strftime("%Y-%m-%d")
    year = today[:4]

    user_input = state.get("question") or ""
    if not user_input.strip():
        return {"current_step": "analyzing"}

    current_step = state.get("current_step")
    # 필드 업데이트(새값 우선)는 awaiting_confirmation/optimized 모두 허용
    is_correction = current_step in ["awaiting_confirmation", "optimized"]
    # itinerary_feedback 추출은 일정이 이미 존재할 때만 허용
    can_extract_feedback = is_correction and bool(state.get("itinerary"))

    if is_correction and _is_only_confirmation(user_input):
        # "네", "맞아요" 등 단순 확인은 LLM 불필요 — 기존 값 그대로 유지
        call_llm, regex_date = False, None
    elif is_correction:
        call_llm, regex_date = True, None
    else:
        call_llm, regex_date = _needs_llm(state, user_input, year)

    result = {}

    if call_llm:
        already_known = {
            k: state.get(k)
            for k in ["city", "traveldates", "budget", "origin_city", "num_people"]
            if state.get(k) and state.get(k) != "NULL"
        }
        if state.get("districts"):
            already_known["districts"] = state.get("districts")
        if state.get("preferences"):
            already_known["preferences"] = state.get("preferences")
            
        # 🟢 [추가] 기존 피드백 context 전달
        existing_feedback = state.get("itinerary_feedback") or "없음"

        if is_correction:
            system_prompt = f"""
        너는 여행 전문가야. 오늘 날짜는 {today}야.
        현재 완성된 일정 혹은 정보: {already_known if already_known else "없음"}
        기존 누적된 일정 수정 사항: {existing_feedback}

        사용자가 기존의 세부 일정이나 특정 일차의 장소를 수정/삭제해달라고 요청하고 있어.
        사용자의 요구사항에서 '일정 세부 수정 피드백'을 추출해줘.

        추출할 필드:
        - 출발지 시 단위 (origin_city)
        - 목적지 시 단위 (city)
        - 목적지 읍면동 단위 리스트 (districts): 여러 지역을 언급하면 모두 리스트로 담을 것 (예: ["남포동", "송도"])
        - 여행일정 (traveldates)
        - 예산 (budget): 반드시 1인당 예산 숫자만 (총액 아님, 인원 수로 나누거나 곱하지 말 것)
        - 여행 인원 수 (num_people)
        - 여행 스타일 (preferences)
        - 꼭 가고 싶은 장소 (must_visit)
        - 일정 세부 수정 피드백 (itinerary_feedback): 일정 내용 수정 요구사항을 한 문장으로 요약정리.
          수정 유형에는 장소/식당/카페 삭제, 지역 변경, 관광지 교체, 시간 조정 등 모든 일정 변경이 포함됨.
          (예시: "1일차 카페 삭제, 2일차 저녁 식당 변경", "3일차 다대포 제외 부산역 근처로 변경",
                 "조은느낌 라이브 식당 전체 일정에서 제외", "2일차 오후 일정 여유롭게 조정")
          만약 출발일·인원·도시·예산 등 기본 여행 조건만 바꾸는 거라면 null로 반환해.

        중요: 사용자가 명시적으로 수정한 항목만 반환하고, 나머지는 반드시 null로 반환해.
            """
        else:
            # 일반 모드 프롬프트
            system_prompt = f"""
        너는 여행 전문가야. 오늘 날짜는 {today}야.
        이미 수집된 정보: {already_known if already_known else "없음"}
        아직 수집되지 않은 정보만 추출해. 이전 대화 맥락도 반드시 참고해.
        
        사용자가 '7월 5일부터 2박3일'과 같이 기간을 이야기하면, 시작 날짜를 기준으로 종료 날짜를 계산해서 포맷에 맞게 추출해줘.
        [중요] 'N박M일'에서 종료일 = 시작일 + N일 (박수만큼만 더함). 예: 7월 5일부터 2박3일 → 2024-07-05 ~ 2024-07-07 (2일 더함, 3일 더하면 안 됨)

        추출할 필드:
        - city: 목적지 시 단위
        - districts: 목적지 읍면동 단위 리스트. 여러 지역을 언급하면 모두 리스트로 담을 것 (예: ["남포동", "송도"])
        - traveldates: 여행 일정 포맷은 반드시 'YYYY-MM-DD ~ YYYY-MM-DD' 형식이어야 함 (예: 2박 3일이면 종료일 계산 필수)
        - budget: 1인당 예산 (숫자만 추출)
        - origin_city: 출발지 시 단위
        - num_people: 여행 인원 수 (숫자만 추출)
        - preferences: 선호 스타일 리스트
        - must_visit: 꼭 가고 싶은 장소 리스트
        - itinerary_feedback: 일반 정보 수집 단계이므로 반드시 null로 반환해.
            """

        llm = ChatGroq(model="openai/gpt-oss-20b", api_key=os.getenv("GROQ_API_KEY"), timeout=30, reasoning_effort="low")
        recent_messages = state.get("messages", [])[-6:]
        messages_to_llm = [SystemMessage(content=system_prompt)]
        messages_to_llm.extend(recent_messages)
        messages_to_llm.append(HumanMessage(content=user_input))

        extracted = llm.with_structured_output(TravelInfo).invoke(messages_to_llm)

        # 기존 일반 필드 병합 로직 (기존과 동일)
        for field in ["city", "traveldates", "budget", "origin_city", "num_people"]:
            existing = state.get(field)
            new_val = getattr(extracted, field, None)

            if is_correction:
                # optimized 상태에서 budget은 예산 관련 명시 키워드가 있을 때만 업데이트
                # (예: "3만원짜리 식당" 같은 개별 가격 언급이 전체 예산으로 오추출되는 것 방지)
                if (
                    field == "budget"
                    and current_step == "optimized"
                    and new_val and new_val != "NULL"
                ):
                    _BUDGET_KW = {"예산", "비용", "budget"}
                    if not any(kw in user_input.lower() for kw in _BUDGET_KW):
                        new_val = None

                if new_val and new_val != "NULL": result[field] = new_val
                elif existing and existing != "NULL": result[field] = existing
            else:
                if new_val and new_val != "NULL": result[field] = new_val
                elif existing and existing != "NULL": result[field] = existing

        # itinerary_feedback은 일정이 이미 존재할 때만 추출
        new_feedback = extracted.itinerary_feedback
        if can_extract_feedback and new_feedback:
            if state.get("itinerary_feedback"):
                result["itinerary_feedback"] = f"{state.get('itinerary_feedback')}, {new_feedback}"
            else:
                result["itinerary_feedback"] = new_feedback
        else:
            result["itinerary_feedback"] = state.get("itinerary_feedback")

        # 선호도 및 필수방문 병합 (기존과 동일)
        result["preferences"] = list(set((state.get("preferences") or []) + (extracted.preferences or [])))
        result["must_visit"] = list(set((state.get("must_visit") or []) + (extracted.must_visit or [])))

        # districts 병합: "없음"류 응답 제외 후 기존 리스트에 누적 (preferences/must_visit과 동일 패턴)
        new_districts = [
            d for d in (extracted.districts or [])
            if d.replace(" ", "") not in _NONE_DISTRICT
        ]
        result["districts"] = list(dict.fromkeys((state.get("districts") or []) + new_districts))

    else:
        # LLM 없이 처리 규칙 (기존 코드에 필드 유지 추가)
        for field in ["city", "traveldates", "budget", "origin_city", "num_people"]:
            val = state.get(field)
            if val and val != "NULL": result[field] = val
        if regex_date: result["traveldates"] = regex_date
        result["districts"] = state.get("districts") or []
        result["preferences"] = state.get("preferences") or []
        result["must_visit"] = state.get("must_visit") or []
        result["itinerary_feedback"] = state.get("itinerary_feedback") # 유지

    result["messages"] = [HumanMessage(content=user_input)]
    result["current_step"] = "awaiting_confirmation" if is_correction else "analyzing"

    return result
