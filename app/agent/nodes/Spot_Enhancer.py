import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import List
from pydantic import BaseModel
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_groq import ChatGroq
from dotenv import load_dotenv

sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from _naver_api import geocode, search_local, is_franchise as _is_franchise
from state import TravelState

load_dotenv()


# ── 후보 수집: category별 고정 테마 쿼리 (맛집/카페는 절대 포함하지 않음 — Restaurant_Searcher 영역과 분리) ──
_THEME_QUERIES: dict[str, list[str]] = {
    "자연경관":     ["자연경관", "공원", "해변"],
    "역사·문화":    ["역사유적", "박물관", "문화유산"],
    "체험·액티비티": ["체험", "액티비티"],
    "쇼핑·시장":    ["전통시장", "쇼핑거리"],
    "야경·전망대":  ["야경명소", "전망대"],
}

# preferences(자유 텍스트) → 검색 키워드. substring 매칭이라 "힐링여행"도 "힐링" 키에 걸림.
_PREF_KEYWORDS_MAP: dict[str, list[str]] = {
    "자연":     ["자연경관", "숲길", "산책로"],
    "힐링":     ["힐링스팟", "정원", "산책로"],
    "액티비티":  ["체험", "액티비티", "테마파크"],
    "쇼핑":     ["전통시장", "쇼핑거리"],
    "야경":     ["야경명소", "전망대"],
    "역사":     ["역사유적", "문화유산"],
    "캠핑":     ["캠핑장", "글램핑"],
    "서핑":     ["서핑", "해변액티비티"],
    "사진":     ["포토스팟"],
    "가족":     ["가족여행", "키즈"],
}

# 프랜차이즈 판정은 _naver_api.is_franchise 로 통일 (관광지·식당·카페 공통)

_EXCLUDE_KEYWORDS = {
    "식당", "맛집", "횟집", "국밥", "갈비", "냉면", "치킨", "빵집",
    "베이커리", "분식", "초밥", "짬뽕", "삼겹살", "돼지", "곱창",
    "카페", "커피", "조개구이", "족발", "보쌈",
    "라이브", "주점", "술집", "포차", "클럽", "나이트", "호프", "bar", "pub",
    "의원", "병원", "외과", "내과", "치과", "한의원", "약국",
    "부동산", "은행", "편의점", "마트", "주유소", "세탁",
}

_EXCLUDE_CATEGORY_KEYWORDS = {
    # 네이버 category는 "음식점>한식"이 아니라 "한식>장어,먹장어요리"처럼 요리 종류가
    # 최상위로 오는 경우가 많아, "음식점" 리터럴만으로는 식당 카테고리를 다 못 거른다.
    "음식점", "한식", "중식", "일식", "양식", "아시아음식", "분식", "치킨", "피자",
    "버거", "고기,구이", "해물,생선", "족발,보쌈", "찜,탕", "술집", "호프", "이자카야",
    "카페", "디저트", "베이커리",
    "병원", "약국", "학원", "부동산", "은행",
    "숙박", "주유소", "정비", "미용", "종교시설",
}

_ADMIN_SUFFIX = re.compile(r'[시구군읍면동리]$')


def _is_non_tourist(title: str) -> bool:
    t = title.lower()
    return any(kw in t for kw in _EXCLUDE_KEYWORDS)


def _category_ok(category: str) -> bool:
    return not any(kw in category for kw in _EXCLUDE_CATEGORY_KEYWORDS)


def _district_match(district: str, address: str) -> bool:
    core = _ADMIN_SUFFIX.sub('', district)
    return bool(core) and core in address


def _normalize(text: str) -> str:
    return text.replace(" ", "").lower()


def _is_duplicate(title: str, existing_titles: list[str]) -> bool:
    """검색 후보/필수방문 장소가 API 결과와 중복인지 확인 (부분 문자열 기준)"""
    norm = _normalize(title)
    for t in existing_titles:
        t_norm = _normalize(t)
        if norm in t_norm or t_norm in norm:
            return True
    return False


def _build_queries(locations: list[str], preferences: list[str]) -> set[str]:
    """지역별로 쿼리를 따로 만든다 — 여러 지역을 한 문자열로 합치면 검색 결과가 한쪽으로 쏠린다
    (예: "부산 중구 서구 전망대"로 합쳐 보내면 서구 결과만 나오고 중구는 아예 안 나옴)."""
    queries: set[str] = set()

    for location in locations:
        for keywords in _THEME_QUERIES.values():
            for kw in keywords:
                queries.add(f"{location} {kw}")

        for pref in preferences:
            pref = pref.strip()
            if not pref:
                continue
            matched = False
            for key, keywords in _PREF_KEYWORDS_MAP.items():
                if key in pref:
                    matched = True
                    for kw in keywords:
                        queries.add(f"{location} {kw}")
            if not matched:
                # 사전에 없는 새로운 취향 표현도 버리지 않고 원문 그대로 검색 (커버리지 유지)
                queries.add(f"{location} {pref}")

    return queries


