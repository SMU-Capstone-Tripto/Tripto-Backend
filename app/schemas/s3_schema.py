from typing import Literal

from pydantic import BaseModel, Field

UploadCategory = Literal["chat", "profile", "travel", "memo"]


class PresignedUrlRequest(BaseModel):
    content_type: str = Field(..., description="업로드할 파일의 MIME 타입 (예: image/jpeg)")
    category: UploadCategory = Field(..., description="업로드 용도 (chat, profile, travel, memo)")


class PresignedUrlResponse(BaseModel):
    upload_url: str = Field(..., description="S3에 직접 업로드(PUT)할 presigned URL")
    file_url: str = Field(..., description="업로드 완료 후 파일에 접근할 수 있는 URL")
    key: str = Field(..., description="S3 객체 키")
