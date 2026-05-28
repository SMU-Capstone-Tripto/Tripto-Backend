import re
from _tour_api import find_area_codes, fetch_area_list
from state import TravelState

_ADMIN_SUFFIX = re.compile(r'[시구군읍면동리]$')

def _district_match(district: str, address: str) -> bool:
    core = _ADMIN_SUFFIX.sub('', district)
    return bool(core) and core in address


def Tourist_Searcher(state: TravelState) -> dict:
    """한국관광공사 API로 관광지 정보 검색 (contentTypeId=12)"""

    city = state.get("city")
    district = state.get("district")

    if not city:
        return {"current_step": "searching", "tourist_spots": []}

    sido_code, sigungu_code = find_area_codes(city, district)
    if not sido_code:
        return {"current_step": "searching", "tourist_spots": []}

    num_rows = 100 if district else 30
    try:
        raw_list = fetch_area_list(sido_code, "12", sigungu_code, num_rows=num_rows)
    except Exception:
        return {"current_step": "searching", "tourist_spots": []}

    tourist_spots = [
        {
            "title": item.get("title", ""),
            "address": (item.get("addr1", "") + " " + item.get("addr2", "")).strip(),
            "tel": item.get("tel", ""),
            "image": item.get("firstimage", ""),
            "content_id": item.get("contentid", ""),
            "category": item.get("cat3", ""),
            "mapx": item.get("mapx", ""),
            "mapy": item.get("mapy", ""),
        }
        for item in raw_list
    ]

    if district:
        tourist_spots = [
            t for t in tourist_spots
            if _district_match(district, t.get("address", ""))
        ]

    return {
        "current_step": "searching",
        "tourist_spots": tourist_spots,
    }
