from pydantic import BaseModel, EmailStr, field_validator, Field
from typing import Optional, List
from datetime import datetime
from app.models.user_model import AuthProvider
import re

# ── 회원가입 ──────────────────────────────────────────────
class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    nickname: str
    tags: List[str] = []
    verification_code: str  # 이메일 인증 코드

    @field_validator("password")
    @classmethod
    def validate_password(cls, v: str) -> str:
        password_regex = re.compile( # 비밀번호 검증 정규식(영문 대소문자 + 특수문자 혼합 8자 이상)
            r"^(?=.*[A-Z])(?=.*[a-z])(?=.*\d)(?=.*[@$!%*?&])[A-Za-z\d@$!%*?&]{8,}$"
        )
        if not password_regex.match(v):
            raise ValueError(
                "비밀번호는 영문 대소문자, 숫자, 특수문자(@$!%*?&)를 포함하여 최소 8자 이상이어야 합니다."
            )
        return v

    @field_validator("nickname")
    @classmethod
    def validate_nickname(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 2 or len(v) > 20:
            raise ValueError("닉네임은 2~20자 사이여야 합니다.")
        return v

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, v: List[str]) -> List[str]:
        if len(v) > 10:
            raise ValueError("태그는 최대 10개까지 가능합니다.")
        return [t.strip() for t in v if t.strip()]


# ── 이메일 인증 코드 요청 ──────────────────────────────────
class EmailVerifyRequest(BaseModel):
    email: EmailStr


class EmailVerifyConfirm(BaseModel):
    email: EmailStr
    code: str


# ── 로그인 ────────────────────────────────────────────────
class LoginRequest(BaseModel):
    email: EmailStr
    password: str


# ── 토큰 ──────────────────────────────────────────────────
class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshTokenRequest(BaseModel):
    refresh_token: str


# ── 소셜 로그인 ────────────────────────────────────────────
class OAuthCallbackRequest(BaseModel):
    code: str
    state: Optional[str] = None


# ── 사용자 응답 ────────────────────────────────────────────
class UserResponse(BaseModel):
    user_id: int
    unique_id: str
    nickname: str
    auth_provider: AuthProvider
    email: str
    tags: List[str]
    social_id: Optional[str] = None  
    is_active: bool
    is_email_verified: bool
    created_at: datetime

    model_config = {"from_attributes": True}

class UserUpdateRequest(BaseModel):
    nickname: Optional[str] = None
    tags: Optional[List[str]] = None

# 비밀번호 변경(로그인 환경에서) 
class PasswordChangeRequest(BaseModel):
    old_password: str
    new_password: str
    verification_code: str

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, v: str) -> str:
        password_regex = re.compile(
            r"^(?=.*[A-Z])(?=.*[a-z])(?=.*\d)(?=.*[@$!%*?&])[A-Za-z\d@$!%*?&]{8,}$"
        )
        if not password_regex.match(v):
            raise ValueError(
                "새 비밀번호는 영문 대소문자, 숫자, 특수문자(@$!%*?&)를 포함하여 최소 8자 이상이어야 합니다."
            )
        return v

# 비밀번호 변경(비로그인 환경에서) 
class PasswordResetRequest(BaseModel):
    email: EmailStr = Field(..., description="계정 이메일 주소")
    verification_code: str = Field(..., description="6자리 인증 코드")
    new_password: str = Field(..., min_length=8, description="변경할 비밀번호 (영문 대소문자 + 특수문자 혼합 8자 이상)")