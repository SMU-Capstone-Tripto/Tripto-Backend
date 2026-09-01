import os
import re
import math
from datetime import datetime, timedelta
from typing import List
from pydantic import BaseModel
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_groq import ChatGroq
from dotenv import load_dotenv

from _naver_api import search_route, geocode, poi_kind as _poi_kind, is_franchise as _is_franchise
from _odsay_api import search_transit
from _tour_api import fetch_use_fee as _fetch_use_fee
from state import TravelState
from _dates import parse_range, is_peak_season as _is_peak_season

load_dotenv()


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


def _grade_rank(grade: str) -> int:
    """열차 등급 선호 순위. KTX 계열을 최우선으로 하고, 없을 때만 하위 등급으로 내려간다.
    (버스는 등급 개념이 없어 전부 같은 순위 → fare 순서로만 정렬됨)"""
    g = str(grade).upper()
    if "KTX" in g or "SRT" in g:
        return 0
    if "ITX" in g:
        return 1
    if "새마을" in str(grade):
        return 2
    if "무궁화" in str(grade):
        return 3
    return 4


def _representative_route(routes: list) -> dict | None:
    """API가 준 경로 중 '실제 요금이 있는' 대표 편도 경로 1개.
    KTX 계열 우선 → 그 안에서 최저가 → 이른 출발 순. KTX가 없으면 다음 등급으로.
    요금 있는 경로가 없으면 None. 교통비는 이 경로의 fare를 그대로 사용한다 (파이썬 추정 금지)."""
    priced = [r for r in (routes or []) if _to_int_fare(r.get("fare", 0)) > 0]
    if not priced:
        return None
    return min(priced, key=lambda r: (_grade_rank(r.get("grade", "")),
                                      _to_int_fare(r.get("fare", 0)),
                                      str(r.get("dep_time", "") or "~")))


def _walk_str(km: float) -> str:
    # 직선거리 → 실제 도보거리 보정(약 1.2배), 80 m/min
    return f"도보 약 {max(1, int(km * 1.2 * 1000 / _WALK_M_PM))}분"


def _taxi_estimate(km: float) -> str:
    """네이버 택시요금 조회 실패 시에만 쓰는 최후 추정.
    직선거리를 도로거리(×1.3)로 보정, 전국 평균 기본요금 4,000원 + 800원/km, 도심 22km/h."""
    road_km   = km * 1.3
    taxi_min  = int(road_km / 22 * 60) + 3
    taxi_fare = int(round((4000 + max(0, road_km - 1.6) * 800) / 100) * 100)
    return f"택시 약 {taxi_min}분·{taxi_fare:,}원(추정)"


def _transit_info(km: float,
                  prev_coord: tuple[float, float] | None,
                  coord: tuple[float, float] | None) -> str:
    """장소 간 이동 안내. 대중교통은 ODsay 실요금, 택시는 네이버 실요금 사용.
    prev_coord/coord = (위도, 경도)."""
    # 직선거리 기준 도보 판정 (좌표 오차 감안해 1.2 km까지 도보)
    if km < _WALK_KM:
        return _walk_str(km)

    if not (prev_coord and coord):
        return f"버스/택시 이용 (현지 확인) · 직선 {km:.1f}km"

    slat, slon = prev_coord
    elat, elon = coord

    # ── 택시: 네이버 자동차 경로의 실제 taxiFare ──
    naver = search_route(slon, slat, elon, elat)
    if naver and naver.get("time", 0) > 0 and naver.get("fare", 0) > 0:
        # 자동차로도 아주 짧으면 도보가 현실적
        if naver["time"] <= 8 and km < 1.6:
            return _walk_str(km)
        taxi_str = f"택시 약 {naver['time']}분·{naver['fare']:,}원"
    else:
        taxi_str = _taxi_estimate(km)

    # ── 대중교통: ODsay 실제 버스/지하철 요금 ──
    transit = search_transit(slon, slat, elon, elat)
    if transit is None:
        return taxi_str  # 대중교통 경로 없음 → 택시만
    if transit.get("walk"):
        return _walk_str(km)

    label = transit.get("summary") or "버스/지하철"
    seg   = f"{label} 약 {transit['time']}분"
    if transit.get("fare", 0) > 0:
        seg += f"·{transit['fare']:,}원"
    if transit.get("transfers", 0) > 0:
        seg += f" (환승 {transit['transfers']}회)"
    return f"{seg} / {taxi_str}"


