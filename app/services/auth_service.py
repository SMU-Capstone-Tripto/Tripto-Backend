from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from fastapi import HTTPException, status
import httpx

from app.models.user_model import User, AuthProvider
from app.schemas.auth_schema import PasswordResetRequest, RegisterRequest, LoginRequest, PasswordChangeRequest
from app.core.security import hash_password, verify_password, create_access_token, create_refresh_token
from app.core.unique_id import generate_unique_id
from app.core.config import settings
from app.services.email_service import verify_email_code
import redis.asyncio as aioredis

http_client = httpx.AsyncClient(timeout=10.0)

# ── 자체 회원가입 ──────────────────────────────────────────
async def register_user(
    data: RegisterRequest,
    db: AsyncSession,
    redis: aioredis.Redis,
) -> User:
    # 이메일 인증 코드 확인
    is_valid = await verify_email_code(redis, data.email, data.verification_code)
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="이메일 인증 코드가 올바르지 않거나 만료되었습니다.",
        )

    # 이메일 중복 확인
    result = await db.execute(select(User).where(User.email == data.email))
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="이미 사용 중인 이메일입니다.",
        )

    unique_id = await generate_unique_id(db)

    user = User(
        email=data.email,
        unique_id=unique_id,
        nickname=data.nickname,
        hashed_password=hash_password(data.password),
        tags=data.tags,
        auth_provider=AuthProvider.LOCAL,
        is_email_verified=True,
        is_active=True,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return user


# ── 자체 로그인 ────────────────────────────────────────────
async def login_user(data: LoginRequest, db: AsyncSession) -> dict:
    result = await db.execute(select(User).where(User.email == data.email))
    user = result.scalar_one_or_none()

    if not user or not user.hashed_password:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="이메일 또는 비밀번호가 올바르지 않습니다.",
        )
    if not verify_password(data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="이메일 또는 비밀번호가 올바르지 않습니다.",
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="비활성화된 계정입니다.",
        )

    token_data = {"sub": str(user.user_id)}
    return {
        "access_token": create_access_token(token_data),
        "refresh_token": create_refresh_token(token_data),
        "token_type": "bearer",
    }


