from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
import redis.asyncio as aioredis
from datetime import timedelta

from app.core.database import get_async_db
from app.infra.redis_client import get_redis
from app.core.dependencies import get_current_user, bearer_scheme
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
)
from app.core.config import settings
from app.models.user_model import User
from app.schemas.auth_schema import (
    PasswordResetRequest,
    RegisterRequest,
    LoginRequest,
    TokenResponse,
    RefreshTokenRequest,
    EmailVerifyRequest,
    EmailVerifyConfirm,
    UserResponse,
    UserUpdateRequest,
    PasswordChangeRequest,
    WithdrawRequest,
)
from app.services.auth_service import (
    register_user, 
    login_user, 
    kakao_login, 
    google_login, 
    change_user_password, 
    reset_user_password,
    withdraw_user
)
from app.services.email_service import (
    generate_verification_code,
    send_verification_email,
    store_verification_code,
    verify_email_code,
)
from fastapi.security import HTTPAuthorizationCredentials

router = APIRouter(prefix="/auth", tags=["인증"])


# ── 이메일 인증 코드 발송 ─────────────────────────────────
@router.post("/email/send-code", summary="이메일 인증 코드 발송")
async def send_email_code(
    body: EmailVerifyRequest,
    redis: aioredis.Redis = Depends(get_redis),
):
    code = generate_verification_code()
    await store_verification_code(redis, body.email, code)
    await send_verification_email(body.email, code)
    return {"message": "인증 코드가 발송되었습니다. 5분 안에 입력해주세요."}


# ── 이메일 인증 코드 확인 (회원가입 전 별도 확인용) ─────────
@router.post("/email/verify-code", summary="이메일 인증 코드 확인")
async def check_email_code(
    body: EmailVerifyConfirm,
    redis: aioredis.Redis = Depends(get_redis),
):
    # 코드를 지우지 않고 존재 여부만 확인 (실제 삭제는 회원가입 시)
    key = f"email_verify:{body.email}"
    stored = await redis.get(key)
    if not stored or stored != body.code:
        raise HTTPException(status_code=400, detail="인증 코드가 올바르지 않거나 만료되었습니다.")
    return {"message": "인증 코드가 확인되었습니다."}


# ── 자체 회원가입 ─────────────────────────────────────────
@router.post("/register", response_model=UserResponse, status_code=201, summary="회원가입")
async def register(
    body: RegisterRequest,
    db: AsyncSession = Depends(get_async_db),
    redis: aioredis.Redis = Depends(get_redis),
):
    user = await register_user(body, db, redis)
    return UserResponse.model_validate(user)


# ── 자체 로그인 ───────────────────────────────────────────
@router.post("/login", response_model=TokenResponse, summary="로그인")
async def login(
    body: LoginRequest,
    db: AsyncSession = Depends(get_async_db),
):
    tokens = await login_user(body, db)
    return TokenResponse(**tokens)


# ── 토큰 갱신 ─────────────────────────────────────────────
@router.post("/refresh", response_model=TokenResponse, summary="액세스 토큰 갱신")
async def refresh_token(body: RefreshTokenRequest):
    payload = decode_token(body.refresh_token)
    if payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="유효하지 않은 리프레시 토큰입니다.")
    user_id = payload.get("sub")
    token_data = {"sub": user_id}
    return TokenResponse(
        access_token=create_access_token(token_data),
        refresh_token=create_refresh_token(token_data),
    )


# ── 로그아웃 ──────────────────────────────────────────────
@router.post("/logout", summary="로그아웃")
async def logout(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    redis: aioredis.Redis = Depends(get_redis),
    current_user: User = Depends(get_current_user),
):
    token = credentials.credentials
    # 토큰 블랙리스트 등록 (남은 유효 시간만큼 TTL)
    payload = decode_token(token)
    import time
    ttl = int(payload.get("exp", 0) - time.time())
    if ttl > 0:
        await redis.setex(f"blacklist:{token}", ttl, "1")

    # 프론트에서 로그인 화면으로 리다이렉트 처리
    return {
        "message": "로그아웃 되었습니다.",
        "redirect_url": f"{settings.FRONTEND_URL}/login",
    }


# ── 내 정보 조회 ──────────────────────────────────────────
@router.get("/me", response_model=UserResponse, summary="내 정보 조회")
async def get_me(current_user: User = Depends(get_current_user)):
    return UserResponse.model_validate(current_user)


# ── 내 정보 수정 ──────────────────────────────────────────
@router.patch("/me", response_model=UserResponse, summary="내 정보 수정")
async def update_me(
    body: UserUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    if body.nickname is not None:
        current_user.nickname = body.nickname
    if body.tags is not None:
        current_user.tags = body.tags
    await db.flush()
    await db.refresh(current_user)
    return UserResponse.model_validate(current_user)


# ── 카카오 OAuth ──────────────────────────────────────────
@router.get("/kakao/login", summary="카카오 로그인 시작")
async def kakao_oauth_start():
    url = (
        f"https://kauth.kakao.com/oauth/authorize"
        f"?client_id={settings.KAKAO_CLIENT_ID}"
        f"&redirect_uri={settings.KAKAO_REDIRECT_URI}"
        f"&response_type=code"
    )
    return RedirectResponse(url)

@router.get("/kakao/callback", response_model=TokenResponse, summary="카카오 OAuth 콜백")
async def kakao_callback(code: str, db: AsyncSession = Depends(get_async_db)):
    tokens = await kakao_login(code, db)
    return TokenResponse(**tokens)


# ── 구글 OAuth ────────────────────────────────────────────
@router.get("/google/login", summary="구글 로그인 시작")
async def google_oauth_start():
    scope = "openid email profile"
    url = (
        f"https://accounts.google.com/o/oauth2/v2/auth"
        f"?client_id={settings.GOOGLE_CLIENT_ID}"
        f"&redirect_uri={settings.GOOGLE_REDIRECT_URI}"
        f"&response_type=code"
        f"&scope={scope}"
    )
    return RedirectResponse(url)

@router.get("/google/callback", response_model=TokenResponse, summary="구글 OAuth 콜백")
async def google_callback(code: str, db: AsyncSession = Depends(get_async_db)):
    tokens = await google_login(code, db)
    return TokenResponse(**tokens)


# ── 비밀번호 변경 ────────────────────────────────────────
@router.patch("/password", summary="비밀번호 변경")
async def change_password(
    body: PasswordChangeRequest,
    redis: aioredis.Redis = Depends(get_redis),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
):
    await change_user_password(db, redis, current_user, body)
    return {"message": "비밀번호 변경이 완료되었습니다."}

# 비밀번호 초기화(잊은 경우)
@router.post("/password/reset", summary="비밀번호 잊음 - 이메일 인증 후 재설정")
async def reset_password(
    body: PasswordResetRequest,
    db: AsyncSession = Depends(get_async_db),
    redis_client = Depends(get_redis) # Redis를 쓰고 계시다면 주입
):
    await reset_user_password(db, redis_client, body)
    return {"message": "비밀번호가 성공적으로 초기화되었습니다. 새 비밀번호로 로그인해 주세요."}

# 회원 탈퇴 
@router.delete("/me", summary="회원 탈퇴")
async def withdraw(
    body: WithdrawRequest, # 클라이언트가 입력한 인증 코드
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_async_db),
    redis: aioredis.Redis = Depends(get_redis) # Redis 주입 추가
):
    return await withdraw_user(
        db=db, 
        redis=redis,
        current_user=current_user,
        verification_code=body.verification_code
    )