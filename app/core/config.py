from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # App
    APP_NAME: str = ""
    APP_ENV: str = ""
    SECRET_KEY: str = ""
    ALGORITHM: str = ""
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # Database
    SQLALCHEMY_DATABASE_URL: str = ""
    SQLALCHEMY_DATABASE_URL_ASYNC: str = ""
    # Redis
    REDIS_URL: str = ""

    # Email
    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    EMAIL_FROM: str = ""

    # Kakao
    KAKAO_CLIENT_ID: str = ""
    KAKAO_REDIRECT_URI: str = ""

    # Google
    GOOGLE_CLIENT_ID: str = ""
    GOOGLE_CLIENT_SECRET: str = ""
    GOOGLE_REDIRECT_URI: str = ""

    # Frontend
    FRONTEND_URL: str = ""

    GOOGLE_API_KEY: str = ""
    GOOGLE_API_KEY2: str =""

    GROQ_API_KEY: str = ""

    tour_key: str = ""
    TAGO_KEY: str = ""

    NAVER_MAPS_ID: str = ""
    NAVER_MAPS: str = ""
    NAVER_SEARCH_ID: str = ""
    NAVER_SEARCH: str = ""

    # Email verification code TTL (seconds)
    EMAIL_CODE_TTL: int = 300  # 5 minutes


    class Config:
        env_file = ".env"
        case_sensitive = True


@lru_cache()
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
