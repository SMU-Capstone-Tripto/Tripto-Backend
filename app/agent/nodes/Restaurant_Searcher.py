import os
import sys
from concurrent.futures import ThreadPoolExecutor
sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from _naver_api import search_local, poi_kind
from state import TravelState


_EXCLUDE_KEYWORDS = {
    "라이브", "주점", "술집", "포차", "클럽", "나이트", "호프",
    "이자카야", "bar", "pub", "유흥", "노래방", "단란주점",
    "의원", "병원", "외과", "내과", "치과", "약국",
}

def _is_excluded(title: str) -> bool:
    t = title.lower()
    return any(kw in t for kw in _EXCLUDE_KEYWORDS)


def _norm(s: str) -> str:
    return s.replace(" ", "").lower()


# 지역·관광지 단위 검색 키워드. 네이버 지역검색 API는 쿼리당 최대 5건만 주므로
# 로컬 후보 풀을 두껍게 쌓으려면 키워드·정렬을 다양화해 쿼리 수를 늘리는 수밖에 없다.
# (프랜차이즈는 여기서 거르지 않고 Optimizer._pick_*에서 '후순위'로 밀어낸다 —
#  근처에 로컬이 충분하면 자연히 안 뽑히고, 없을 때만 체인이 채워진다.)
_REST_KEYWORDS = ("맛집", "유명 맛집", "현지 맛집", "노포", "한식")
_CAFE_KEYWORDS = ("카페", "감성 카페", "디저트 카페", "베이커리 카페")
_SORTS = ("comment", "random")  # 리뷰순 + 정확도순 — 결과 집합이 꽤 달라 병합하면 커버리지↑


def _dedup(items: list, want: str, block_titles: set[str]) -> list:
    """네이버 category가 want('restaurant'|'cafe')인 항목만 남긴다.
    관광지로 이미 잡힌 장소(패러글라이딩 등)는 이름 기준으로 제외."""
    seen, result = set(), []
    for item in items:
        title = item.get("title", "")
        if not title or title in seen or _is_excluded(title):
            continue
        if _norm(title) in block_titles:
            continue
        if poi_kind(item.get("category", "")) != want:
            continue
        seen.add(title)
        result.append(item)
    return result


def _run(spot_queries: list[str], area_queries: list[str]) -> list[dict]:
    # 관광지 앵커링 쿼리는 정렬 1종(comment), 지역 baseline 쿼리는 2종 — 총 호출 수 억제
    tasks = [(q, "comment") for q in spot_queries]
    tasks += [(q, s) for q in area_queries for s in _SORTS]
    with ThreadPoolExecutor(max_workers=12) as ex:
        futures = [ex.submit(search_local, q, 5, s) for q, s in tasks]
        return [r for f in futures for r in f.result()]


def Restaurant_Searcher(state: TravelState) -> dict:
    """식당·카페 검색. spot_enhancer 완료 후 실행.
    - 관광지 앵커링 쿼리("{관광지} 맛집") + 지역 baseline 쿼리("{도시} 맛집" 등) 둘 다
    - 키워드·정렬 다양화로 로컬 후보를 최대한 많이 확보
    - 비식당(패러글라이딩 등)만 poi_kind로 제거, 프랜차이즈는 남겨두되 Optimizer가 후순위 처리
    - 근접 선택은 Optimizer._pick_restaurants(관광지 중심 거리순)가 담당"""

    city          = state.get("city", "")
    districts     = state.get("districts") or []
    tourist_spots = state.get("tourist_spots") or []

    if not city:
        return {"current_step": "searching", "restaurants": [], "cafes": []}

    spot_titles = [s.get("title", "") for s in tourist_spots[:6] if s.get("title")]
    areas = [f"{city} {d}".strip() for d in districts if d.strip()]
    if city not in areas:
        areas.append(city)

    rest_spot = sorted({f"{t} 맛집" for t in spot_titles})
    rest_area = sorted({f"{a} {kw}" for a in areas for kw in _REST_KEYWORDS})
    cafe_spot = sorted({f"{t} 카페" for t in spot_titles})
    cafe_area = sorted({f"{a} {kw}" for a in areas for kw in _CAFE_KEYWORDS})

    # 관광지로 이미 잡힌 장소는 식당/카페 후보에서 배제 (예: '여수 국가대표 패러글라이딩')
    block_titles = {
        s.get("title", "").replace(" ", "").lower()
        for s in tourist_spots if s.get("title")
    }

    restaurants = _dedup(_run(rest_spot, rest_area), "restaurant", block_titles)
    cafes       = _dedup(_run(cafe_spot, cafe_area), "cafe", block_titles)

    return {
        "current_step": "searching",
        "restaurants": restaurants,
        "cafes": cafes,
    }
