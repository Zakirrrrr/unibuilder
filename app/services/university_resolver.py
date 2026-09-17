import asyncio
import logging
from collections.abc import Iterable
from difflib import SequenceMatcher
from typing import Any
from urllib.parse import urlparse
from uuid import NAMESPACE_URL, uuid5

import httpx

from app.config import settings
from app.models.resolution import ResolutionStatus, UniversityResolutionResponse
from app.models.university import University
from app.utils.text import comparison_key, normalize_query


logger = logging.getLogger(__name__)

WIKIDATA_UNIVERSITY_TYPES = {
    "Q3918",  # university
    "Q875538",  # public university
    "Q902104",  # private university
    "Q1371037",  # institute of technology
    "Q15936437",  # research university
}
OUTPUT_LANGUAGES = ("en", "ru", "kk")
MAX_TYPE_DEPTH = 4
MAX_ENTITY_IDS_PER_REQUEST = 50
MAX_UPSTREAM_ATTEMPTS = 3
MIN_UNIQUE_FUZZY_SIMILARITY = 0.86
MIN_AMBIGUOUS_SIMILARITY = 0.65


class UniversityResolverError(RuntimeError):
    """Raised when the upstream structured-data service cannot be used."""


class UniversityResolver:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def resolve(self, raw_query: str) -> UniversityResolutionResponse:
        query = normalize_query(raw_query)
        if not query:
            return UniversityResolutionResponse(status=ResolutionStatus.NOT_FOUND)

        try:
            search_ids = await self._search(query)
            if not search_ids:
                return UniversityResolutionResponse(status=ResolutionStatus.NOT_FOUND)

            entities = await self._get_entities(search_ids)
            university_entities = await self._university_entities(
                [entities[item_id] for item_id in search_ids if item_id in entities]
            )
            if not university_entities:
                return UniversityResolutionResponse(status=ResolutionStatus.NOT_FOUND)

            universities = await self._build_universities(university_entities)
            return self._select(query, university_entities, universities)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            logger.warning("Wikidata request failed: %s", type(exc).__name__)
            raise UniversityResolverError("Wikidata is temporarily unavailable") from exc
        except (httpx.HTTPStatusError, ValueError, KeyError, TypeError) as exc:
            logger.exception("Invalid or unsuccessful Wikidata response")
            raise UniversityResolverError("Could not process the Wikidata response") from exc

    async def _request(self, params: dict[str, Any]) -> dict[str, Any]:
        request_params = {
            "format": "json",
            "formatversion": "2",
            "maxlag": str(settings.wikidata_maxlag_seconds),
            **params,
        }
        for attempt in range(MAX_UPSTREAM_ATTEMPTS):
            response = await self._client.get(
                settings.wikidata_api_url, params=request_params
            )
            if response.status_code == 429 or response.status_code >= 500:
                if attempt < MAX_UPSTREAM_ATTEMPTS - 1:
                    await asyncio.sleep(self._retry_delay(response, attempt))
                    continue
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise ValueError("Wikidata payload must be an object")
            error = payload.get("error")
            if not isinstance(error, dict):
                return payload
            error_code = error.get("code", "unknown")
            logger.warning(
                "Wikidata API error code=%s info=%s",
                error_code,
                error.get("info", "not provided"),
            )
            if error_code == "maxlag" and attempt < MAX_UPSTREAM_ATTEMPTS - 1:
                await asyncio.sleep(2.0 * (attempt + 1))
                continue
            raise ValueError("Wikidata returned an API error")
        raise ValueError("Wikidata retry limit exceeded")

    @staticmethod
    def _retry_delay(response: httpx.Response, attempt: int) -> float:
        retry_after = response.headers.get("Retry-After")
        if retry_after and retry_after.isdigit():
            return min(float(retry_after), 5.0)
        return 2.0 * (attempt + 1)

    async def _search(self, query: str) -> list[str]:
        languages = self._search_languages(query)
        query_key = comparison_key(query)
        exact: list[str] = []
        plausible: list[str] = []
        for language in languages:
            payload = await self._request(
                {
                    "action": "wbsearchentities",
                    "search": query,
                    "language": language,
                    "uselang": "en",
                    "type": "item",
                    "limit": str(settings.wikidata_search_limit),
                }
            )
            for result in payload.get("search", []):
                item_id = result.get("id")
                if not isinstance(item_id, str):
                    continue
                result_terms = [
                    result.get("label"),
                    result.get("match", {}).get("text"),
                    *result.get("aliases", []),
                ]
                term_keys = [
                    comparison_key(term)
                    for term in result_terms
                    if isinstance(term, str) and term
                ]
                if query_key in term_keys:
                    if item_id not in exact:
                        exact.append(item_id)
                    continue
                similarity = max(
                    (
                        SequenceMatcher(None, query_key, term_key).ratio()
                        for term_key in term_keys
                    ),
                    default=0.0,
                )
                if (
                    similarity >= MIN_AMBIGUOUS_SIMILARITY
                    and item_id not in plausible
                ):
                    plausible.append(item_id)
        return exact or plausible

    @staticmethod
    def _search_languages(query: str) -> tuple[str, ...]:
        contains_cyrillic = any("\u0400" <= char <= "\u04ff" for char in query)
        return ("ru", "kk") if contains_cyrillic else ("en",)

    async def _get_entities(
        self, item_ids: Iterable[str], *, claims: bool = True
    ) -> dict[str, dict[str, Any]]:
        ids = list(dict.fromkeys(item_ids))
        if not ids:
            return {}
        props = "labels|aliases|claims" if claims else "labels"
        collected: dict[str, dict[str, Any]] = {}
        for start in range(0, len(ids), MAX_ENTITY_IDS_PER_REQUEST):
            chunk = ids[start : start + MAX_ENTITY_IDS_PER_REQUEST]
            payload = await self._request(
                {
                    "action": "wbgetentities",
                    "ids": "|".join(chunk),
                    "props": props,
                    "languages": "|".join(OUTPUT_LANGUAGES),
                    "languagefallback": "1",
                }
            )
            entities = payload.get("entities", {})
            if not isinstance(entities, dict):
                raise ValueError("Wikidata entities must be an object")
            collected.update(entities)
        return collected

    async def _university_entities(
        self, entities: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        types_by_entity = {
            entity["id"]: set(self._claim_entity_ids(entity, "P31"))
            for entity in entities
        }
        unresolved_entities = [
            entity
            for entity in entities
            if not types_by_entity[entity["id"]] & WIKIDATA_UNIVERSITY_TYPES
        ]
        unresolved_types = (
            set().union(
                *(types_by_entity[entity["id"]] for entity in unresolved_entities)
            )
            if unresolved_entities
            else set()
        )
        university_types = await self._types_descending_from_university(unresolved_types)
        return [
            entity
            for entity in entities
            if types_by_entity[entity["id"]]
            & (WIKIDATA_UNIVERSITY_TYPES | university_types)
        ]

    async def _types_descending_from_university(self, type_ids: set[str]) -> set[str]:
        accepted = set(WIKIDATA_UNIVERSITY_TYPES)
        unresolved = set(type_ids) - accepted
        parents_by_type: dict[str, set[str]] = {}
        frontier = set(unresolved)

        for _ in range(MAX_TYPE_DEPTH):
            if not frontier:
                break
            type_entities = await self._get_entities(frontier)
            next_frontier: set[str] = set()
            for type_id in frontier:
                parents = set(
                    self._claim_entity_ids(type_entities.get(type_id, {}), "P279")
                )
                parents_by_type[type_id] = parents
                next_frontier.update(parents - accepted - set(parents_by_type))
            frontier = next_frontier

        changed = True
        while changed:
            changed = False
            for type_id, parents in parents_by_type.items():
                if type_id not in accepted and parents & accepted:
                    accepted.add(type_id)
                    changed = True
        return accepted

    async def _build_universities(
        self, entities: list[dict[str, Any]]
    ) -> list[University]:
        related_ids: set[str] = set()
        for entity in entities:
            related_ids.update(self._claim_entity_ids(entity, "P131"))
            related_ids.update(self._claim_entity_ids(entity, "P17"))
        related = await self._get_entities(related_ids, claims=False)

        universities: list[University] = []
        for entity in entities:
            item_id = entity["id"]
            name = self._preferred_label(entity) or item_id
            aliases = self._aliases(entity, excluding=name)
            city = self._first_related_label(entity, "P131", related)
            country = self._first_related_label(entity, "P17", related)
            universities.append(
                University(
                    id=uuid5(NAMESPACE_URL, f"https://www.wikidata.org/wiki/{item_id}"),
                    name=name,
                    aliases=aliases,
                    city=city,
                    country=country,
                    official_domain=self._official_domain(entity),
                )
            )
        return universities

    def _select(
        self,
        query: str,
        entities: list[dict[str, Any]],
        universities: list[University],
    ) -> UniversityResolutionResponse:
        query_key = comparison_key(query)
        scores: list[float] = []
        exact_indexes: list[int] = []
        for index, entity in enumerate(entities):
            terms = self._all_terms(entity)
            term_keys = [comparison_key(term) for term in terms]
            if query_key in term_keys:
                exact_indexes.append(index)
            scores.append(
                max(
                    (SequenceMatcher(None, query_key, term).ratio() for term in term_keys),
                    default=0.0,
                )
            )

        if len(exact_indexes) == 1:
            return UniversityResolutionResponse(
                status=ResolutionStatus.RESOLVED,
                university=universities[exact_indexes[0]],
            )
        if len(exact_indexes) > 1:
            return UniversityResolutionResponse(
                status=ResolutionStatus.AMBIGUOUS,
                candidates=[universities[index] for index in exact_indexes],
            )

        strong = [
            index
            for index, score in enumerate(scores)
            if score >= MIN_UNIQUE_FUZZY_SIMILARITY
        ]
        if len(universities) == 1 and strong == [0]:
            return UniversityResolutionResponse(
                status=ResolutionStatus.RESOLVED, university=universities[0]
            )
        plausible = [
            university
            for university, score in zip(universities, scores, strict=True)
            if score >= MIN_AMBIGUOUS_SIMILARITY
        ]
        if plausible:
            return UniversityResolutionResponse(
                status=ResolutionStatus.AMBIGUOUS, candidates=plausible
            )
        return UniversityResolutionResponse(status=ResolutionStatus.NOT_FOUND)

    @staticmethod
    def _claim_entity_ids(entity: dict[str, Any], property_id: str) -> list[str]:
        result: list[str] = []
        for claim in entity.get("claims", {}).get(property_id, []):
            value = claim.get("mainsnak", {}).get("datavalue", {}).get("value", {})
            item_id = value.get("id") if isinstance(value, dict) else None
            if isinstance(item_id, str):
                result.append(item_id)
        return result

    @staticmethod
    def _all_terms(entity: dict[str, Any]) -> list[str]:
        terms = [
            value.get("value")
            for value in entity.get("labels", {}).values()
            if isinstance(value, dict)
        ]
        for aliases in entity.get("aliases", {}).values():
            terms.extend(
                alias.get("value") for alias in aliases if isinstance(alias, dict)
            )
        return [term for term in terms if isinstance(term, str) and term]

    @classmethod
    def _preferred_label(cls, entity: dict[str, Any]) -> str | None:
        labels = entity.get("labels", {})
        for language in OUTPUT_LANGUAGES:
            value = labels.get(language, {}).get("value")
            if isinstance(value, str) and value:
                return value
        terms = cls._all_terms({"labels": labels})
        return terms[0] if terms else None

    @classmethod
    def _aliases(cls, entity: dict[str, Any], excluding: str) -> list[str]:
        seen = {comparison_key(excluding)}
        aliases: list[str] = []
        for term in cls._all_terms(entity):
            key = comparison_key(term)
            if key not in seen:
                seen.add(key)
                aliases.append(term)
        return aliases

    @classmethod
    def _first_related_label(
        cls,
        entity: dict[str, Any],
        property_id: str,
        related: dict[str, dict[str, Any]],
    ) -> str | None:
        for item_id in cls._claim_entity_ids(entity, property_id):
            label = cls._preferred_label(related.get(item_id, {}))
            if label:
                return label
        return None

    @staticmethod
    def _official_domain(entity: dict[str, Any]) -> str | None:
        for claim in entity.get("claims", {}).get("P856", []):
            value = claim.get("mainsnak", {}).get("datavalue", {}).get("value")
            if isinstance(value, str):
                hostname = urlparse(value).hostname
                if hostname:
                    return hostname.removeprefix("www.").lower()
        return None
