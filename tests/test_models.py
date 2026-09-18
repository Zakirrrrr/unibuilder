from datetime import timezone

import pytest
from pydantic import ValidationError

from app.models import ImageCandidate, ImageCategory, University, UniversityProfile


def test_university_profile_defaults() -> None:
    university = University(name="Example University")
    profile = UniversityProfile(university=university)

    assert set(profile.categories) == {
        ImageCategory.CAMPUS,
        ImageCategory.LIBRARY,
        ImageCategory.DORMITORY,
        ImageCategory.CLASSROOM,
        ImageCategory.STUDENT_LIFE,
        ImageCategory.FACILITIES,
        ImageCategory.OTHER,
    }
    assert all(images == [] for images in profile.categories.values())
    assert profile.statistics.found == 0
    assert profile.generated_at.tzinfo == timezone.utc


def test_image_candidate_requires_valid_urls() -> None:
    with pytest.raises(ValidationError):
        ImageCandidate(
            image_url="not-a-url",
            source_url="https://example.org/source",
            source_name="Example source",
        )