_transit_cache: dict = {}


def _warm_transit_pairs(spots_per_day, restaurants, cafes, accommodation,
                        num_days, rest_chains, cafe_chains) -> None:
    """_build_skeletons 본 루프의 pick/동선 로직을 fresh set으로 재현해 필요한
    (출발지, 도착지) 쌍을 모으고, _transit_between 을 병렬로 호출해 캐시를 채운다."""
    from concurrent.futures import ThreadPoolExecutor

    is_day_trip = num_days == 1
    ur, uc = set(), set()
    pairs: list[tuple[dict, dict]] = []

    for d, day_spots in enumerate(spots_per_day):
        bf, ln, dn = _pick_restaurants(day_spots, restaurants, ur, rest_chains)
        cf = _pick_cafe(day_spots, cafes, uc, cafe_chains)
        mid = math.ceil(len(day_spots) / 2)

        prev = accommodation if (not is_day_trip and accommodation) else None
        chain = [bf] + list(day_spots[:mid]) + [ln] + list(day_spots[mid:]) + [cf, dn]
        if not (d == num_days - 1) and not is_day_trip and accommodation:
            chain.append(accommodation)
        for place in chain:
            if not place:
                continue
            if prev is not None:
                pairs.append((prev, place))
            prev = place

    uniq = {}
    for a, b in pairs:
        ca, cb = _parse_coord(a), _parse_coord(b)
        if ca and cb:
            uniq[(round(ca[0], 5), round(ca[1], 5), round(cb[0], 5), round(cb[1], 5))] = (a, b)

    todo = [ab for k, ab in uniq.items() if k not in _transit_cache]
    if not todo:
        return
    with ThreadPoolExecutor(max_workers=12) as ex:
        list(ex.map(lambda ab: _transit_between(*ab), todo))


def _transit_between(a: dict, b: dict) -> str:
    """두 장소 dict 간 이동 정보 문자열 반환 (좌표 기준 캐싱)"""
    ca = _parse_coord(a)
    cb = _parse_coord(b)
    if not ca or not cb:
        return "도보 약 5~15분 (정확한 경로는 현지 확인)"
    key = (round(ca[0], 5), round(ca[1], 5), round(cb[0], 5), round(cb[1], 5))
    if key in _transit_cache:
        return _transit_cache[key]
    km = _haversine_km(ca[0], ca[1], cb[0], cb[1])
    result = _transit_info(km, ca, cb)
    _transit_cache[key] = result
    return result


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
    best = _representative_route(routes) or (routes[0] if routes else None)
    if not best:
        return [f"{origin} → {dest} (대중교통 이용 예정)"]

    rtype = best.get("type", "교통편")
    grade = best.get("grade", "")
    fare  = _to_int_fare(best.get("fare", 0))
    dep   = str(best.get("dep_time", ""))
    arr   = str(best.get("arr_time", ""))
    dep_st = best.get("dep_station") or f"{origin}"      # API 실제 역/터미널명
    arr_st = best.get("arr_station") or f"{dest}"

    # 출발·도착 시각 파싱
    dep_fmt = f"{dep[8:10]}:{dep[10:12]}" if len(dep) >= 12 else ""
    arr_fmt = f"{arr[8:10]}:{arr[10:12]}" if len(arr) >= 12 else ""

    vehicle = (f"{rtype} {grade}").strip() if grade else rtype
    fare_str = f"{fare:,}원/인" if fare > 0 else "요금미정"

    lines: list[str] = []

    if dep_fmt:
        lines.append(f"{dep_fmt} {dep_st} 출발")
    lines.append(f"{dep_st} → {arr_st} ({vehicle}·{fare_str})")
    if arr_fmt:
        lines.append(f"{arr_fmt} {arr_st} 도착")

    # 도착역 → 숙소 이동 계산
    if accommodation:
        acc_coord = _parse_coord(accommodation)
        station_coord = geocode(arr_st) or geocode(f"{dest}역")
        if acc_coord and station_coord:
            km = _haversine_km(station_coord[0], station_coord[1],
                               acc_coord[0], acc_coord[1])
            transit_str = _transit_info(km, station_coord, acc_coord)
            acc_title = accommodation.get("title", "숙소")
            lines.append(f"{arr_st} → {acc_title} ({transit_str})")

    return lines


