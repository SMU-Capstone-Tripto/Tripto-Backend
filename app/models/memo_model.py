from sqlalchemy import Column, ForeignKey, Integer, Text
from sqlalchemy.orm import relationship

from app.models.base import Base, TimestampMixin


class Memo(Base, TimestampMixin):

    __tablename__ = "memos"

    id = Column(Integer, primary_key=True, autoincrement=True)
    schedule_id = Column(
        Integer,
        ForeignKey("schedules.schedule_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        comment="소속 일정 ID",
    )
    content = Column(Text, nullable=False, comment="메모 내용")

    # Relationships
    schedule = relationship("Schedule", back_populates="memos")