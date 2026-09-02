import asyncio
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from contextlib import asynccontextmanager

from app.core.config import settings
from app.api.v1.endpoints import api_router
from app.infra.redis_client import init_redis, close_redis
from app.core.database import async_engine as engine, AsyncSessionLocal
from app.core.firebase import initialize_firebase
from app.models.base import Base
from app.models import User, Friendship, Travel, Schedule, Memo, ChatRoom, ChatMessage, ChatRoomMember, Notification  # noqa: F401
from app.models import ItinerarySnapshot, VoteSession, VoteRecord  # noqa: F401

logger = logging.getLogger(__name__)

_VOTE_SWEEP_INTERVAL_SEC = 300  # 5분마다 만료 투표 자동 마감


async def _vote_sweep_loop():
    """만료된 그룹 투표를 주기적으로 자동 마감(당선작 등록 + 알림).
    조회 시에도 마감되지만, 아무도 안 보는 사이 만료돼도 제때 알림이 가도록 하는 백그라운드 루프."""
    from app.services.vote_service import sweep_expired_votes
    while True:
        try:
            await asyncio.sleep(_VOTE_SWEEP_INTERVAL_SEC)
            async with AsyncSessionLocal() as db:
                closed = await sweep_expired_votes(db)
            if closed:
                logger.info("만료 투표 %d건 자동 마감", closed)
        except asyncio.CancelledError:
            break
        except Exception:
            logger.exception("투표 자동 마감 루프 오류")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 시작 시
    await init_redis()
    initialize_firebase()
    # 개발 환경에서 테이블 자동 생성 (운영은 Alembic 사용)
    if settings.APP_ENV == "development":
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    sweep_task = asyncio.create_task(_vote_sweep_loop())
    yield
    # 종료 시
    sweep_task.cancel()
    try:
        await sweep_task
    except asyncio.CancelledError:
        pass
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
