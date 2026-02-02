import logging
from typing import Any, Dict, List, Optional
import tweepy
from .base_fetcher import BaseFetcher
from ..cache import CacheManager
from ..utils import (NormalizedMedia, NormalizedPost, NormalizedProfile, 
                   UserData, download_media, extract_and_resolve_urls)

logger = logging.getLogger("SocialOSINTAgent.platforms.twitter")

class TwitterFetcher(BaseFetcher):
    def __init__(self):
        super().__init__(platform_name="twitter")

    def _get_or_fetch_profile(self, username: str, cached_data: Optional[UserData], force_refresh: bool, **kwargs) -> Optional[NormalizedProfile]:
        client: tweepy.Client = kwargs.get("client")
        try:
            res = client.get_user(
                username=username,
                user_fields=["created_at", "public_metrics", "description", "location", "verified", "profile_image_url"]
            )
            if not res or not res.data: return None
            u = res.data
            return NormalizedProfile(
                platform="twitter", id=str(u.id), username=u.username,
                display_name=u.name, bio=u.description, created_at=u.created_at,
                profile_url=f"https://twitter.com/{u.username}",
                metrics={
                    "followers": u.public_metrics.get("followers_count", 0),
                    "following": u.public_metrics.get("following_count", 0),
                    "post_count": u.public_metrics.get("tweet_count", 0),
                    "verified": u.verified,
                    "location": u.location or "N/A"
                }
            )
        except Exception as e:
            self._handle_api_error(e, username)

    def _fetch_posts(self, username: str, profile: NormalizedProfile, needed_count: int, processed_ids: set, **kwargs) -> List[NormalizedPost]:
        client, cache, allow_ext = kwargs.get("client"), kwargs.get("cache"), kwargs.get("allow_external_media", False)
        new_posts, token = [], None
        try:
            while len(new_posts) < needed_count:
                limit = max(min(needed_count - len(new_posts), 100), 5)
                res = client.get_users_tweets(
                    id=profile["id"], 
                    max_results=limit, 
                    pagination_token=token,
                    tweet_fields=["created_at", "public_metrics", "attachments", "in_reply_to_user_id", "referenced_tweets", "entities", "author_id"],
                    expansions=["attachments.media_keys", "author_id"],
                    media_fields=["url", "preview_image_url", "type"],
                    user_fields=["username"]
                )
                
                # Create the user map here to resolve Author IDs to usernames
                user_map = {u.id: u for u in res.includes.get("users", [])} if res.includes else {}
                
                if not res or not res.data: break
                media_map = {m.media_key: m for m in res.includes.get("media", [])} if res.includes else {}
                
                for t in res.data:
                    if str(t.id) not in processed_ids:
                        # Pass user_map to _normalize
                        new_posts.append(self._normalize(t, media_map, user_map, cache, client.bearer_token, allow_ext))
                        processed_ids.add(str(t.id))
                token = res.meta.get("next_token")
                if not token: break
        except Exception as e:
            self._handle_api_error(e, username)
        return new_posts

    def _normalize(self, t: Any, media_map: Dict, user_map: Dict, cache: CacheManager, token: str, allow_ext: bool) -> NormalizedPost:
        media_items = []
        if t.attachments and "media_keys" in t.attachments:
            for k in t.attachments["media_keys"]:
                if m := media_map.get(k):
                    url = m.url or m.preview_image_url
                    if path := download_media(cache.base_dir, url, cache.is_offline, "twitter", {"bearer_token": token}, allow_ext):
                        media_items.append(NormalizedMedia(url=url, local_path=str(path), type=m.type))
        
        # Resolve username using the passed user_map
        author_user = user_map.get(t.author_id)
        author_handle = author_user.username if author_user else str(t.author_id)
        
        return NormalizedPost(
            platform="twitter", 
            id=str(t.id), 
            created_at=t.created_at, 
            text=t.text, 
            media=media_items, 
            external_links=extract_and_resolve_urls(t.text),
            post_url=f"https://twitter.com/i/status/{t.id}",
            author_username=author_handle, # Use the resolved handle
            metrics={
                "likes": t.public_metrics.get("like_count", 0),
                "reposts": t.public_metrics.get("retweet_count", 0),
                "replies": t.public_metrics.get("reply_count", 0),
                "quotes": t.public_metrics.get("quote_count", 0)
            },
            type="reply" if t.in_reply_to_user_id else "post"
        )

def fetch_data(**kwargs):
    u, c = kwargs.pop("username"), kwargs.pop("cache")
    return TwitterFetcher().fetch_data(u, c, **kwargs)