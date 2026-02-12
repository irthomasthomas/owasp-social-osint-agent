## Generation Metadata
- **Model:** cns-opus46-g3flash-k25-glm5-synth-i2
- **Conversation ID:** 01kh7fxczjzd3s8jbqd4mdgtfa
- **Context Files:** owasp-social-osint-agent/docker-compose.yml, owasp-social-osint-agent/Dockerfile, owasp-social-osint-agent/env.example, owasp-social-osint-agent/input.json.example, owasp-social-osint-agent/LICENSE.md, owasp-social-osint-agent/README_CNS_1.md, owasp-social-osint-agent/README_CNS_2.md, owasp-social-osint-agent/socialosintagent/analyzer.py, owasp-social-osint-agent/socialosintagent/cache.py, owasp-social-osint-agent/socialosintagent/client_manager.py, owasp-social-osint-agent/socialosintagent/cli_handler.py, owasp-social-osint-agent/socialosintagent/exceptions.py, owasp-social-osint-agent/socialosintagent/__init__.py, owasp-social-osint-agent/socialosintagent/llm.py, owasp-social-osint-agent/socialosintagent/main.py, owasp-social-osint-agent/socialosintagent/platforms/bluesky.py, owasp-social-osint-agent/socialosintagent/platforms/github.py, owasp-social-osint-agent/socialosintagent/platforms/hackernews.py, owasp-social-osint-agent/socialosintagent/platforms/__init__.py, owasp-social-osint-agent/socialosintagent/platforms/mastodon.py, owasp-social-osint-agent/socialosintagent/platforms/reddit.py, owasp-social-osint-agent/socialosintagent/platforms/twitter.py, owasp-social-osint-agent/socialosintagent/prompts/image_analysis.prompt, owasp-social-osint-agent/socialosintagent/prompts/system_analysis.prompt, owasp-social-osint-agent/socialosintagent/pytest.ini, owasp-social-osint-agent/socialosintagent/tests/conftest.py, owasp-social-osint-agent/socialosintagent/tests/__init__.py, owasp-social-osint-agent/socialosintagent/tests/test_analyzer.py, owasp-social-osint-agent/socialosintagent/tests/test_cache.py, owasp-social-osint-agent/socialosintagent/tests/test_cli_handler.py, owasp-social-osint-agent/socialosintagent/tests/test_github.py, owasp-social-osint-agent/socialosintagent/tests/test_llm.py, owasp-social-osint-agent/socialosintagent/tests/test_reddit.py, owasp-social-osint-agent/socialosintagent/tests/test_twitter.py, owasp-social-osint-agent/socialosintagent/tests/test_utils.py, owasp-social-osint-agent/socialosintagent/utils.py
- **Original Prompt:**
```text
Conduct a rigorous audit of the codebase to synthesize a definitive technical README. The documentation should be characterized by structural clarity and technical sobriety, articulating the project’s current architecture, functional utility, and strategic trajectory without recourse to hyperbole.
```

# OWASP Social OSINT Agent: Technical Specification

**Autonomous Multi-Platform Social Media Intelligence Framework**

---

## 1. Overview
The OWASP Social OSINT Agent is a Python-based engine designed for the automated collection, normalization, and analysis of public social media data. The system utilizes platform-specific APIs to aggregate user activity, normalizes heterogeneous data into a unified schema, and applies vision-capable Large Language Models (LLMs) to synthesize evidence-based investigative reports.

The system operates through a **two-phase pipeline**:
1. **Data Acquisition:** Sequential fetching of text and media from configured platform APIs. Results are cached locally as JSON with a 24-hour TTL. Media files are deduplicated by MD5 hash of the source URL.
2. **Analysis Synthesis:** Vision analysis of images via vision-LLMs, followed by aggregated text analysis. The system injects a real-time UTC timestamp into prompts to ground the model’s temporal reasoning and ensures all analytical claims are linked to original source URLs.

---

## 2. Architecture and Data Flow

### 2.1 Component Map
```text
socialosintagent/
├── main.py                 # Entry point; parses arguments and initializes dependencies
├── analyzer.py             # Pipeline Orchestrator; manages fetch → vision → synthesis
├── cli_handler.py          # Interactive TUI; session management and report persistence
├── client_manager.py       # Lazy API client initialization and credential validation
├── cache.py                # File-based JSON cache with 24h TTL and offline override
├── llm.py                  # LLM integration; formats summaries and manages API calls
├── utils.py                # Shared TypedDict schemas, media download, and URL extraction
├── platforms/              # Platform-specific adapters (Twitter, Reddit, Bluesky, etc.)
└── prompts/                # Externalized prompt templates (System & Image analysis)
```

