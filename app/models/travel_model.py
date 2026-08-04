from sqlalchemy import Boolean, Column, Date, Integer, String, ForeignKey
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

    owner = relationship("User", foreign_keys=[owner_id], back_populates="travels")
    schedules = relationship(
        "Schedule",
        back_populates="travel",
        cascade="all, delete-orphan",
        order_by="Schedule.day_number, Schedule.order_index",
    )
