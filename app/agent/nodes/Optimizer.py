import os
import re
import math
from datetime import datetime, timedelta
from typing import List
from pydantic import BaseModel
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_groq import ChatGroq
from dotenv import load_dotenv

from _naver_api import search_route, geocode
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


def _to_int_fare(val) -> int:
    """fare 값을 안전하게 int로 변환. 문자열·콤마·'원' 등 포함 형태 모두 처리."""
    if isinstance(val, int):
        return val
    try:
        return int(str(val).replace(",", "").replace("원", "").strip())
    except (ValueError, AttributeError):
        return 0


def _walk_str(km: float) -> str:
    return f"도보 약 {max(1, int(km * 1000 / _WALK_M_PM))}분"


def _transit_info(km: float,
                  prev_coord: tuple[float, float] | None,
                  coord: tuple[float, float] | None) -> str:
    taxi_min  = int(km / 30 * 60) + 5
    taxi_fare = int(4800 + max(0, km - 1.6) * 1000)
    taxi_str  = f"택시 약 {taxi_min}분·{taxi_fare:,}원"
    bus_min   = int(km / 20 * 60) + 10
    bus_str   = f"버스 약 {bus_min}분·1,500원(추정)"

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
            # 드라이빙 fallback: 대중교통 추정치도 함께 제공
            return f"{bus_str} / 택시 약 {naver['time']}분·{fare_str}"

    return f"{bus_str} / {taxi_str}"


def _transit_between(a: dict, b: dict) -> str:
    """두 장소 dict 간 이동 정보 문자열 반환"""
    ca = _parse_coord(a)
    cb = _parse_coord(b)
    if not ca or not cb:
        return "도보 약 5~15분 (정확한 경로는 현지 확인)"
    km = _haversine_km(ca[0], ca[1], cb[0], cb[1])
    return _transit_info(km, ca, cb)


def _build_departure_sequence(
    origin: str,
    dest: str,
    routes: list,
    accommodation: dict | None,
) -> list[str]:
    """
    1일차 출발 시퀀스를 최대 4줄로 반환.
      1) "HH:MM {origin}역 출발"
      2) "{origin}역 → {dest}역 (수단·요금/인)"
      3) "HH:MM {dest}역 도착"
      4) "{dest}역 → {숙소명} (대중교통 / 택시)"  ← 숙소 좌표 있을 때만
    routes 없으면 단순 이동 1줄만 반환.
    """
    if not routes:
        return [f"{origin} → {dest} (대중교통 이용 예정)"]

    def _key(r):
        return (0 if _to_int_fare(r.get("fare", 0)) > 0 else 1, str(r.get("dep_time", "")))
    best  = min(routes, key=_key)
    rtype = best.get("type", "교통편")
    grade = best.get("grade", "")
    fare  = _to_int_fare(best.get("fare", 0))
    dep   = str(best.get("dep_time", ""))
    arr   = str(best.get("arr_time", ""))

    # 출발·도착 시각 파싱
    dep_fmt = f"{dep[8:10]}:{dep[10:12]}" if len(dep) >= 12 else ""
    arr_fmt = f"{arr[8:10]}:{arr[10:12]}" if len(arr) >= 12 else ""

    vehicle = (f"{rtype} {grade}").strip() if grade else rtype
    fare_str = f"{fare:,}원/인" if fare > 0 else "요금미정"

    lines: list[str] = []

    if dep_fmt:
        lines.append(f"{dep_fmt} {origin}역 출발")
    lines.append(f"{origin}역 → {dest}역 ({vehicle}·{fare_str})")
    if arr_fmt:
        lines.append(f"{arr_fmt} {dest}역 도착")

    # 도착역 → 숙소 이동 계산
    if accommodation:
        acc_coord = _parse_coord(accommodation)
        station_coord = geocode(f"{dest}역")
        if acc_coord and station_coord:
            km = _haversine_km(station_coord[0], station_coord[1],
                               acc_coord[0], acc_coord[1])
            transit_str = _transit_info(km, station_coord, acc_coord)
            acc_title = accommodation.get("title", "숙소")
            lines.append(f"{dest}역 → {acc_title} ({transit_str})")

    return lines


