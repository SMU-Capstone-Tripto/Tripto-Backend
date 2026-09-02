from _tago_api import search_transport, parse_dates
from _naver_api import geocode, search_route
from _odsay_api import search_transit
from state import TravelState


def _intercity_fallback(origin: str, destination: str) -> list:
    """TAGO(열차·고속·시외버스)에 결과가 없을 때의 폴백.
    1) ODsay 대중교통 길찾기 → 실제 요금 확보 (근교·비KTX 구간)
    2) 실패 시 네이버 자동차 경로 → 요금은 신뢰 불가라 fare=0 (소요시간만 참고용)"""
    try:
        o = geocode(origin)
        d = geocode(destination)
        if not o or not d:
            return []

        transit = search_transit(o[1], o[0], d[1], d[0], pick="cheap")
        if transit and not transit.get("walk") and transit.get("fare", 0) > 0:
            return [{
                "type":     "대중교통",
                "grade":    transit.get("summary", "") or f"약 {transit['time']}분",
                "dep_time": "",
                "arr_time": "",
                "fare":     transit["fare"],
            }]

        drive = search_route(o[1], o[0], d[1], d[0])
        if drive and drive.get("time", 0) > 0:
            return [{
                "type":     "자동차",
                "grade":    f"약 {drive['time']}분 소요",
                "dep_time": "",
                "arr_time": "",
                "fare":     0,  # 택시요금을 왕복·인원 곱하면 크게 왜곡됨
            }]
        return []
    except Exception:
        return []


def Transport_Searcher(state: TravelState) -> dict:
    """TAGO API로 가는편·오는편 교통편을 각각 검색. 없으면 네이버 대중교통으로 폴백."""

    origin      = state.get("origin_city")
    destination = state.get("city")
    traveldates = state.get("traveldates")

    if not origin or not destination or not traveldates:
        return {
            "current_step": "searching",
            "transport_routes": [],
            "transport_return_routes": [],
        }

    start_date, end_date = parse_dates(traveldates)
    if not start_date:
        return {
            "current_step": "searching",
            "transport_routes": [],
            "transport_return_routes": [],
        }

    # 가는편: 출발일 origin → destination
    try:
        outbound = search_transport(origin, destination, start_date)
    except Exception:
        outbound = []
    if not outbound:
        outbound = _intercity_fallback(origin, destination)

    # 오는편: 마지막날 destination → origin (당일치기면 생략)
    return_routes: list = []
    if end_date and end_date != start_date:
        try:
            return_routes = search_transport(destination, origin, end_date)
        except Exception:
            return_routes = []
        if not return_routes:
            return_routes = _intercity_fallback(destination, origin)

    return {
        "current_step":            "searching",
        "transport_routes":         outbound,
        "transport_return_routes":  return_routes,
    }
