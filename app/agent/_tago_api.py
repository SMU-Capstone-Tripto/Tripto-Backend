import os
import re
import requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

# "여수시" / "강릉시" / "서울특별시" 등 행정 접미사 제거용
_ADMIN_SUFFIX = re.compile(r'(특별자치시|특별자치도|광역시|특별시|자치시|자치도|시|군|구|도)$')


def _city_core(name: str) -> str:
    """'여수시' → '여수', '서울특별시' → '서울'.
    접미사를 떼서 2글자 미만이 되면('대구'→'대') 원본을 유지한다."""
    n = (name or '').strip()
    stripped = _ADMIN_SUFFIX.sub('', n)
    return stripped if len(stripped) >= 2 else n

TAGO_KEY = os.getenv("TAGO_KEY")

EXP_TERMINAL_URL   = "http://apis.data.go.kr/1613000/ExpBusInfo/GetExpBusTrminlList"
EXP_TIMETABLE_URL  = "http://apis.data.go.kr/1613000/ExpBusInfo/GetStrtpntAlocFndExpbusInfo"
SUB_TERMINAL_URL   = "http://apis.data.go.kr/1613000/SuburbsBusInfo/GetSuberbsBusTrminlList"
SUB_TIMETABLE_URL  = "http://apis.data.go.kr/1613000/SuburbsBusInfo/GetStrtpntAlocFndSuberbsBusInfo"
TRAIN_SEARCH_URL   = "http://apis.data.go.kr/1613000/TrainInfo/GetStrtpntAlocFndTrainInfo"
TRAIN_STATION_URL  = "http://apis.data.go.kr/1613000/TrainInfo/GetCtyAcctoTrainSttnList"

# TAGO 기차 도시코드 매핑 (광역시·도 단위)
_CITY_CODE = {
    "서울": 11, "세종": 12,
    "부산": 21, "대구": 22, "인천": 23, "광주": 24, "대전": 25, "울산": 26,
    "경기": 31, "강원": 32, "충북": 33, "충남": 34,
    "전북": 35, "전남": 36, "경북": 37, "경남": 38,
}

# 도시명 → 실제 열차역명 별칭.
# 한 도시에 역이 여러 개라 이름만으로는 KTX 정차역을 고를 수 없는 경우에만 등록한다.
# (예: "대구"로 조회하면 KTX 미정차역인 대구역이 잡혀 요금이 틀어진다 → 동대구로 강제)
_TRAIN_STATION_ALIAS = {
    "대구": "동대구",
    "광주": "광주송정",
    "천안": "천안아산",
    "울산": "울산",
}

# 시·군 도시명 → 광역시·도 키 매핑 (TAGO cityCode 조회용)
_CITY_TO_PROVINCE_KEY = {
    "경주": "경북", "포항": "경북", "안동": "경북", "구미": "경북",
    "창원": "경남", "진주": "경남", "통영": "경남", "거제": "경남", "마산": "경남", "거창": "경남",
    "전주": "전북", "군산": "전북", "익산": "전북", "남원": "전북", "정읍": "전북",
    "여수": "전남", "순천": "전남", "목포": "전남", "광양": "전남", "나주": "전남",
    "춘천": "강원", "강릉": "강원", "원주": "강원", "속초": "강원", "동해": "강원", "삼척": "강원",
    "청주": "충북", "충주": "충북", "제천": "충북",
    "천안": "충남", "공주": "충남", "보령": "충남", "아산": "충남", "서산": "충남",
}


def _to_items(data: dict) -> list:
    items = data.get("response", {}).get("body", {}).get("items", {})
    if not items:
        return []
    item = items.get("item", [])
    return [item] if isinstance(item, dict) else item