def _build_return_sequence(origin: str, dest: str, routes: list) -> list[str]:
    """
    마지막날 귀환 시퀀스를 최대 3줄로 반환.
      1) "HH:MM {dest}역 출발"
      2) "{dest}역 → {origin}역 (수단·요금/인)"
      3) "HH:MM {origin}역 도착"
    routes 없으면 단순 이동 1줄만 반환.
    """
    if not routes:
        return [f"{dest} → {origin} (대중교통 이용 예정)"]

    def _key(r):
        return (0 if _to_int_fare(r.get("fare", 0)) > 0 else 1, str(r.get("dep_time", "")))
    best  = min(routes, key=_key)
    rtype = best.get("type", "교통편")
    grade = best.get("grade", "")
    fare  = _to_int_fare(best.get("fare", 0))

    vehicle  = (f"{rtype} {grade}").strip() if grade else rtype
    fare_str = f"{fare:,}원/인" if fare > 0 else "요금미정"

    return [f"{dest}역 → {origin}역 ({vehicle}·{fare_str})"]


# ── 동선 최적화  ──────────────────────────────────────

def _sort_nearest_neighbor(spots: list, start_coord: tuple[float, float] | None = None) -> list:
    """nearest-neighbor 순 정렬. start_coord가 주어지면 해당 좌표에 가장 가까운 장소부터 시작."""
    if len(spots) <= 1:
        return spots

    coords = [_parse_coord(s) for s in spots]
    with_coord    = [(i, s) for i, s in enumerate(spots) if coords[i]]
    without_coord = [s for i, s in enumerate(spots) if not coords[i]]

    if not with_coord:
        return spots

    if start_coord:
        first = min(
            with_coord,
            key=lambda x: _haversine_km(start_coord[0], start_coord[1], coords[x[0]][0], coords[x[0]][1]),
        )
    else:
        first = with_coord[0]

    visited   = [first[0]]
    remaining = [x for x in with_coord if x[0] != first[0]]

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
        fare  = _to_int_fare(r.get("fare", 0))
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
    acc_coord    = _parse_coord(accommodation) if accommodation else None
    sorted_spots = _sort_nearest_neighbor(tourist_spots, start_coord=acc_coord)

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


