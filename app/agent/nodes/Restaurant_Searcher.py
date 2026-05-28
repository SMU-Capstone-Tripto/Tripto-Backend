import os
import sys
from concurrent.futures import ThreadPoolExecutor
sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from _naver_api import search_local
from state import TravelState


def _dedup(items: list) -> list:
    seen, result = set(), []
    for item in items:
        key = item.get("title", "")
        if key and key not in seen:
            seen.add(key)
            result.append(item)
    return result


def Restaurant_Searcher(state: TravelState) -> dict:
    """네이버 검색 API로 식당·카페 정보 병렬 검색 (리뷰 많은 순)"""

    city     = state.get("city", "")
    district = state.get("district", "")
    location = f"{city} {district}".strip() if district else city

    if not city:
        return {"current_step": "searching", "restaurants": [], "cafes": []}

    with ThreadPoolExecutor(max_workers=3) as executor:
        f_matjip   = executor.submit(search_local, f"{location} 맛집",  20)
        f_sikdang  = executor.submit(search_local, f"{location} 음식점", 20)
        f_cafes    = executor.submit(search_local, f"{location} 카페",  20)
        restaurants = _dedup(f_matjip.result() + f_sikdang.result())
        cafes       = f_cafes.result()

    return {
        "current_step": "searching",
        "restaurants": restaurants,
        "cafes": cafes,
    }