def _build_return_sequence(origin: str, dest: str, routes: list) -> list[str]:
    """
    마지막날 귀환 시퀀스를 최대 3줄로 반환.
      1) "HH:MM {dest}역 출발"
      2) "{dest}역 → {origin}역 (수단·요금/인)"
      3) "HH:MM {origin}역 도착"
    routes 없으면 단순 이동 1줄만 반환.
    """
    best = _representative_route(routes) or (routes[0] if routes else None)
    if not best:
        return [f"{dest} → {origin} (대중교통 이용 예정)"]

    rtype = best.get("type", "교통편")
    grade = best.get("grade", "")
    fare  = _to_int_fare(best.get("fare", 0))
    dep_st = best.get("dep_station") or f"{dest}"   # 귀환편이므로 출발=목적지
    arr_st = best.get("arr_station") or f"{origin}"

    dep   = str(best.get("dep_time", ""))
    arr   = str(best.get("arr_time", ""))
    dep_fmt = f"{dep[8:10]}:{dep[10:12]}" if len(dep) >= 12 else ""
    arr_fmt = f"{arr[8:10]}:{arr[10:12]}" if len(arr) >= 12 else ""

    vehicle  = (f"{rtype} {grade}").strip() if grade else rtype
    fare_str = f"{fare:,}원/인" if fare > 0 else "요금미정"

    lines: list[str] = []
    if dep_fmt:
        lines.append(f"{dep_fmt} {dep_st} 출발")
    lines.append(f"{dep_st} → {arr_st} ({vehicle}·{fare_str})")
    if arr_fmt:
        lines.append(f"{arr_fmt} {arr_st} 도착")
    return lines


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


_BRANCH_SUFFIX = re.compile(r'\s*\S+(점|지점|본점|직영점|DT점?|드라이브스루)$')


def _brand_of(title: str) -> str:
    """'스타벅스 여수돌산DT점' → '스타벅스', '카멜리아 회센터' → '카멜리아 회센터'"""
    return _BRANCH_SUFFIX.sub('', title).strip() or title


def _chain_titles(pool: list) -> set[str]:
    """후보 풀에서 '체인'으로 볼 title 집합.
    ① is_franchise 시드 리스트(버거킹·KFC 등 지점 표기 없는 것 포함)
    ② 같은 브랜드 접두어가 풀 안에 2곳 이상 + 지점 접미사 → 자동 감지 (시드 리스트 없이도)."""
    titles = [p.get("title", "") for p in pool if p.get("title")]
    brand_count: dict[str, int] = {}
    for t in titles:
        b = _brand_of(t)
        brand_count[b] = brand_count.get(b, 0) + 1

    chain: set[str] = set()
    for t in titles:
        if _is_franchise(t):
            chain.add(t)
        elif brand_count[_brand_of(t)] >= 2 and _BRANCH_SUFFIX.search(t):
            chain.add(t)
    return chain


