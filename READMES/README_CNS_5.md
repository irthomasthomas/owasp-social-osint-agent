## Generation Metadata
- **Model:** cns-opus46-g3flash-k25-glm5-synth-i2
- **Conversation ID:** 01kh7hk6jf4pb4e4vr36cse9v3
- **Context Files:** owasp-social-osint-agent/docker-compose.yml, owasp-social-osint-agent/Dockerfile, owasp-social-osint-agent/env.example, owasp-social-osint-agent/input.json.example, owasp-social-osint-agent/LICENSE.md, owasp-social-osint-agent/README_CNS_1.md, owasp-social-osint-agent/README_CNS_2.md, owasp-social-osint-agent/socialosintagent/analyzer.py, owasp-social-osint-agent/socialosintagent/cache.py, owasp-social-osint-agent/socialosintagent/client_manager.py, owasp-social-osint-agent/socialosintagent/cli_handler.py, owasp-social-osint-agent/socialosintagent/exceptions.py, owasp-social-osint-agent/socialosintagent/__init__.py, owasp-social-osint-agent/socialosintagent/llm.py, owasp-social-osint-agent/socialosintagent/main.py, owasp-social-osint-agent/socialosintagent/platforms/bluesky.py, owasp-social-osint-agent/socialosintagent/platforms/github.py, owasp-social-osint-agent/socialosintagent/platforms/hackernews.py, owasp-social-osint-agent/socialosintagent/platforms/__init__.py, owasp-social-osint-agent/socialosintagent/platforms/mastodon.py, owasp-social-osint-agent/socialosintagent/platforms/reddit.py, owasp-social-osint-agent/socialosintagent/platforms/twitter.py, owasp-social-osint-agent/socialosintagent/prompts/image_analysis.prompt, owasp-social-osint-agent/socialosintagent/prompts/system_analysis.prompt, owasp-social-osint-agent/socialosintagent/pytest.ini, owasp-social-osint-agent/socialosintagent/tests/conftest.py, owasp-social-osint-agent/socialosintagent/tests/__init__.py, owasp-social-osint-agent/socialosintagent/tests/test_analyzer.py, owasp-social-osint-agent/socialosintagent/tests/test_cache.py, owasp-social-osint-agent/socialosintagent/tests/test_cli_handler.py, owasp-social-osint-agent/socialosintagent/tests/test_github.py, owasp-social-osint-agent/socialosintagent/tests/test_llm.py, owasp-social-osint-agent/socialosintagent/tests/test_reddit.py, owasp-social-osint-agent/socialosintagent/tests/test_twitter.py, owasp-social-osint-agent/socialosintagent/tests/test_utils.py, owasp-social-osint-agent/socialosintagent/utils.py
- **Original Prompt:**
```text
Draft a comprehensive technical specification for this project that mirrors the logic of the source code. Articulate the project’s objectives through the lens of understated elegance—replacing marketing jargon with technical rigor. Focus on the intersection of system design, realistic implementation, and the project’s long-term developmental trajectory.
```

# Technical Specification: OWASP Social OSINT Agent

**Document Version:** 1.1  
**Status:** Implementation Reference & Constraint Analysis  
**License:** MIT  

---

## 1. System Objective & Design Philosophy
The OWASP Social OSINT Agent is an autonomous intelligence framework designed for the deterministic collection, normalization, and synthesis of public social media data. The system prioritizes **forensic integrity** and **operational security** over processing velocity, employing a two-phase computational pipeline with a strict, platform-agnostic persistence layer.

The design philosophy emphasizes:
*   **Decoupling:** Separation of platform-specific API logic from analytical synthesis.
*   **Normalization:** Heterogeneous data is forced into a unified schema (`UserData`) before analysis.
*   **Temporal Anchoring:** Injection of UTC timestamps to mitigate LLM hallucinations regarding data freshness.

---

## 2. Architectural Topology
The system is implemented as a synchronous, single-threaded Python application orchestrated by five principal components.

### 2.1 Component Responsibilities
*   **Orchestrator (`analyzer.py`):** Manages the fetch-analyze-synthesize lifecycle. It handles error aggregation across multiple targets and manages the state of the investigation.
*   **Client Manager (`client_manager.py`):** A lazy factory for API clients. It instantiates Tweepy, PRAW, Atproto, or Mastodon clients only upon request and memoizes them for the session.
*   **Cache Manager (`cache.py`):** A file-based JSON persistence layer. It maps `(platform, username)` pairs to sanitized filenames and enforces a 24-hour TTL for online operations.
*   **LLM Analyzer (`llm.py`):** Bridges the normalized data to OpenAI-compatible endpoints. It manages both vision-based media analysis and text-based report synthesis.
*   **Platform Fetchers (`platforms/`):** Specialized modules that implement a standardized `fetch_data()` contract, returning `Optional[UserData]`.