def _get_terminal_id(city: str, url: str) -> str | None:
    """도시명으로 버스 터미널 ID 조회 ('여수시' 등 접미사 제거 후 조회)"""
    params = {
        "serviceKey": TAGO_KEY,
        "numOfRows": "10",
        "pageNo": "1",
        "_type": "json",
        "terminalNm": _city_core(city),
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
    """도시명으로 기차 역 nodeid 조회 (세션 내 캐싱으로 중복 API 호출 방지).
    '여수시' 같은 행정 접미사가 붙어도 매칭되도록 core로 정규화한다."""
    # 끝의 '역'만 제거 ('여수역'→'여수'). 전체 치환은 '대구광역시'→'대구광시'처럼 망가뜨림.
    core = _city_core(re.sub(r'역$', '', (city or '').strip()))

    if core in _node_id_cache:
        return _node_id_cache[core]

    city_code = None
    for key, code in _CITY_CODE.items():
        if key in core:
            city_code = code
            break
    # 광역시·도 직접 매핑 실패 시 시·군 → 도 매핑으로 재시도
    if city_code is None:
        province_key = _CITY_TO_PROVINCE_KEY.get(core)
        if province_key:
            city_code = _CITY_CODE.get(province_key)
    if city_code is None:
        _node_id_cache[core] = None
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
    except Exception:
        return None

    node_id = _match_station(core, items)
    _node_id_cache[core] = node_id
    return node_id


def _match_station(city_clean: str, items: list) -> str | None:
    """도시명 → 역 nodeid. 별칭 > 정확일치 > 접두일치 > 부분일치 순.
    엉뚱한 역(도내 첫 번째 역)을 잡느니 None을 반환해 버스 조회로 폴백하게 한다."""
    names = {it.get("nodename"): it.get("nodeid")
             for it in items if it.get("nodename") and it.get("nodeid")}
    if not names:
        return None

    alias = _TRAIN_STATION_ALIAS.get(city_clean)
    if alias and alias in names:
        return names[alias]

    if city_clean in names:
        return names[city_clean]

    # "여수" → "여수EXPO" 처럼 도시명으로 시작하는 역
    for nm, nid in names.items():
        if nm.startswith(city_clean):
            return nid

    for nm, nid in names.items():
        if city_clean in nm or nm in city_clean:
            return nid

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


_DATE_RE = re.compile(r'(\d{4})\D+(\d{1,2})\D+(\d{1,2})')


def parse_dates(traveldates) -> tuple[str, str]:
    """'2026-05-20 ~ 2026-05-23' / '2026.05.20~2026.05.23' / dict 등 → ('20260520', '20260523').
    구분자(-, ., /, 공백) 무관. 단일 날짜면 (d, d), 파싱 실패 시 ('', '')."""
    if isinstance(traveldates, dict):
        text = f"{traveldates.get('start', '')} ~ {traveldates.get('end', '')}"
    else:
        text = str(traveldates or "")

    found = _DATE_RE.findall(text)
    if not found:
        return "", ""
    try:
        fmt = lambda t: f"{int(t[0]):04d}{int(t[1]):02d}{int(t[2]):02d}"
        start = fmt(found[0])
        end   = fmt(found[1]) if len(found) > 1 else start
        return start, end
    except (ValueError, IndexError):
        return "", ""


def _station_label(api_name: str | None, city_fallback: str, kind: str) -> str:
    """API가 준 실제 역/터미널명을 우선 사용. 없으면 '도시명+종류'로 폴백.
    'EXPO'는 '엑스포'로, 열차는 '역'을 붙여 실제 표기와 맞춘다 (예: 여수EXPO → 여수엑스포역)."""
    name = (api_name or "").strip()
    if not name:
        return f"{city_fallback}역" if kind == "역" else f"{city_fallback} {kind}"
    name = name.replace("EXPO", "엑스포")
    if kind == "역" and not name.endswith("역"):
        name += "역"
    return name


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
            "dep_station": _station_label(item.get("depPlaceNm"), origin, "고속터미널"),
            "arr_station": _station_label(item.get("arrPlaceNm"), destination, "고속터미널"),
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
            "dep_station": _station_label(item.get("depPlaceNm"), origin, "시외버스터미널"),
            "arr_station": _station_label(item.get("arrPlaceNm"), destination, "시외버스터미널"),
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
        "numOfRows": "30",  # 일부 편성은 요금이 0으로 내려와 유효 요금 확보용으로 넉넉히
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
            "dep_station": _station_label(item.get("depplacename"), origin, "역"),
            "arr_station": _station_label(item.get("arrplacename"), destination, "역"),
            "dep_time": item.get("depplandtime", ""),
            "arr_time": item.get("arrplandtime", ""),
            "fare": item.get("adultcharge", 0),
        }
        for item in items
    ]


def search_transport(origin: str, destination: str, dep_date: str) -> list:
    """열차 + 고속버스 + 시외버스 통합 조회. dep_date: 'yyyymmdd' (한 방향)."""
    if not dep_date or len(dep_date) != 8:
        return []

    routes = search_train(origin, destination, dep_date)
    if not routes:
        routes = search_express_bus(origin, destination, dep_date)
    if not routes:
        routes = search_suburbs_bus(origin, destination, dep_date)

    return routes
