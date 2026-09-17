import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.models.resolution import (
    UniversityResolutionRequest,
    UniversityResolutionResponse,
)
from app.services.university_resolver import UniversityResolver, UniversityResolverError


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/universities", tags=["universities"])


def get_university_resolver(request: Request) -> UniversityResolver:
    return request.app.state.university_resolver


@router.post("/resolve", response_model=UniversityResolutionResponse)
async def resolve_university(
    payload: UniversityResolutionRequest,
    resolver: UniversityResolver = Depends(get_university_resolver),
) -> UniversityResolutionResponse:
    try:
        return await resolver.resolve(payload.query)
    except UniversityResolverError as exc:
        logger.warning("University resolution failed")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="University data provider is temporarily unavailable",
        ) from exc