def _collect_candidates(city: str, districts: list[str], preferences: list[str]) -> list[dict]:
    """네이버 지역 검색으로 실존 관광지 후보 풀을 수집 (맛집/카페 제외, 프랜차이즈 제외).
    지역이 여러 곳이면 지역별로 따로 검색해서 병합 (Tourist_Searcher/Travel_Searcher와 동일 패턴)."""
    locations = [f"{city} {d}".strip() for d in districts] or [city]
    queries = _build_queries(locations, preferences)

    with ThreadPoolExecutor(max_workers=10) as executor:
        results = list(executor.map(lambda q: search_local(q, 10), queries))

    seen_titles: set[str] = set()
    candidates: list[dict] = []
    for items in results:
        for item in items:
            title = item.get("title", "")
            if not title or title in seen_titles:
                continue
            if _is_non_tourist(title) or _is_franchise(title):
                continue
            if not _category_ok(item.get("category", "")):
                continue
            seen_titles.add(title)
            candidates.append(item)

    if districts:
        filtered = [c for c in candidates
                    if any(_district_match(d, c.get("address", "")) for d in districts)]
        if filtered:
            candidates = filtered

    return candidates


class SpotSelection(BaseModel):
    """LLM은 후보 목록의 인덱스만 반환 — 목록 밖의 장소를 만들어낼 방법이 없어 환각이 구조적으로 차단됨"""
    selected_indices: List[int]


def _build_candidate_listing(candidates: list[dict]) -> str:
    lines = []
    for i, c in enumerate(candidates):
        lines.append(f"{i}. {c.get('title', '')} ({c.get('category', '')}) - {c.get('address', '')}")
    return "\n".join(lines)


def _to_spot_dict(title: str, address: str, category: str, location: str, **extra) -> dict:
    """네이버 지오코딩으로 좌표를 조회하여 spot dict 반환 (필수방문 장소 전용 — 검색 후보와 무관하게 항상 실행)"""
    coord = (
        geocode(f"{location} {title}")     # 1순위: "부산 깡통시장"
        or geocode(f"{address} {title}")   # 2순위: 주소 결합
        # 도시명 없는 단독 검색은 다른 도시 결과가 나올 수 있어 제거
    )
    return {
        "title":      title,
        "address":    address if address else location,
        "tel":        "",
        "image":      "",
        "content_id": "",
        "category":   category,
        "mapx":       str(coord[1]) if coord else "",  # 경도
        "mapy":       str(coord[0]) if coord else "",  # 위도
        **extra,
    }


def Spot_Enhancer(state: TravelState) -> dict:
    """네이버 검색으로 실존 관광지 후보를 먼저 모으고, LLM은 후보 중에서 선택만 하도록 제한해 tourist_spots를 보완"""

    city        = state.get("city", "")
    districts   = state.get("districts") or []
    preferences = state.get("preferences") or []
    api_spots   = state.get("tourist_spots") or []

    existing_titles = [s.get("title", "") for s in api_spots]
    location = f"{city} {' '.join(districts)}".strip() if districts else city

    # 1. 실존 후보 풀 수집 (검색 기반) — Tourist_Searcher가 이미 찾은 장소는 제외
    candidates = _collect_candidates(city, districts, preferences)
    candidates = [c for c in candidates if not _is_duplicate(c.get("title", ""), existing_titles)]

    # 2. LLM은 후보 인덱스만 선택 (자유 생성 금지)
    selected: list[dict] = []
    if candidates:
        system_prompt = f"""
                    너는 한국 여행 전문가야. 아래는 네이버 지역 검색으로 확인된 실존 장소 후보 목록이야.

                    ### 여행 조건
                    - 여행 지역: {location}
                    - 선호 스타일: {', '.join(preferences) if preferences else '제한 없음'}

                    ### 후보 목록 (index. 이름 (카테고리) - 주소)
                    {_build_candidate_listing(candidates)}

                    ### 지시
                    - 반드시 위 후보 목록 안에서만 골라야 해. 목록에 없는 장소는 절대 만들어내지 마.
                    - 선호 스타일과 가장 잘 맞는 곳 위주로 최대 10개를 골라 인덱스로만 답해.
                    - 같은 장소가 이름만 다르게 중복돼 보이면 하나만 선택해.
                    - 체인점처럼 보이는 곳은 피하고 지역색이 강한 곳을 우선해.
                        """

        llm = ChatGroq(
            model="openai/gpt-oss-20b",
            api_key=os.getenv("GROQ_API_KEY"),
            timeout=30,
            reasoning_effort="low",
        )

        try:
            result: SpotSelection = llm.with_structured_output(SpotSelection).invoke([
                SystemMessage(content=system_prompt),
                HumanMessage(content="후보 목록에서 선호 스타일에 맞는 장소를 골라줘."),
            ])
            selected = [candidates[i] for i in result.selected_indices if 0 <= i < len(candidates)]
        except Exception:
            # 선택 실패해도 must_visit 지오코딩은 아래에서 항상 수행되어야 하므로 빈 리스트로 계속 진행
            selected = []

    for s in selected:
        existing_titles.append(s.get("title", ""))

    # 3. must_visit 중 미등록 장소 — LLM 성공/실패와 무관하게 항상 처리
    must_visit    = state.get("must_visit") or []
    unresolved_mv = [p for p in must_visit if not _is_duplicate(p, existing_titles)]
    for p in unresolved_mv:
        existing_titles.append(p)

    def resolve_mv(place: str) -> dict:
        return _to_spot_dict(place, f"{location} {place}", "필수방문", location,
                             llm_suggested=True, must_visit=True)

    if unresolved_mv:
        with ThreadPoolExecutor(max_workers=8) as executor:
            mv_spots = list(executor.map(resolve_mv, unresolved_mv))
    else:
        mv_spots = []
    mv_spots = [s for s in mv_spots if s.get("mapx") and s.get("mapy")]

    new_spots = [
        {**s, "llm_suggested": True}
        for s in selected
    ] + mv_spots

    return {"tourist_spots": api_spots + new_spots}
