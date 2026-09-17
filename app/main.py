from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from app.api.health import router as health_router
from app.api.images import router as images_router
from app.api.profiles import router as profiles_router
from app.api.verification import router as verification_router
from app.api.universities import router as universities_router
from app.config import settings
from app.services.ai_providers.base import (
    AIProviderConfigurationError,
    ImageVerificationProvider,
)
from app.services.ai_providers.gemini import GeminiService
from app.services.image_discovery import ImageDiscoveryService
from app.services.image_deduplicator import ImageDeduplicator
from app.services.image_sources.wikimedia import WikimediaSource
from app.services.image_verifier import ImageVerifier
from app.services.profile_pipeline import ProfilePipelineService
from app.services.university_resolver import UniversityResolver


class UnsupportedAIProvider(ImageVerificationProvider):
    def __init__(self, provider_name: str) -> None:
        self._provider_name = provider_name

    async def verify(self, image, university):
        raise AIProviderConfigurationError(
            f"Unsupported AI provider: {self._provider_name}"
        )


def build_ai_provider(client: httpx.AsyncClient) -> ImageVerificationProvider:
    if settings.ai_provider.casefold() == "gemini":
        return GeminiService(http_client=client)
    return UnsupportedAIProvider(settings.ai_provider)


@asynccontextmanager
async def lifespan(app: FastAPI):
    timeout = httpx.Timeout(settings.wikidata_timeout_seconds)
    transport = httpx.AsyncHTTPTransport(retries=1)
    async with httpx.AsyncClient(
        timeout=timeout,
        transport=transport,
        headers={"User-Agent": settings.wikidata_user_agent},
    ) as client:
        resolver = UniversityResolver(client=client)
        discovery = ImageDiscoveryService(
            sources=[WikimediaSource(client=client)]
        )
        deduplicator = ImageDeduplicator(client=client)
        ai_provider = build_ai_provider(client)
        verifier = ImageVerifier(provider=ai_provider)
        app.state.university_resolver = resolver
        app.state.image_discovery_service = discovery
        app.state.image_deduplicator = deduplicator
        app.state.image_verifier = verifier
        app.state.profile_pipeline = ProfilePipelineService(
            resolver=resolver,
            discovery=discovery,
            deduplicator=deduplicator,
            verifier=verifier,
        )
        try:
            yield
        finally:
            ai_provider.close()


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        debug=settings.debug,
        lifespan=lifespan,
    )
    app.include_router(health_router)
    app.include_router(universities_router)
    app.include_router(images_router)
    app.include_router(verification_router)
    app.include_router(profiles_router)
    return app


app = create_app()
