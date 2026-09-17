import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.models.discovery import ImageSearchRequest, ImageSearchResponse
from app.services.image_discovery import ImageDiscoveryService
from app.services.image_sources.base import ImageSourceError


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/images", tags=["images"])


def get_image_discovery_service(request: Request) -> ImageDiscoveryService:
    return request.app.state.image_discovery_service


@router.post("/search", response_model=ImageSearchResponse)
async def search_images(
    payload: ImageSearchRequest,
    service: ImageDiscoveryService = Depends(get_image_discovery_service),
) -> ImageSearchResponse:
    try:
        images = await service.search(payload.university, payload.limit_per_query)
    except ImageSourceError as exc:
        logger.warning("Image discovery failed")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Image source is temporarily unavailable",
        ) from exc
    return ImageSearchResponse(total=len(images), images=images)