def _is_intercity_event(line: str, origin: str, dest: str) -> bool:
    """LLM이 중복 생성한 단일 시각 출발지·목적지 이벤트 줄 감지.
    예: '05:13 서울 출발', '07:50 부산역 도착' → True
    '06:13 서울 → 11:26 부산 (KTX)' → True  (도시 간 이동 복합 줄)
    '09:00~10:30 관광 (부산타워)' → False  (활동 항목은 제외)
    """
    if not line or re.match(r'^\d{2}:\d{2}~', line):
        return False
    m = re.match(r'^\d{2}:\d{2}\s+(.*)', line.strip())
    if not m:
        return False
    desc = m.group(1).strip()

    # 형식 1: "서울역 출발" / "부산역 도착"
    for city in (origin, dest):
        for suffix in ("", "역"):
            for kw in ("출발", "도착"):
                if desc == f"{city}{suffix} {kw}":
                    return True

    # 형식 2: "서울 → 11:26 부산 (KTX)" — 두 도시 모두 포함된 이동 복합 줄
    if "→" in desc:
        has_origin = any(origin + s in desc for s in ("", "역"))
        has_dest   = any(dest   + s in desc for s in ("", "역"))
        if has_origin and has_dest:
            return True

    return False


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
    # 2번 단계에서 추출한 세부 수정 피드백 가져오기
    itinerary_feedback = state.get("itinerary_feedback")

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

    # itinerary_feedback에 언급된 장소명을 스켈레톤에서 미리 제거
    # (재최적화 시 Restaurant_Searcher가 동일 장소를 재검색해서 다시 배정되는 것 방지)
    if itinerary_feedback:
        def _mentioned(title: str) -> bool:
            return bool(title) and title in itinerary_feedback
        restaurants   = [r for r in restaurants   if not _mentioned(r.get("title", ""))]
        cafes         = [c for c in cafes         if not _mentioned(c.get("title", ""))]
        tourist_spots = [t for t in tourist_spots if not _mentioned(t.get("title", ""))]

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
    
    # 프롬프트에 주입할 세부 피드백 섹션 정의
    feedback_section = ""
    if itinerary_feedback:
        feedback_section = f"""
                        [⚠️ 중요: 사용자 세부 수정 요구사항]
                        사용자가 이전 일정에 대해 다음과 같은 세부 수정을 요구했습니다. 
                        스켈레톤 구조를 기본으로 하되 아래 요구사항을 **반드시** 반영하여 일정을 완성해 주세요.
                        - 요구사항: **{itinerary_feedback}**
                        
                        * 지침:
                          - '카페 삭제' 요청: 해당 일차 스켈레톤에서 카페 활동 및 관련 이동 항목을 완전히 제외하고 앞뒤 시간을 매끄럽게 연결해줘.
                          - '식당 변경' 요청: 스켈레톤의 해당 식당명 대신 지역의 다른 식당명으로 교체해줘.
                          - '특정 장소 삭제/제외' 요청: 해당 장소는 이미 스켈레톤에서 제거된 상태야. 만약 스켈레톤에 남아있다면 완전히 제외하고 앞뒤 시간을 자연스럽게 연결해줘. 절대 해당 장소를 일정에 포함하지 말 것.
                        """

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
    # ── [추가] 교통비 및 식비 고정 계산 로직 ──────────────────────────────

    # 1. KTX 왕복 교통비 계산 (TAGO API 결과 중 KTX/철도 요금 기준 우선 추출)
    transport_cost_total = 0
    routes = state.get("transport_routes") or []
    ktx_fare = 0

    for r in routes:
        if "KTX" in str(r.get("type", "")) or "철도" in str(r.get("type", "")):
            ktx_fare = _to_int_fare(r.get("fare", 0))
            break

    # 만약 API 결과에 없거나 0원인 경우 일반적인 KTX 편도 평균 요금(예: 50,000원)을 Fallback으로 지정
    if ktx_fare == 0:
        ktx_fare = 50000 

    # KTX 왕복 교통비 (편도 요금 × 2 × 인원수)
    transport_cost_total = ktx_fare * 2 * num_people

    # 2. 숙박비 계산
    accommodation_cost_total = 0
    if not is_day_trip:
        if selected_acc:
            rooms    = selected_acc.get("rooms", [])
            suitable = [r for r in rooms if r.get("max_capacity", 0) >= num_people] or rooms
            prices   = [r["min_price"] for r in suitable if r["min_price"] > 0]
            min_room_price = min(prices) if prices else 100000
        else:
            min_room_price = 100000  # 숙소 검색 결과 없을 때 기본값 (성수기 1박 기준)
        accommodation_cost_total = min_room_price * (num_days - 1)

    # 3. 식비 고정 계산 (1인 1끼 15,000원 × 3끼 × 여행일수 × 인원수)
    meals_cost_total = 15000 * 3 * num_days * num_people

    # 4. 관광/활동비는 남은 예산으로 배정하되, 음수가 되지 않도록 방어
    remaining_budget = budget - (transport_cost_total + accommodation_cost_total + meals_cost_total)
    activities_cost_total = max(0, remaining_budget)

    # 5. 최종 합계 재계산 (설정 예산 총액 이하로 안전하게 통제)
    total_calculated = transport_cost_total + accommodation_cost_total + meals_cost_total + activities_cost_total

    system_prompt = fsystem_prompt = f"""
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
                           - 중복 방지: 같은 장소가 두 번 등장하면 나중 항목을 삭제하고 직전 장소 체류 연장으로 채울 것.
                           - [★중요] 2일차 이후 포함 모든 일차의 식당·관광지명은 반드시 스켈레톤에 명시된 고유 명칭을 그대로 사용할 것.
                             '식당', '근처 식당', '맛집', '관광지' 같은 일반 명칭 절대 금지. 스켈레톤에 없는 장소 임의 추가 금지.
                        2. schedule 항목은 세 종류:
                        ① 이벤트 항목 (단일 시각): "HH:MM [장소명] [도착/출발/귀환/체크인/체크아웃]"
                           - 장소에 처음 도착하거나 떠날 때 사용. 시간 범위 없음.
                        ② 활동 항목 (시간 범위): "HH:MM~HH:MM 활동내용 (장소명)"
                           - 관광·식사·카페 등 체류 활동. 반드시 시작~종료 시간 명시.
                        ③ 이동 항목 (타임스탬프 없음): "장소A → 장소B (대중교통 정보 / 택시 정보)"
                        ★ 이동 항목에 타임스탬프 절대 금지. 도착 시각은 ① 이벤트 항목으로 별도 표시.
                        
                        3. [★중요 - 식사 시간 고정 규칙]
                        하루에 아침, 점심, 저녁 세 끼 식사 시간을 아래 시각에 정확히 맞추어 일정을 전개해야 함:
                        - 아침 식사: 반드시 "09:00~10:00 아침 식사 (식당명)"으로 고정
                        - 점심 식사: 반드시 "13:00~14:00 점심 식사 (식당명)"으로 고정
                        - 저녁 식사: 반드시 "18:00~19:00 저녁 식사 (식당명)"으로 고정
                        * 식사 시작 시각에 맞추기 위해 직전 관광 활동 종료 시각과 이동 시간을 조밀하게 계산할 것.

                        4. 활동별 표준 소요시간:
                        - 관광지: 60~90분 / 식사: 60분 분량 고정 / 카페: 30분
                        - 숙소 체크인: "HH:MM 숙소명 체크인" (15:00 고정) / 체크아웃: "HH:MM 숙소명 체크아웃" (11:30)

                        5. 시간 배정 원칙 (연속 흐름 — 공백 금지):
                        - 각 이벤트 시각 = 이전 활동 종료 시각 + 이동 소요시간. 30분 이상 공백 절대 금지.
                        - 식사 고정 시각 사이에 비는 시간이 발생할 경우, 이전 장소 체류를 연장하거나 "자유 시간 / 산책 (장소명 인근)" 항목을 추가하여 시간을 촘촘하게 연결할 것.

                        6. 매일(마지막날 제외) 저녁 식사 후 반드시 숙소 귀환:
                        - "저녁식당 → 숙소명 (이동정보)" (③ 이동 항목)
                        - "HH:MM 숙소명 귀환" (① 이벤트 항목, 20:00~22:00 범위)

                        {accommodation_rule}
                        7. 필수 방문 장소(★)가 있으면 반드시 포함

                        8. [★중요 - 경비 산출 규칙]
                        cost_breakdown 항목은 계산 규칙을 무시하고 절대 임의의 숫자를 지어내거나 뻥튀기하지 마라.
                        반드시 아래에 지정된 숫자를 **그대로** 복사해서 출력해라:
                        - transportation: {transport_cost_total}  (KTX 왕복 교통비만 한정 반영된 총액)
                        - accommodation: {accommodation_cost_total}
                        - meals: {meals_cost_total}  (1인 1끼 15,000원 기준 고정 총액)
                        - activities: {activities_cost_total}
                        - total: {total_calculated}

                        9. title: 여행지와 테마가 담긴 매력적인 한국어 제목
                        10. 출발/귀환 교통편은 Python이 자동 삽입하므로 schedule에 추가하지 말 것.
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

    transport_routes = state.get("transport_routes") or []
    departure_seq = (
        _build_departure_sequence(origin_city, city, transport_routes, selected_acc)
        if origin_city and city else []
    )
    return_seq = (
        _build_return_sequence(origin_city, city, transport_routes)
        if origin_city and city else []
    )

    itinerary = []
    for dp in plan.daily_plans:
        day_idx  = dp.day - 1
        is_first = day_idx == 0
        is_last  = day_idx == num_days - 1

        # ── _inject_transits 실행 전 LLM 불필요 줄 제거 ──────────────────
        # (이후 제거하면 transit 슬롯이 잘못 배정돼 고아 transit 줄 발생)
        raw = list(dp.schedule)

        # ① LLM이 HH:MM~HH:MM 활동 형식으로 쓴 이동 줄 제거
        #    예: "09:00~09:01 장소A → 장소B (도보 약 1분)"
        raw = [l for l in raw if not re.match(r'^\d{2}:\d{2}~\d{2}:\d{2}\s+.*→', l)]

        # ② 1일차: LLM이 생성한 광역 이동 줄(→) 및 단일 이벤트(도착/출발) 제거
        if is_first and origin_city:
            raw = [l for l in raw
                   if not (l and not l[0:1].isdigit() and "→" in l and origin_city in l)]
            raw = [l for l in raw if not _is_intercity_event(l, origin_city, city)]

        # ③ 마지막날: LLM이 생성한 귀환 줄 제거
        if is_last and not is_first and origin_city:
            raw = [l for l in raw
                   if not (l and not l[0:1].isdigit() and "→" in l and city in l and origin_city in l)]
            raw = [l for l in raw if not _is_intercity_event(l, city, origin_city)]

        transits = day_transits[day_idx] if 0 <= day_idx < len(day_transits) else []
        schedule = _inject_transits(raw, transits)

        # ── 광역 이동 시퀀스 삽입 ─────────────────────────────────────────
        if is_first and departure_seq:
            for i, line in enumerate(departure_seq):
                schedule.insert(i, line)

        if is_last and not is_first and return_seq:
            schedule.extend(return_seq)

        itinerary.append(f"[{dp.day}일차 | {dp.date}]\n" + "\n".join(schedule))

    return {
        "current_step": "optimized",
        "plan_title": plan.title,
        "itinerary": itinerary,
        "itinerary_feedback": None,  # 처리 완료 후 초기화
        "estimated_cost": {
            "transportation": transport_cost_total,
            "accommodation":  accommodation_cost_total,
            "meals":          meals_cost_total,
            "activities":     activities_cost_total,
            "total":          total_calculated,
            "budget":         budget,  # 설정 예산 (비교용)
        },
    }
