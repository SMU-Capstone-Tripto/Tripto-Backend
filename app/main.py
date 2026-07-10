from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager

from app.core.config import settings
from app.api.v1.endpoints import api_router
from app.infra.redis_client import init_redis, close_redis
from app.core.database import async_engine as engine
from app.models.base import Base
from app.models import User, Friendship, Travel, Schedule, Memo, ChatRoom, ChatMessage, ChatRoomMember, Notification  # noqa: F401
from app.models import ItinerarySnapshot, VoteSession, VoteRecord  # noqa: F401


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 시작 시
    await init_redis()
    # 개발 환경에서 테이블 자동 생성 (운영은 Alembic 사용)
    if settings.APP_ENV == "development":
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    yield
    # 종료 시
    await close_redis()
    await engine.dispose()


app = FastAPI(
    title="트립토 API",
    description="여행 도우미 AI Agent '트립토' 백엔드 API",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.FRONTEND_URL, "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 정적 파일 마운트 설정 
app.mount("/static", StaticFiles(directory="static"), name="static")
# 라우터 등록
app.include_router(api_router)


@app.get("/health", tags=["헬스체크"])
async def health_check():
    return {"status": "ok", "app": settings.APP_NAME}
