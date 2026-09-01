import os
import sys
from datetime import datetime, timedelta
from typing import List, Optional
from pydantic import BaseModel
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from langchain_groq import ChatGroq
from dotenv import load_dotenv

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _naver_api import geocode as _naver_geocode
from _dates import parse_range
from state import TravelState

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from Optimizer import _normalize_schedule

load_dotenv()


# ── Pydantic 모델 ────────────────────────────────────────────────────

class DayRequest(BaseModel):
    day: int                                     # 수정 일차 (1-based, 0=전체)
    action: str                                  # area_change | add | remove | change | other
    target_area: Optional[str] = None            # area_change 시 이동할 지역명
    exclude_area: Optional[str] = None           # area_change 시 제외할 지역명
    excluded_places: Optional[List[str]] = None  # 삭제할 특정 장소명 목록
    add_places: Optional[List[str]] = None       # 추가할 특정 장소명 목록 (add 액션)
    description: str = ""                        # LLM에 전달할 수정 지시문 (한국어)


class RevisionPlan(BaseModel):
    day_requests: List[DayRequest]
    global_excluded_places: List[str] = []       # 전체 일차에서 제거할 장소명


class DailyPlan(BaseModel):
    day: int
    date: str
    schedule: List[str]


# ── 헬퍼 ─────────────────────────────────────────────────────────────

def _date_labels(traveldates, num_days: int) -> list:
    r = parse_range(traveldates)
    if not r:
        return [f"Day {i+1}" for i in range(num_days)]
    start = datetime(r[0].year, r[0].month, r[0].day)
    return [(start + timedelta(days=i)).strftime("%Y-%m-%d (%a)") for i in range(num_days)]


def _area_filter(items: list, target: str, exclude: Optional[str] = None) -> list:
    """주소 또는 타이틀에 target이 포함된 항목 반환. exclude 포함 항목은 제거."""
    result = []
    for item in items:
        addr  = item.get("address", "")
        title = item.get("title", "")
        if exclude and exclude in addr:
            continue
        if target in addr or target in title:
            result.append(item)
    return result


def _fmt_spots(items: list, limit: int = 6) -> str:
    lines = [f"  - {x.get('title', '')} ({x.get('address', '')})" for x in items[:limit]]
    return "\n".join(lines) if lines else "  (검색 결과 없음 — LLM 지식으로 대체)"


# ── 피드백 파싱 ───────────────────────────────────────────────────────

def _parse_feedback(feedback: str, itinerary: list, num_days: int, llm) -> RevisionPlan:
    itinerary_preview = "\n".join(
        f"[{i+1}일차] " + day.split("\n")[0]
        for i, day in enumerate(itinerary)
    )
    system_prompt = f"""
사용자의 여행 일정 수정 요청을 구조화된 JSON으로 반환해줘. 현재 일정은 총 {num_days}일이야.

[현재 일정 첫 줄 요약]
{itinerary_preview}

[action 분류 기준]
- area_change: 특정 일차의 방문 지역 자체를 다른 지역으로 변경
  예: "3일차 다대포 말고 부산역 근처로"
- add: 특정 장소/관광지를 일정에 추가
  예: "2일차에 송도해수욕장 추가해줘", "깡통시장 넣어줘"
- remove: 특정 장소/카페/식당을 일정에서 삭제
  예: "조은느낌 라이브 삭제", "1일차 카페 빼줘"
- change: 특정 장소를 다른 곳으로 교체
  예: "2일차 저녁 식당 바꿔줘"
- other: 시간 조정, 스타일 변경 등 그 외

[필드 안내]
- day: 수정 대상 일차 (1~{num_days}). 모든 일차 적용 시 0.
- target_area: 이동할 새 지역명 (area_change 시)
- exclude_area: 제외할 기존 지역명 (area_change 시)
- excluded_places: 해당 일차에서만 제거할 장소명 리스트
- add_places: 추가할 장소명 리스트 (add 액션 시)
- description: 수정 내용을 구체적으로 설명하는 한국어 지시문
- global_excluded_places: 모든 일차에서 제거할 장소명 리스트 (전체 삭제 요청 시)

중요:
- 특정 장소를 '모든 일정에서' 삭제하는 요청은 global_excluded_places에 추가하고
  day_requests는 비워도 됩니다.
- area_change는 반드시 day_requests에 포함해야 합니다.
    """
    try:
        return llm.with_structured_output(RevisionPlan).invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=feedback),
        ])
    except Exception:
        return RevisionPlan(
            day_requests=[DayRequest(day=0, action="other", description=feedback)],
            global_excluded_places=[],
        )


