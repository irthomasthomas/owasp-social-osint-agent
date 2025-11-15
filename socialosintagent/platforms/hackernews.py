import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

import httpx
from bs4 import BeautifulSoup

from ..cache import MAX_CACHE_ITEMS, CacheManager
from ..exceptions import RateLimitExceededError, UserNotFoundError
from ..utils import (NormalizedPost, NormalizedProfile, UserData,
                     extract_and_resolve_urls, get_sort_key)

logger = logging.getLogger("SocialOSINTAgent.platforms.hackernews")

REQUEST_TIMEOUT = 20.0
DEFAULT_FETCH_LIMIT = 100
ALGOLIA_MAX_HITS = 1000

def fetch_data(
    username: str,
    cache: CacheManager,
    force_refresh: bool = False,
    fetch_limit: int = DEFAULT_FETCH_LIMIT,
) -> Optional[UserData]:
    """Fetches user activity from HackerNews via Algolia API and normalizes it."""
    
    cached_data = cache.load("hackernews", username)
    if cache.is_offline:
        return cached_data

    if not force_refresh and cached_data and len(cached_data.get("posts", [])) >= fetch_limit:
        return cached_data

    logger.info(f"Fetching HackerNews data for {username} (Limit: {fetch_limit})")
    
    existing_posts = cached_data.get("posts", []) if not force_refresh and cached_data else []
    post_ids = {p['id'] for p in existing_posts}
    
    try:
        # Minimal profile, as Algolia doesn't provide rich user data
        profile_obj = NormalizedProfile(
            platform="hackernews",
            username=username,
            profile_url=f"https://news.ycombinator.com/user?id={username}",
            id=username # Use username as ID for uniqueness
        )

        base_url = "https://hn.algolia.com/api/v1/search"
        params: Dict[str, Any] = {
            "tags": f"author_{quote_plus(username)}", 
            "hitsPerPage": min(fetch_limit, ALGOLIA_MAX_HITS)
        }

        with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
            response = client.get(base_url, params=params)
            response.raise_for_status()
            data = response.json()

        for hit in data.get("hits", []):
            if hit.get("objectID") not in post_ids:
                existing_posts.append(_to_normalized_post(hit, username))
                post_ids.add(hit.get("objectID"))
            
        final_posts = sorted(existing_posts, key=lambda x: get_sort_key(x, "created_at"), reverse=True)[:max(fetch_limit, MAX_CACHE_ITEMS)]
        
        user_data = UserData(profile=profile_obj, posts=final_posts)
        cache.save("hackernews", username, user_data)
        return user_data

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 429: raise RateLimitExceededError("HackerNews API rate limited.")
        if e.response.status_code == 404: raise UserNotFoundError(f"HackerNews username '{username}' seems invalid or not found.") from e
        return None # Other HTTP errors
    except Exception as e:
        logger.error(f"Unexpected error fetching HN data for {username}: {e}", exc_info=True)
        return None

def _to_normalized_post(hit: Dict[str, Any], username: str) -> NormalizedPost:
    post_type = "comment" if "comment" in hit.get("_tags", []) else "story"
    text_content = BeautifulSoup(hit.get("story_text") or hit.get("comment_text") or "", "html.parser").get_text()
    title = hit.get("title")
    full_text = f"Title: {title}\n\n{text_content}" if title and post_type == "story" else text_content
    post_url = f"https://news.ycombinator.com/item?id={hit.get('objectID')}"

    return NormalizedPost(
        platform="hackernews",
        id=hit.get("objectID"),
        created_at=datetime.fromtimestamp(hit["created_at_i"], tz=timezone.utc),
        author_username=username,
        text=full_text.strip(),
        media=[],
        external_links=extract_and_resolve_urls(hit.get("url", "")),
        post_url=post_url,
        metrics={"score": hit.get("points", 0), "comment_count": hit.get("num_comments", 0)},
        type=post_type
    )