import uuid

from fastapi import HTTPException

from app.core.config import settings
from app.core.s3 import get_s3_client

ALLOWED_CONTENT_TYPES = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/gif": "gif",
    "image/webp": "webp",
}


def create_presigned_upload_url(content_type: str, category: str) -> dict:
    ext = ALLOWED_CONTENT_TYPES.get(content_type)
    if not ext:
        raise HTTPException(status_code=400, detail="지원하지 않는 파일 형식입니다.")

    key = f"{category}/{uuid.uuid4()}.{ext}"

    client = get_s3_client()
    upload_url = client.generate_presigned_url(
        ClientMethod="put_object",
        Params={
            "Bucket": settings.AWS_S3_BUCKET,
            "Key": key,
            "ContentType": content_type,
        },
        ExpiresIn=settings.AWS_S3_PRESIGNED_URL_EXPIRE_SECONDS,
    )
    file_url = f"https://{settings.AWS_S3_BUCKET}.s3.{settings.AWS_REGION}.amazonaws.com/{key}"

    return {"upload_url": upload_url, "file_url": file_url, "key": key}
