import os
import requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

_KEY_ID = os.getenv("NAVER_MAPS_ID", "")
_KEY    = os.getenv("NAVER_MAPS", "")

_KEY_ID_S = os.getenv("NAVER_SEARCH_ID", "")
_KEY_S    = os.getenv("NAVER_SEARCH", "")

GEOCODE_URL = "https://openapi.naver.com/v1/search/local"
DRIVING_URL = "https://maps.apigw.ntruss.com/map-direction/v1/driving"


def _headers() -> dict:
    return {
        "X-NCP-APIGW-API-KEY-ID": _KEY_ID,
        "X-NCP-APIGW-API-KEY":    _KEY,
    }

def _headers_s() -> dict:
    return {
        "X-Naver-Client-Id":     _KEY_ID_S,
        "X-Naver-Client-Secret": _KEY_S,
    }

# 여행 일정에서 배제할 프랜차이즈/체인 (관광지·식당·카페 공통).
# 제목 정규화(공백 제거·소문자) 후 substring 매칭하므로 대표 키워드만.
_FRANCHISE_KEYWORDS = {
    # 카페·베이커리·디저트
    "스타벅스", "투썸플레이스", "투썸", "이디야", "메가커피", "메가엠지씨", "메가mgc",
    "빽다방", "컴포즈커피", "컴포즈", "커피빈", "폴바셋", "탐앤탐스", "할리스",
    "엔젤리너스", "파스쿠찌", "카페베네", "요거프레소", "더벤티", "매머드커피", "매머드익스프레스",
    "감성커피", "커피에반하다", "백억커피", "토프레소", "드롭탑", "셀렉토커피", "더리터",
    "공차", "쥬씨", "스무디킹", "설빙", "던킨", "배스킨라빈스", "베스킨라빈스",
    "파리바게뜨", "파리바게트", "뚜레쥬르", "뚜레주르",
    # 패스트푸드·버거
    "맥도날드", "버거킹", "롯데리아", "kfc", "맘스터치", "서브웨이", "노브랜드버거", "프랭크버거",
    # 치킨
    "교촌치킨", "교촌", "bbq", "bhc", "굽네치킨", "굽네", "페리카나", "네네치킨", "네네",
    "처갓집", "60계", "푸라닭", "지코바", "또래오래", "호식이두마리치킨", "자담치킨",
    "노랑통닭", "오빠닭", "아웃닭", "티바두마리치킨", "부어치킨",
    # 분식·한식·기타 체인
    "김밥천국", "김가네", "바르다김선생", "고봉민김밥", "죠스떡볶이", "신전떡볶이", "엽기떡볶이",
    "두끼", "한솥", "본죽", "본도시락", "홍콩반점", "새마을식당", "한신포차", "명륜진사갈비",
    "놀부", "채선당", "큰맘할매순대국", "유가네", "원할머니보쌈", "박가부대", "두찜", "하남돼지집",
    # 피자
    "미스터피자", "도미노피자", "도미노", "피자헛", "파파존스", "피자스쿨", "피자마루", "반올림피자",
    # 패밀리레스토랑·뷔페
    "빕스", "아웃백", "애슐리", "자연별곡", "계절밥상",
}


def is_franchise(title: str) -> bool:
    """상호명이 프랜차이즈/체인점이면 True (여행 일정에서 배제용)."""
    t = (title or "").replace(" ", "").lower()
    return any(kw in t for kw in _FRANCHISE_KEYWORDS)


