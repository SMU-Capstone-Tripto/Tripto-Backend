from sqlalchemy import JSON, Column, Integer, ForeignKey, String, Text
from sqlalchemy.orm import relationship
from app.models.base import Base, TimestampMixin

class ChatRoom(Base, TimestampMixin):
    __tablename__ = "chat_rooms"

    room_id = Column(Integer, primary_key=True, autoincrement=True)
    room_name = Column(String(100), nullable=True)  # 그룹채팅 시 방 이름
    owner_id = Column(Integer, ForeignKey("users.user_id"), nullable=False)
    member_ids = Column(JSON, default=[], nullable=False)
    
    owner_rel = relationship("User", back_populates="owned_chat_rooms")

    members = relationship("ChatRoomMember", back_populates="room", cascade="all, delete-orphan")
    messages = relationship("ChatMessage", back_populates="room", cascade="all, delete-orphan")

class ChatRoomMember(Base):
    __tablename__ = "chat_room_members"

    member_id = Column(Integer, primary_key=True, autoincrement=True)
    room_id = Column(Integer, ForeignKey("chat_rooms.room_id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True)

    room = relationship("ChatRoom", back_populates="members")
    user = relationship("User")
    last_read_message_id = Column(Integer, ForeignKey("chat_messages.message_id", ondelete="SET NULL"), nullable=True)

class ChatMessage(Base, TimestampMixin):
    __tablename__ = "chat_messages"

    message_id = Column(Integer, primary_key=True, autoincrement=True)
    room_id = Column(Integer, ForeignKey("chat_rooms.room_id", ondelete="CASCADE"), nullable=False, index=True)
    sender_id = Column(Integer, ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False)
    content = Column(Text, nullable=False)

    room = relationship("ChatRoom", back_populates="messages")
    sender = relationship("User")
    message_type = Column(String(10), default="text") # 'text' 또는 'image' 구분