import random
import string
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.user_model import User


def _generate_candidate() -> str:
    """숫자 + 알파벳(대소문자) 혼합 6자 고유 ID 생성"""
    chars = string.ascii_letters + string.digits  # a-z A-Z 0-9
    # 최소 1개 숫자 + 1개 알파벳 보장
    must_have = [
        random.choice(string.digits),
        random.choice(string.ascii_letters),
    ]
    rest = [random.choice(chars) for _ in range(4)]
    combined = must_have + rest
    random.shuffle(combined)
    return "".join(combined)


async def generate_unique_id(db: AsyncSession) -> str:
    """DB 중복 없는 고유 ID 반환 (최대 10회 재시도)"""
    for _ in range(10):
        candidate = _generate_candidate()
        result = await db.execute(select(User).where(User.unique_id == candidate))
        if result.scalar_one_or_none() is None:
            return candidate
    raise RuntimeError("고유 ID 생성에 실패했습니다. 잠시 후 다시 시도해주세요.")
