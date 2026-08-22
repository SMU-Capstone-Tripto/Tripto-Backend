import os
import sys
from concurrent.futures import ThreadPoolExecutor
sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from _naver_api import search_local
from state import TravelState


_EXCLUDE_KEYWORDS = {
    "라이브", "주점", "술집", "포차", "클럽", "나이트", "호프",
    "이자카야", "bar", "pub", "유흥", "노래방", "단란주점",
    "의원", "병원", "외과", "내과", "치과", "약국",
}

def _is_excluded(title: str) -> bool:
    t = title.lower()
    return any(kw in t for kw in _EXCLUDE_KEYWORDS)


def _dedup(items: list) -> list:
    seen, result = set(), []
    for item in items:
        key = item.get("title", "")
        if key and key not in seen and not _is_excluded(key):
            seen.add(key)
            result.append(item)
    return result


def Restaurant_Searcher(state: TravelState) -> dict:
    """관광지별 근처 식당·카페 검색 (spot_enhancer 완료 후 실행)"""

    city          = state.get("city", "")
    districts     = state.get("districts") or []
    tourist_spots = state.get("tourist_spots") or []
    location      = f"{city} {' '.join(districts)}".strip() if districts else city

    if not city:
        return {"current_step": "searching", "restaurants": [], "cafes": []}

    # 관광지별 근처 검색 쿼리 생성 (최대 8개 관광지)
    spot_titles = [s.get("title", "") for s in tourist_spots[:8] if s.get("title")]

    rest_queries = [f"{t} 맛집" for t in spot_titles] + [f"{location} 맛집"]
    cafe_queries = [f"{t} 카페" for t in spot_titles] + [f"{location} 카페"]

    with ThreadPoolExecutor(max_workers=10) as executor:
        rest_futures = [executor.submit(search_local, q, 5) for q in rest_queries]
        cafe_futures = [executor.submit(search_local, q, 5) for q in cafe_queries]

        restaurants = _dedup([r for f in rest_futures for r in f.result()])
        cafes       = _dedup([r for f in cafe_futures for r in f.result()])

    return {
        "current_step": "searching",
        "restaurants": restaurants,
        "cafes": cafes,
    }