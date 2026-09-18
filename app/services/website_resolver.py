"""Bounded website-backed identification, independent of Wikidata and LLMs."""
import asyncio
import ipaddress
import json
import logging
import re
import socket
from html.parser import HTMLParser
from urllib.parse import urlsplit, urljoin
from uuid import NAMESPACE_URL, uuid5

import httpx

from app.models.university import University
from app.models.resolution import ResolutionStatus, UniversityResolutionResponse
from app.utils.text import comparison_key
# Installs query-key redaction for httpx's request logs.
from app.services.image_sources.google_images import _RedactSearchKey  # noqa: F401

logger = logging.getLogger(__name__)


class IdentityPage(HTMLParser):
    def __init__(self):
        super().__init__()
        self.title = ""
        self.text = []
        self.schemas = []
        self._title = False
        self._script = False
        self._json = False
        self._buffer = ""

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "title":
            self._title = True
        if tag == "script":
            self._script = True
            self._json = attrs.get("type", "").lower() == "application/ld+json"
            self._buffer = ""

    def handle_endtag(self, tag):
        if tag == "title":
            self._title = False
        if tag == "script":
            if self._json:
                try:
                    self.schemas.append(json.loads(self._buffer))
                except ValueError:
                    pass
            self._script = self._json = False

    def handle_data(self, data):
        if self._title:
            self.title += data
        if self._json:
            self._buffer += data
        elif not self._script:
            self.text.append(data)


async def public_host(host):
    try:
        addresses = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        return bool(addresses) and all(ipaddress.ip_address(a[4][0]).is_global for a in addresses)
    except (OSError, ValueError):
        return False


class WebsiteUniversityResolver:
    def __init__(self, client: httpx.AsyncClient, api_key: str):
        self._client, self._key = client, api_key

    async def resolve(self, query):
        try:
            async with asyncio.timeout(8):
                response = await self._client.get("https://serpapi.com/search.json", params={
                    "engine": "google", "q": query + " official university website", "num": 5,
                    "api_key": self._key,
                }, timeout=4)
                response.raise_for_status()
                data = response.json()
                if not isinstance(data, dict) or data.get("error"):
                    raise ValueError("Search unavailable")
                urls, hosts = [], set()
                for row in data.get("organic_results", []):
                    url = row.get("link", "")
                    host = (urlsplit(url).hostname or "").removeprefix("www.")
                    if host and host not in hosts:
                        hosts.add(host)
                        urls.append(url)
                    if len(urls) == 3:
                        break
                results = await asyncio.gather(*(self._candidate(url, query) for url in urls), return_exceptions=True)
                candidates = {u.official_domain: u for u in results if isinstance(u, University)}
                values = list(candidates.values())
                if len(values) == 1:
                    return UniversityResolutionResponse(status=ResolutionStatus.RESOLVED, university=values[0])
                if values:
                    return UniversityResolutionResponse(status=ResolutionStatus.AMBIGUOUS, candidates=values)
                if results and all(isinstance(r, BaseException) for r in results):
                    raise ValueError("Candidate websites unavailable")
                return UniversityResolutionResponse(status=ResolutionStatus.NOT_FOUND)
        except Exception as exc:
            # Never log request URLs or provider exception text containing credentials.
            logger.warning("website resolution unavailable error=%s", type(exc).__name__)
            raise WebsiteResolutionError("Official website lookup unavailable") from None

    async def _candidate(self, url, query):
        domain = (urlsplit(url).hostname or "").removeprefix("www.")
        async with asyncio.timeout(3.5):
            for _ in range(3):
                parts = urlsplit(url)
                host = parts.hostname or ""
                if (parts.scheme != "https" or parts.username or parts.password
                    or parts.port not in (None, 443)
                    or host.removeprefix("www.") != domain or not await public_host(host)):
                    return None
                async with self._client.stream("GET", url, timeout=3) as response:
                    if response.is_redirect:
                        url = urljoin(url, response.headers.get("location", ""))
                        continue
                    response.raise_for_status()
                    if "text/html" not in response.headers.get("content-type", ""):
                        return None
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > 1_500_000:
                            return None
                page = IdentityPage()
                page.feed(content.decode("utf-8", errors="replace"))
                return self.identify(page, query, url)
        return None

    @staticmethod
    def identify(page, query, url):
        domain = urlsplit(url).hostname.removeprefix("www.")
        key = comparison_key(query)

        def nodes(value):
            if isinstance(value, list):
                for item in value:
                    yield from nodes(item)
            elif isinstance(value, dict):
                yield value
                # Do not accept nested mentions of somebody else's university.
                yield from nodes(value.get("@graph", []))

        for schema in page.schemas:
            for node in nodes(schema):
                types = node.get("@type", [])
                if isinstance(types, str):
                    types = [types]
                if not isinstance(types, list):
                    continue
                if not any(t.rsplit("/", 1)[-1] == "CollegeOrUniversity" for t in types if isinstance(t, str)):
                    continue
                name = node.get("name")
                aliases = node.get("alternateName", [])
                if isinstance(aliases, str):
                    aliases = [aliases]
                if not isinstance(aliases, list):
                    aliases = []
                names = [n for n in [name, *aliases] if isinstance(n, str)]
                site = node.get("url")
                if not isinstance(name, str) or key not in map(comparison_key, names) or not isinstance(site, str):
                    continue
                if (urlsplit(site).hostname or "").removeprefix("www.") != domain:
                    continue
                address = node.get("address", {})
                if not isinstance(address, dict):
                    address = {}
                country = address.get("addressCountry")
                if isinstance(country, dict):
                    country = country.get("name")
                city = address.get("addressLocality")
                return University(id=uuid5(NAMESPACE_URL, "https://" + domain), name=name,
                    aliases=[n for n in names if n != name], city=city if isinstance(city, str) else None,
                    country=country if isinstance(country, str) else None, official_domain=domain,
                    resolution_source="official_website", evidence_urls=[url])

        # Conservative HTML fallback: full university name in page title, academic
        # domain and multiple teaching/admissions signals. Never infer short aliases.
        name_parts = [p.strip() for p in re.split(r"[|–—]|\s-\s", page.title)]
        academic = domain.endswith(".edu") or ".edu." in domain or ".ac." in domain
        body = comparison_key(" ".join(page.text))
        signals = sum(any(term in body for term in terms) for terms in (
            ("admissions", "admission", "поступление", "приемная"),
            ("bachelor", "undergraduate", "бакалавр"),
            ("master", "graduate", "магистр"),
        ))
        if (len(key) >= 8 and academic and signals >= 2
            and re.search(r"\b(university|университет|университеті)\b", key)
            and not re.search(r"\b(school|школа)\b", key)
            and key in map(comparison_key, name_parts)):
            return University(id=uuid5(NAMESPACE_URL, "https://" + domain), name=query,
                official_domain=domain, resolution_source="official_website", evidence_urls=[url])
        return None


class WebsiteResolutionError(RuntimeError):
    pass
