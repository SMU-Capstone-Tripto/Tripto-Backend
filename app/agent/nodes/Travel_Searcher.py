import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from _tour_api import find_area_codes, fetch_area_list, fetch_detail_info
from state import TravelState


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
    """한국관광공사 API로 숙소 정보 검색 (contentTypeId=32)"""

    city        = state.get("city")
    district    = state.get("district")
    budget      = state.get("budget")
    num_people  = state.get("num_people") or 1
    traveldates = state.get("traveldates") or ""
    is_peak     = _is_peak_season(traveldates)

    if not city:
        return {"current_step": "searching", "accommodations": []}

    sido_code, sigungu_code = find_area_codes(city, district)
    if not sido_code:
        return {"current_step": "searching", "accommodations": []}

    num_rows = 100 if district else 30
    try:
        raw_list = fetch_area_list(sido_code, "32", sigungu_code, num_rows=num_rows, arrange="B")
    except Exception:
        return {"current_step": "searching", "accommodations": []}

    total_budget = (budget or 0) * num_people

    def _eff(room: dict) -> int:
        """성수기/비성수기에 따라 적용할 방 요금 반환"""
        if is_peak:
            return room.get("peak_price") or room.get("min_price") or 0
        return room.get("min_price") or 0

    def _fetch_one(item: dict):
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
        }

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(_fetch_one, item) for item in raw_list[:50]]
        accommodations = [r for f in futures for r in [f.result()] if r is not None]

    if district:
        filtered = [a for a in accommodations if _district_match(district, a.get("address", ""))]
        if filtered:
            accommodations = filtered

    if budget:
        def _sort_key(a: dict) -> float:
            rooms = a.get("rooms", [])
            return _min_cost_for_group(rooms, num_people, _eff)
        accommodations.sort(key=_sort_key)

    return {
        "current_step": "searching",
        "accommodations": accommodations,
    }
