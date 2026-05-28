import os
import math
from datetime import datetime, timedelta
from typing import List
from pydantic import BaseModel
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_groq import ChatGroq
from dotenv import load_dotenv

from _naver_api import search_route
from state import TravelState

load_dotenv()


class DailyPlan(BaseModel):
    day: int
    date: str
    schedule: List[str]  # ["09:00 활동 (장소명)", "장소A → 장소B (수단·시간·요금)", ...]


class CostBreakdown(BaseModel):
    transportation: int
    accommodation: int
    meals: int
    activities: int
    total: int


class OptimizedPlan(BaseModel):
    title: str
    daily_plans: List[DailyPlan]
    cost_breakdown: CostBreakdown


# ── 좌표 / 거리 ─────────────────────────────────────────────────────────

def _parse_coord(item: dict) -> tuple[float, float] | None:
    try:
        lat = float(item.get("mapy", 0))
        lon = float(item.get("mapx", 0))
        return (lat, lon) if lat and lon else None
    except Exception:
        return None


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2))
         * math.sin(dlon / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


_WALK_KM   = 1.2   # 직선 1.2 km 이하 → 도보 (좌표 오차 감안, 약 15분)
_WALK_M_PM = 80    # 도보 속도 80m/min


def _walk_str(km: float) -> str:
    return f"도보 약 {max(1, int(km * 1000 / _WALK_M_PM))}분"


def _transit_info(km: float,
                  prev_coord: tuple[float, float] | None,
                  coord: tuple[float, float] | None) -> str:
    taxi_min  = int(km / 30 * 60) + 5
    taxi_fare = int(4800 + max(0, km - 1.6) * 1000)
    taxi_str  = f"택시 약 {taxi_min}분·{taxi_fare:,}원"

    # 직선거리 기준 도보 판정 (좌표 오차 감안해 1.2 km까지 도보)
    if km < _WALK_KM:
        return _walk_str(km)

    if prev_coord and coord:
        naver = search_route(prev_coord[1], prev_coord[0], coord[1], coord[0])
        if naver and naver["time"] > 0:
            fare_str = f"{naver['fare']:,}원" if naver["fare"] > 0 else "요금미정"
            if naver["type"] == "transit":
                route_detail = naver.get("summary", "")
                if route_detail:
                    return f"{route_detail} (도합 {naver['time']}분)·{fare_str} / {taxi_str}"
                return f"버스/지하철 약 {naver['time']}분·{fare_str} / {taxi_str}"
            # 자동차 경로 10분 이하 → 도보로 충분한 거리로 판단
            if naver["type"] == "driving" and naver["time"] <= 10:
                return _walk_str(km)
            return f"택시 약 {naver['time']}분·{fare_str}"

    bus_min = int(km / 20 * 60) + 10
    return f"버스 약 {bus_min}분·1,500원(추정) / {taxi_str}"


def _transit_between(a: dict, b: dict) -> str:
    """두 장소 dict 간 이동 정보 문자열 반환"""
    ca = _parse_coord(a)
    cb = _parse_coord(b)
    if not ca or not cb:
        # 좌표 없는 경우 도보 5분 기본값으로 LLM이 경로를 생략하지 않도록 유도
        return "도보 약 5~15분 (정확한 경로는 현지 확인)"
    km = _haversine_km(ca[0], ca[1], cb[0], cb[1])
    return _transit_info(km, ca, cb)


# ── 동선 최적화  ──────────────────────────────────────

def _sort_nearest_neighbor(spots: list) -> list:
    if len(spots) <= 1:
        return spots

    coords = [_parse_coord(s) for s in spots]
    with_coord    = [(i, s) for i, s in enumerate(spots) if coords[i]]
    without_coord = [s for i, s in enumerate(spots) if not coords[i]]

    if not with_coord:
        return spots

    visited   = [with_coord[0][0]]
    remaining = list(with_coord[1:])

    while remaining:
        last = coords[visited[-1]]
        nearest = min(
            remaining,
            key=lambda x: _haversine_km(last[0], last[1], coords[x[0]][0], coords[x[0]][1])
        )
        visited.append(nearest[0])
        remaining.remove(nearest)

    return [spots[i] for i in visited] + without_coord


# ── 데이터 요약 (교통편·숙소) ────────────────────────────────────────────

def _summarize_transport(routes: list) -> str:
    if not routes:
        return "교통편 정보 없음"
    lines = []
    for r in routes[:5]:
        rtype = r.get("type", "")
        grade = r.get("grade", "")
        fare  = int(r.get("fare", 0))
        dep   = str(r.get("dep_time", ""))
        arr   = str(r.get("arr_time", ""))
        if len(dep) >= 12:
            dep_fmt = f"{dep[8:10]}:{dep[10:12]}"
            arr_fmt = f"{arr[8:10]}:{arr[10:12]}" if len(arr) >= 12 else arr
            lines.append(f"- {rtype} {grade} | 출발 {dep_fmt} → 도착 {arr_fmt} | {fare:,}원/인")
        elif grade:
            lines.append(f"- {rtype} | {grade} | {fare:,}원/인")
        else:
            lines.append(f"- {rtype} | {fare:,}원/인")
    return "\n".join(lines)


def _summarize_accommodations(accommodations: list, num_people: int) -> str:
    if not accommodations:
        return "숙소 정보 없음"
    lines = []
    for a in accommodations[:5]:
        rooms    = a.get("rooms", [])
        suitable = [r for r in rooms if r.get("max_capacity", 0) >= num_people] or rooms
        prices   = [r["min_price"] for r in suitable if r.get("min_price", 0) > 0]
        price_str = f"{min(prices):,}원~" if prices else "가격미정"
        lines.append(f"- {a.get('title', '')} ({a.get('address', '')}) | {price_str}")
    return "\n".join(lines)


# ── 스켈레톤 빌드 ────────────────────────────────────────────────────────

def _centroid(spots: list) -> tuple[float, float] | None:
    coords = [_parse_coord(s) for s in spots]
    valid  = [c for c in coords if c]
    if not valid:
        return None
    return (sum(c[0] for c in valid) / len(valid),
            sum(c[1] for c in valid) / len(valid))


def _pick_restaurants(day_spots: list, restaurants: list, used: set) -> tuple:
    """하루 관광지 중심과 가장 가까운 미사용 식당 3개 선택"""
    if not restaurants:
        return None, None, None

    center = _centroid(day_spots)

    def dist(idx: int) -> float:
        c = _parse_coord(restaurants[idx])
        if not c or not center:
            return float("inf")
        return _haversine_km(center[0], center[1], c[0], c[1])

    all_idx   = list(range(len(restaurants)))
    available = sorted([i for i in all_idx if i not in used], key=dist)

    picked = available[:3]
    for i in picked:
        used.add(i)

    # 부족한 슬롯은 None으로 채워 겹침 방지 (식당 수 부족 시 해당 끼니 생략)
    result = [restaurants[i] for i in picked]
    while len(result) < 3:
        result.append(None)

    return result[0], result[1], result[2]


def _pick_cafe(day_spots: list, cafes: list, used: set) -> dict | None:
    """하루 관광지 중심과 가장 가까운 미사용 카페 1개 선택"""
    if not cafes:
        return None

    center = _centroid(day_spots)

    def dist(idx: int) -> float:
        c = _parse_coord(cafes[idx])
        if not c or not center:
            return float("inf")
        return _haversine_km(center[0], center[1], c[0], c[1])

    all_idx   = list(range(len(cafes)))
    available = sorted([i for i in all_idx if i not in used], key=dist)

    if not available:
        return None

    idx = available[0]
    used.add(idx)
    return cafes[idx]


def _build_skeletons(
    tourist_spots: list,
    restaurants: list,
    cafes: list,
    num_days: int,
    transport_summary: str,
    origin_city: str = "",
    dest_city: str = "",
    accommodation: dict | None = None,
) -> tuple[str, list[list[str]]]:
    """
    모든 일자의 스켈레톤 생성.
    각 장소 전환 사이에 이동 정보(수단·시간·요금)를 Python 코드가 미리 계산해 삽입.
    LLM은 시간(HH:MM)과 세부 문구만 채우면 됨.

    Returns:
        (skeleton_text, day_transits)
        day_transits[d]: d일차의 이동 줄 목록 — LLM이 누락해도 후처리로 강제 삽입
    """
    sorted_spots = _sort_nearest_neighbor(tourist_spots)

    # 관광지를 일자별로 배분
    spots_per_day: list[list] = []
    idx = 0
    for d in range(num_days):
        is_first = d == 0
        is_last  = d == num_days - 1
        n = min(2, len(sorted_spots) - idx) if (is_first or is_last) else min(4, len(sorted_spots) - idx)
        spots_per_day.append(sorted_spots[idx: idx + max(n, 0)])
        idx += max(n, 0)

    used_rest: set = set()
    used_cafe: set = set()
    parts = []
    day_transits: list[list[str]] = [[] for _ in range(num_days)]

    for d, day_spots in enumerate(spots_per_day):
        is_first = d == 0
        is_last  = d == num_days - 1

        breakfast, lunch, dinner = _pick_restaurants(day_spots, restaurants, used_rest)
        cafe = _pick_cafe(day_spots, cafes, used_cafe)

        # 오전/오후 관광지 분할
        n_spots        = len(day_spots)
        morning_spots  = day_spots[:math.ceil(n_spots / 2)]
        afternoon_spots = day_spots[math.ceil(n_spots / 2):]

        lines = [f"=== {d + 1}일차 스켈레톤 ==="]

        is_day_trip = num_days == 1

        if is_first:
            route_label = f"{origin_city} → {dest_city}" if origin_city and dest_city else "출발지 → 목적지"
            lines.append(f"   → 이동: {route_label} [아래 교통편 중 1개 선택, 이동수단·소요시간·요금/인 명시 필수]")
            for tline in transport_summary.strip().split("\n"):
                lines.append(f"      {tline}")
            if not is_day_trip and accommodation:
                acc_title = accommodation.get("title", "숙소")
                lines.append(f"   숙소 체크인 | {acc_title} ({accommodation.get('address', '')})")

        # 첫날이 아니면 숙소에서 출발, 첫날이면 체크인 후 숙소가 시작점
        prev: dict | None = accommodation if (not is_day_trip and accommodation) else None

        def _add_transit(a: dict, b: dict):
            transit = _transit_between(a, b)
            label = f"{a.get('title', '')} → {b.get('title', '')} ({transit})"
            lines.append(f"   → 이동: {a.get('title', '')} → {b.get('title', '')} | {transit}")
            day_transits[d].append(label)

        # 아침 식사
        if breakfast:
            if prev:
                _add_transit(prev, breakfast)
            lines.append(f"   아침 식사 | {breakfast.get('title', '식당')} ({breakfast.get('address', '')})")
            prev = breakfast

        # 오전 관광지
        for spot in morning_spots:
            if prev:
                _add_transit(prev, spot)
            lines.append(f"   관광 | {spot.get('title', '관광지')} ({spot.get('address', '')})")
            prev = spot

        # 점심 식사
        if lunch:
            if prev:
                _add_transit(prev, lunch)
            lines.append(f"   점심 식사 | {lunch.get('title', '식당')} ({lunch.get('address', '')})")
            prev = lunch

        # 오후 관광지
        for spot in afternoon_spots:
            if prev:
                _add_transit(prev, spot)
            lines.append(f"   관광 | {spot.get('title', '관광지')} ({spot.get('address', '')})")
            prev = spot

        # 오후 카페
        if cafe:
            if prev:
                _add_transit(prev, cafe)
            lines.append(f"   카페 | {cafe.get('title', '카페')} ({cafe.get('address', '')})")
            prev = cafe

        # 저녁 식사
        if dinner:
            if prev:
                _add_transit(prev, dinner)
            lines.append(f"   저녁 식사 | {dinner.get('title', '식당')} ({dinner.get('address', '')})")
            prev = dinner

        # 마지막날이 아닌 경우: 저녁 식사 후 숙소 귀환
        if not is_last and not is_day_trip and accommodation:
            if prev:
                _add_transit(prev, accommodation)
            lines.append(f"   숙소 귀환 | {accommodation.get('title', '숙소')}")

        if is_last:
            if not is_day_trip:
                lines.append("   숙소 체크아웃 (12:00 이전)")
            lines.append("   귀환 교통편 [1일차와 동일 교통수단·등급, 편도 요금 명시]")

        parts.append("\n".join(lines))

    return "\n\n".join(parts), day_transits


def _inject_transits(schedule: list[str], transits: list[str]) -> list[str]:
    """LLM 이동 줄을 precomputed transit으로 항상 교체."""
    if not transits:
        return schedule

    # LLM이 생성한 이동 줄(→ 포함, 숫자 미시작) 제거
    activity_only = [
        line for line in schedule
        if not (line and "→" in line and not line[0].isdigit())
    ]

    result = []
    t_idx = 0
    for i, line in enumerate(activity_only):
        result.append(line)
        if not line or not line[0].isdigit():
            continue
        next_line = activity_only[i + 1] if i + 1 < len(activity_only) else None
        if next_line and next_line[0].isdigit() and t_idx < len(transits):
            result.append(transits[t_idx])
            t_idx += 1
    return result


# ── 메인 노드 ─────────────────────────────────────────────────────────────

def Optimizer(state: TravelState) -> dict:
    """동선 최적화 및 예산에 맞게 일정 재구성"""

    city              = state.get("city", "")
    district          = state.get("district", "")
    traveldates       = state.get("traveldates", "")
    budget_per_person = state.get("budget") or 0
    num_people        = state.get("num_people") or 1
    budget            = budget_per_person * num_people
    preferences       = state.get("preferences") or []
    origin_city       = state.get("origin_city", "")

    try:
        start_str, end_str = traveldates.split("~")
        start_date  = datetime.strptime(start_str.strip(), "%Y-%m-%d")
        end_date    = datetime.strptime(end_str.strip(), "%Y-%m-%d")
        num_days    = (end_date - start_date).days + 1
        date_labels = [
            (start_date + timedelta(days=i)).strftime("%Y-%m-%d (%a)")
            for i in range(num_days)
        ]
    except Exception:
        num_days    = 1
        date_labels = [traveldates]

    must_visit         = state.get("must_visit") or []
    transport_summary  = _summarize_transport(state.get("transport_routes") or [])
    acc_summary        = _summarize_accommodations(state.get("accommodations") or [], num_people)
    tourist_spots      = state.get("tourist_spots") or []
    restaurants        = state.get("restaurants") or []
    cafes              = state.get("cafes") or []

    # 숙소 선택 (첫 번째 숙소 사용)
    accommodations = state.get("accommodations") or []
    selected_acc   = accommodations[0] if accommodations else None

    # 스켈레톤 사전 계산 (이동 정보 포함)
    skeletons, day_transits = _build_skeletons(
        tourist_spots, restaurants, cafes, num_days, transport_summary,
        origin_city=origin_city, dest_city=city,
        accommodation=selected_acc,
    )

    must_visit_section = ""
    if must_visit:
        must_visit_section = (
            "\n[필수 방문 장소 ★ - 예산·동선과 무관하게 반드시 일정에 포함]\n"
            + "\n".join(f"- {p}" for p in must_visit)
        )

    is_day_trip = num_days == 1
    accommodation_rule = (
        "5. 당일치기 여행: 숙소 체크인/체크아웃 일정 없음. 숙박비는 0원."
        if is_day_trip else
        "5. 숙소는 전 기간 동일 1곳. 1일차 체크인(도착 후), 마지막날 12:00 이전 체크아웃."
    )
    accommodation_cost = (
        "   - accommodation: 0 (당일치기, 숙박 없음)"
        if is_day_trip else
        f"   - accommodation: 숙박비 (1박 요금 × {num_days - 1}박)"
    )

    system_prompt = f"""
                        너는 여행 일정 최적화 전문가야. 아래 **스켈레톤**을 기반으로 완성된 일정을 만들어줘.
                        스켈레톤의 장소 순서와 이동 정보는 이미 최적화되어 있으니 그대로 사용할 것.

                        [여행 기본 정보]
                        - 출발지: {origin_city}
                        - 목적지: {city} {district or ""}
                        - 여행 기간: {traveldates} ({num_days}일)
                        - 인원: {num_people}명
                        - 1인당 예산: {budget_per_person:,}원 → 총 예산: {budget:,}원
                        - 선호 스타일: {', '.join(preferences) if preferences else '없음'}
                        - 일자별 날짜: {', '.join(date_labels)}

                        [숙소 후보]
                        {acc_summary}
                        {must_visit_section}

                        [일정 스켈레톤]
                        {skeletons}

                        [작성 규칙]
                        1. 스켈레톤의 장소 순서·식사 장소·'→ 이동:' 줄을 그대로 사용할 것. 순서 변경·생략·식당명 교체 금지.
                           - 중복 방지: 전 일정에 걸쳐 같은 장소(또는 동일 지역의 유사 명칭, 예: 광안리해수욕장 / 광안리바다)가 두 번 등장하면 나중 항목을 삭제하고 해당 시간은 직전 장소 체류 연장으로 채울 것.
                        2. schedule 항목은 두 종류만:
                        ① 활동 항목: "HH:MM~HH:MM 활동내용 (장소명)"
                           - 반드시 시작~종료 시간 범위를 명시할 것. 단일 시각 형식 절대 금지.
                           - 예: "09:00~10:30 관광 (해동용궁사)", "11:45~12:45 점심 식사 (초량밀면)"
                        ② 이동 항목 (타임스탬프 없음): "장소A → 장소B (수단·소요시간·비용)"
                        ★ 스켈레톤의 '→ 이동:' 줄은 반드시 ② 이동 항목으로 삽입. 타임스탬프 금지.
                        3. 활동별 표준 소요시간:
                        - 관광지: 60~90분 / 식사: 60분 / 카페: 30분
                        - 숙소 체크인: 15:00~15:30 / 체크아웃: 11:30~12:00
                        4. 시간 배정 원칙 (연속 흐름 — 공백 금지):
                        - 각 활동 시작 시각 = 이전 활동 종료 시각 + 이동 소요시간. 30분 이상 공백 절대 금지.
                        - 아침 식사: 08:00 고정 / 오전 관광: 09:00 시작
                        - 점심 식사: 오전 마지막 관광 종료 + 이동 후 바로 시작 (11:00~13:30 범위)
                        - 저녁 식사: 카페 또는 오후 마지막 관광 종료 + 이동 후 바로 시작 (17:30~20:00 범위)
                        - 공백이 발생하면: 이전 장소 체류 시간을 연장하거나 "자유 시간 / 산책 (장소명 인근)" 항목 추가
                        5. 오전 관광 09:00 시작, 오후 20:00 이전 마무리. 카페는 오후 관광 직후 배치.
                        {accommodation_rule}
                        7. 필수 방문 장소(★)가 있으면 반드시 포함
                        8. cost_breakdown은 {num_people}명 전체 합산 금액 기준
                        - transportation: 왕복 교통비 (편도요금 × 2 × {num_people}명)
                        {accommodation_cost}
                        - meals: 식비 (1인 1끼 평균 15,000원 × 3끼 × {num_days}일 × {num_people}명)
                        - activities: 관광/입장료 (1인 요금 × {num_people}명)
                        - total: 위 항목 합계. 반드시 {budget:,}원 이하여야 함. 초과 시 activities → meals 순으로 줄일 것.
                        9. title: 여행지와 테마가 담긴 매력적인 한국어 제목
                        10. 1일차 schedule의 첫 번째 항목은 반드시 출발지→목적지 이동 항목이어야 함:
                        "{origin_city} → {city} (교통수단·출발시간·소요시간·요금/인)" 형식, 타임스탬프 없음.
                        교통편 정보가 없을 경우 "{origin_city} → {city} (대중교통 이용 예정)" 으로 작성.
                    """

    llm = ChatGroq(
        model="llama-3.3-70b-versatile",
        api_key=os.getenv("GROQ_API_KEY"),
    )

    try:
        plan: OptimizedPlan = llm.with_structured_output(OptimizedPlan).invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content="위 스켈레톤을 기반으로 최적화된 여행 계획을 완성해줘."),
        ])
    except Exception:
        return {
            "current_step": "optimized",
            "plan_title": f"{city} 여행",
            "itinerary": ["일정 생성 중 오류가 발생했습니다."],
            "estimated_cost": {},
        }

    itinerary = []
    for dp in plan.daily_plans:
        day_idx = dp.day - 1
        transits = day_transits[day_idx] if 0 <= day_idx < len(day_transits) else []
        schedule = _inject_transits(list(dp.schedule), transits)
        itinerary.append(f"[{dp.day}일차 | {dp.date}]\n" + "\n".join(schedule))

    return {
        "current_step": "optimized",
        "plan_title": plan.title,
        "itinerary": itinerary,
        "estimated_cost": {
            "transportation": plan.cost_breakdown.transportation,
            "accommodation":  plan.cost_breakdown.accommodation,
            "meals":          plan.cost_breakdown.meals,
            "activities":     plan.cost_breakdown.activities,
            "total":          plan.cost_breakdown.total,
            "budget":         budget,  # 설정 예산 (비교용)
        },
    }
