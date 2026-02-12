## Generation Metadata
- **Model:** cns-k2.5-glm5-n1-3i-rank-gpt120b
- **Conversation ID:** 01kh7f7f5axh614awexkdxpbhx
- **Context Files:** owasp-social-osint-agent/docker-compose.yml, owasp-social-osint-agent/Dockerfile, owasp-social-osint-agent/env.example, owasp-social-osint-agent/input.json.example, owasp-social-osint-agent/LICENSE.md, owasp-social-osint-agent/README_CNS_1.md, owasp-social-osint-agent/README_CNS_2.md, owasp-social-osint-agent/socialosintagent/analyzer.py, owasp-social-osint-agent/socialosintagent/cache.py, owasp-social-osint-agent/socialosintagent/client_manager.py, owasp-social-osint-agent/socialosintagent/cli_handler.py, owasp-social-osint-agent/socialosintagent/exceptions.py, owasp-social-osint-agent/socialosintagent/__init__.py, owasp-social-osint-agent/socialosintagent/llm.py, owasp-social-osint-agent/socialosintagent/main.py, owasp-social-osint-agent/socialosintagent/platforms/bluesky.py, owasp-social-osint-agent/socialosintagent/platforms/github.py, owasp-social-osint-agent/socialosintagent/platforms/hackernews.py, owasp-social-osint-agent/socialosintagent/platforms/__init__.py, owasp-social-osint-agent/socialosintagent/platforms/mastodon.py, owasp-social-osint-agent/socialosintagent/platforms/reddit.py, owasp-social-osint-agent/socialosintagent/platforms/twitter.py, owasp-social-osint-agent/socialosintagent/prompts/image_analysis.prompt, owasp-social-osint-agent/socialosintagent/prompts/system_analysis.prompt, owasp-social-osint-agent/socialosintagent/pytest.ini, owasp-social-osint-agent/socialosintagent/tests/conftest.py, owasp-social-osint-agent/socialosintagent/tests/__init__.py, owasp-social-osint-agent/socialosintagent/tests/test_analyzer.py, owasp-social-osint-agent/socialosintagent/tests/test_cache.py, owasp-social-osint-agent/socialosintagent/tests/test_cli_handler.py, owasp-social-osint-agent/socialosintagent/tests/test_github.py, owasp-social-osint-agent/socialosintagent/tests/test_llm.py, owasp-social-osint-agent/socialosintagent/tests/test_reddit.py, owasp-social-osint-agent/socialosintagent/tests/test_twitter.py, owasp-social-osint-agent/socialosintagent/tests/test_utils.py, owasp-social-osint-agent/socialosintagent/utils.py
- **Original Prompt:**
```text
Conduct a rigorous audit of the codebase to synthesize a definitive technical README. The documentation should be characterized by structural clarity and technical sobriety, articulating the project’s current architecture, functional utility, and strategic trajectory without recourse to hyperbole.
```

# OWASP Social OSINT Agent: Technical Specification

## 1. System Architecture

### 1.1 Component Graph

```
┌─────────────────────────────────────────────────────────────┐
│                         Entry Points                          │
│  • socialosintagent/main.py (CLI/argparse)                   │
│  • --stdin mode (JSON ingestion)                              │
│  • Interactive TUI (rich library)                             │
└──────────────────────────┬──────────────────────────────────┘
                           │
              ┌────────────▼────────────┐
              │   SocialOSINTAgent      │
              │   (analyzer.py)         │
              │  • analyze()            │
              │  • _fetch_all_platform_ │
              │    _data()              │
              │  • _perform_vision_     │
              │    _analysis()          │
              └───────────┬─────────────┘
                          │
        ┌─────────────────┼─────────────────┐
        │                 │                 │
┌───────▼──────┐ ┌────────▼────────┐ ┌─────▼──────┐
│CacheManager  │ │  ClientManager  │ │LLMAnalyzer │
│(cache.py)    │ │(client_manager. │ │(llm.py)    │
│              │ │    py)          │ │            │
│• load()      │ │• get_platform_  │ │• analyze_  │
│• save()      │ │  _client()      │ │  image()   │
│• get_cache_  │ │• get_mastodon_  │ │• run_      │
│  _path()     │ │  _clients()     │ │  analysis()│
└───────┬──────┘ └────────┬────────┘ └─────┬──────┘
        │                 │                 │
        │    ┌────────────▼─────────────┐   │
        │    │    Platform Fetchers     │   │
        │    │    (platforms/*.py)      │   │
        │    │  • fetch_data() contract │   │
        │    │  • NormalizedUserData    │   │
        │    └──────────────────────────┘   │
        │                                   │
        └───────────────────────────────────┘
                    (Data Flow)
```

