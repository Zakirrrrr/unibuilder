# University Visual Profile API

Local website and API for structured university visual profiles. It combines
university resolution, image discovery, duplicate removal, and multimodal
verification through Gemini with the frontend supplied for LOCUSCASE1.

## Requirements

- Python 3.11

## Setup

```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

Copy `.env.example` to `.env`. Most endpoints work without secrets; image
verification additionally requires `GEMINI_API_KEY`.

## Run

```powershell
uvicorn app.main:app --reload
```

The integrated frontend and API are available at `http://127.0.0.1:8000`.
Enter a university name and press **Найти**. The loading screen counts down from
30 seconds while the real API request runs. Messages appear every four seconds
in random order and random positions, remaining visible without repeating or
overlapping each other or the timer. Small screens gain scrollable space when
needed. Only after a successful response, it displays the final
message for 1.2 seconds before opening the results. Errors and cancellation
stop the animation immediately. Ambiguous names show a choice of universities. The gallery
shows actual image URLs, source links, available dates, category filters, and
unverified candidates separately. Check the backend with:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

## Test

```powershell
pytest
```

## Current API

### Website-backed university identification

Wikidata is no longer the only identity source. With `SERPAPI_API_KEY` configured,
missing/unavailable Wikidata results trigger an official-website lookup using
Google organic search (one additional SerpAPI search per uncached fallback).
The primary source receives up to 10 seconds before switching to the alternative;
website lookup is capped at 8 seconds and examines at most three distinct hosts.
The overall profile deadline remains 29 seconds. Known Wikidata ambiguity is
preserved rather than overridden by a search ranking.

Website verification reads actual HTML, not just search snippets: matching
`CollegeOrUniversity` JSON-LD with a same-host organization URL and exact name or
declared alias; alternatively a matching full university title on an academic
domain with multiple teaching/admissions signals. These are identity heuristics,
not accreditation or legal-status verification. Missing metadata stays null.
Multiple matching sites return `ambiguous`. HTTPS/public-host checks, bounded
HTML sizes and same-host redirects limit the crawl. No LLM is used.

University results include `resolution_source` and `evidence_urls`; the UI links
to the identity evidence. This does not automatically confirm any photograph.
Live standalone website check: Astana IT University resolved via
`https://astanait.edu.kz/en` in 2.52 seconds, without consulting Wikidata.

Resolver reliability: the resolver shares the overall 29-second profile deadline
instead of having a separate seven-second cutoff. An upstream resolver timeout
returns HTTP 503, not a misleading overall-generation HTTP 504. When resolution
takes longer, subsequent stages use only the remaining time budget.
Wikidata entries typed only as educational institutions are accepted only when
their own labels identify a university and an official website is present;
school labels are excluded. Exact/ambiguous name-selection rules still apply.

- `GET /health` returns `{ "status": "ok" }`.
- `POST /api/universities/resolve` resolves a university name or alias using
  structured Wikidata entities and claims.
- `POST /api/images/search` searches Wikimedia Commons for unverified image
  candidates and their source metadata.
- `POST /api/profile/generate` runs the complete resolver, discovery,
  deduplication, Gemini verification, categorization, and filtering pipeline.

Example:

```powershell
Invoke-RestMethod `
  -Method Post `
  -ContentType application/json `
  -Body '{"query":"Nazarbayev University"}' `
  http://127.0.0.1:8000/api/universities/resolve
```

The resolver returns `resolved`, `ambiguous`, or `not_found`. Upstream timeout,
network, rate-limit, and invalid-response failures are returned as HTTP 503.

Image discovery runs bounded queries for campus, library, dormitory, students,
classroom, building, sport, laboratory, and the university's city when known.
Results remain unverified: `is_real_photo` and `is_relevant` are `null`
until a later verification stage.

## Deduplication

