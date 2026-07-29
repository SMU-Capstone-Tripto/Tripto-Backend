import enum

from sqlalchemy import Column, Date, Enum, Float, ForeignKey, Integer, String, Time
from sqlalchemy.orm import relationship

from app.models.base import Base, TimestampMixin


class ScheduleCategoryEnum(str, enum.Enum):
    SIGHTSEEING = "관광"
    RESTAURANT = "식사"
    ACCOMMODATION = "숙소"
    TRANSPORT = "이동"
    SHOPPING = "쇼핑"
    ETC = "기타"


class Schedule(Base, TimestampMixin):

    __tablename__ = "schedules"

    schedule_id = Column(Integer, primary_key=True, autoincrement=True)
    travel_id = Column(
        Integer,
        ForeignKey("travels.travel_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="소속 여행 ID",
    )

    # 날짜/순서 정보
    day_number = Column(Integer, nullable=False, comment="몇 일차 (1-based)")
    date = Column(Date, nullable=False, comment="해당 일정 날짜")
    order_index = Column(Integer, nullable=False, default=0, comment="같은 날 내 방문 순서 (0-based)")

    # 장소 정보
    place_name = Column(String(100), nullable=False, comment="장소명")
    place_address = Column(String(255), nullable=True, comment="주소")
    latitude = Column(Float, nullable=True, comment="위도 (지도 뷰)")
    longitude = Column(Float, nullable=True, comment="경도 (지도 뷰)")
    category = Column(
        Enum(ScheduleCategoryEnum),
        nullable=False,
        default=ScheduleCategoryEnum.ETC,
        comment="장소 카테고리",
    )
    cost = Column(Integer, nullable=True, comment="예상 비용 (원, 0=무료, None=미입력)")

    # 시간 정보 (타임라인 뷰)
    start_time = Column(Time, nullable=True, comment="방문 시작 시각")
    end_time = Column(Time, nullable=True, comment="방문 종료 시각")

    # Relationships
    travel = relationship("Travel", back_populates="schedules")
    memos = relationship(
        "Memo",
        back_populates="schedule",
        cascade="all, delete-orphan",
    )