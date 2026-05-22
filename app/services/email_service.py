import random
import string
import aiosmtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from app.core.config import settings
import redis.asyncio as aioredis


def generate_verification_code(length: int = 6) -> str:
    """6자리 숫자 인증 코드 생성"""
    return "".join(random.choices(string.digits, k=length))


async def send_verification_email(email: str, code: str) -> None:
    """이메일 인증 코드 발송"""
    message = MIMEMultipart("alternative")
    message["Subject"] = f"[트립토] 이메일 인증 코드: {code}"
    message["From"] = settings.EMAIL_FROM
    message["To"] = email

    html_body = f"""
    <html>
    <body style="font-family: Arial, sans-serif; background: #f4f6fb; padding: 40px;">
      <div style="max-width: 480px; margin: auto; background: white; border-radius: 12px; padding: 40px; box-shadow: 0 2px 12px rgba(0,0,0,0.08);">
        <h2 style="color: #3B82F6; margin-bottom: 8px;">✈️ 트립토</h2>
        <h3 style="color: #1e293b;">이메일 인증 코드</h3>
        <p style="color: #475569;">아래 인증 코드를 입력해주세요. 코드는 <strong>5분간</strong> 유효합니다.</p>
        <div style="background: #EFF6FF; border-radius: 8px; padding: 24px; text-align: center; margin: 24px 0;">
          <span style="font-size: 36px; font-weight: bold; letter-spacing: 8px; color: #2563EB;">{code}</span>
        </div>
        <p style="color: #94a3b8; font-size: 13px;">본인이 요청하지 않았다면 이 이메일을 무시하세요.</p>
      </div>
    </body>
    </html>
    """

    message.attach(MIMEText(html_body, "html"))

    await aiosmtplib.send(
        message,
        hostname=settings.SMTP_HOST,
        port=settings.SMTP_PORT,
        username=settings.SMTP_USER,
        password=settings.SMTP_PASSWORD,
        start_tls=True,
    )


async def store_verification_code(redis: aioredis.Redis, email: str, code: str) -> None:
    """Redis에 인증 코드 저장 (TTL: 5분)"""
    key = f"email_verify:{email}"
    await redis.setex(key, settings.EMAIL_CODE_TTL, code)


async def verify_email_code(redis: aioredis.Redis, email: str, code: str) -> bool:
    """Redis에서 인증 코드 검증"""
    key = f"email_verify:{email}"
    stored_code = await redis.get(key)
    if stored_code and stored_code == code:
        await redis.delete(key)
        return True
    return False
