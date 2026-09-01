from typing import Annotated, List, TypedDict, Optional
from langgraph.graph import add_messages
from langchain_core.messages import BaseMessage

class InputState(TypedDict):
    """1. 입력 스키마"""
    question : str


class TravelState(InputState):
    """2. 대화 관리(private)"""
    user_name : str                 # user의 닉네임
    user_id : str                   # 대화 아이디
    messages: Annotated[list[BaseMessage], add_messages]  # MessagesState 역할 대체
    city: Optional[str]      # 목적지 (예: 제주시, 부산시)
    districts : Optional[List[str]]  # 목적지 세부 지역 리스트 (예: ["애월읍"], ["남포동", "송도"])
    traveldates: Optional[str]     # 여행 일정 "YYYY-MM-DD ~ YYYY-MM-DD" (파싱은 _dates.parse_range)
    budget: Optional[int]           # 예산 범위
    preferences: List[str]          # 선호도 (예: ["자연", "맛집", "쇼핑"])
    must_visit: Optional[List[str]] # 사용자가 꼭 가고 싶다고 강조한 장소
    current_step: Annotated[str, lambda old, new: new]  # 현재 대화 단계

    plan_title: Optional[str]               # Optimizer가 생성한 여행 제목
    itinerary: List[str]                    # 확정된 일정 리스트 (일자별)
    itinerary_history: Optional[List[dict]] # 최근 3개 버전 스냅샷 (투표용)

    itinerary_feedback: Optional[str]       # "1일차 카페 삭제, 2일차 저녁 식당 변경" 등 세부 요구사항

    estimated_cost: Optional[dict]          # 예상 경비 breakdown
    accommodations: Optional[List[dict]]   # 검색된 숙소 목록
    tourist_spots: Optional[List[dict]]   # 검색된 관광지 목록
    restaurants: Optional[List[dict]]     # 검색된 식당 목록
    cafes: Optional[List[dict]]           # 검색된 카페 목록
    origin_city: Optional[str]            # 사용자 출발지 (예: 서울, 부산)
    transport_routes: Optional[List[dict]] # 가는편(출발지→목적지) 교통 경로
    transport_return_routes: Optional[List[dict]] # 오는편(목적지→출발지) 교통 경로
    num_people: Optional[int]             # 여행 인원 수
    last_asked_field: Optional[str]      # 마지막으로 봇이 물어본 필드명
    selected_acc: Optional[dict]         # Optimizer가 선택한 숙소
    room_combination: Optional[List[dict]]  # DP로 계산한 최적 객실 조합



class OutputState(TypedDict):
    """3. 출력 스키마"""
    answer: str

