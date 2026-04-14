# OSINT Social Agent — REST API v1

Base URL: `/api/v1`

Authentication: HTTP Basic Auth (optional, configured via `OSINT_WEB_USER` and
`OSINT_WEB_PASSWORD` environment variables).

All request/response bodies are JSON (`Content-Type: application/json`).

---

## Table of Contents

- [Platforms](#platforms)
- [Sessions](#sessions)
- [Analysis Jobs](#analysis-jobs)
- [Cache](#cache)
- [Contacts / Network](#contacts--network)
- [Export, Timeline, Media](#export-timeline-media)
- [Data Models](#data-models)

---

## Platforms

### `GET /platforms`

Returns which social platforms have credentials configured and are available for
data collection.

**Response** `200`

```json
{
  "platforms": [
    { "name": "bluesky", "available": true, "reason": null },
    { "name": "twitter", "available": false, "reason": "API credentials not configured" },
    { "name": "mastodon", "available": true, "reason": null },
    { "name": "reddit", "available": false, "reason": "API credentials not configured" },
    { "name": "github", "available": false, "reason": "API credentials not configured" },
    { "name": "hackernews", "available": true, "reason": null }
  ]
}
```

---

## Sessions

Sessions are the primary organisational unit. Each session groups a set of
targets, fetch options, and a chronological query history with analysis results.

### `GET /sessions`

List all sessions, sorted by most recently updated.

**Response** `200`

```json
{
  "sessions": [
    {
      "session_id": "a1b2c3d4-...",
      "name": "Target Alpha Assessment",
      "platforms": { "bluesky": ["user.bsky.social"], "twitter": ["user123"] },
      "target_count": 2,
      "query_count": 5,
      "recent_queries": [
        { "query_id": "e5f6a7b8", "query": "What are the target's interests?", "timestamp": "2025-04-14T10:30:00+00:00" }
      ],
      "created_at": "2025-04-10T08:00:00+00:00",
      "updated_at": "2025-04-14T10:30:00+00:00"
    }
  ]
}
```

### `POST /sessions`

Create a new session with one or more targets across one or more platforms.

**Request Body**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | `string` | Yes | Human-readable session name (1-100 chars) |
| `platforms` | `object` | Yes | Map of platform name to array of usernames |
| `fetch_options` | `object` | No | Fetch configuration |

**Example — Single target:**

```json
{
  "name": "Quick Lookup",
  "platforms": { "bluesky": ["alice.bsky.social"] },
  "fetch_options": { "default_count": 50, "targets": {} }
}
```

**Example — Multiple targets, multiple platforms:**

```json
{
  "name": "Cross-Platform Assessment",
  "platforms": {
    "bluesky": ["alice.bsky.social", "bob.bsky.social"],
    "twitter": ["alice_tw"],
    "reddit": ["alice_reddit"]
  },
  "fetch_options": { "default_count": 100, "targets": {} }
}
```

**Valid platform names:** `bluesky`, `twitter`, `mastodon`, `reddit`, `github`,
`hackernews`.

**Response** `201`

Returns the full session object (see `GET /sessions/{session_id}`).

**Errors**

| Status | Detail |
|--------|--------|
| 422 | Validation error: empty platforms, unknown platform, missing name, etc. |

### `GET /sessions/{session_id}`

Get a full session including all query history and analysis reports.

**Response** `200`

```json
{
  "session_id": "a1b2c3d4-...",
  "name": "Target Alpha Assessment",
  "platforms": { "bluesky": ["user.bsky.social"] },
  "fetch_options": { "default_count": 50, "targets": {} },
  "query_history": [
    {
      "query_id": "e5f6a7b8",
      "query": "What are the target's main interests?",
      "report": "# OSINT Analysis Report\n\n...",
      "metadata": {
        "query": "What are the target's main interests?",
        "targets": { "bluesky": ["user.bsky.social"] },
        "generated_utc": "2025-04-14 10:30:00 UTC",
        "mode": "Online",
        "models": { "text": "gpt-4o", "image": "gpt-4o-mini" },
        "fetch_stats": { "successful": 1, "failed": 0, "rate_limited": 0 },
        "vision_stats": { "total": 3, "analyzed": 3, "failed": 0, "skipped": 0 },
        "llm_usage": {
          "text": { "prompt_tokens": 8420, "completion_tokens": 1530, "total_tokens": 9950 },
          "vision": { "prompt_tokens": 2100, "completion_tokens": 450, "total_tokens": 2550 }
        }
      },
      "entities": {
        "locations": ["London, UK"],
        "emails": [],
        "phones": [],
        "crypto": [],
        "aliases": ["@user_handle"]
      },
      "timestamp": "2025-04-14T10:30:00+00:00"
    }
  ],
  "dismissed_contacts": ["twitter/sometroll"],
  "created_at": "2025-04-10T08:00:00+00:00",
  "updated_at": "2025-04-14T10:30:00+00:00"
}
```

### `DELETE /sessions/{session_id}`

Permanently delete a session and all its query history.

**Response** `200`

```json
{ "deleted": "a1b2c3d4-..." }
```

### `PATCH /sessions/{session_id}/rename`

Rename a session.

**Request Body**

```json
{ "name": "New Session Name" }
```

**Response** `200` — Returns the session summary.

### `PUT /sessions/{session_id}/targets`

Replace the complete target list for a session. This is a full replacement, not
a delta — send the entire platforms dict you want to persist.

**Request Body**

```json
{
  "platforms": {
    "bluesky": ["user.bsky.social", "newuser.bsky.social"],
    "twitter": ["user123"]
  },
  "fetch_options": { "default_count": 50, "targets": {} }
}
```

**Response** `200` — Returns the updated session summary.

---

## Analysis Jobs

Analysis is asynchronous. Submitting a query returns a `job_id` immediately.
Track progress via SSE streaming or polling.

### `POST /sessions/{session_id}/analyse`

Start an analysis job for a session.

**Request Body**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `query` | `string` | Yes | Natural language analysis query (1-500 chars) |
| `force_refresh` | `boolean` | No | Bypass the 24h cache and re-fetch data (default: `false`) |

**Example**

```json
{ "query": "What social circles does this target interact with most?", "force_refresh": false }
```

**Response** `202`

```json
{ "job_id": "f7e6d5c4-...", "session_id": "a1b2c3d4-...", "status": "running" }
```

**Errors**

| Status | Detail |
|--------|--------|
| 404 | Session not found |
| 409 | Session already has a running analysis job |

### `GET /jobs/{job_id}`

Poll the status of an analysis job.

**Response** `200`

```json
{
  "job_id": "f7e6d5c4-...",
  "session_id": "a1b2c3d4-...",
  "status": "running",
  "query": "What social circles...",
  "query_id": null,
  "error": null,
  "progress": { "message": "Analyzing images...", "stage": "vision" }
}
```

| `status` | Meaning |
|-----------|---------|
| `running` | Job is in progress. `progress` contains the latest stage/message. |
| `complete` | Job finished. `query_id` references the new history entry. |
| `error` | Job failed. `error` contains the error message. |

### `GET /jobs/{job_id}/stream`

Stream live progress via Server-Sent Events (SSE). Replays all past events on
connection, so late-connecting clients (e.g. page reloads) catch up.

**Response** `200` (`Content-Type: text/event-stream`)

**Event types:**

| Event | Data | Description |
|-------|------|-------------|
| `stage` | `{ "stage": "fetch"|"vision"|"synthesis", "message": "..." }` | Major pipeline stage started |
| `log` | `{ "message": "..." }` | Informational log message |
| `status` | `{ "message": "..." }` | Status update |
| `complete` | `{ "message": "Analysis complete", "query_id": "...", "llm_usage": {...} }` | Job finished successfully. `llm_usage` contains token counts (see below). |
| `error` | `{ "message": "..." }` | Job failed |

**`llm_usage` structure (in `complete` event):**

```json
{
  "llm_usage": {
    "text": { "prompt_tokens": 8420, "completion_tokens": 1530, "total_tokens": 9950 },
    "vision": { "prompt_tokens": 2100, "completion_tokens": 450, "total_tokens": 2550 }
  }
}
```

---

## Cache

### `GET /cache`

Get the status of all cached platform data.

**Response** `200`

```json
{
  "entries": [
    {
      "platform": "bluesky",
      "username": "user.bsky.social",
      "post_count": 50,
      "media_found": 5,
      "media_analyzed": 5,
      "cached_at": "2025-04-14T10:00:00+00:00",
      "age_seconds": 1800,
      "is_fresh": true
    }
  ]
}
```

Cache entries are considered fresh for 24 hours (`is_fresh` field).

### `POST /cache/purge`

Purge cached data.

**Purge specific keys:**

```json
{ "targets": ["specific"], "keys": ["bluesky_user.bsky.social", "twitter_user123"] }
```

**Purge entire data categories:**

```json
{ "targets": ["cache"] }
```

Valid `targets` values: `cache`, `media`, `outputs`, `all`, `specific`.

**Response** `200`

```json
{ "purged": ["cache", "media"] }
```

---

## Contacts / Network

### `GET /sessions/{session_id}/contacts`

Get contacts discovered from cached post data — @mentions, retweets, repo
interactions, etc. Operates entirely on local cache (no API calls). Active
targets and dismissed contacts are excluded.

**Response** `200`

```json
{
  "contacts": [
    {
      "platform": "bluesky",
      "username": "contact_user",
      "interaction_types": ["mention", "reply"],
      "weight": 12,
      "first_seen": "2025-03-01T14:00:00+00:00",
      "last_seen": "2025-04-10T09:00:00+00:00"
    }
  ],
  "dismissed": ["twitter/troll_account"],
  "total_extracted": 25
}
```

### `POST /sessions/{session_id}/contacts/dismiss`

Hide a contact from the network panel.

**Request Body**

```json
{ "platform": "bluesky", "username": "noisy_contact" }
```

**Response** `200`

```json
{ "dismissed": "bluesky/noisy_contact" }
```

### `POST /sessions/{session_id}/contacts/undismiss`

Restore a previously dismissed contact.

**Request Body & Response** — Same shape as dismiss, with `undismissed` key.

---

## Export, Timeline, Media

### `GET /sessions/{session_id}/export`

Export the full session data as a downloadable JSON file, including all queries,
reports, entities, and extracted network contacts.

**Response** `200` — Full session JSON with `Content-Disposition: attachment` header.

### `GET /sessions/{session_id}/timeline`

Get timestamped events for a pattern-of-life heatmap. Returns post timestamps
from all cached targets.

**Response** `200`

```json
{
  "events": [
    { "timestamp": "2025-04-14T08:30:00+00:00", "platform": "bluesky", "author": "user.bsky.social" },
    { "timestamp": "2025-04-14T09:15:00+00:00", "platform": "bluesky", "author": "user.bsky.social" }
  ]
}
```

### `GET /sessions/{session_id}/media`

Get all downloaded media items for a session, including LLM vision analysis
results.

**Response** `200`

```json
{
  "media": [
    {
      "url": "https://cdn.example.com/img1.jpg",
      "path": "data/media/bluesky_user/img1.jpg",
      "analysis": "Image shows a city skyline with identifiable landmarks...",
      "post_id": "at://post/abc123",
      "platform": "bluesky",
      "author": "user.bsky.social"
    }
  ]
}
```

### `GET /sessions/{session_id}/media/file?path=...`

Serve a downloaded media file. The `path` query parameter must reference a file
within the `data/media/` directory.

**Query Parameters**

| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `path` | `string` | Yes | Absolute or relative path to the media file |

**Response** `200` — Binary file response (image/jpeg, etc.)

**Errors**

| Status | Detail |
|--------|--------|
| 403 | Requested path is outside the media directory |
| 404 | File not found |

---

## Data Models

### Session Object

| Field | Type | Description |
|-------|------|-------------|
| `session_id` | `string` | UUID |
| `name` | `string` | Human-readable name |
| `platforms` | `Dict[str, List[str]]` | Platform -> usernames |
| `fetch_options` | `object` | `{ "default_count": int, "targets": {} }` |
| `query_history` | `array` | Chronological list of query results |
| `dismissed_contacts` | `List[str]` | `"platform/username"` strings |
| `created_at` | `string` | ISO 8601 |
| `updated_at` | `string` | ISO 8601 |

### Query History Entry

| Field | Type | Description |
|-------|------|-------------|
| `query_id` | `string` | 8-char unique ID |
| `query` | `string` | The natural language query |
| `report` | `string` | Markdown analysis report |
| `metadata` | `object` | Generation metadata (see below) |
| `entities` | `object` | Extracted OSINT entities |
| `timestamp` | `string` | ISO 8601 |

### Analysis Metadata

| Field | Type | Description |
|-------|------|-------------|
| `query` | `string` | Original query |
| `targets` | `Dict[str, List[str]]` | Targets analysed |
| `generated_utc` | `string` | Report generation timestamp |
| `mode` | `string` | `"Online"` or `"Offline"` |
| `models` | `object` | `{ "text": "...", "image": "..." }` |
| `fetch_stats` | `object` | `{ "successful", "failed", "rate_limited" }` |
| `vision_stats` | `object` | `{ "total", "analyzed", "failed", "skipped" }` |
| `llm_usage` | `object` | Token usage breakdown (see below) |

### LLM Usage

Available in `metadata.llm_usage` on completed analyses and in the SSE
`complete` event.

```json
{
  "text": {
    "prompt_tokens": 8420,
    "completion_tokens": 1530,
    "total_tokens": 9950
  },
  "vision": {
    "prompt_tokens": 2100,
    "completion_tokens": 450,
    "total_tokens": 2550
  }
}
```

| Field | Description |
|-------|-------------|
| `text.prompt_tokens` | Tokens sent to the text analysis model |
| `text.completion_tokens` | Tokens generated by the text analysis model |
| `text.total_tokens` | Sum of text prompt + completion |
| `vision.prompt_tokens` | Tokens sent to the vision model (accumulated across all images) |
| `vision.completion_tokens` | Tokens generated by the vision model (accumulated) |
| `vision.total_tokens` | Sum of vision prompt + completion (accumulated) |

### Entities

```json
{
  "locations": ["string"],
  "emails": ["string"],
  "phones": ["string"],
  "crypto": ["string"],
  "aliases": ["string"]
}
```

### Error Response

All errors follow a standard envelope:

```json
{ "error": "Brief description", "detail": "Optional additional context" }
```

Common HTTP status codes: `400` (bad request), `404` (not found),
`409` (conflict), `422` (validation error), `500` (internal error).

---

## Running the Server

```bash
# Direct
uvicorn socialosintagent.web_server:app --host 0.0.0.0 --port 8000

# Docker
docker-compose up web

# With authentication
OSINT_WEB_USER=admin OSINT_WEB_PASSWORD=secret uvicorn socialosintagent.web_server:app
```
