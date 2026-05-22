from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime,
    Enum as SAEnum, Text
)
from sqlalchemy.types import TypeDecorator, TEXT
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.models.base import Base
import enum
import json


class JSONList(TypeDecorator):
    """PostgreSQL에서는 ARRAY, SQLite(테스트)에서는 JSON 문자열로 저장"""
    impl = TEXT
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return "[]"
        return json.dumps(value, ensure_ascii=False)

    def process_result_value(self, value, dialect):
        if value is None:
            return []
        try:
            return json.loads(value)
        except (TypeError, ValueError):
            return []


class AuthProvider(str, enum.Enum):
    LOCAL = "local"
    KAKAO = "kakao"
    GOOGLE = "google"


class User(Base):
    __tablename__ = "users"

    user_id = Column(Integer, primary_key=True, index=True)
    # 고유 ID: 숫자+알파벳 6자 (예: aB3k9Z)
    unique_id = Column(String(6), unique=True, nullable=False, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    nickname = Column(String(50), nullable=False)
    hashed_password = Column(String(255), nullable=True)  # 소셜 로그인은 null
    tags = Column(JSONList, nullable=False, default=list)
    auth_provider = Column(SAEnum(AuthProvider, native_enum=False), nullable=False, default=AuthProvider.LOCAL)
    social_id = Column(String(255), nullable=True)  # 소셜 고유 ID
    is_active = Column(Boolean, default=True, nullable=False)
    is_email_verified = Column(Boolean, default=False, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # 관계
    sent_friend_requests = relationship(
        "Friendship",
        foreign_keys="Friendship.requester_id",
        back_populates="requester",
    )
    received_friend_requests = relationship(
        "Friendship",
        foreign_keys="Friendship.addressee_id",
        back_populates="addressee",
    )