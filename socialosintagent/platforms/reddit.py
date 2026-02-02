import logging, praw, prawcore
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from .base_fetcher import BaseFetcher
from ..utils import (SUPPORTED_IMAGE_EXTENSIONS, NormalizedMedia,
                     NormalizedPost, NormalizedProfile, UserData,
                     download_media, get_sort_key)

class RedditFetcher(BaseFetcher):
    def __init__(self):
        super().__init__(platform_name="reddit")

    def _get_or_fetch_profile(self, username: str, cached_data: Optional[UserData], force_refresh: bool, **kwargs) -> Optional[NormalizedProfile]:
        client: praw.Reddit = kwargs.get("client")
        try:
            r = client.redditor(username)
            return NormalizedProfile(
                platform="reddit", id=r.id, username=r.name,
                created_at=datetime.fromtimestamp(r.created_utc, tz=timezone.utc),
                profile_url=f"https://reddit.com/u/{r.name}",
                metrics={"post_karma": r.link_karma, "comment_karma": r.comment_karma}
            )
        except Exception as e: self._handle_api_error(e, username)

    def _fetch_posts(self, username: str, profile: NormalizedProfile, needed_count: int, processed_ids: set, **kwargs) -> List[NormalizedPost]:
        client, cache, allow_ext = kwargs.get("client"), kwargs.get("cache"), kwargs.get("allow_external_media", False)
        new_posts = []
        try:
            r = client.redditor(username)
            
            # Fetch and explicitly label Submissions
            submissions = r.submissions.new(limit=needed_count)
            for item in submissions:
                if item.id not in processed_ids:
                    new_posts.append(self._normalize(item, "submission", cache, allow_ext))
                    processed_ids.add(item.id)
            
            # Fetch and explicitly label Comments
            comments = r.comments.new(limit=needed_count)
            for item in comments:
                if item.id not in processed_ids:
                    new_posts.append(self._normalize(item, "comment", cache, allow_ext))
                    processed_ids.add(item.id)
                    
        except Exception as e: 
            self._handle_api_error(e, username)
            
        return new_posts

    def _normalize(self, item: Any, itype: str, cache: Any, allow_ext: bool) -> NormalizedPost:
        # itype is now explicitly passed as "submission" or "comment"
        text = f"Title: {item.title}\n\n{item.selftext}" if itype == "submission" else item.body
        media = []
        
        if itype == "submission" and hasattr(item, 'url'):
            if any(item.url.lower().endswith(ex) for ex in SUPPORTED_IMAGE_EXTENSIONS):
                p = download_media(cache.base_dir, item.url, cache.is_offline, "reddit", allow_external=allow_ext)
                if p: media.append(NormalizedMedia(url=item.url, local_path=str(p), type="image"))

        return NormalizedPost(
            platform="reddit", 
            id=item.id, 
            created_at=datetime.fromtimestamp(item.created_utc, tz=timezone.utc),
            author_username=str(item.author), 
            text=text, 
            media=media,
            post_url=f"https://reddit.com{item.permalink}", 
            metrics={"score": item.score},
            type=itype,
            context={"subreddit": str(item.subreddit.display_name) if hasattr(item.subreddit, 'display_name') else str(item.subreddit)}
        )

def fetch_data(**kwargs):
    u, c = kwargs.pop("username"), kwargs.pop("cache")
    return RedditFetcher().fetch_data(u, c, **kwargs)