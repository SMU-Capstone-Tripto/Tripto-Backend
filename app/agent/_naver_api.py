import os
import time
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
TRANSIT_URL = "https://maps.apigw.ntruss.com/map-direction-15/v1/transit"


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


def _build_transit_summary(legs: list) -> str:
    """legs 목록에서 '273번 버스 탑승(15분) → 도보(5분)' 형태의 상세 요약 생성"""
    parts = []
    for leg in legs:
        mode  = leg.get("mode", "")
        route = leg.get("route", "")
        sec   = int(leg.get("sectionTime", 0))
        mins  = sec // 60

        if mode == "WALK":
            if mins >= 2:  # 2분 미만 도보는 생략
                parts.append(f"도보({mins}분)")
        elif mode == "BUS" and route:
            t = f"({mins}분)" if mins else ""
            parts.append(f"{route}번 버스 탑승{t}")
        elif mode == "SUBWAY" and route:
            t = f"({mins}분)" if mins else ""
            parts.append(f"{route} 탑승{t}")
        elif mode == "EXPRESSBUS" and route:
            t = f"({mins}분)" if mins else ""
            parts.append(f"{route} 고속버스{t}")
    return " → ".join(parts)


def search_route(
    start_lon: float, start_lat: float,
    end_lon:   float, end_lat:   float,
) -> dict | None:
    """
    두 좌표 간 경로 탐색. 대중교통 우선, 실패 시 자동차 경로.
    Returns: {"time": int(분), "distance": float(km), "fare": int(원), "type": str, "summary": str}
    """
    # 대중교통 시도
    try:
        resp = requests.get(
            TRANSIT_URL,
            params={
                "start":         f"{start_lon},{start_lat}",
                "goal":          f"{end_lon},{end_lat}",
                "departureTime": int(time.time() * 1000),
            },
            headers=_headers(),
            timeout=8,
        )
        resp.raise_for_status()
        paths = resp.json().get("paths", [])
        if paths:
            best = min(paths, key=lambda p: p.get("duration", 99999))
            return {
                "time":     max(1, best["duration"] // 60),
                "distance": round(best.get("distance", 0) / 1000, 1),
                "fare":     best.get("fare", {}).get("public", {}).get("total", 0),
                "type":     "transit",
                "summary":  _build_transit_summary(best.get("legs", [])),
            }
    except Exception:
        pass

    # 자동차 경로 fallback
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
