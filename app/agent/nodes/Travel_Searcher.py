import math
import re
from concurrent.futures import ThreadPoolExecutor

from _naver_api import geocode
from _dates import is_peak_season as _is_peak_season
from _tour_api import find_area_codes, fetch_area_list, fetch_detail_info
from state import TravelState

# 구역 미지정(도시 전체) 검색 시 도심에서 이 거리를 넘는 숙소(먼 섬 펜션 등) 제외
_CITYWIDE_MAX_KM = 40


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = (math.sin(dp / 2) ** 2
         + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dl / 2) ** 2)
    return r * 2 * math.asin(math.sqrt(a))


def _ll(item: dict):
    try:
        lat, lon = float(item.get("mapy") or 0), float(item.get("mapx") or 0)
    except (ValueError, TypeError):
        return None
    return (lat, lon) if abs(lat) >= 1 and abs(lon) >= 1 else None


def _min_cost_for_group(rooms: list, num_people: int, price_fn) -> float:
    """num_people을 수용하는 최저 비용 방 조합 계산 (DP)"""
    options = [
        (r.get("max_capacity", 0), price_fn(r))
        for r in rooms
        if r.get("max_capacity", 0) > 0 and price_fn(r) > 0
    ]
    if not options:
        return float("inf")

    INF = float("inf")
    dp = [INF] * (num_people + 1)
    dp[0] = 0

    for i in range(1, num_people + 1):
        for cap, price in options:
            prev = max(0, i - cap)
            if dp[prev] != INF and dp[prev] + price < dp[i]:
                dp[i] = dp[prev] + price

    return dp[num_people]


_ADMIN_SUFFIX = re.compile(r'[시구군읍면동리]$')

def _district_match(district: str, address: str) -> bool:
    core = _ADMIN_SUFFIX.sub('', district)
    return bool(core) and core in address


def _parse_room(room: dict) -> dict:
    def _min_fee(*fields):
        try:
            vals = [int(room.get(f) or 0) for f in fields]
            candidates = [v for v in vals if v > 0]
            return min(candidates) if candidates else 0
        except (ValueError, TypeError):
            return 0

    return {
        "room_name":     room.get("roomtitle", ""),
        "base_capacity": int(room.get("roombasecount") or 0),
        "max_capacity":  int(room.get("roommaxcount") or 0),
        "min_price":     _min_fee("roomoffseasonminfee1", "roomoffseasonminfee2"),
        "peak_price":    _min_fee("roompeakseasonminfee1", "roompeakseasonminfee2"),
    }


def Travel_Searcher(state: TravelState) -> dict:
    """한국관광공사 API로 숙소 정보 검색 (contentTypeId=32). districts가 여러 개면 지역별로 검색해 병합."""

    city        = state.get("city")
    districts   = state.get("districts") or []
    budget      = state.get("budget")
    num_people  = state.get("num_people") or 1
    traveldates = state.get("traveldates") or ""
    is_peak     = _is_peak_season(traveldates)

    if not city:
        return {"current_step": "searching", "accommodations": []}

    search_targets = districts or [None]
    total_budget = (budget or 0) * num_people

    def _eff(room: dict) -> int:
        """성수기/비성수기에 따라 적용할 방 요금 반환"""
        if is_peak:
            return room.get("peak_price") or room.get("min_price") or 0
        return room.get("min_price") or 0

    def _fetch_one(item: dict, district: str | None):
        content_id = item.get("contentid", "")
        try:
            rooms_raw = fetch_detail_info(content_id, "32")
        except Exception:
            rooms_raw = []

        rooms = [_parse_room(r) for r in rooms_raw]

        if total_budget and rooms:
            filtered = [r for r in rooms if _eff(r) == 0 or _eff(r) <= total_budget]
            if filtered:
                rooms = filtered

        return {
            "title":      item.get("title", ""),
            "address":    (item.get("addr1", "") + " " + item.get("addr2", "")).strip(),
            "tel":        item.get("tel", ""),
            "image":      item.get("firstimage", ""),
            "content_id": content_id,
            "mapx":       str(item.get("mapx", "")),
            "mapy":       str(item.get("mapy", "")),
            "rooms":      rooms,
            "area":       district,
        }

    # 도시 전체 검색일 때만 쓰는 도심 좌표 (먼 섬 숙소 필터용)
    city_center = geocode(city) if not districts else None

    seen_titles: set[str] = set()
    accommodations: list[dict] = []

    for district in search_targets:
        sido_code, sigungu_code = find_area_codes(city, district)
        if not sido_code:
            continue

        try:
            raw_list = fetch_area_list(sido_code, "32", sigungu_code, num_rows=100, arrange="O")
        except Exception:
            continue

        # 도시 전체 검색: 방 정보 조회(느림) 전에 먼 섬 숙소부터 걸러낸다.
        if not district and city_center:
            near = [
                it for it in raw_list
                if (ll := _ll(it)) is None
                or _haversine_km(city_center[0], city_center[1], ll[0], ll[1]) <= _CITYWIDE_MAX_KM
            ]
            if len(near) >= 10:
                raw_list = near

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(_fetch_one, item, district) for item in raw_list[:50]]
            fetched = [f.result() for f in futures]

        if district:
            filtered = [a for a in fetched if _district_match(district, a.get("address", ""))]
            if filtered:
                fetched = filtered

        for a in fetched:
            title = a.get("title", "")
            if title and title not in seen_titles:
                seen_titles.add(title)
                accommodations.append(a)

    if budget:
        def _sort_key(a: dict) -> float:
            rooms = a.get("rooms", [])
            return _min_cost_for_group(rooms, num_people, _eff)
        accommodations.sort(key=_sort_key)

    return {
        "current_step": "searching",
        "accommodations": accommodations,
    }
