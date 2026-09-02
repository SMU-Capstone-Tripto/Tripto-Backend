from sqlalchemy import Boolean, Column, Date, Integer, JSON, String, ForeignKey
from sqlalchemy.orm import relationship

from app.models.base import Base, TimestampMixin


class Travel(Base, TimestampMixin):

    __tablename__ = "travels"

    travel_id = Column(Integer, primary_key=True, autoincrement=True)
    owner_id = Column(Integer, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True)
    title = Column(String(100), nullable=False, comment="여행 제목")
    destination = Column(String(100), nullable=False, comment="목적지 (예: 제주도, 파리)")
    start_date = Column(Date, nullable=False, comment="여행 시작일")
    end_date = Column(Date, nullable=False, comment="여행 종료일")
    share_token = Column(String(36), unique=True, nullable=True, index=True, comment="공개 공유 토큰 (UUID)")
    # AI가 생성해 투표로 확정된 일정 원문(일자별 텍스트 리스트). 수동 생성 여행은 NULL.
    itinerary = Column(JSON, nullable=True, comment="AI 일정 원문 (일자별 텍스트)")

    owner = relationship("User", foreign_keys=[owner_id], back_populates="travels")
    schedules = relationship(
        "Schedule",
        back_populates="travel",
        cascade="all, delete-orphan",
        order_by="Schedule.day_number, Schedule.order_index",
    )
