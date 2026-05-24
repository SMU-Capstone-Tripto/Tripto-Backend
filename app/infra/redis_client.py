import redis.asyncio as aioredis
from app.core.config import settings

_redis: aioredis.Redis = None


async def init_redis():
    global _redis
    _redis = aioredis.from_url(
        settings.REDIS_URL,
        encoding="utf-8",
        decode_responses=True,
    )


async def close_redis():
    global _redis
    if _redis:
        await _redis.aclose()


async def get_redis() -> aioredis.Redis:
    return _redis
