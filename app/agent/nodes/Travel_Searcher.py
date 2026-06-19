import re
from concurrent.futures import ThreadPoolExecutor

from _tour_api import find_area_codes, fetch_area_list, fetch_detail_info
from state import TravelState

_ADMIN_SUFFIX = re.compile(r'[시구군읍면동리]$')

def _district_match(district: str, address: str) -> bool:
    core = _ADMIN_SUFFIX.sub('', district)
    return bool(core) and core in address


def _parse_room(room: dict) -> dict:
    try:
        fee1 = int(room.get("roomoffseasonminfee1") or 0)
        fee2 = int(room.get("roomoffseasonminfee2") or 0)
        candidates = [f for f in [fee1, fee2] if f > 0]
        min_price = min(candidates) if candidates else 0
    except (ValueError, TypeError):
        min_price = 0

    return {
        "room_name": room.get("roomtitle", ""),
        "base_capacity": int(room.get("roombasecount") or 0),
        "max_capacity": int(room.get("roommaxcount") or 0),
        "min_price": min_price,
    }


def Travel_Searcher(state: TravelState) -> dict:
    """한국관광공사 API로 숙소 정보 검색 (contentTypeId=32)"""

    city       = state.get("city")
    district   = state.get("district")
    budget     = state.get("budget")
    num_people = state.get("num_people") or 1

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

    def _fetch_one(item: dict):
        content_id = item.get("contentid", "")
        try:
            rooms_raw = fetch_detail_info(content_id, "32")
        except Exception:
            rooms_raw = []

        rooms = [_parse_room(r) for r in rooms_raw]

        if budget and rooms:
            rooms = [r for r in rooms if r["min_price"] == 0 or r["min_price"] <= budget]
            if not rooms:
                return None

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

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(_fetch_one, item) for item in raw_list[:20]]
        accommodations = [r for f in futures for r in [f.result()] if r is not None]

    if district:
        accommodations = [
            a for a in accommodations
            if _district_match(district, a.get("address", ""))
        ]

    if budget:
        def _sort_key(a: dict) -> float:
            rooms = a.get("rooms", [])
            suitable = [r for r in rooms if r.get("max_capacity", 0) >= num_people] or rooms
            prices = [r["min_price"] for r in suitable if r["min_price"] > 0]
            if not prices:
                return float("inf")
            return min(abs(p / num_people - budget) for p in prices)
        accommodations.sort(key=_sort_key)

    return {
        "current_step": "searching",
        "accommodations": accommodations,
    }
