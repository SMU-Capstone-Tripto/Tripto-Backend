import os
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import List
from pydantic import BaseModel
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_groq import ChatGroq
from dotenv import load_dotenv

sys.path.append(os.path.dirname(os.path.dirname(__file__)))
from _naver_api import geocode
from state import TravelState

load_dotenv()


class FamousSpot(BaseModel):
    title: str
    address: str    # 실제 도로명 또는 지번 주소
    category: str   # 자연경관, 역사·문화, 체험·액티비티, 쇼핑·시장, 야경·전망대, 맛집, 카페


class FamousSpotList(BaseModel):
    spots: List[FamousSpot]


def _normalize(text: str) -> str:
    return text.replace(" ", "").lower()


def _is_duplicate(llm_title: str, existing_titles: list[str]) -> bool:
    """LLM 추천 장소가 API 결과와 중복인지 확인 (부분 문자열 기준)"""
    norm = _normalize(llm_title)
    for t in existing_titles:
        t_norm = _normalize(t)
        if norm in t_norm or t_norm in norm:
            return True
    return False


def _to_spot_dict(title: str, address: str, category: str, location: str, **extra) -> dict:
    """네이버 지오코딩으로 좌표를 조회하여 spot dict 반환"""
    sort = "comment" if category in ("맛집", "카페") else "random"
    coord = (
        geocode(f"{location} {title}", sort)     # 1순위: "부산 깡통시장"
        or geocode(title, sort)                  # 2순위: 장소명 단독
        or geocode(f"{address} {title}", sort)   # 3순위: 주소 결합
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
    """LLM 사전 지식으로 유명 관광지를 보완하고 네이버 지오코딩으로 좌표를 채워 tourist_spots에 추가"""

    city        = state.get("city", "")
    district    = state.get("district", "")
    preferences = state.get("preferences") or []
    api_spots   = state.get("tourist_spots") or []

    existing_titles = [s.get("title", "") for s in api_spots]
    location = f"{city} {district}".strip()

    system_prompt = f"""
                너는 한국 여행 전문가야. 아래 조건에 맞춰 지정된 지역의 관광지를 추천해줘.

                ### 1. 여행 조건
                - 여행 지역: {location}
                - 선호 스타일: {', '.join(preferences) if preferences else '제한 없음'}

                ### 2. 추천 구성
                - 관광지 10개 (category: 자연경관 / 역사·문화 / 체험·액티비티 / 쇼핑·시장 / 야경·전망대 중 하나)

                ### 3. 필수 준수 조건 (위반 시 실패)
                - [존재성]: 실제로 존재하는 장소만 추천할 것 (지어내거나 폐업한 곳 절대 금지). 지역 내 대표 명소 위주로 추천.
                - [상호명 제한]: title은 반드시 공식 고유명사로 작성할 것. '야경 명소', '힐링 스팟' 같은 설명형 이름은 절대 금지.
                * 올바른 예: "해동용궁사", "감천문화마을", "광안대교"
                * 잘못된 예: "부산 야경명소", "김포 힐링 공원"
                - [텍스트 제한]: title과 address에 한자(漢字) 사용 절대 금지. 반드시 한글과 숫자, 영문으로만 작성할 것.
                - [주소 조건]: address는 네이버 지도 검색창에 입력했을 때 해당 장소가 바로 나오는 검색어로 작성할 것.
                * 잘못된 예: "부산광역시 기장군 기장읍" (단순 행정구역명 금지)
                    """

    llm = ChatGroq(
        model="llama-3.3-70b-versatile",
        api_key=os.getenv("GROQ_API_KEY"),
    )

    try:
        result: FamousSpotList = llm.with_structured_output(FamousSpotList).invoke([
            SystemMessage(content=system_prompt),
            HumanMessage(content=f"{location} 관광지 10개 추천해줘."),
        ])
        llm_spots = result.spots
    except Exception:
        return {"tourist_spots": api_spots}

    # 중복 제거
    unique_spots = [s for s in llm_spots if not _is_duplicate(s.title, existing_titles)]
    for s in unique_spots:
        existing_titles.append(s.title)

    # must_visit 중 미등록 장소
    must_visit   = state.get("must_visit") or []
    unresolved_mv = [p for p in must_visit if not _is_duplicate(p, existing_titles)]
    for p in unresolved_mv:
        existing_titles.append(p)

    # 병렬 지오코딩 (LLM 스팟 + must_visit 동시 처리)
    def resolve_llm(spot: FamousSpot) -> dict:
        return _to_spot_dict(spot.title, spot.address, spot.category, location,
                             llm_suggested=True)

    def resolve_mv(place: str) -> dict:
        return _to_spot_dict(place, f"{location} {place}", "필수방문", location,
                             llm_suggested=True, must_visit=True)

    tasks_llm = [(resolve_llm, s) for s in unique_spots]
    tasks_mv  = [(resolve_mv,  p) for p in unresolved_mv]
    all_tasks = tasks_llm + tasks_mv

    if all_tasks:
        with ThreadPoolExecutor(max_workers=8) as executor:
            new_spots = list(executor.map(lambda t: t[0](t[1]), all_tasks))
    else:
        new_spots = []

    valid_spots = [
        s for s in new_spots
        if s.get("mapx") and s.get("mapy") or s.get("must_visit")
    ]
    return {"tourist_spots": api_spots + valid_spots}
