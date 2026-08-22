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


def _is_peak_season(traveldates: str) -> bool:
    """여행 시작일이 성수기(여름 7/15~8/31, 겨울 12/20~1/10)인지 판단"""
    try:
        start_str = traveldates.split("~")[0].strip()
        start = datetime.strptime(start_str, "%Y-%m-%d")
        month, day = start.month, start.day
        if (month == 7 and day >= 15) or month == 8:
            return True
        if (month == 12 and day >= 20) or (month == 1 and day <= 10):
            return True
        return False
    except Exception:
        return False


def _min_cost_for_group(rooms: list, num_people: int, price_fn) -> int:
    """num_people을 수용하는 최저 비용 방 조합 계산 (DP)
    dp[i] = i명을 수용하는 최저 비용
    각 인원 i마다 모든 방 종류를 시도:
        나머지 = max(0, i - 방수용인원)
        dp[i] = min(dp[i], dp[나머지] + 이 방 가격)
    """
    options = [
        (r.get("max_capacity", 0), price_fn(r))
        for r in rooms
        if r.get("max_capacity", 0) > 0 and price_fn(r) > 0
    ]
    if not options:
        return 100000

    INF = float("inf")
    dp = [INF] * (num_people + 1)
    dp[0] = 0

    for i in range(1, num_people + 1):
        for cap, price in options:
            prev = max(0, i - cap)
            if dp[prev] != INF and dp[prev] + price < dp[i]:
                dp[i] = dp[prev] + price

    if dp[num_people] != INF:
        return int(dp[num_people])
    max_cap = max(c for c, _ in options)
    return 100000 * math.ceil(num_people / max_cap)


def _get_room_combination(rooms: list, num_people: int, price_fn) -> list:
    """DP 역추적으로 최적 방 조합 반환 [{room, count, price_per_night}, ...]"""
    options = [
        (r, r.get("max_capacity", 0), price_fn(r))
        for r in rooms
        if r.get("max_capacity", 0) > 0 and price_fn(r) > 0
    ]
    if not options:
        return []

    INF = float("inf")
    dp     = [INF]  * (num_people + 1)
    parent = [None] * (num_people + 1)
    dp[0]  = 0

    for i in range(1, num_people + 1):
        for room, cap, price in options:
            prev = max(0, i - cap)
            if dp[prev] != INF and dp[prev] + price < dp[i]:
                dp[i]     = dp[prev] + price
                parent[i] = (room, cap, price)

    if dp[num_people] == INF:
        return []

    counts: dict = {}
    cur = num_people
    while cur > 0 and parent[cur]:
        room, cap, price = parent[cur]
        key = room.get("room_name") or id(room)
        if key not in counts:
            counts[key] = {"room": room, "count": 0, "price_per_night": price}
        counts[key]["count"] += 1
        cur = max(0, cur - cap)

    return list(counts.values())


def _select_accommodation(
    accommodations: list,
    tourist_spots: list,
    num_people: int,
    is_peak: bool,
    price_weight: float = 0.6,
    distance_weight: float = 0.4,
) -> dict | None:
    """그룹 실비용(DP) + 관광지 중심 거리를 합산해 최적 숙소 선택"""
    if not accommodations:
        return None
    if len(accommodations) == 1:
        return accommodations[0]

    def _room_price(room: dict) -> int:
        if is_peak:
            return room.get("peak_price") or room.get("min_price") or 0
        return room.get("min_price") or 0

    # 관광지 중심 좌표 계산
    centroid = None
    coords = []
    for s in tourist_spots:
        try:
            lat = float(s.get("mapy") or 0)
            lon = float(s.get("mapx") or 0)
            if lat and lon:
                coords.append((lat, lon))
        except Exception:
            pass
    if coords:
        centroid = (
            sum(c[0] for c in coords) / len(coords),
            sum(c[1] for c in coords) / len(coords),
        )

    def _group_cost(acc: dict) -> int:
        rooms = acc.get("rooms", [])
        suitable = [r for r in rooms if r.get("max_capacity", 0) >= num_people]
        if suitable:
            prices = [_room_price(r) for r in suitable if _room_price(r) > 0]
            return min(prices) if prices else 100000
        return _min_cost_for_group(rooms, num_people, _room_price) if rooms else 100000

    def _distance_km(acc: dict) -> float:
        if not centroid:
            return 0.0
        try:
            lat = float(acc.get("mapy") or 0)
            lon = float(acc.get("mapx") or 0)
            if not lat or not lon:
                return float("inf")
            # 한국 위도 기준 근사값: 위도 1도 ≈ 111km, 경도 1도 ≈ 88km
            dlat = (lat - centroid[0]) * 111
            dlon = (lon - centroid[1]) * 88
            return (dlat ** 2 + dlon ** 2) ** 0.5
        except Exception:
            return float("inf")

    scored = [(acc, _group_cost(acc), _distance_km(acc)) for acc in accommodations]

    costs = [s[1] for s in scored]
    dists = [s[2] for s in scored if s[2] != float("inf")]

    cost_min, cost_max = min(costs), max(costs)
    dist_min = min(dists) if dists else 0
    dist_max = max(dists) if dists else 1
    cost_range = (cost_max - cost_min) or 1
    dist_range  = (dist_max - dist_min) or 1

    def _score(cost: int, dist: float) -> float:
        norm_cost = (cost - cost_min) / cost_range
        norm_dist = (dist - dist_min) / dist_range if dist != float("inf") else 1.0
        return price_weight * norm_cost + distance_weight * norm_dist

    scored.sort(key=lambda x: _score(x[1], x[2]))
    return scored[0][0]