---

## 3. Data Model & Normalization
The `UserData` TypedDict hierarchy is the central design constraint, ensuring downstream components remain agnostic to source platform variations.

### 3.1 Canonical Schema
*   **`NormalizedProfile`:** Captures identity metadata, including platform-native IDs, bios, and engagement metrics (followers/following/post counts).
*   **`NormalizedPost`:** A generic container for textual content, media references, and external link extractions. It includes a `context` field for type-specific metadata (e.g., subreddit names or repository identifiers).
*   **`NormalizedMedia`:** Tracks original URLs and local MD5-hashed file paths. Crucially, it stores the `analysis` string generated by the vision LLM, which is persisted back to the cache to prevent redundant API consumption.

---

## 4. Execution Phases & Latency Analysis
The system currently operates on a sequential I/O model where the wall-clock time scales linearly ($O(n)$) with the number of platforms and targets.

### 4.1 Phase 1: Data Acquisition
Acquisition is the most time-intensive phase due to synchronous HTTP round-trips for profiles, posts, and media.

| Operation | Latency Factor | Estimated Duration |
| :--- | :--- | :--- |
| **Client Auth** | Handshake + Verification | 200–800ms per platform |
| **Data Fetch** | API round-trip (Profile + Posts) | 500–2000ms per target |
| **Media Download** | HTTP GET + Disk I/O | 100–2000ms per file |
| **Cache Write** | Serialization | 5–50ms |

### 4.2 Phase 2: Multi-Modal Analysis
*   **Vision Sub-phase:** Employs PIL for image normalization (RGB conversion, 1536px dimension capping, and JPEG re-encoding). Analysis takes approximately **3–12s per image**.
*   **Synthesis Sub-phase:** Aggregates post snippets (truncated to 750 chars), media analysis results, and domain frequencies. A single LLM call synthesizes the report in **5–30s**.

---

## 5. Persistence & Resource Profile

### 5.1 Caching Strategy
*   **Incremental Fetching:** If a cache exists but contains fewer items than requested, the system reuses the cached profile and only fetches the deficit, deduplicating against stored post IDs.
*   **Media Deduplication:** Uses MD5 hex digests of source URLs.Identical images across platforms resolve to the same local file.

### 5.2 Memory Utilization
The process footprint typically ranges from **200 MB to 500 MB**.
*   **Baseline:** ~160 MB (Runtime + standard libraries).
*   **PIL Processing Spike:** The vision pipeline can spike memory usage during image decompression. A 4000x4000 pixel image decompresses to ~64 MB in memory; concurrent processing of multiple large images in a containerized environment (e.g., 512 MB limit) risks OOM termination.

---

## 6. Security & Integrity Analysis

### 6.1 Prompt Injection Surface
The synthesis phase constructs prompts by concatenating untrusted User-Generated Content (UGC) with system instructions. This creates several vectors:
*   **Direct Post Injection:** A target user can craft posts containing LLM directives (e.g., "Ignore all previous instructions") to bias the report.
*   **Second-Order Injection:** Attacker-controlled images can influence vision model outputs, which then act as injection payloads for the text model.
*   **Mitigation:** The current system relies on analytical objectivity instructions in the system prompt. Structural delimiting (e.g., XML tagging) of UGC is identified as a high-priority developmental hardening step.

### 6.2 Sanitization & OPSEC
*   **Username Sanitization:** `sanitize_username()` applies NFKC normalization and strips Unicode category 'C' control characters.
*   **Credential Isolation:** Secrets are restricted to the `ClientManager` scope and are never persisted in reports or logs.

---

## 7. Technical Debt & Roadmap

### 7.1 Identified Defects
*   **Log Mount Mismatch:** The `docker-compose.yml` incorrectly maps a directory to a file path (`./logs:/app/analyzer.log`), preventing correct log persistence on the host.
*   **Sequential Bottleneck:** The absence of `asyncio` results in idle CPU time during I/O waits.

### 7.2 Developmental Trajectory
1.  **Near-term:** Refactoring to asynchronous fetching via `httpx.AsyncClient` and `asyncio.gather()` to achieve a projected 6–10x throughput improvement.
2.  **Mid-term:** Implementing Vector-indexed retrieval (RAG) to bypass LLM context window limits for targets with high-volume historical data.
3.  **Long-term:** Graph database integration (Neo4j) for cross-platform identity correlation and link analysis.
