import asyncio
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx

from app.models.image import ImageCandidate
from app.models.university import University
from app.services.image_sources.base import ImageSource


class PageImages(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.images = []
        self.links = []
        self.title = ""
        self.in_title = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "title":
            self.in_title = True
        if tag == "img":
            url = attrs.get("data-src") or attrs.get("src")
            if url:
                self.images.append((url, attrs.get("alt") or None))
        if tag == "meta" and attrs.get("property") == "og:image":
            self.images.append((attrs.get("content", ""), None))
        if tag == "a" and attrs.get("href"):
            self.links.append(attrs["href"])

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False

    def handle_data(self, data):
        if self.in_title:
            self.title += data


class OfficialWebsiteSource(ImageSource):
    """Bounded discovery from the resolved official website and campus pages."""
    university_wide = True

    def __init__(self, client: httpx.AsyncClient):
        self._client = client

    async def _page(self, url, domain):
        # Follow only same-university redirects. No arbitrary off-site crawling.
        for _ in range(3):
            host = urlsplit(url).hostname or ""
            if host != domain and not host.endswith("." + domain):
                return None
            async with self._client.stream("GET", url, timeout=3) as response:
                if response.is_redirect:
                    url = urljoin(url, response.headers.get("location", ""))
                    continue
                response.raise_for_status()
                if "text/html" not in response.headers.get("content-type", ""):
                    return None
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > 2_000_000:
                        return None
            parser = PageImages()
            parser.feed(data.decode("utf-8", errors="replace"))
            return url, parser
        return None

    async def search(self, university: University, query: str, limit: int):
        domain = university.official_domain
        if not domain:
            return []
        root = await self._page(f"https://{domain}/", domain)
        if root is None:
            return []
        root_url, parser = root
        links = list(dict.fromkeys(
            urljoin(root_url, link) for link in parser.links
            if any(word in link.lower() for word in ("campus", "visit", "library", "housing"))
        ))[:2]
        results = await asyncio.gather(
            *(self._page(link, domain) for link in links), return_exceptions=True
        )
        pages = [root] + [item for item in results if isinstance(item, tuple)]
        images = []
        seen = set()
        for page_url, page in pages:
            for raw_url, alt in page.images:
                url = urljoin(page_url, raw_url)
                if urlsplit(url).scheme != "https" or url in seen:
                    continue
                if any(word in url.lower() for word in ("logo", ".svg", "icon", "sprite")):
                    continue
                seen.add(url)
                images.append(ImageCandidate(
                    image_url=url, source_url=page_url, source_name="Official website",
                    search_query=query, title=alt or page.title.strip() or None,
                    description=alt,
                ))
        # Campus-specific alt text is a ranking signal, never proof of affiliation.
        images.sort(key=lambda image: -sum(
            word in (image.description or "").lower()
            for word in ("campus", "library", "yard", "hall", "student", "building")
        ))
        return images[:min(limit * 3, 8)]
