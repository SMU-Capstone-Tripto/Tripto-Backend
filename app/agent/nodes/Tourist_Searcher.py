import re
from _tour_api import find_area_codes, fetch_area_list
from state import TravelState

_ADMIN_SUFFIX = re.compile(r'[시구군읍면동리]$')

_EXCLUDE_KEYWORDS = {
    # 식음료
    "식당", "맛집", "카페", "커피", "조개구이", "국밥", "횟집",
    "갈비", "냉면", "치킨", "빵집", "베이커리", "분식", "초밥",
    "스시", "파스타", "라멘", "레스토랑", "돼지", "곱창", "족발",
    "보쌈", "쌈밥", "찜닭", "불고기", "짬뽕", "짜장", "삼겹살",
    # 유흥·주점
    "라이브", "주점", "술집", "포차", "클럽", "나이트", "호프",
    "이자카야", "bar", "pub", "막걸리",
    # 의료·상업
    "의원", "병원", "외과", "내과", "치과", "한의원", "약국",
    "부동산", "은행", "편의점", "마트", "주유소", "세탁",
}

def _district_match(district: str, address: str) -> bool:
    core = _ADMIN_SUFFIX.sub('', district)
    return bool(core) and core in address

def _is_non_tourist(title: str) -> bool:
    return any(kw in title for kw in _EXCLUDE_KEYWORDS)


def Tourist_Searcher(state: TravelState) -> dict:
    """한국관광공사 API로 관광지 정보 검색 (contentTypeId=12). districts가 여러 개면 지역별로 검색해 병합."""

    city = state.get("city")
    districts = state.get("districts") or []

    if not city:
        return {"current_step": "searching", "tourist_spots": []}

    # 지역 미지정 시 도시 전체 검색 (기존 동작 유지)
    search_targets = districts or [None]

    seen_titles: set[str] = set()
    tourist_spots: list[dict] = []

    for district in search_targets:
        sido_code, sigungu_code = find_area_codes(city, district)
        if not sido_code:
            continue

        num_rows = 100 if district else 30
        try:
            raw_list = fetch_area_list(sido_code, "12", sigungu_code, num_rows=num_rows)
        except Exception:
            continue

        spots = [
            {
                "title": item.get("title", ""),
                "address": (item.get("addr1", "") + " " + item.get("addr2", "")).strip(),
                "tel": item.get("tel", ""),
                "image": item.get("firstimage", ""),
                "content_id": item.get("contentid", ""),
                "category": item.get("cat3", ""),
                "mapx": item.get("mapx", ""),
                "mapy": item.get("mapy", ""),
                "area": district,
            }
            for item in raw_list
        ]

        # 식당·주점·병원 등 비관광지 제거
        spots = [s for s in spots if not _is_non_tourist(s.get("title", ""))]

        if district:
            filtered = [s for s in spots if _district_match(district, s.get("address", ""))]
            if filtered:
                spots = filtered

        for s in spots:
            title = s.get("title", "")
            if title and title not in seen_titles:
                seen_titles.add(title)
                tourist_spots.append(s)

    return {
        "current_step": "searching",
        "tourist_spots": tourist_spots,
    }
