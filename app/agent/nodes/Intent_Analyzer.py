import os
import re
from dotenv import load_dotenv

from typing import Optional
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


class TravelInfo(BaseModel):
    city: Optional[str] = None
    district: Optional[str] = None
    traveldates: Optional[str] = None
    budget: Optional[int] = None
    preferences: Optional[list[str]] = None
    origin_city: Optional[str] = None
    num_people: Optional[int] = None
    must_visit: Optional[list[str]] = None


def Intent_Analyzer(state: TravelState) -> dict:
    """사용자 입력에서 여행 정보를 추출하고 기존 state와 병합"""

    today = datetime.now().strftime("%Y-%m-%d")
    year = today[:4]

    user_input = state.get("question") or ""
    if not user_input.strip():
        return {"current_step": "analyzing"}

    # 확인 대기 중 수정 요청은 항상 LLM으로 처리
    is_correction = state.get("current_step") == "awaiting_confirmation"

    if is_correction:
        call_llm, regex_date = True, None
    else:
        call_llm, regex_date = _needs_llm(state, user_input, year)

    result = {}

    if call_llm:
        already_known = {
            k: state.get(k)
            for k in ["city", "district", "traveldates", "budget", "origin_city", "num_people"]
            if state.get(k) and state.get(k) != "NULL"
        }
        if state.get("preferences"):
            already_known["preferences"] = state.get("preferences")

        if is_correction:
            system_prompt = f"""
        너는 여행 전문가야.
        오늘 날짜는 {today}야.

        현재 수집된 여행 정보: {already_known if already_known else "없음"}
        사용자가 위 정보 중 일부를 수정하고 있어. 사용자의 말에서 수정된 항목만 추출해.

        추출할 필드:
        - 출발지 시 단위 (origin_city): 출발지/거주지/사는 곳.
        - 목적지 시 단위 (city)
        - 목적지 읍면동 단위 (district): '없음' 등 부재 표현이면 null.
        - 여행일정 (traveldates): 형식 "yyyy-mm-dd ~ yyyy-mm-dd".
        - 예산 (budget): 1인당 예산, 원 단위 정수.
        - 여행 인원 수 (num_people): 숫자만.
        - 여행 스타일 (preferences): 리스트.
        - 꼭 가고 싶은 장소 (must_visit): 리스트.

        중요: 사용자가 명시적으로 수정한 항목만 반환하고, 나머지는 반드시 null로 반환해.
            """
        else:
            last_asked = state.get("last_asked_field")
            last_asked_hint = (
                f"\n\n[중요] 봇이 마지막으로 '{last_asked}' 필드를 물어봤어. "
                f"사용자의 현재 답변은 높은 확률로 '{last_asked}' 값이야. "
                f"문맥상 명백히 다른 필드가 아닌 이상 '{last_asked}'에 넣어."
                if last_asked else ""
            )
            system_prompt = f"""
        너는 여행 전문가야.
        오늘 날짜는 {today}야.

        이미 수집된 정보: {already_known if already_known else "없음"}
        아직 수집되지 않은 정보만 추출해. 이전 대화 맥락도 반드시 참고해.{last_asked_hint}

        추출할 필드:
        - 출발지 시 단위 (origin_city): 예) 서울, 부산. 출발지/거주지/사는 곳을 의미.
        - 목적지 시 단위 (city): 예) 제주시, 부산시
        - 목적지 읍면동 단위 (district): 예) 애월읍, 해운대구. 사용자가 '없음', '없어요', '딱히 없다' 등 부재를 의미하면 null.
        - 여행일정 (traveldates): 형식 "yyyy-mm-dd ~ yyyy-mm-dd".
          * 이전 대화에서 기간(N일, N박)이 언급되고 지금 시작일이 나오면 종료일을 계산해.
          * N박은 N+1일이 아니라 N일을 더해. 예) 3박 → 시작일+3일=종료일. '3박 4일'이면 시작일+3=종료일.
          * 시작일 없이 기간만 있으면 null.
        - 예산 (budget): 1인당 예산, 원 단위 정수. '30만원'→300000, '50만'→500000. 없으면 null.
        - 여행 인원 수 (num_people): 숫자만. 없으면 null.
        - 여행 스타일 (preferences): 리스트. 없으면 빈 리스트.
        - 꼭 가고 싶은 장소 (must_visit): '꼭', '반드시', '무조건', '필수' 등을 사용해 특정 장소를 강조한 경우 그 장소명만 추출. 리스트. 없으면 빈 리스트.

        중요: 이미 수집된 정보는 다시 추출하지 않아도 돼. 대화에서 명시적으로 언급되지 않은 정보는 null로 반환해.
            """

        llm = ChatGroq(
            model="llama-3.3-70b-versatile",
            api_key=os.getenv("GROQ_API_KEY")
        )
        recent_messages = state.get("messages", [])[-6:]
        messages_to_llm = [SystemMessage(content=system_prompt)]
        messages_to_llm.extend(recent_messages)
        messages_to_llm.append(HumanMessage(content=user_input))

        extracted = llm.with_structured_output(TravelInfo).invoke(messages_to_llm)

        for field in ["city", "district", "traveldates", "budget", "origin_city", "num_people"]:
            existing = state.get(field)
            new_val = getattr(extracted, field, None)
            if field == "district" and isinstance(new_val, str):
                normalized = new_val.replace(" ", "")
                if normalized in _NONE_DISTRICT:
                    new_val = None

            if is_correction:
                # 수정 모드: LLM이 추출한 새 값 우선, 없으면 기존 값 유지
                if new_val and new_val != "NULL":
                    result[field] = new_val
                elif existing and existing != "NULL":
                    result[field] = existing
            else:
                # 일반 모드: 기존 값 유지 우선
                if existing and existing != "NULL":
                    result[field] = existing
                elif new_val and new_val != "NULL":
                    result[field] = new_val

        existing_prefs = state.get("preferences") or []
        new_prefs = extracted.preferences or []
        result["preferences"] = list(set(existing_prefs + new_prefs))

        existing_must = state.get("must_visit") or []
        new_must = extracted.must_visit or []
        result["must_visit"] = list(set(existing_must + new_must))

    else:
        # LLM 없이 처리: 기존 state 유지 + regex로 추출한 날짜만 반영
        for field in ["city", "district", "traveldates", "budget", "origin_city", "num_people"]:
            val = state.get(field)
            if val and val != "NULL":
                result[field] = val
        if regex_date:
            result["traveldates"] = regex_date
        result["preferences"] = state.get("preferences") or []
        result["must_visit"] = state.get("must_visit") or []

    result["messages"] = [HumanMessage(content=user_input)]
    # 수정 모드일 때는 awaiting_confirmation을 유지해야 Info_Gatherer가 확인 여부를 판단할 수 있음
    result["current_step"] = "awaiting_confirmation" if is_correction else "analyzing"

    return result