class DaySchedule(BaseModel):
    """일자별 LLM 호출 1건의 출력. 프롬프트를 하루 단위로 쪼개 토큰당 한도(TPM) 초과를 방지한다."""
    schedule: List[str]  # ["09:00 활동 (장소명)", "장소A → 장소B (수단·시간·요금)", ...]


class FirstDaySchedule(DaySchedule):
    title: str  # 1일차 호출에서만 여행 제목도 함께 생성


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


def _fix_accommodation_name(itinerary: list, correct_name: str, all_acc_titles: list) -> list:
    """LLM이 바꿔버린 숙소명을 selected_acc 이름으로 강제 교정"""
    wrong_names = [t for t in all_acc_titles if t and t != correct_name]
    if not wrong_names or not correct_name:
        return itinerary
    fixed = []
    for day_text in itinerary:
        for wrong in wrong_names:
            day_text = day_text.replace(wrong, correct_name)
        fixed.append(day_text)
    return fixed


def _cluster_areas(area_coords: dict[str, tuple[float, float]], target: int) -> list[list[str]]:
    """지역 기준좌표를 target개 그룹으로 병합 (평균연결 greedy). target개 이하로 줄어들면 중단."""
    groups: list[list[str]] = [[name] for name in area_coords]

    def _centroid(g: list[str]) -> tuple[float, float] | None:
        coords = [area_coords[n] for n in g if area_coords.get(n)]
        if not coords:
            return None
        return (sum(c[0] for c in coords) / len(coords), sum(c[1] for c in coords) / len(coords))

    while len(groups) > max(target, 1):
        best: tuple[float, int, int] | None = None
        for i in range(len(groups)):
            ci = _centroid(groups[i])
            if not ci:
                continue
            for j in range(i + 1, len(groups)):
                cj = _centroid(groups[j])
                if not cj:
                    continue
                d = _haversine_km(ci[0], ci[1], cj[0], cj[1])
                if best is None or d < best[0]:
                    best = (d, i, j)
        if not best:
            break
        _, i, j = best
        groups[i] = groups[i] + groups.pop(j)

    return groups


def _nearest_area(coord: tuple[float, float] | None, area_coords: dict[str, tuple]) -> str | None:
    """좌표에서 가장 가까운 지역명 반환 (area 태그 없는 스팟을 지역에 귀속시킬 때 사용)"""
    if not coord or not area_coords:
        return None
    best_name, best_dist = None, float("inf")
    for name, c in area_coords.items():
        if not c:
            continue
        d = _haversine_km(coord[0], coord[1], c[0], c[1])
        if d < best_dist:
            best_name, best_dist = name, d
    return best_name