### 2.2 Data Flow Logic
1. **Request:** Targets are provided via interactive CLI or STDIN (JSON).
2. **Cache Check:** `CacheManager` checks for existing valid data (<24h).
3. **Acquisition:** If cache is missing/expired, platform fetchers retrieve data. `download_media()` persists images locally.
4. **Normalization:** Platform-specific responses are coerced into the unified `UserData` schema.
5. **Enrichment:** Unanalyzed images are processed by the `IMAGE_ANALYSIS_MODEL`. Results are written back to the cache to prevent redundant API calls.
6. **Synthesis:** `LLMAnalyzer` formats the data (max 25 posts/user) and submits it to the `ANALYSIS_MODEL` for report generation.

---

## 3. Data Model
All data is normalized into a common `UserData` structure to ensure interoperability.

| Object | Description |
| :--- | :--- |
| **NormalizedProfile** | Platform, ID, handle, bio, and account metrics (followers, karma, repos). |
| **NormalizedPost** | Content units including text, timestamps, engagement metrics, and extracted links. |
| **NormalizedMedia** | Media metadata including `local_path` and `analysis` (vision LLM output). |

---

## 4. Supported Platforms

| Platform | Access Method | Media Support | Implementation Detail |
| :--- | :--- | :--- | :--- |
| **Twitter/X** | Tweepy (v2 API) | Yes | Uses Bearer Token; handles pagination tokens. |
| **Reddit** | PRAW (OAuth2) | Yes | Merges submissions and comments; supports galleries. |
| **Bluesky** | atproto SDK | Yes | App-password login; downloads via CDN links. |
| **Mastodon** | Mastodon.py | Yes | Multi-instance support; instance-specific routing. |
| **GitHub** | httpx (REST v3) | No | Optional PAT; tracks public events (Push, Issue, PR). |
| **Hacker News** | Algolia API | No | Minimal profile data (no bio/karma available). |

---

## 5. Operational Interfaces

### 5.1 Interactive CLI (TUI)
Utilizes the `rich` library for terminal-based investigation.
* **Commands:** `loadmore <count>` (increase fetch depth), `refresh` (force update), `cache status` (inspect local data inventory), and `purge data` (selective deletion).

### 5.2 Programmatic STDIN
Enables non-interactive automation for batch processing.
```bash
cat investigation.json | docker-compose run --rm -T social-osint-agent --stdin --format json
```

### 5.3 Offline Mode
Initiated via `--offline`, the system bypasses all platform API calls. It relies entirely on stale cache and local media files. *Note: Text LLM synthesis still requires network access unless report generation is suppressed.*

---

## 6. Technical Audit and Debt

### 6.1 Known Limitations
* **Synchronous Bottleneck:** Platform fetching and LLM calls are sequential. Large investigations scale linearly with the number of targets.
* **Mastodon Pagination:** The current implementation retrieves only the first page (max 40 items) of statuses.
* **Redundant .env Loading:** Environment variables are processed twice in local mode (once at module import in `analyzer.py` and once in `main.py`).
* **Docker Mount Conflict:** `docker-compose.yml` mounts a host directory to a path the app expects to be a file (`/app/analyzer.log`), which may conflict with internal directory creation.

### 6.2 Testing Status
* **Covered:** Core normalization (Twitter, Reddit, GitHub), caching logic, username sanitization, and summary formatting.
* **Gaps:** Bluesky/Mastodon fetchers, media download verification, and full vision-analysis integration.

---

## 7. Strategic Trajectory
1. **Async Migration:** Transitioning to `asyncio` and `httpx.AsyncClient` for concurrent platform data retrieval.
2. **Knowledge Graph Export:** Implementing GraphML/Neo4j exports to visualize cross-platform user interactions.
3. **RAG Integration:** Utilizing a vector database (e.g., ChromaDB) to handle large-scale datasets exceeding the LLM's context window.
4. **Media Forensics:** Integrating EXIF extraction to correlate visual data with device/GPS metadata.

---

## 8. License
Distributed under the **MIT License**.