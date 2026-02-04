import logging
import httpx
from typing import Any, Dict, List, Optional

from .base_fetcher import BaseFetcher
from ..utils import (
    NormalizedPost, 
    NormalizedProfile, 
    UserData, 
    extract_and_resolve_urls, 
    get_sort_key
)

logger = logging.getLogger("SocialOSINTAgent.platforms.hackernews")


class HackerNewsFetcher(BaseFetcher):
    def __init__(self):
        super().__init__(platform_name="hackernews")

    def _get_or_fetch_profile(
        self, 
        username: str, 
        cached_data: Optional[UserData], 
        force_refresh: bool, 
        **kwargs
    ) -> Optional[NormalizedProfile]:
        # Check cache first
        if not force_refresh and cached_data and cached_data.get("profile"):
            return cached_data["profile"]
        
        try:
            # Fetch from official Firebase API for karma and about text
            url = f"https://hacker-news.firebaseio.com/v0/user/{username}.json"
            with httpx.Client(timeout=20.0) as client:
                resp = client.get(url)
                resp.raise_for_status()
                d = resp.json()
                if not d:
                    return None
                
                return NormalizedProfile(
                    platform="hackernews", 
                    id=username, 
                    username=username,
                    display_name=username, 
                    bio=d.get("about", ""),
                    created_at=get_sort_key({"ts": d.get("created")}, "ts"),
                    profile_url=f"https://news.ycombinator.com/user?id={username}",
                    metrics={
                        "karma": d.get("karma", 0), 
                        "submitted_count": len(d.get("submitted", []))
                    }
                )
        except Exception as e:
            self._handle_api_error(e, username)

    def _fetch_posts(
        self, 
        username: str, 
        profile: NormalizedProfile, 
        needed_count: int, 
        processed_ids: set, 
        **kwargs
    ) -> List[NormalizedPost]:
        new_posts = []
        try:
            # Algolia API is better for searching by author
            url = f"https://hn.algolia.com/api/v1/search?tags=author_{username}&hitsPerPage={needed_count}"
            with httpx.Client(timeout=20.0) as client:
                resp = client.get(url)
                resp.raise_for_status()
                for hit in resp.json().get("hits", []):
                    if hit["objectID"] not in processed_ids:
                        new_posts.append(self._normalize(hit))
                        processed_ids.add(hit["objectID"])
        except Exception as e:
            self._handle_api_error(e, username)
        return new_posts

    def _normalize(self, hit: Dict) -> NormalizedPost:
        """Normalize HackerNews story/comment to standard format."""
        from bs4 import BeautifulSoup
        
        raw_text = hit.get("comment_text") or hit.get("story_text") or ""
        clean_text = BeautifulSoup(raw_text, "html.parser").get_text(separator=" ")
        
        itype = "comment" if "comment" in hit.get("_tags", []) else "story"
        
        # Prefix title for stories
        if itype == "story" and hit.get("title"):
            clean_text = f"Title: {hit['title']}\n\n{clean_text}".strip()

        # Collect external links from text AND story URL
        links = extract_and_resolve_urls(clean_text)
        if hit.get("url"):
            links.append(hit["url"])
        
        return NormalizedPost(
            platform="hackernews", 
            id=hit["objectID"], 
            author_username=hit["author"],
            text=clean_text, 
            created_at=get_sort_key({"ts": hit["created_at_i"]}, "ts"),
            post_url=f"https://news.ycombinator.com/item?id={hit['objectID']}",
            metrics={
                "score": hit.get("points", 0), 
                "comment_count": hit.get("num_comments", 0)
            },
            type=itype,
            external_links=list(set(links))
        )


def fetch_data(**kwargs):
    u, c = kwargs.pop("username"), kwargs.pop("cache")
    return HackerNewsFetcher().fetch_data(u, c, **kwargs)
