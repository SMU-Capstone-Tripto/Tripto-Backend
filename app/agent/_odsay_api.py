import os
import requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

_ODSAY_KEY  = os.getenv("Odsay", "")
_SEARCH_URL = "https://api.odsay.com/v1/api/searchPubTransPathT"

# 출발지-도착지가 700m 이내라 대중교통 경로를 못 만드는 경우 ODsay가 주는 코드
_TOO_CLOSE_CODES = {"-98", "-8"}

_transit_cache: dict[tuple, dict | None] = {}


def _summarize(subpaths: list) -> str:
    """subPath 목록 → '2호선 → 273번 버스' 형태 요약 (도보 구간 생략).
    구간이 4개 이상이면 요약이 장황해져 '버스+지하철'로 대체."""
    parts = []
    for sp in subpaths:
        tt    = sp.get("trafficType")
        lanes = sp.get("lane") or []
        if tt == 3 or not lanes:  # 3 = 도보
            continue
        if tt == 2:               # 버스 (지방 노선은 busNo에 경유지 설명이 붙어 '(' 앞만 사용)
            no = (lanes[0].get("busNo") or "").split("(")[0].strip()
            parts.append(f"{no}번 버스" if no else "버스")
        elif tt == 1:             # 지하철
            parts.append(lanes[0].get("name", "지하철"))
    if not parts:
        return ""
    if len(parts) >= 4:
        return "버스+지하철"
    return " → ".join(parts)


def search_transit(sx: float, sy: float, ex: float, ey: float) -> dict | None:
    """대중교통(버스+지하철) 길찾기. 좌표는 경도(x)/위도(y).

    Returns:
        {"walk": True}                        출·도착지가 너무 가까워 도보 권장
        {"walk": False, "time", "fare",       유효 경로 (time 분, fare 원)
         "transfers", "summary"}
        None                                  키 없음 / 조회 실패 / 경로 없음
    """
    if not _ODSAY_KEY:
        return None

    key = (round(sx, 5), round(sy, 5), round(ex, 5), round(ey, 5))
    if key in _transit_cache:
        return _transit_cache[key]

    try:
        resp = requests.get(_SEARCH_URL, params={
            "apiKey": _ODSAY_KEY,   # requests가 '/' 등을 URL 인코딩
            "SX": sx, "SY": sy, "EX": ex, "EY": ey,
            "OPT": 0, "SearchPathType": 0, "output": "json",
        }, timeout=8)
        resp.raise_for_status()
        data = resp.json()
    except Exception:
        _transit_cache[key] = None
        return None

    err = data.get("error")
    if err:
        node = err[0] if isinstance(err, list) else err
        result = {"walk": True} if str(node.get("code", "")) in _TOO_CLOSE_CODES else None
        _transit_cache[key] = result
        return result

    paths = (data.get("result") or {}).get("path") or []
    if not paths:
        _transit_cache[key] = None
        return None

    best = min(paths, key=lambda p: p.get("info", {}).get("totalTime", 99999))
    info = best.get("info", {})
    boarded = int(info.get("busTransitCount", 0)) + int(info.get("subwayTransitCount", 0))
    result = {
        "walk":      False,
        "time":      int(info.get("totalTime", 0)),
        "fare":      int(info.get("payment", 0)),
        "transfers": max(0, boarded - 1),
        "summary":   _summarize(best.get("subPath", [])),
    }
    _transit_cache[key] = result
    return result