def _pick_restaurants(day_spots: list, restaurants: list, used: set,
                      chains: set[str] | None = None) -> tuple:
    """하루 관광지 중심과 가장 가까운 미사용 식당 3개 선택.
    chains에 든 곳은 후순위 — 근처에 로컬이 충분하면 안 뽑히고, 없을 때만 채워진다."""
    if not restaurants:
        return None, None, None

    chains = chains or set()
    center = _centroid(day_spots)

    def dist(idx: int) -> float:
        c = _parse_coord(restaurants[idx])
        if not c or not center:
            return float("inf")
        return _haversine_km(center[0], center[1], c[0], c[1])

    def sort_key(idx: int) -> tuple:
        return (restaurants[idx].get("title", "") in chains, dist(idx))

    all_idx   = list(range(len(restaurants)))
    available = sorted([i for i in all_idx if i not in used], key=sort_key)

    picked = available[:3]
    for i in picked:
        used.add(i)

    # 미사용 식당이 3개 미만이면 이미 쓴 식당이라도 가까운 순으로 채운다
    # (끼니를 '현지 식당 직접 검색'으로 비우는 것보다 재방문이 낫다)
    if len(picked) < 3:
        refill = sorted([i for i in all_idx if i not in picked], key=sort_key)
        picked += refill[: 3 - len(picked)]

    result = [restaurants[i] for i in picked]
    while len(result) < 3:
        result.append(None)   # 식당 자체가 3개 미만일 때만 도달

    return result[0], result[1], result[2]


def _pick_cafe(day_spots: list, cafes: list, used: set,
               chains: set[str] | None = None) -> dict | None:
    """하루 관광지 중심과 가장 가까운 미사용 카페 1개 선택 (체인은 후순위)"""
    if not cafes:
        return None

    chains = chains or set()
    center = _centroid(day_spots)

    def dist(idx: int) -> float:
        c = _parse_coord(cafes[idx])
        if not c or not center:
            return float("inf")
        return _haversine_km(center[0], center[1], c[0], c[1])

    def sort_key(idx: int) -> tuple:
        return (cafes[idx].get("title", "") in chains, dist(idx))

    all_idx   = list(range(len(cafes)))
    available = sorted([i for i in all_idx if i not in used], key=sort_key)

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

    # area 태그가 없거나 알 수 없는 지역인 스팟(LLM 추천·필수방문 등)은 가장 가까운 지역으로 귀속.
    # 원본 dict를 건드리면 LangGraph state에 남아 재최적화 시 결과가 달라지므로 로컬 맵으로만 관리.
    area_of: dict[int, str | None] = {}
    for s in tourist_spots:
        a = s.get("area")
        if not a or a not in area_coords:
            a = _nearest_area(_parse_coord(s), area_coords) or a
        area_of[id(s)] = a

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
        spots = [s for s in tourist_spots if area_of.get(id(s)) in g]
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
    demote_chains: bool = True,
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
    rest_chains = _chain_titles(restaurants) if demote_chains else set()
    cafe_chains = _chain_titles(cafes)       if demote_chains else set()
    parts = []
    day_transits: list[list[str]] = [[] for _ in range(num_days)]

    # 이동 정보를 병렬로 미리 계산 — 스팟 쌍마다 ODsay+네이버 호출이라 순차로는 매우 느림.
    # 아래 실제 루프와 같은 pick 로직을 fresh set으로 재현해 필요한 (a,b) 쌍만 워밍한다.
    # (약간 어긋나도 캐시 미스로 개별 계산될 뿐, 결과는 동일)
    _warm_transit_pairs(spots_per_day, restaurants, cafes, accommodation,
                        num_days, rest_chains, cafe_chains)

    for d, day_spots in enumerate(spots_per_day):
        is_first = d == 0
        is_last  = d == num_days - 1

        breakfast, lunch, dinner = _pick_restaurants(day_spots, restaurants, used_rest, rest_chains)
        cafe = _pick_cafe(day_spots, cafes, used_cafe, cafe_chains)

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

    visited_spots = [s for day in spots_per_day for s in day]
    return "\n\n".join(parts), day_transits, visited_spots