# ── 소셜 로그인 공통 ───────────────────────────────────────
async def _get_or_create_social_user(
    db: AsyncSession,
    email: str,
    social_id: str,
    nickname: str,
    provider: AuthProvider,
) -> User:
    # 소셜 ID로 기존 유저 조회
    result = await db.execute(
        select(User).where(User.social_id == social_id, User.auth_provider == provider)
    )
    user = result.scalar_one_or_none()

    if user:
        return user

    # 이메일로 기존 유저 조회 (다른 provider로 이미 가입한 경우)
    result = await db.execute(select(User).where(User.email == email))
    existing = result.scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"이미 {existing.auth_provider.value} 계정으로 가입된 이메일입니다.",
        )

    unique_id = await generate_unique_id(db)
    user = User(
        email=email,
        unique_id=unique_id,
        nickname=nickname,
        social_id=social_id,
        auth_provider=provider,
        is_email_verified=True,
        is_active=True,
        tags=[],
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    return user


# ── 카카오 OAuth ───────────────────────────────────────────
async def kakao_login(code: str, db: AsyncSession) -> dict:
    
    token_res = await http_client.post(
        "https://kauth.kakao.com/oauth/token",
        data={
            "grant_type": "authorization_code",
            "client_id": settings.KAKAO_CLIENT_ID,
            "redirect_uri": settings.KAKAO_REDIRECT_URI,
            "code": code,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    
    if token_res.status_code != 200:
        raise HTTPException(status_code=400, detail="카카오 토큰 발급 실패")

    kakao_token = token_res.json()
    access_token = kakao_token["access_token"]

    user_res = await http_client.get(
        "https://kapi.kakao.com/v2/user/me",
        headers={"Authorization": f"Bearer {access_token}"},
    )
    
    if user_res.status_code != 200:
        raise HTTPException(status_code=400, detail="카카오 사용자 정보 조회 실패")

    kakao_user = user_res.json()

    kakao_account = kakao_user.get("kakao_account", {})
    profile = kakao_account.get("profile", {})

    email = kakao_account.get("email", f"kakao_{kakao_user['id']}@kakao.local")
    nickname = profile.get("nickname", f"카카오유저{kakao_user['id']}")
    social_id = str(kakao_user["id"])

    user = await _get_or_create_social_user(
        db, email, social_id, nickname, AuthProvider.KAKAO
    )
    token_data = {"sub": str(user.user_id)}
    return {
        "access_token": create_access_token(token_data),
        "refresh_token": create_refresh_token(token_data),
        "token_type": "bearer",
    }


# ── 구글 OAuth ─────────────────────────────────────────────
async def google_login(code: str, db: AsyncSession) -> dict:
    
    token_res = await http_client.post(
        "https://oauth2.googleapis.com/token",
        data={
            "code": code,
            "client_id": settings.GOOGLE_CLIENT_ID,
            "client_secret": settings.GOOGLE_CLIENT_SECRET,
            "redirect_uri": settings.GOOGLE_REDIRECT_URI,
            "grant_type": "authorization_code",
        },
    )
    
    if token_res.status_code != 200:
        raise HTTPException(status_code=400, detail="구글 토큰 발급 실패")
    google_token = token_res.json()

    user_res = await http_client.get(
        "https://www.googleapis.com/oauth2/v2/userinfo",
        headers={"Authorization": f"Bearer {google_token['access_token']}"},
    )
    
    if user_res.status_code != 200:
        raise HTTPException(status_code=400, detail="구글 사용자 정보 조회 실패")
    google_user = user_res.json()

    email = google_user["email"]
    nickname = google_user.get("name", email.split("@")[0])
    social_id = google_user["id"]

    user = await _get_or_create_social_user(
        db, email, social_id, nickname, AuthProvider.GOOGLE
    )
    token_data = {"sub": str(user.user_id)}
    return {
        "access_token": create_access_token(token_data),
        "refresh_token": create_refresh_token(token_data),
        "token_type": "bearer",
    }

# 비밀번호 변경(로그인 환경)
async def change_user_password(
    db: AsyncSession,
    redis: aioredis.Redis,
    current_user: User,
    data: PasswordChangeRequest
    
) -> None:
    # 소셜 로그인 유저는 자체 비밀번호가 없으므로 차단
    if current_user.auth_provider != AuthProvider.LOCAL or not current_user.hashed_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="소셜 로그인 계정은 비밀번호를 변경할 수 없습니다."
        )
    # 이메일 인증 코드 검증 
    is_valid = await verify_email_code(redis, current_user.email, data.verification_code)
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="이메일 인증 코드가 올바르지 않거나 만료되었습니다."
        )
    # 현재 비밀번호 검증
    if not verify_password(data.old_password, current_user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="현재 비밀번호가 올바르지 않습니다."
        )
    # 새 비밀번호와 기존 비밀번호 비교 
    if data.old_password == data.new_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="기존 비밀번호와 다른 비밀번호를 사용해주세요."
        )
    # 새 비밀번호 해싱 및 저장
    current_user.hashed_password = hash_password(data.new_password)
    await db.flush()

# 비밀번호 변경(비로그인 환경)
async def reset_user_password(db: AsyncSession, redis_client, data: PasswordResetRequest):
    # 이메일 인증 코드 검증
    is_valid = await verify_email_code(redis_client, data.email, data.verification_code)
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="이메일 인증 코드가 올바르지 않거나 만료되었습니다."
        )

    # 유저 존재 여부 확인
    result = await db.execute(select(User).where(User.email == data.email))
    user = result.scalar_one_or_none()
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="가입되지 않은 이메일입니다."
        )

    # 소셜 로그인 유저 예외 처리 
    if user.auth_provider != AuthProvider.LOCAL:  # 프로젝트의 Enum이나 상수 값에 맞게 수정하세요
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"해당 계정은 {user.auth_provider} 연동 계정입니다. 소셜 로그인을 이용해주세요."
        )

    # 새 비밀번호 해싱 후 업데이트
    user.hashed_password = hash_password(data.new_password)
    
    # db.add(user) # SQLAlchemy 버전에 따라 생략 가능
    await db.flush() # 영속성 컨텍스트 반영

async def withdraw_user(
    db: AsyncSession, 
    redis: aioredis.Redis, 
    current_user: User, 
    verification_code: str
):
    is_valid = await verify_email_code(redis, current_user.email, verification_code)
    if not is_valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="이메일 인증 코드가 올바르지 않거나 만료되었습니다."
        )

    result = await db.execute(select(User).where(User.user_id == current_user.user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="이미 탈퇴했거나 존재하지 않는 유저입니다.")

    await db.delete(user)
    await db.commit()

    return {"message": "정상적으로 탈퇴 처리되었습니다."}