# ── 단일 일차 수정 ────────────────────────────────────────────────────

def _revise_day(
    day_num: int,
    req: DayRequest,
    state: TravelState,
    date_labels: list,
    num_days: int,
    llm,
) -> str:
    day_idx    = day_num - 1
    date_label = date_labels[day_idx] if day_idx < len(date_labels) else f"Day {day_num}"
    is_first   = day_idx == 0
    is_last    = day_idx == num_days - 1

    existing_days = state.get("itinerary") or []
    existing = existing_days[day_idx] if day_idx < len(existing_days) else ""

    tourist_spots = list(state.get("tourist_spots") or [])
    restaurants   = list(state.get("restaurants") or [])
    cafes         = list(state.get("cafes") or [])

    # 제외 장소 필터
    for place in (req.excluded_places or []):
        tourist_spots = [t for t in tourist_spots if t.get("title") != place]
        restaurants   = [r for r in restaurants   if r.get("title") != place]
        cafes         = [c for c in cafes         if c.get("title") != place]

    # area_change: 대상 지역 spots 구성
    spots_section = ""
    if req.action == "area_change" and req.target_area:
        area_spots = _area_filter(tourist_spots, req.target_area, req.exclude_area)
        area_rests = _area_filter(restaurants,   req.target_area, req.exclude_area)
        area_cafes = _area_filter(cafes,         req.target_area, req.exclude_area)

        spots_section = f"""
[{req.target_area} 주변 관광지 (우선 사용)]
{_fmt_spots(area_spots)}

[{req.target_area} 주변 식당 (우선 사용)]
{_fmt_spots(area_rests)}

[{req.target_area} 주변 카페 (우선 사용)]
{_fmt_spots(area_cafes)}
"""

    elif req.action == "add" and req.add_places:
        # 추가 요청된 장소를 Naver 지오코딩으로 검색해서 LLM에게 제공
        city = state.get("city", "")
        add_spot_lines = []
        for place in req.add_places:
            coord = _naver_geocode(f"{city} {place}") or _naver_geocode(place)
            coord_str = f"({coord[0]:.4f}, {coord[1]:.4f})" if coord else "(좌표 미확인)"
            add_spot_lines.append(f"  - {place} {coord_str}")
        spots_section = f"""
[추가 요청 장소 ★ 반드시 {day_num}일차 일정에 포함]
{"".join(add_spot_lines)}

[참고 가능 관광지]
{_fmt_spots(tourist_spots, 4)}
"""

    elif req.action in ("change", "other"):
        spots_section = f"""
[참고 가능 관광지 (일부)]
{_fmt_spots(tourist_spots, 4)}

[참고 가능 식당 (일부)]
{_fmt_spots(restaurants, 4)}
"""

    # description이 비어있으면 action/area 정보로 의미있는 지시문 생성
    revision_instruction = req.description
    if not revision_instruction:
        if req.action == "area_change":
            revision_instruction = (
                f"일정을 {req.exclude_area or '기존 지역'} 대신 "
                f"{req.target_area or '새 지역'} 근처로 변경"
            )
        elif req.action == "remove":
            excluded = ", ".join(req.excluded_places or [])
            revision_instruction = f"다음 장소를 일정에서 제거: {excluded}" if excluded else "요청된 장소 제거"
        elif req.action == "change":
            revision_instruction = "요청된 장소를 다른 곳으로 교체"
        else:
            revision_instruction = "위 수정 요청 반영"

    first_rule = "4. 첫날: 기존 출발 교통편(역 출발·도착·체크인 줄) 그대로 유지." if is_first else ""
    last_rule  = "4. 마지막날: 기존 귀환 교통편 그대로 유지." if is_last else "4. 저녁 식사 후 숙소 귀환으로 마무리."

    system_prompt = f"""
너는 여행 일정 수정 전문가야. {day_num}일차 일정을 아래 요청대로 수정해줘.

[수정 요청]
{revision_instruction}
{spots_section}
[기존 {day_num}일차 일정]
{existing}

[작성 규칙]
1. 수정 요청에 해당하는 부분만 변경. 요청 미언급 항목은 최대한 유지.
2. schedule 줄 형식:
   - 활동: "HH:MM~HH:MM 활동내용 (장소명)"
   - 이동: "장소A → 장소B (이동수단·소요시간)"
   - 이벤트: "HH:MM 이벤트내용"
3. 아침(09:00), 점심(13:00), 저녁(18:00) 시각 고정.
{first_rule}
{last_rule}
5. day={day_num}, date="{date_label}" 로 설정.
    """

    try:
        plan: DailyPlan = llm.with_structured_output(DailyPlan).invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=f"{day_num}일차를 위 요청대로 수정해줘."),
        ])
        schedule = _normalize_schedule(list(plan.schedule))
        return f"[{day_num}일차 | {plan.date or date_label}]\n" + "\n".join(schedule)
    except Exception:
        return existing  # 실패 시 기존 일정 유지