def poi_kind(category: str) -> str:
    """네이버 지역검색 category 문자열 → 대분류.
    반환: 'restaurant' | 'cafe' | 'bar' | 'attraction' | 'activity' | 'lodging' | 'other'

    네이버 category는 '음식점>한식'뿐 아니라 '한식>생선회', '카페,디저트>카페',
    '스포츠,오락>행글라이딩,패러글라이딩'처럼 형태가 제각각이라 substring 매칭으로 판정한다.
    카페·술집은 '음식점>' 하위로도 오므로 음식점 판정보다 먼저 검사한다."""
    c = category or ""
    if any(k in c for k in ("카페", "디저트", "베이커리", "제과", "빙수", "커피전문")):
        return "cafe"
    if any(k in c for k in ("술집", "호프", "이자카야", "요리주점", "포장마차", "와인바", "칵테일", "펍")):
        return "bar"
    if any(k in c for k in ("숙박", "호텔", "모텔", "펜션", "게스트하우스", "리조트", "민박", "콘도")):
        return "lodging"
    if any(k in c for k in ("스포츠", "오락", "레저", "레포츠", "체험", "액티비티", "테마파크",
                            "패러글라이딩", "행글라이딩", "짚라인", "카약", "서핑", "승마",
                            "카트", "요트", "스쿠버", "번지")):
        return "activity"
    if any(k in c for k in ("음식점", "한식", "중식", "일식", "양식", "분식", "아시아음식",
                            "뷔페", "치킨", "피자", "햄버거", "패스트푸드", "고기", "구이",
                            "생선회", "해물", "해산물", "찜", "탕", "국수", "국밥", "죽",
                            "족발", "보쌈", "요리", "food")):
        return "restaurant"
    if any(k in c for k in ("관광", "명소", "공원", "박물관", "미술관", "전시", "유적", "유원지",
                            "산", "해수욕장", "해변", "폭포", "계곡", "전망대", "동물원",
                            "식물원", "수목원", "사찰", "성당", "교회", "시장", "거리",
                            "다리", "항구", "등대", "정원", "섬", "호수")):
        return "attraction"
    return "other"


def search_local(query: str, display: int = 20, sort: str = "comment") -> list[dict]:
    """네이버 지역 검색 → 결과 items 반환 (title의 <b> 태그 제거, 좌표 변환 포함)"""
    import re
    _TAG = re.compile(r"<[^>]+>")
    try:
        resp = requests.get(
            GEOCODE_URL,
            params={"query": query, "display": display, "sort": sort},
            headers=_headers_s(),
            timeout=5,
        )
        resp.raise_for_status()
        items = resp.json().get("items", [])
        result = []
        for item in items:
            mapx = item.get("mapx", "")
            mapy = item.get("mapy", "")
            if not mapx or not mapy:
                continue
            result.append({
                "title":    _TAG.sub("", item.get("title", "")),
                "address":  item.get("roadAddress") or item.get("address", ""),
                "tel":      item.get("telephone", ""),
                "image":    "",
                "content_id": "",
                "category": item.get("category", ""),
                "mapx":     str(int(mapx) / 1e7),
                "mapy":     str(int(mapy) / 1e7),
            })
        return result
    except Exception:
        return []


def geocode(query: str, sort: str = "random") -> tuple[float, float] | None:
    """장소명 또는 주소 → (위도, 경도). 실패 시 None. sort: random(정확도순) | comment(리뷰순)"""
    try:
        resp = requests.get(
            GEOCODE_URL,
            params={"query": query, "sort": sort},
            headers=_headers_s(),
            timeout=5,
        )
        resp.raise_for_status()
        items = resp.json().get("items", [])
        if not items:
            return None
        lat = int(items[0]["mapy"]) / 1e7
        lon = int(items[0]["mapx"]) / 1e7
        return lat, lon
    except Exception:
        return None


def search_route(
    start_lon: float, start_lat: float,
    end_lon:   float, end_lat:   float,
) -> dict | None:
    """
    두 좌표 간 자동차 경로 탐색 (택시요금 산정용).
    Returns: {"time": int(분), "distance": float(km), "fare": int(taxiFare 원), "type": "driving", "summary": ""}
    ※ NCP Directions API에는 대중교통 길찾기가 없다 → 대중교통은 _odsay_api.search_transit 사용.
    """
    try:
        resp = requests.get(
            DRIVING_URL,
            params={
                "start":  f"{start_lon},{start_lat}",
                "goal":   f"{end_lon},{end_lat}",
                "option": "traoptimal",
            },
            headers=_headers(),
            timeout=5,
        )
        resp.raise_for_status()
        routes = resp.json().get("route", {}).get("traoptimal", [])
        if not routes:
            return None
        summary = routes[0]["summary"]
        return {
            "time":     max(1, summary["duration"] // 60000),  # ms → min
            "distance": round(summary["distance"] / 1000, 1),
            "fare":     summary.get("taxiFare", 0),
            "type":     "driving",
            "summary":  "",
        }
    except Exception:
        return None