_LONGHAUL_WORDS = ("자동차", "자가용", "렌터카", "렌트카", "승용차", "자차",
                   "KTX", "고속버스", "시외버스")


def _is_intercity_line(line: str, origin: str, dest: str) -> bool:
    """LLM이 만든 광역(출발지↔목적지) 이동 줄인지 판정. 형식(시각 접두사 유무)과 무관.
    - 한 줄에 출발지·목적지 두 도시가 모두 등장하거나
    - 한 도시 + 장거리 이동수단 키워드(KTX·고속버스·자동차 등)
    → departure_seq / return_seq 가 대체하므로 제거 대상.
    시간범위(관광·식사) 줄은 보존."""
    if not line:
        return False
    s = line.strip()
    if re.match(r'^\d{2}:\d{2}\s*~\s*\d{2}:\d{2}', s):
        return False
    has_o = origin in s
    has_d = dest in s
    if has_o and has_d:
        return True
    if (has_o or has_d) and any(w in s for w in _LONGHAUL_WORDS):
        return True
    return False


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

    # 형식 1: "서울역 출발" / "부산역 도착" / "여수엑스포역 도착" / "동서울종합터미널 출발"
    #   1일차·마지막날의 '역/터미널 + 출발/도착' 단일 이벤트는 항상 광역 이동 →
    #   departure_seq / return_seq 가 대체하므로 역명이 무엇이든 제거.
    if re.match(r'^\S*(역|터미널)\s+(출발|도착)$', desc):
        return True
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

    _range = parse_range(traveldates)
    if _range:
        start_date  = datetime(_range[0].year, _range[0].month, _range[0].day)
        num_days    = (_range[1] - _range[0]).days + 1
        date_labels = [
            (start_date + timedelta(days=i)).strftime("%Y-%m-%d (%a)")
            for i in range(num_days)
        ]
    else:
        num_days    = 1
        date_labels = [str(traveldates)]

    transport_summary  = _summarize_transport(state.get("transport_routes") or [])
    tourist_spots      = state.get("tourist_spots") or []
    restaurants        = state.get("restaurants") or []
    cafes              = state.get("cafes") or []

    # 카테고리 오분류 방어: 식당 슬롯에 관광/카페/액티비티가, 카페 슬롯에 식당이 섞여
    # 들어오는 경우가 있어(예: '패러글라이딩'이 아침식사) 네이버 category로 한 번 더 거른다.
    # (프랜차이즈는 제거하지 않는다 — _pick_*에서 후순위로 밀어내므로 근처에 로컬이 없을 때 채워짐)
    _WRONG_FOR_MEAL = {"cafe", "bar", "attraction", "activity", "lodging"}
    _WRONG_FOR_CAFE = {"restaurant", "bar", "attraction", "activity", "lodging"}
    restaurants = [r for r in restaurants
                   if _poi_kind(r.get("category", "")) not in _WRONG_FOR_MEAL]
    cafes       = [c for c in cafes
                   if _poi_kind(c.get("category", "")) not in _WRONG_FOR_CAFE]

    # 사용자가 익숙함·편의를 원하면 프랜차이즈 후순위화를 끈다
    _CHAIN_OK_HINTS = ("프랜차이즈", "체인", "편하게", "편한", "무난", "익숙")
    demote_chains = not any(
        h in p for p in (preferences or []) for h in _CHAIN_OK_HINTS
    )

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
    skeletons, day_transits, visited_spots = _build_skeletons(
        tourist_spots, restaurants, cafes, num_days, transport_summary,
        origin_city=origin_city, dest_city=city,
        accommodation=selected_acc,
        area_coords=area_coords,
        demote_chains=demote_chains,
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
    # ── 교통비: TAGO API가 준 실제 요금만 사용 (파이썬 추정 없음) ──────────
    #
    #   왕복 = (가는편 대표 편성 fare) + (오는편 대표 편성 fare), 각 × 인원수.
    #   오는편 조회가 실패하면 가는편 요금을 왕복 대칭으로 가정(× 2).
    #   가는편 요금조차 못 받으면 0 + source="unknown" → UI에서 "요금 확인 필요" 표기.
    out_route = _representative_route(state.get("transport_routes") or [])
    ret_route = _representative_route(state.get("transport_return_routes") or [])

    out_fare = _to_int_fare(out_route.get("fare", 0)) if out_route else 0
    ret_fare = _to_int_fare(ret_route.get("fare", 0)) if ret_route else 0

    if out_fare and ret_fare:
        transport_cost_total  = (out_fare + ret_fare) * num_people
        transport_fare_source = "api"
    elif out_fare:
        transport_cost_total  = out_fare * 2 * num_people
        transport_fare_source = "api_oneway"  # 오는편 조회 실패 → 편도 × 2 가정
    else:
        transport_cost_total  = 0
        transport_fare_source = "unknown"     # API가 요금을 주지 못함

    transport_fare_label = ""
    if out_route:
        transport_fare_label = (
            f'{out_route.get("type", "")} {out_route.get("grade", "")}'.strip()
        )

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

    # 4. 관광/활동비: 실제 방문하는 관광지의 입장료(관광공사 usefee) 합산 × 인원수.
    #    문화시설(type 14)만 usefee 실값이 있으므로 그 중 방문 확정분만 조회한다.
    #    요금을 못 받은 곳(type 12 유료·네이버발 등)은 0으로 취급 (추정하지 않음).
    activities_fee_per_person = 0
    for s in visited_spots:
        if s.get("content_id") and s.get("content_type") == "14":
            fee = _fetch_use_fee(s["content_id"], "14")
            if fee:
                activities_fee_per_person += fee
    activities_cost_total = activities_fee_per_person * num_people

    # 5. 최종 합계 = 실제 추정 지출 (예산 초과/여유가 그대로 드러남)
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

    transport_routes         = state.get("transport_routes") or []
    # 오는편 조회 실패 시 [] 그대로 — 가는편(transport_routes)으로 폴백하면 역명이
    # "서울역 → 여수엑스포역"처럼 반대로 찍힌다. 없으면 _build_return_sequence가 일반 문구를 낸다.
    transport_return_routes  = state.get("transport_return_routes") or []
    departure_seq = (
        _build_departure_sequence(origin_city, city, transport_routes, selected_acc)
        if origin_city and city else []
    )
    return_seq = (
        _build_return_sequence(origin_city, city, transport_return_routes)
        if origin_city and city else []
    )

    itinerary = []
    for day_idx, day_schedule in enumerate(daily_schedules):
        is_first = day_idx == 0
        is_last  = day_idx == num_days - 1

        # ── _inject_transits 실행 전 LLM 출력 형식 정규화 ───────────────
        raw = _normalize_schedule(list(day_schedule))

        # ② 1일차: LLM이 생성한 광역 이동 줄 제거 (형식 무관 — departure_seq로 대체)
        if is_first and origin_city:
            raw = [l for l in raw if not _is_intercity_line(l, origin_city, city)]
            raw = [l for l in raw if not _is_intercity_event(l, origin_city, city)]

        # ③ 마지막날: LLM이 생성한 귀환 줄 제거
        if is_last and not is_first and origin_city:
            raw = [l for l in raw if not _is_intercity_line(l, city, origin_city)]
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
        "transportation":        transport_cost_total,
        "transportation_source": transport_fare_source,  # api | api_oneway | unknown
        "transportation_label":  transport_fare_label,   # 예: "열차 KTX"
        "accommodation":  accommodation_cost_total,
        "meals":          meals_cost_total,
        "activities":     activities_cost_total,   # 방문지 입장료 합 (관광공사 usefee, 못 받은 곳은 0)
        "total":          total_calculated,        # 실제 추정 지출 — budget과 별개
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