### 1.2 Interface Contracts

**SocialOSINTAgent Initialization** (`analyzer.py:39-53`):
```python
def __init__(
    self, 
    args: argparse.Namespace, 
    cache_manager: CacheManager, 
    llm_analyzer: LLMAnalyzer, 
    client_manager: ClientManager
)
```

**Fetcher Function Signature** (enforced via `platforms/__init__.py`):
```python
def fetch_data(
    username: str,
    cache: CacheManager,
    force_refresh: bool = False,
    fetch_limit: int = 50,
    **kwargs
) -> Optional[UserData]
```

## 2. Data Model Specification

### 2.1 Core Types (`utils.py:25-56`)

```python
class NormalizedProfile(TypedDict, total=False):
    platform: str              # lowercase identifier
    id: str                    # platform-native UUID
    username: str              # canonical handle
    display_name: Optional[str]
    bio: Optional[str]
    created_at: Optional[datetime]  # timezone-aware UTC
    profile_url: str           # HTTPS permalink
    metrics: Dict[str, int]    # {followers: int, ...}

class NormalizedMedia(TypedDict, total=False):
    url: str                   # source URL
    local_path: Optional[str]  # data/media/{md5_hash}.{ext}
    type: str                  # Enum: "image", "video", "gif"
    analysis: Optional[str]    # cached vision output

class NormalizedPost(TypedDict, total=False):
    platform: str
    id: str
    created_at: datetime       # sorted via get_sort_key()
    author_username: str
    text: str
    media: List[NormalizedMedia]
    external_links: List[str]  # extracted via URL_REGEX
    post_url: str
    metrics: Dict[str, int]
    type: str                  # Enum values vary by platform
    context: Optional[Dict]    # platform-specific metadata

class UserData(TypedDict, total=False):
    profile: NormalizedProfile
    posts: List[NormalizedPost]
    timestamp: datetime        # cache write timestamp
    stats: Dict[str, Any]      # {total_posts_cached: int}
```

### 2.2 Cache Schema

**Filesystem Layout:**
```
data/
├── cache/
│   └── {platform}_{safe_username}.json   # max 100 char filename
├── media/
│   └── {md5_hash}.{jpg|png|gif|webp|mp4|webm}
└── outputs/
    └── analysis_{timestamp}_{platforms}_{query}.{md|json}
```

**Cache File Structure:**
```json
{
  "profile": { ... },
  "posts": [ ... ],
  "timestamp": "2024-01-15T10:30:00+00:00",
  "stats": {"total_posts_cached": 50}
}
```

**TTL Logic** (`cache.py:56-89`):
- Online mode: `CACHE_EXPIRY_HOURS = 24` (datetime comparison in UTC)
- Offline mode (`is_offline=True`): Stale entries returned regardless of age
- Validation: Files missing required keys (`timestamp`, `profile`, `posts`) are deleted

## 3. Implementation Details

### 3.1 Username Sanitization (`utils.py:77-82`)

```python
def sanitize_username(username: str) -> str:
    normalized = unicodedata.normalize('NFKC', username)
    sanitized = "".join(
        ch for ch in normalized 
        if unicodedata.category(ch)[0] != 'C'  # strip control chars
    )
    return sanitized[:100]  # filesystem limit
```

### 3.2 Media Deduplication (`utils.py:157-180`)

**Hash Generation:**
```python
url_hash = hashlib.md5(url.encode()).hexdigest()
```

**Storage Logic:**
1. Check existing files: `{url_hash}.{ext}` for all supported extensions
2. Download via `httpx` with platform-specific auth headers:
   - Twitter: `Authorization: Bearer {token}`
   - Bluesky: `Authorization: Bearer {jwt}`
3. Content-type validation against whitelist:
   - Images: `image/jpeg`, `image/png`, `image/gif`, `image/webp`
   - Video: `video/mp4`, `video/webm`

### 3.3 Rate Limit Handling (`utils.py:85-118`)

**Exception Translation:**
- `tweepy.TooManyRequests` → parses `x-rate-limit-reset` header (Unix timestamp)
- `httpx.HTTPStatusError(429)` → raises `RateLimitExceededError`
- `prawcore.exceptions.RequestException(429)` → catches Reddit rate limits

**Reset Time Calculation:**
```python
reset_time = datetime.fromtimestamp(
    int(headers["x-rate-limit-reset"]), 
    tz=timezone.utc
)
wait_duration = reset_time - datetime.now(timezone.utc)
```