Wikimedia requests identify this application with `WIKIDATA_USER_AGENT` and a
link to the project repository, following the
[Wikimedia User-Agent policy](https://foundation.wikimedia.org/wiki/Policy:Wikimedia_Foundation_User-Agent_Policy).
The old `local-development` contact placeholder caused HTTP 403 responses on
this machine. Keep a real project/contact URL when changing this setting; the
same header is used for Commons, Wikidata, and backend image downloads.

`ImageDeduplicator` is a separate processing stage and downloads files only
when `deduplicate(images)` is explicitly called. It computes SHA-256 over the
original bytes and a 64-bit DCT perceptual hash. Exact byte matches are removed;
perceptual matches are removed only when their Hamming distance is within the
conservative configurable threshold (2 bits by default). Download or decode
failures keep the candidate instead of dropping it without evidence.

## AI verification

`POST /api/images/verify` verifies up to ten image candidates per request with a
configurable multimodal provider. The default `GeminiService` uses the official
`google-genai` package and Gemini Interactions API with a text prompt, image
bytes, and structured JSON output. Put `GEMINI_API_KEY` in `.env`; never commit
the key. Verification uses local rate limiting, timeouts, bounded retries, and
validates provider output again with Pydantic. A malformed response marks only
that image as `uncertain`, so the rest of the request can continue.

Statuses are derived conservatively from visual and source context. Visual
similarity alone is insufficient to establish affiliation. Low-confidence and
conflicting results become `uncertain`, while clear non-photos or irrelevant
images become `rejected`.

The opt-in live test uses real Wikimedia Commons images and incurs API usage:

```powershell
$env:RUN_LIVE_AI_TESTS = "1"
pytest tests/test_gemini_live.py -q
```

It remains skipped unless both the flag and `GEMINI_API_KEY` from `.env` exist.

## Profile pipeline

The gallery is displayed above statistics, including preliminary candidates.
Static UI responses are not cached so a stale script cannot hide new fields.
AI decides affiliation using pixels, signage, landmarks and source context;
there is no exact address/title/domain match required for acceptance.

Optional Google Maps place photos: set `GOOGLE_PLACES_API_KEY` in `.env` with
Places API (New) enabled. This is separate from the Gemini key. Place search
returns candidates, not confirmed affiliations; source links and photographer
attributions are retained and displayed. Profiles containing Google photos
are not cached. 2GIS public Places API does not expose photo downloads:
https://docs.2gis.com/en/api/search/places/examples/filtering

The UI places `preliminary_images` directly into the matching category gallery
after curated photos. Each card shows its verification status. Categories for
unverified candidates may come from the search topic rather than Gemini, and
these candidates never count as confirmed. Candidates marked non-photos or
irrelevant are hidden from the gallery. Rejected images remain hidden.
Technical warnings and deduplication counts remain in the API response but
are not shown in the consumer UI.
Up to six AI checks now run concurrently, sharing a service-wide semaphore and
the provider's rate limiter; the 29-second server deadline still applies.

Image discovery combines the resolved university's official website, Openverse
(original publisher URLs and available licence/author metadata), and Wikimedia
Commons. Official images do not receive an invented licence or automatic
confirmation: Gemini still evaluates the pixels and source context.

Generation is capped at 29 seconds on the server (30 seconds in the UI).
Resolution shares that budget, discovery has an eight-second deadline, and
deduplication receives up to 3.5 seconds. AI uses the remaining budget.
Up to six candidates per search category are checked in parallel batches. Unfinished checks are
reported in warnings and never presented as confirmed. Partial profiles have
a maximum 15-second cache lifetime so temporary failures can be retried soon.
Successful profiles retain the normal configured TTL. Cache keys are exact
normalized queries: ambiguous aliases are always resolved independently.

Generate a profile with:

```powershell
Invoke-RestMethod `
  -Method Post `
  -ContentType application/json `
  -Body '{"query":"Nazarbayev University"}' `
  http://127.0.0.1:8000/api/profile/generate
```

Independent Wikimedia searches and Gemini checks run concurrently with bounded
AI concurrency. Rejected and unverified images are not exposed in category
lists. Per-image failures are isolated, while `warnings` reports stage-level
partial failures. The complete request has a configurable timeout and successful
profiles are cached in memory by normalized name/alias for 15 minutes by
default. Configure these controls with `PROFILE_TIMEOUT_SECONDS`,
`PROFILE_CACHE_TTL_SECONDS`, `PROFILE_LIMIT_PER_QUERY`, and
`PROFILE_AI_CONCURRENCY`.

A real saved response is available at
`examples/nazarbayev_university_profile.json`.
# Google Images → Gemini selection

## Runtime fixes and verified configuration

The default vision model is now `gemini-3.5-flash-lite` (override with
`GEMINI_MODEL` in `.env`), using the same official SDK and Interactions API.
This [multimodal low-latency model](https://ai.google.dev/gemini-api/docs/models/gemini-3.5-flash-lite)
replaces the slower model for the 30-second interactive workflow.
Resolver requests omit `maxlag`, as permitted for
[interactive MediaWiki requests](https://www.mediawiki.org/wiki/Manual:Maxlag_parameter).
Successful resolutions are cached for one hour by exact normalized query,
without assuming aliases are unambiguous.

Image downloads follow redirects, have per-image deadlines, retain completed
hashing results when another image is slow, and do not retry failed downloads
sequentially during deduplication. Google search gets up to eight seconds;
the overall backend deadline remains 29 seconds. External source failures can
still produce partial results; no system can guarantee an external API response.

Live checks on 2026-09-18: NU returned 10 AI-verified images across all six
categories (~6.8s), Harvard 11 across five categories (~6.8s). All ten NU
category images loaded in a real Edge browser. These are observed timings,
not a latency guarantee. Example: `examples/working_nu_profile.json`;
UI screenshot: `examples/ui-working.png`.

Set `SERPAPI_API_KEY` in `.env` and restart the backend. This is a separate
credential from `GEMINI_API_KEY`; do not commit either key. The integration uses
[SerpAPI Google Images](https://serpapi.com/google-images-api), not Google Places.
Provider quotas/pricing apply: up to nine image searches per uncached profile.

With the search key configured, Google Images joins the other discovery
sources: category queries, six candidates per query by default
(`PROFILE_LIMIT_PER_QUERY=5` for five), capped at six. Original image and
publisher-page URLs are retained; unknown authors and licenses remain null.
Search results are not proof of affiliation or permission to reuse an image.
Without the key, existing sources remain available with an explicit warning.

Gemini compares the candidates in one multimodal request per category, with
bounded concurrency and the existing 29-second backend deadline. It judges
affiliation, actual category and visual quality; no exact-address gate is used.
Only real, relevant `likely`/`confirmed` images with quality >= 0.6 enter curated
categories. Highest quality first, with `is_primary=true` on the best image;
other qualifying images are included. `quality_score` is an AI assessment, not
proof of provenance. Failed/unavailable checks remain marked as unverified
within their category, never silently confirmed. A Gemini quota error cannot be
fixed by changing the prompt: restore quota to enable AI selection.

### Expanded selection and Other

The profile now returns ten category keys including `other` (UI: «Другое»).
Discovery searches eight university topics plus the city when known. The
Google Images source can yield up to 54 candidates before URL/content deduplication.
Gemini compares at most six images per call and marks `is_interesting` plus
`interest_reason`. Relevant, clear photos with identifiable interesting subjects
that do not fit the main categories may enter `other`; AI must explicitly flag
them as interesting and explain why. It is not a fallback for unavailable checks,
rejections or poor-quality photos, and may legitimately remain empty.
Hashing preserves completed results when the 3.5-second stage budget expires.
AI selection uses the remaining overall 29-second budget and returns completed
batches without waiting for slow batches. The UI stops waiting at 30 seconds;
external network/rendering latency cannot be guaranteed by the backend.

Live expanded-selection checks: NU 32 found / 28 verified in 10.94s;
Harvard 36 found / 32 verified in 14.62s. Both returned HTTP 200 with explicit
partial-result warnings. No `other` candidates were selected in these samples.