# ── 메인 노드 ─────────────────────────────────────────────────────────

def Revision_Manager(state: TravelState) -> dict:
    """전체 재검색 없이 특정 일차만 수정하는 경량 수정 노드"""

    feedback = (state.get("itinerary_feedback") or "").strip()
    existing = list(state.get("itinerary") or [])

    if not feedback or not existing:
        return {"current_step": "optimized", "itinerary_feedback": None}

    llm = ChatGroq(model="openai/gpt-oss-20b", api_key=os.getenv("GROQ_API_KEY"), timeout=30, reasoning_effort="low")

    num_days = len(existing)
    labels   = _date_labels(state.get("traveldates", ""), num_days)

    # Step 1: 피드백 → 구조화된 수정 계획 파싱
    revision = _parse_feedback(feedback, existing, num_days, llm)

    new_itinerary = list(existing)

    # Step 2: 전체 일차 장소 라인 제거 (빠른 string-level 필터)
    for place in (revision.global_excluded_places or []):
        new_itinerary = [
            "\n".join(line for line in day.split("\n") if place not in line)
            for day in new_itinerary
        ]

    # Step 3: 일차별 LLM 수정
    for req in revision.day_requests:
        # day=0 remove: excluded_places가 있으면 string 필터로 처리 후 skip
        if req.action == "remove" and req.day == 0 and req.excluded_places:
            for place in req.excluded_places:
                new_itinerary = [
                    "\n".join(line for line in day.split("\n") if place not in line)
                    for day in new_itinerary
                ]
            continue
        # excluded_places가 없는 day=0 remove → 아래 LLM 경로로 fall-through

        target_days = list(range(1, num_days + 1)) if req.day == 0 else [req.day]
        for d in target_days:
            if 1 <= d <= num_days:
                new_itinerary[d - 1] = _revise_day(d, req, state, labels, num_days, llm)

    # 수정 완료 메시지
    changed_days = sorted({
        req.day for req in revision.day_requests if req.day != 0
    } | ({i + 1 for i in range(num_days)} if any(r.day == 0 for r in revision.day_requests) else set()))
    if revision.global_excluded_places:
        removed = ", ".join(revision.global_excluded_places)
        done_msg = f"'{removed}'을(를) 전체 일정에서 제거했어요. 오른쪽에서 수정된 일정을 확인해 보세요!"
    elif changed_days:
        days_str = ", ".join(f"{d}일차" for d in changed_days)
        done_msg = f"{days_str} 일정을 수정했어요. 오른쪽에서 확인해 보세요!"
    else:
        done_msg = "일정을 수정했어요. 오른쪽에서 확인해 보세요!"

    history = list(state.get("itinerary_history") or [])
    new_snap = {
        "version":        len(history) + 1,
        "plan_title":     state.get("plan_title", ""),
        "itinerary":      new_itinerary,
        "estimated_cost": state.get("estimated_cost"),
        "selected_acc":   state.get("selected_acc"),
        "traveldates":    state.get("traveldates", ""),
        "city":           state.get("city", ""),
        "created_at":     datetime.now().isoformat(),
    }
    itinerary_history = ([new_snap] + history)[:3]

    return {
        "current_step":      "optimized",
        "itinerary":         new_itinerary,
        "itinerary_history": itinerary_history,
        "itinerary_feedback": None,
        "messages":          [AIMessage(content=done_msg)],
    }
