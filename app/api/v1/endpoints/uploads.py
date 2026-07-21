from fastapi import APIRouter, Depends

from app.core.dependencies import get_current_user
from app.models.user_model import User
from app.schemas.s3_schema import PresignedUrlRequest, PresignedUrlResponse
from app.services import s3_service

router = APIRouter(prefix="/uploads", tags=["파일"])


@router.post("/presigned-url", response_model=PresignedUrlResponse, summary="S3 presigned URL 발급")
async def issue_presigned_url(
    body: PresignedUrlRequest,
    current_user: User = Depends(get_current_user),
):
    result = s3_service.create_presigned_upload_url(body.content_type, body.category)
    return result