def _assign_day_groups(
    tourist_spots: list,
    area_coords: dict[str, tuple[float, float]],
    num_days: int,
    acc_coord: tuple[float, float] | None,
) -> list[list[dict]]:
    """
    요청 지역이 여러 개일 때 관광지를 지역별로 묶어 day 단위로 배분한다.
    - 지역 수 <= 여행일수: 지역별 최소 1일 보장, 남는 day는 스팟이 많은 지역에 우선 배분.
    - 지역 수 >  여행일수: 좌표상 가까운 지역끼리 병합해 여행일수만큼의 그룹으로 축소.
    - 지역 정보가 없으면 기존 방식(전체 하나의 동선)으로 폴백.
    """
    if not area_coords:
        mv_spots    = [s for s in tourist_spots if s.get("must_visit")]
        other_spots = [s for s in tourist_spots if not s.get("must_visit")]
        ordered = mv_spots + _sort_nearest_neighbor(other_spots, start_coord=acc_coord)
        return _slice_by_day(ordered, num_days)

    # area 태그가 없거나 알 수 없는 지역인 스팟(LLM 추천·필수방문 등)은 가장 가까운 지역으로 귀속
    for s in tourist_spots:
        if not s.get("area") or s["area"] not in area_coords:
            nearest = _nearest_area(_parse_coord(s), area_coords)
            if nearest:
                s["area"] = nearest

    target = min(len(area_coords), num_days)
    groups = _cluster_areas(area_coords, target)

    def _group_centroid(g: list[str]) -> tuple[float, float] | None:
        coords = [area_coords[n] for n in g if area_coords.get(n)]
        if not coords:
            return None
        return (sum(c[0] for c in coords) / len(coords), sum(c[1] for c in coords) / len(coords))

    # 그룹별 관광지 정렬 (필수방문 우선 + 숙소 기준 nearest-neighbor)
    group_spots: list[list[dict]] = []
    for g in groups:
        spots = [s for s in tourist_spots if s.get("area") in g]
        mv    = [s for s in spots if s.get("must_visit")]
        rest  = [s for s in spots if not s.get("must_visit")]
        group_spots.append(mv + _sort_nearest_neighbor(rest, start_coord=acc_coord))

    # day 배분: 기본 1일씩, 남는 day는 스팟이 많은 그룹에 우선 배정
    base = num_days // len(groups)
    extra = num_days % len(groups)
    order_by_size = sorted(range(len(groups)), key=lambda i: -len(group_spots[i]))
    days_for_group = [base] * len(groups)
    for k in range(extra):
        days_for_group[order_by_size[k]] += 1

    # 그룹 순서: 숙소에서 가까운 그룹부터 (동선 최소화)
    def _dist_from_acc(gi: int) -> float:
        c = _group_centroid(groups[gi])
        if not acc_coord or not c:
            return 0.0
        return _haversine_km(acc_coord[0], acc_coord[1], c[0], c[1])

    groups_sorted = sorted(range(len(groups)), key=_dist_from_acc)

    day_group_for_day: list[int] = []
    for gi in groups_sorted:
        day_group_for_day.extend([gi] * days_for_group[gi])
    while len(day_group_for_day) < num_days:  # 반올림 오차 보정
        day_group_for_day.append(groups_sorted[-1])
    day_group_for_day = day_group_for_day[:num_days]

    spots_per_day: list[list[dict]] = []
    cursor = [0] * len(groups)
    for d in range(num_days):
        gi = day_group_for_day[d]
        is_first = d == 0
        is_last  = d == num_days - 1
        pool = group_spots[gi]
        remaining = len(pool) - cursor[gi]
        n = min(2, remaining) if (is_first or is_last) else min(4, remaining)
        n = max(n, 0)
        spots_per_day.append(pool[cursor[gi]: cursor[gi] + n])
        cursor[gi] += n

    return spots_per_day


def _slice_by_day(ordered: list[dict], num_days: int) -> list[list[dict]]:
    spots_per_day: list[list[dict]] = []
    idx = 0
    for d in range(num_days):
        is_first = d == 0
        is_last  = d == num_days - 1
        n = min(2, len(ordered) - idx) if (is_first or is_last) else min(4, len(ordered) - idx)
        spots_per_day.append(ordered[idx: idx + max(n, 0)])
        idx += max(n, 0)
    return spots_per_day


