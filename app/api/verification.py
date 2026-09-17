import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.models.verification import ImageVerificationRequest, ImageVerificationResponse
from app.services.ai_providers.base import AIProviderError
from app.services.image_verifier import ImageVerifier


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/images", tags=["images"])


def get_image_verifier(request: Request) -> ImageVerifier:
    return request.app.state.image_verifier


@router.post("/verify", response_model=ImageVerificationResponse)
async def verify_images(
    payload: ImageVerificationRequest,
    verifier: ImageVerifier = Depends(get_image_verifier),
) -> ImageVerificationResponse:
    try:
        images = await verifier.verify_many(payload.images, payload.university)
    except AIProviderError as exc:
        logger.warning("AI image verification failed: %s", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI verification provider is temporarily unavailable",
        ) from exc
    return ImageVerificationResponse(images=images)
