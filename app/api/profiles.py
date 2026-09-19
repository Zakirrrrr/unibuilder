import logging

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.models.profile import ProfileGenerationRequest, UniversityProfile
from app.services.profile_pipeline import (
    ProfileAmbiguousError,
    ProfileGenerationTimeoutError,
    ProfileNotFoundError,
    ProfilePipelineService,
)
from app.services.university_resolver import UniversityResolverError


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/profile", tags=["profile"])


def get_profile_pipeline(request: Request) -> ProfilePipelineService:
    return request.app.state.profile_pipeline


@router.post("/generate", response_model=UniversityProfile)
async def generate_profile(
    payload: ProfileGenerationRequest,
    pipeline: ProfilePipelineService = Depends(get_profile_pipeline),
) -> UniversityProfile:
    try:
        if payload.selected_university_id is None:
            return await pipeline.generate(payload.query)
        return await pipeline.generate(payload.query, selected_university_id=payload.selected_university_id)
    except ProfileNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"status": "not_found"},
        ) from exc
    except ProfileAmbiguousError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "status": "ambiguous",
                "candidates": [
                    candidate.model_dump(mode="json") for candidate in exc.candidates
                ],
            },
        ) from exc
    except ProfileGenerationTimeoutError as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="Profile generation timed out",
        ) from exc
    except UniversityResolverError as exc:
        logger.warning("profile university resolution failed")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="University data provider is temporarily unavailable",
        ) from exc