## 4. LLM Integration

### 4.1 Vision Pipeline (`llm.py:58-108`)

**Image Preprocessing:**
1. Load via PIL
2. Handle animated GIFs: `image.seek(0)` (first frame only)
3. Convert to RGB if necessary
4. Resize: `thumbnail((1536, 1536), Image.Resampling.LANCZOS)`
5. Save temporary JPEG (quality=85)
6. Base64 encode: `base64.b64encode(image_bytes).decode("utf-8")`
7. Data URI format: `data:image/jpeg;base64,{encoded}`

**Prompt Template:**
- Source: `socialosintagent/prompts/image_analysis.prompt`
- Context injection: `format(context=f"{platform} user {username}")`

### 4.2 Analysis Pipeline (`llm.py:141-178`)

**Data Aggregation:**
1. `_format_user_data_summary()`: Serializes UserData to Markdown (max 25 posts/user)
2. `_analyze_shared_links()`: Domain frequency analysis excluding:
   - twitter.com, x.com, t.co
   - reddit.com, redd.it
   - bsky.app
   - news.ycombinator.com
3. Media analysis collation with preserved Markdown links: `[{url}]({url})`

**System Prompt:**
- Source: `socialosintagent/prompts/system_analysis.prompt`
- Temporal injection: `{current_timestamp}` formatted as `%Y-%m-%d %H:%M:%S UTC`

## 5. Platform-Specific Implementation

### 5.1 Authentication Patterns

| Platform | Client Type | Auth Method | Validation |
|----------|-------------|-------------|------------|
| Twitter | `tweepy.Client` | Bearer Token | `get_user(username="twitterdev")` |
| Reddit | `praw.Reddit` | OAuth2 (read-only) | Client initialization only |
| Bluesky | `atproto.Client` | App Password | `client.login()` with JWT |
| Mastodon | `mastodon.Mastodon` | Access Token | `client.instance()` |
| GitHub | `httpx.Client` | Bearer Token (optional) | Header injection |

### 5.2 Fetcher Behaviors

**Twitter** (`platforms/twitter.py`):
- Pagination: `pagination_token` from `response.meta`
- Media extraction: `expansions=["attachments.media_keys"]`
- Min fetch: `MIN_API_FETCH_LIMIT = 5`

**Reddit** (`platforms/reddit.py`):
- Parallel streams: `redditor.submissions.new()` + `redditor.comments.new()`
- Gallery handling: `submission.media_metadata` iteration
- Self-post detection: `submission.is_self`

**Bluesky** (`platforms/bluesky.py`):
- CDN URL construction: `https://cdn.bsky.app/img/feed_fullsize/plain/{did}/{cid}@{mime}`
- Cursor pagination: `response.cursor`

**Mastodon** (`platforms/mastodon.py`):
- Instance routing: Extract domain from `user@instance` format
- Client selection: `clients.get(f"https://{domain}")` or fallback to default
- HTML sanitization: `BeautifulSoup(note, "html.parser").get_text()`

**GitHub** (`platforms/github.py`):
- Event API: `/users/{username}/events/public`
- ETag support: Conditional requests via headers (implemented in `httpx` client)
- Rate limit headers: `x-ratelimit-remaining`, `x-ratelimit-reset`

**HackerNews** (`platforms/hackernews.py`):
- Algolia API: `hn.algolia.com/api/v1/search?tags=author_{username}`
- Max hits: `ALGOLIA_MAX_HITS = 1000`
- Type detection: `"comment" in hit.get("_tags", [])`

## 6. Operational Interface

### 6.1 CLI Commands (Interactive Mode)

**Session Commands:**
- `loadmore [platform/user] <count>`: Adjusts `fetch_options["targets"][f"{platform}:{username}"]["count"]`
- `refresh`: Sets `force_refresh=True`, invalidates cache
- `cache status`: Displays table with columns:
  - Platform, Username, Last Fetched (UTC), Age, Items, Media (Analyzed/Found)
- `purge data`: Interactive deletion of cache/media/outputs subdirectories

**Argument Parsing:**
```python
parser.add_argument("--stdin", action="store_true")
parser.add_argument("--format", choices=["json", "markdown"], default="markdown")
parser.add_argument("--no-auto-save", action="store_true")
parser.add_argument("--offline", action="store_true")
parser.add_argument("--log-level", choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"])
```

### 6.2 STDIN Mode (Programmatic)

