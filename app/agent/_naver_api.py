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