def _build_skeletons(
    tourist_spots: list,
    restaurants: list,
    cafes: list,
    num_days: int,
    transport_summary: str,
    origin_city: str = "",
    dest_city: str = "",
    accommodation: dict | None = None,
    area_coords: dict[str, tuple[float, float]] | None = None,
) -> tuple[str, list[list[str]]]:
    """
    모든 일자의 스켈레톤 생성.
    각 장소 전환 사이에 이동 정보(수단·시간·요금)를 Python 코드가 미리 계산해 삽입.
    LLM은 시간(HH:MM)과 세부 문구만 채우면 됨.

    Returns:
        (skeleton_text, day_transits)
        day_transits[d]: d일차의 이동 줄 목록 — LLM이 누락해도 후처리로 강제 삽입
    """
    acc_coord = _parse_coord(accommodation) if accommodation else None

    # 요청 지역이 여러 곳이면 지역별로 day를 묶어서 배분, 없으면 기존 방식대로 전체 하나의 동선으로 배분
    spots_per_day = _assign_day_groups(tourist_spots, area_coords or {}, num_days, acc_coord)

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
            lines.append(f"   아침 식사 | {breakfast.get('title')} ({breakfast.get('address', '')})")
            prev = breakfast
        else:
            lines.append(f"   아침 식사 | 현지 식당 (직접 검색 추천)")

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
            lines.append(f"   점심 식사 | {lunch.get('title')} ({lunch.get('address', '')})")
            prev = lunch
        else:
            lines.append(f"   점심 식사 | 현지 식당 (직접 검색 추천)")

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
            lines.append(f"   카페 | {cafe.get('title')} ({cafe.get('address', '')})")
            prev = cafe
        else:
            lines.append(f"   카페 | 현지 카페 (직접 검색 추천)")

        # 저녁 식사
        if dinner:
            if prev:
                _add_transit(prev, dinner)
            lines.append(f"   저녁 식사 | {dinner.get('title')} ({dinner.get('address', '')})")
            prev = dinner
        else:
            lines.append(f"   저녁 식사 | 현지 식당 (직접 검색 추천)")

        # 마지막날이 아닌 경우: 저녁 식사 후 숙소 귀환
        if not is_last and not is_day_trip and accommodation:
            if prev:
                _add_transit(prev, accommodation)
            lines.append(f"   숙소 귀환 | {accommodation.get('title', '숙소')}")

        if is_last:
            if not is_day_trip:
                acc_name = accommodation.get("title", "숙소") if accommodation else "숙소"
                lines.append(f"   {acc_name} 체크아웃 (11:30)")
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


_ACTIVITY_DURATION: dict[str, int] = {
    "관광": 90, "방문": 90, "탐방": 90, "투어": 90, "체험": 60,
    "카페": 30, "디저트": 30,
    "식사": 60, "아침": 60, "점심": 60, "저녁": 60, "브런치": 60,
}

_ACTIVITY_PATTERN = re.compile(
    r"^(\d{2}:\d{2})\s+(관광|방문|탐방|투어|체험|카페|디저트|식사|아침|점심|저녁|브런치)(.*)"
)


