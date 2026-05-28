import os
import requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

TAGO_KEY = os.getenv("TAGO_KEY")

EXP_TERMINAL_URL   = "http://apis.data.go.kr/1613000/ExpBusInfo/GetExpBusTrminlList"
EXP_TIMETABLE_URL  = "http://apis.data.go.kr/1613000/ExpBusInfo/GetStrtpntAlocFndExpbusInfo"
SUB_TERMINAL_URL   = "http://apis.data.go.kr/1613000/SuburbsBusInfo/GetSuberbsBusTrminlList"
SUB_TIMETABLE_URL  = "http://apis.data.go.kr/1613000/SuburbsBusInfo/GetStrtpntAlocFndSuberbsBusInfo"
TRAIN_SEARCH_URL   = "http://apis.data.go.kr/1613000/TrainInfo/GetStrtpntAlocFndTrainInfo"
TRAIN_STATION_URL  = "http://apis.data.go.kr/1613000/TrainInfo/GetCtyAcctoTrainSttnList"

# TAGO 기차 도시코드 매핑
_CITY_CODE = {
    "서울": 11, "세종": 12,
    "부산": 21, "대구": 22, "인천": 23, "광주": 24, "대전": 25, "울산": 26,
    "경기": 31, "강원": 32, "충북": 33, "충남": 34,
    "전북": 35, "전남": 36, "경북": 37, "경남": 38,
}


def _to_items(data: dict) -> list:
    items = data.get("response", {}).get("body", {}).get("items", {})
    if not items:
        return []
    item = items.get("item", [])
    return [item] if isinstance(item, dict) else item


def _get_terminal_id(city: str, url: str) -> str | None:
    """도시명으로 버스 터미널 ID 조회"""
    params = {
        "serviceKey": TAGO_KEY,
        "numOfRows": "10",
        "pageNo": "1",
        "_type": "json",
        "terminalNm": city,
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        items = _to_items(resp.json())
        return items[0].get("terminalId") if items else None
    except Exception:
        return None


_node_id_cache: dict[str, str | None] = {}


def _get_train_node_id(city: str) -> str | None:
    """도시명으로 기차 역 nodeid 조회 (세션 내 캐싱으로 중복 API 호출 방지)"""
    city_clean = city.replace("역", "").strip()

    if city_clean in _node_id_cache:
        return _node_id_cache[city_clean]

    city_code = None
    for key, code in _CITY_CODE.items():
        if key in city_clean:
            city_code = code
            break
    if city_code is None:
        _node_id_cache[city_clean] = None
        return None

    params = {
        "serviceKey": TAGO_KEY,
        "numOfRows": "50",
        "pageNo": "1",
        "_type": "json",
        "cityCode": str(city_code),
    }
    try:
        resp = requests.get(TRAIN_STATION_URL, params=params, timeout=10)
        resp.raise_for_status()
        items = _to_items(resp.json())
        # 도시명과 일치하는 역 우선, 없으면 첫 번째 반환
        node_id = None
        for item in items:
            if item.get("nodename") == city_clean:
                node_id = item.get("nodeid")
                break
        if node_id is None and items:
            node_id = items[0].get("nodeid")
        _node_id_cache[city_clean] = node_id
        return node_id
    except Exception:
        return None


def _get_timetable(dep_id: str, arr_id: str, dep_date: str, url: str) -> list:
    """출발/도착 터미널 ID로 버스 시간표 조회 (dep_date: yyyymmdd)"""
    params = {
        "serviceKey": TAGO_KEY,
        "numOfRows": "5",
        "pageNo": "1",
        "_type": "json",
        "depTerminalId": dep_id,
        "arrTerminalId": arr_id,
        "depPlandTime": dep_date,
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        return _to_items(resp.json())
    except Exception:
        return []


def _parse_date(traveldates: str) -> str:
    """'2026-05-20 ~ 2026-05-23' → '20260520'"""
    try:
        start = traveldates.split("~")[0].strip()
        return start.replace("-", "")
    except Exception:
        return ""


def search_express_bus(origin: str, destination: str, dep_date: str) -> list:
    """고속버스 시간표 조회"""
    dep_id = _get_terminal_id(origin, EXP_TERMINAL_URL)
    arr_id = _get_terminal_id(destination, EXP_TERMINAL_URL)
    if not dep_id or not arr_id:
        return []

    return [
        {
            "type": "고속버스",
            "grade": item.get("gradeNm", ""),
            "dep_time": item.get("depPlandTime", ""),
            "arr_time": item.get("arrPlandTime", ""),
            "fare": item.get("charge", 0),
        }
        for item in _get_timetable(dep_id, arr_id, dep_date, EXP_TIMETABLE_URL)
    ]


def search_suburbs_bus(origin: str, destination: str, dep_date: str) -> list:
    """시외버스 시간표 조회"""
    dep_id = _get_terminal_id(origin, SUB_TERMINAL_URL)
    arr_id = _get_terminal_id(destination, SUB_TERMINAL_URL)
    if not dep_id or not arr_id:
        return []

    return [
        {
            "type": "시외버스",
            "dep_time": item.get("depPlandTime", ""),
            "arr_time": item.get("arrPlandTime", ""),
            "fare": item.get("charge", 0),
        }
        for item in _get_timetable(dep_id, arr_id, dep_date, SUB_TIMETABLE_URL)
    ]


def search_train(origin: str, destination: str, dep_date: str) -> list:
    """열차(KTX 포함) 시간표 조회"""
    dep_id = _get_train_node_id(origin)
    arr_id = _get_train_node_id(destination)
    if not dep_id or not arr_id:
        return []

    params = {
        "serviceKey": TAGO_KEY,
        "numOfRows": "5",
        "pageNo": "1",
        "_type": "json",
        "depPlaceId": dep_id,
        "arrPlaceId": arr_id,
        "depPlandTime": dep_date,
    }
    try:
        resp = requests.get(TRAIN_SEARCH_URL, params=params, timeout=10)
        resp.raise_for_status()
        items = _to_items(resp.json())
    except Exception:
        return []

    return [
        {
            "type": "열차",
            "grade": item.get("traingradename", ""),
            "train_no": item.get("trainno", ""),
            "dep_time": item.get("depplandtime", ""),
            "arr_time": item.get("arrplandtime", ""),
            "fare": item.get("adultcharge", 0),
        }
        for item in items
    ]


def search_transport(origin: str, destination: str, traveldates: str) -> list:
    """열차 + 고속버스 + 시외버스 통합 조회"""
    dep_date = _parse_date(traveldates)
    if not dep_date:
        return []

    routes = search_train(origin, destination, dep_date)
    if not routes:
        routes = search_express_bus(origin, destination, dep_date)
    if not routes:
        routes = search_suburbs_bus(origin, destination, dep_date)

    return routes
