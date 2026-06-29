from _tago_api import search_transport
from _naver_api import geocode, search_route
from state import TravelState


def Transport_Searcher(state: TravelState) -> dict:
    """TAGO API로 출발지 → 목적지 교통편 검색. 결과 없으면 네이버 대중교통으로 폴백."""

    origin      = state.get("origin_city")
    destination = state.get("city")
    traveldates = state.get("traveldates")

    if not origin or not destination or not traveldates:
        return {"current_step": "searching", "transport_routes": []}

    try:
        routes = search_transport(origin, destination, traveldates)
    except Exception:
        routes = []

    # TAGO 결과 없으면 네이버 대중교통 경로로 폴백 (수도권 근교 등 단거리 목적지)
    if not routes:
        try:
            origin_coord = geocode(origin)
            dest_coord   = geocode(destination)
            if origin_coord and dest_coord:
                result = search_route(
                    origin_coord[1], origin_coord[0],
                    dest_coord[1],   dest_coord[0],
                )
                if result:
                    grade = f"약 {result['time']}분 소요"
                    if result.get("summary"):
                        grade += f" ({result['summary']})"
                    routes.append({
                        "type":     "대중교통",
                        "grade":    grade,
                        "dep_time": "",
                        "arr_time": "",
                        "fare":     result["fare"],
                    })
        except Exception:
            pass

    return {
        "current_step":    "searching",
        "transport_routes": routes,
    }