def _normalize_schedule(lines: list[str]) -> list[str]:
    """LLM 출력 형식 일괄 정규화.
    1. HH:MM~HH:MM A→B  이동 줄 제거
    2. HH:MM A→B        이동 줄 제거 (단일 시각)
    3. HH:MM 활동       → HH:MM~HH:MM 활동 (종료 시간 추가)
    """
    result = []
    for line in lines:
        # 1. 범위 형식 이동 줄 제거
        if re.match(r"^\d{2}:\d{2}~\d{2}:\d{2}\s+.*→", line):
            continue
        # 2. 단일 시각 이동 줄 제거
        if re.match(r"^\d{2}:\d{2}\s+[^~]+→", line):
            continue
        # 3. 단일 시각 활동 → 시간 범위 변환
        m = _ACTIVITY_PATTERN.match(line)
        if m:
            start, act, rest = m.group(1), m.group(2), m.group(3)
            h, mn = map(int, start.split(":"))
            dur = _ACTIVITY_DURATION.get(act, 60)
            end_total = mn + dur
            end_h = (h + end_total // 60) % 24
            end_m = end_total % 60
            line = f"{start}~{end_h:02d}:{end_m:02d} {act}{rest}"
        result.append(line)
    return result


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
    districts         = state.get("districts") or []
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

    transport_summary  = _summarize_transport(state.get("transport_routes") or [])
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

    # 숙소 선택 (그룹 실비용 + 관광지 거리 점수 기반)
    is_peak        = _is_peak_season(traveldates)
    accommodations = state.get("accommodations") or []
    selected_acc   = _select_accommodation(accommodations, tourist_spots, num_people, is_peak)

    # 요청 지역이 여러 곳이면 지역별 기준좌표를 geocoding (day 배분 클러스터링에 사용)
    area_coords: dict[str, tuple[float, float]] = {}
    for d in districts:
        coord = geocode(f"{city} {d}")
        if coord:
            area_coords[d] = coord

    # 스켈레톤 사전 계산 (이동 정보 포함)
    skeletons, day_transits = _build_skeletons(
        tourist_spots, restaurants, cafes, num_days, transport_summary,
        origin_city=origin_city, dest_city=city,
        accommodation=selected_acc,
        area_coords=area_coords,
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
        "당일치기 여행: 숙소 체크인/체크아웃 없음."
        if is_day_trip else
        "숙소는 전 기간 동일 1곳. 1일차 체크인, 마지막날 11:30 체크아웃."
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
        def _room_price(room: dict) -> int:
            if is_peak:
                return room.get("peak_price") or room.get("min_price") or 0
            return room.get("min_price") or 0

        if selected_acc:
            rooms = selected_acc.get("rooms", [])
            min_room_price   = _min_cost_for_group(rooms, num_people, _room_price) if rooms else 100000
            room_combination = _get_room_combination(rooms, num_people, _room_price)
        else:
            min_room_price   = 100000
            room_combination = []
        accommodation_cost_total = min_room_price * (num_days - 1)

    # 3. 식비 고정 계산 (1인 1끼 15,000원 × 3끼 × 여행일수 × 인원수)
    meals_cost_total = 15000 * 3 * num_days * num_people

    # 4. 관광/활동비는 남은 예산으로 배정하되, 음수가 되지 않도록 방어
    remaining_budget = budget - (transport_cost_total + accommodation_cost_total + meals_cost_total)
    activities_cost_total = max(0, remaining_budget)

    # 5. 최종 합계 재계산 (설정 예산 총액 이하로 안전하게 통제)
    total_calculated = transport_cost_total + accommodation_cost_total + meals_cost_total + activities_cost_total

    # 모든 일자가 공유하는 작성 규칙 (경비 규칙은 제외 — cost_breakdown은 Python이 이미 확정 계산하므로
    # LLM에게 요구하지 않는다. 예전엔 물어보고도 결과를 안 썼던 죽은 필드였음)
    # 규칙 텍스트가 길고 복잡할수록 gpt-oss 계열이 도구 호출 자체를 빼먹는 경향이 실측으로 확인되어
    # (같은 스켈레톤에서 규칙만 줄이면 성공률 100%) 핵심만 남기고 최대한 짧게 유지한다.
    shared_rules = f"""
                        [작성 규칙]
                        1. 스켈레톤의 장소 순서·이름을 그대로 사용. 생략·순서변경·이름교체 금지. 필수방문 장소(★)는 반드시 포함.
                        2. schedule 줄은 세 형식만 사용:
                        - "HH:MM 장소명 도착/출발/귀환/체크인/체크아웃" (단일 시각 이벤트)
                        - "HH:MM~HH:MM 활동내용 (장소명)" (관광/식사/카페 — 시간범위)
                        - "장소A → 장소B (이동수단·시간)" (이동 — 시각 표기 금지)
                        3. 아침 09:00~10:00, 점심 13:00~14:00, 저녁 18:00~19:00 고정. 관광 75분, 카페 45분 기준.
                           빈 시간은 새 장소 대신 직전 장소 체류를 늘려서 채우기 (공백 금지).
                        4. 저녁 식사 후 숙소로 귀환(마지막날 제외). {accommodation_rule}
                        5. 교통편·체크인/체크아웃은 Python이 자동 처리하므로 schedule에 추가하지 말 것.
                    """

    llm = ChatGroq(
        model="openai/gpt-oss-20b",
        api_key=os.getenv("GROQ_API_KEY"),
        timeout=30,
        max_tokens=4000,
        # gpt-oss는 답을 내기 전 내부적으로 reasoning 토큰을 먼저 소모하는 추론 모델이라,
        # effort가 기본값이면 reasoning만 하다 max_tokens를 다 써버려 정작 도구 호출을 못 하고
        # 실패하는 경우가 실측으로 확인됨. low로 낮춰 reasoning 토큰을 줄인다.
        reasoning_effort="low",
    )

    # 일자별로 LLM을 나눠 호출한다 — 3일치를 한 번에 요청하면 프롬프트+응답 합계가
    # Groq 무료티어 TPM(분당 토큰) 한도를 넘어 요청 자체가 거부되거나 응답이 중간에 잘렸다.
    # 하루씩 쪼개면 호출당 토큰량이 작아져 한도 안에 안전하게 들어온다.
    day_skeletons = skeletons.split("\n\n")
    plan_title = f"{city} 여행"
    daily_schedules: list[list[str]] = []
    generation_failed = False

    for d in range(num_days):
        is_first = d == 0
        day_skel = day_skeletons[d] if d < len(day_skeletons) else ""

        day_prompt = f"""
                        너는 여행 일정 최적화 전문가야. 아래 **스켈레톤**을 기반으로 {d + 1}일차({date_labels[d]}) 하루치 일정만 완성해줘.
                        스켈레톤의 장소 순서와 이동 정보는 이미 최적화되어 있으니 그대로 사용할 것.

                        [여행 기본 정보]
                        - 출발지: {origin_city}
                        - 목적지: {city} {'/'.join(districts) if districts else ''}
                        - 인원: {num_people}명 / 선호 스타일: {', '.join(preferences) if preferences else '없음'}

                        [{d + 1}일차 스켈레톤]
                        {day_skel}
                        {feedback_section}
                        {shared_rules}
                        {"6. title: 여행지와 테마가 담긴 매력적인 한국어 제목도 함께 작성." if is_first else ""}
                    """

        schema = FirstDaySchedule if is_first else DaySchedule
        messages = [
            SystemMessage(content=day_prompt),
            HumanMessage(content=f"{d + 1}일차 일정을 스켈레톤 기반으로 완성해줘."),
        ]

        # gpt-oss-20b가 이따금 tool call 자체를 빼먹는 경우가 있어 여러 번 재시도
        _MAX_ATTEMPTS = 3
        day_result = None
        for attempt in range(_MAX_ATTEMPTS):
            try:
                day_result = llm.with_structured_output(schema).invoke(messages)
                break
            except Exception:
                if attempt == _MAX_ATTEMPTS - 1:
                    generation_failed = True
                continue

        if generation_failed:
            break

        daily_schedules.append(list(day_result.schedule))
        if is_first:
            plan_title = day_result.title

    if generation_failed:
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
    for day_idx, day_schedule in enumerate(daily_schedules):
        is_first = day_idx == 0
        is_last  = day_idx == num_days - 1

        # ── _inject_transits 실행 전 LLM 출력 형식 정규화 ───────────────
        raw = _normalize_schedule(list(day_schedule))

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

        itinerary.append(f"[{day_idx + 1}일차 | {date_labels[day_idx]}]\n" + "\n".join(schedule))

    # 숙소명 후처리: LLM이 바꾼 숙소명을 selected_acc로 강제 교정
    if selected_acc and not is_day_trip:
        acc_titles = [a.get("title", "") for a in accommodations]
        itinerary = _fix_accommodation_name(itinerary, selected_acc.get("title", ""), acc_titles)

    estimated_cost = {
        "transportation": transport_cost_total,
        "accommodation":  accommodation_cost_total,
        "meals":          meals_cost_total,
        "activities":     activities_cost_total,
        "total":          total_calculated,
        "budget":         budget,
    }

    history = list(state.get("itinerary_history") or [])
    new_snap = {
        "version":        len(history) + 1,
        "plan_title":     plan_title,
        "itinerary":      itinerary,
        "estimated_cost": estimated_cost,
        "selected_acc":   selected_acc,
        "traveldates":    traveldates,
        "city":           city,
        "created_at":     datetime.now().isoformat(),
    }
    itinerary_history = ([new_snap] + history)[:3]

    return {
        "current_step":     "optimized",
        "plan_title":       plan_title,
        "itinerary":        itinerary,
        "itinerary_history": itinerary_history,
        "itinerary_feedback": None,
        "selected_acc":     selected_acc,
        "room_combination": room_combination if not is_day_trip else [],
        "estimated_cost":   estimated_cost,
    }
