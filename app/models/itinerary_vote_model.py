import enum

from sqlalchemy import (
    Column, DateTime, Enum, ForeignKey, Integer, JSON, String, UniqueConstraint,
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.models.base import Base, TimestampMixin


class VoteType(str, enum.Enum):
    SOLO = "solo"
    GROUP = "group"


class VoteStatus(str, enum.Enum):
    ACTIVE = "active"
    FINALIZED = "finalized"


class ItinerarySnapshot(Base, TimestampMixin):
    """AI가 생성/수정한 일정 버전 스냅샷 (최대 3개 보관)"""
    __tablename__ = "itinerary_snapshots"

    snapshot_id    = Column(Integer, primary_key=True, autoincrement=True)
    user_id        = Column(Integer, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True)
    version_num    = Column(Integer, nullable=False)
    plan_title     = Column(String(100), nullable=True)
    itinerary      = Column(JSON, nullable=False)       # List[str] — 일별 텍스트
    estimated_cost = Column(JSON, nullable=True)
    selected_acc   = Column(JSON, nullable=True)
    traveldates    = Column(String(50), nullable=True)  # "YYYY-MM-DD ~ YYYY-MM-DD"
    city           = Column(String(50), nullable=True)

    user = relationship("User", foreign_keys=[user_id])


class VoteSession(Base, TimestampMixin):
    """투표 세션 (solo: 본인 선택 / group: 채팅방 멤버 투표)"""
    __tablename__ = "vote_sessions"

    vote_id             = Column(Integer, primary_key=True, autoincrement=True)
    creator_id          = Column(Integer, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True)
    vote_type           = Column(Enum(VoteType, values_callable=lambda x: [e.value for e in x]), nullable=False)
    room_id             = Column(Integer, ForeignKey("chat_rooms.room_id", ondelete="SET NULL"), nullable=True)
    snapshot_ids        = Column(JSON, nullable=False)   # [snap_id, ...]
    status              = Column(Enum(VoteStatus, values_callable=lambda x: [e.value for e in x]), nullable=False, default=VoteStatus.ACTIVE)
    winner_snapshot_id  = Column(Integer, ForeignKey("itinerary_snapshots.snapshot_id", ondelete="SET NULL"), nullable=True)
    # 확정된 일정으로 생성한 Travel. 마감(수동/자동/조회시)이 중복으로 Travel을 만들지 않도록 하는 멱등 가드.
    winner_travel_id    = Column(Integer, ForeignKey("travels.travel_id", ondelete="SET NULL"), nullable=True)
    expires_at          = Column(DateTime(timezone=True), nullable=True)  # group만 사용

    creator        = relationship("User", foreign_keys=[creator_id])
    winner_snapshot = relationship("ItinerarySnapshot", foreign_keys=[winner_snapshot_id])
    records        = relationship("VoteRecord", back_populates="session", cascade="all, delete-orphan")
    # winner_travel_id 는 컬럼만 사용 (관계 매핑 안 함 — Travel 조회는 명시적 select로)


class VoteRecord(Base, TimestampMixin):
    """개별 투표 기록 (1인 1표 보장)"""
    __tablename__ = "vote_records"

    record_id       = Column(Integer, primary_key=True, autoincrement=True)
    vote_session_id = Column(Integer, ForeignKey("vote_sessions.vote_id", ondelete="CASCADE"), nullable=False, index=True)
    voter_id        = Column(Integer, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False)
    snapshot_id     = Column(Integer, ForeignKey("itinerary_snapshots.snapshot_id", ondelete="CASCADE"), nullable=False)

    session  = relationship("VoteSession", back_populates="records")
    voter    = relationship("User", foreign_keys=[voter_id])
    snapshot = relationship("ItinerarySnapshot", foreign_keys=[snapshot_id])

    __table_args__ = (
        UniqueConstraint("vote_session_id", "voter_id", name="uq_one_vote_per_session"),
    )
