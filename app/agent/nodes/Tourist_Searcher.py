import math
import re
from itertools import zip_longest
from _naver_api import geocode
from _tour_api import find_area_codes, fetch_area_list
from state import TravelState

_ADMIN_SUFFIX = re.compile(r'[시구군읍면동리]$')

# 구역 미지정(도시 전체) 검색일 때, 도심에서 이 거리를 넘는 스팟(먼 섬 등)은 제외.
# 구역을 콕 집어 요청하면(예: districts=["거문도"]) 이 필터를 타지 않는다.
_CITYWIDE_MAX_KM = 40


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return r * 2 * math.asin(math.sqrt(a))


def _ll(spot: dict):
    try:
        lat, lon = float(spot["mapy"]), float(spot["mapx"])
    except (KeyError, ValueError, TypeError):
        return None
    # 0 근처 = 좌표 미등록 (한국은 위도 33~39, 경도 124~132)
    if abs(lat) < 1 or abs(lon) < 1:
        return None
    return lat, lon

# 관광지(12) + 문화시설(14). 유료 시설(박물관·미술관·전시관 등)은 14로 등록돼 있고
# 14의 detailIntro2에는 usefee(입장료) 실값이 들어있다.
_CONTENT_TYPES = ("12", "14")

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
    """한국관광공사 API로 관광지(12) + 문화시설(14) 검색.
    각 장소의 입장료(use_fee)를 detailIntro2로 조회해 함께 반환한다.
    districts가 여러 개면 지역별로 검색해 병합."""

    city = state.get("city")
    districts = state.get("districts") or []

    if not city:
        return {"current_step": "searching", "tourist_spots": []}

    # 지역 미지정 시 도시 전체 검색 (기존 동작 유지)
    search_targets = districts or [None]

    # 도시 전체 검색일 때만 쓰는 도심 좌표 (먼 섬 필터용)
    city_center = geocode(city) if not districts else None

    seen_titles: set[str] = set()
    tourist_spots: list[dict] = []

    for district in search_targets:
        sido_code, sigungu_code = find_area_codes(city, district)
        if not sido_code:
            continue

        # 도시 전체 검색은 후보를 넉넉히 받아 도심 스팟이 묻히지 않게 한다
        num_rows = 100
        per_type: list[list[dict]] = []
        for ctype in _CONTENT_TYPES:
            try:
                lst = fetch_area_list(sido_code, ctype, sigungu_code, num_rows=num_rows)
            except Exception:
                lst = []
            for item in lst:
                item["_ctype"] = ctype
            per_type.append(lst)

        # type 12/14를 라운드로빈으로 섞어 문화시설(14)이 뒤로 밀리지 않게 한다
        raw_list = [it for row in zip_longest(*per_type) for it in row if it is not None]

        spots = [
            {
                "title": item.get("title", ""),
                "address": (item.get("addr1", "") + " " + item.get("addr2", "")).strip(),
                "tel": item.get("tel", ""),
                "image": item.get("firstimage", ""),
                "content_id": item.get("contentid", ""),
                "content_type": item.get("_ctype", "12"),
                # 구 분류체계(cat1/cat2/cat3)는 실측 결과 부산타워·용두산공원 등 유명 관광지도
                # 값이 비어있는 경우가 많아(19개 중 7개), TourAPI 4.0의 신 분류체계로 대체.
                # category_group: 대분류(예: HS=역사, VE=명소, EX=체험, NA=자연), category: 소분류
                "category_group": item.get("lclsSystm1", ""),
                "category":       item.get("lclsSystm3", ""),
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
        elif city_center:
            # 도시 전체 검색: 도심에서 40km 넘는 먼 섬 등 제외.
            # 단 이렇게 걸러 10곳 미만이면(섬 위주 소도시) 필터를 포기한다.
            near = [
                s for s in spots
                if (ll := _ll(s)) is None
                or _haversine_km(city_center[0], city_center[1], ll[0], ll[1]) <= _CITYWIDE_MAX_KM
            ]
            if len(near) >= 10:
                spots = near

        for s in spots:
            title = s.get("title", "")
            if title and title not in seen_titles:
                seen_titles.add(title)
                tourist_spots.append(s)

    # 입장료(use_fee)는 실제 방문이 확정되는 Optimizer 단계에서 조회한다
    # (여기서 후보 전부 조회하면 서울처럼 수백 건이라 너무 느림).
    return {
        "current_step": "searching",
        "tourist_spots": tourist_spots,
    }
