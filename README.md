# University Visual Profile API

Backend foundation for building structured university visual profiles. This
stage contains data contracts, a health endpoint, a university resolver backed
by Wikidata, image discovery through Wikimedia Commons, deduplication, and
multimodal verification through Gemini.

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

The API is available at `http://127.0.0.1:8000`. Check it with:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

## Test

```powershell
pytest
```

## Current API

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

Image discovery runs six bounded queries (`campus`, `library`, `dormitory`,
`students`, `classroom`, and `building`) with a maximum of five results per
query. Results remain unverified: `is_real_photo` and `is_relevant` are `null`
until a later verification stage.

## Deduplication

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

Statuses are derived conservatively: `confirmed` additionally requires explicit
university evidence in title/description or an official-domain source; visual
similarity alone can produce at most `likely`. Low-confidence and conflicting
results become `uncertain`, while clear non-photos or irrelevant images become
`rejected`.

The opt-in live test uses real Wikimedia Commons images and incurs API usage:

```powershell
$env:RUN_LIVE_AI_TESTS = "1"
pytest tests/test_gemini_live.py -q
```

It remains skipped unless both the flag and `GEMINI_API_KEY` from `.env` exist.

## Profile pipeline

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