**Input Schema:**
```json
{
  "platforms": {
    "twitter": ["handle1"],
    "reddit": ["username1", "username2"]
  },
  "query": "string",
  "fetch_options": {
    "default_count": 50,
    "targets": {
      "twitter:handle1": {"count": 200}
    }
  }
}
```

**Exit Codes:**
- `0`: Success
- `1`: Configuration or input validation error
- `2`: Analysis execution error

## 7. Testing Structure

**Test Organization** (`socialosintagent/tests/`):

| Test File | Target | Mock Strategy |
|-----------|--------|---------------|
| `test_analyzer.py` | Orchestration logic | `create_autospec` for dependencies, `patch` for FETCHERS |
| `test_cache.py` | TTL, offline mode | `tmp_path` fixture for filesystem isolation |
| `test_cli_handler.py` | Command parsing | `MagicMock` for console output capture |
| `test_github.py` | HTTP client | `httpx.Client` context manager patching |
| `test_reddit.py` | PRAW integration | `MagicMock` for `praw.Reddit` and `Redditor` objects |
| `test_twitter.py` | Tweepy pagination | `MagicMock(spec=tweepy.Client)` for type safety |
| `test_llm.py` | Prompt formatting | `patch('_load_prompt')` to avoid filesystem access |
| `test_utils.py` | Sanitization, URL extraction | Direct function testing |

**Fixture Pattern:**
```python
@pytest.fixture
def mock_dependencies(mocker):
    mock_cache = create_autospec(CacheManager, instance=True)
    mock_llm = create_autospec(LLMAnalyzer, instance=True)
    mock_client_manager = create_autospec(ClientManager, instance=True)
    return mock_cache, mock_llm, mock_client_manager
```

## 8. Deployment Configuration

### 8.1 Docker Specification

**Dockerfile:**
- Base: `python:3.11`
- Layer caching: `COPY requirements.txt` before source code
- Directories: `mkdir -p /app/data /app/logs`
- Entrypoint: `CMD ["python", "-m", "socialosintagent.main"]`

**docker-compose.yml:**
```yaml
services:
  social-osint-agent:
    build: .
    image: social-osint-agent:latest
    volumes:
      - ./data:/app/data
      - ./logs:/app/logs
    env_file: .env
    tty: true        # Required for interactive CLI
    stdin_open: true
```

### 8.2 Environment Requirements

**Required:**
- `LLM_API_KEY`
- `LLM_API_BASE_URL`
- `ANALYSIS_MODEL`
- `IMAGE_ANALYSIS_MODEL`

**Platform-Specific (conditional based on target platforms):**
- `TWITTER_BEARER_TOKEN`
- `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET`, `REDDIT_USER_AGENT`
- `BLUESKY_IDENTIFIER`, `BLUESKY_APP_SECRET`
- `MASTODON_INSTANCE_{N}_URL`, `MASTODON_INSTANCE_{N}_TOKEN` (indexed from 1)

**Optional:**
- `OPENROUTER_REFERER`
- `OPENROUTER_X_TITLE`
- `GITHUB_TOKEN` (increases rate limits from 60 to 5000 req/hour)

## 9. Development Trajectory

### 9.1 Architectural Migration Targets

**Asynchronous I/O:**
- Replace synchronous `httpx` calls with `AsyncClient`
- Implement `asyncio.gather()` for parallel platform fetching
- Per-platform semaphore enforcement for rate limit compliance

**Vector Storage:**
- Integration point: Post-analysis phase in `analyzer.py`
- Implementation: ChromaDB or SQLite-VSS for embedding storage
- Use case: Semantic search across cached investigations, context window management for large datasets

### 9.2 Data Export Formats

**Graph Representation:**
- Target: Neo4j or GraphML
- Nodes: Users, posts, domains, media hashes
- Edges: Authorship, mentions, shared links, temporal proximity

**Threat Intelligence:**
- STIX 2.1 bundle export
- Observable types: User accounts, file hashes (media), domain names

### 9.3 Forensic Enhancements

**Metadata Extraction:**
- Library: `piexif` or `exiftool` wrapper
- Execution: Pre-vision analysis in `_perform_vision_analysis()`
- Data injection: EXIF GPS/device data into image analysis prompt

**Perceptual Hashing:**
- Library: `imagehash` (phash, dhash)
- Application: Cross-platform image similarity detection
- Storage: Additional field in `NormalizedMedia` hash map

### 9.4 Extensibility

**Plugin Architecture:**
- Entry point: Dynamic import of `plugins/*.py`
- Contract: Abstract base class enforcing `fetch_data()` signature
- Registration: Runtime addition to `FETCHERS` dictionary without core modification
