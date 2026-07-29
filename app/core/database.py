from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from starlette.config import Config

config = Config('.env.local')
SQLALCHEMY_DATABASE_URL = config('SQLALCHEMY_DATABASE_URL')

# 동기 DB
engine = create_engine(SQLALCHEMY_DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# 비동기 DB
SQLALCHEMY_DATABASE_URL_ASYNC = config('SQLALCHEMY_DATABASE_URL_ASYNC')
async_engine = create_async_engine(
    SQLALCHEMY_DATABASE_URL_ASYNC,
    pool_size=20,
    max_overflow=20,
    pool_timeout=10,
)

AsyncSessionLocal = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

async def get_async_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()