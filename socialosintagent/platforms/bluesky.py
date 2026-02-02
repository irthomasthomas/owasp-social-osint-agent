import logging
from typing import Any, Dict, List, Optional
from atproto import Client
from .base_fetcher import BaseFetcher
from ..cache import CacheManager
from ..utils import (NormalizedMedia, NormalizedPost, NormalizedProfile, 
                   UserData, download_media, get_sort_key)

class BlueskyFetcher(BaseFetcher):
    def __init__(self):
        super().__init__(platform_name="bluesky")

    def _get_or_fetch_profile(self, username: str, cached_data: Optional[UserData], force_refresh: bool, **kwargs) -> Optional[NormalizedProfile]:
        if not force_refresh and cached_data and cached_data.get("profile"): return cached_data["profile"]
        client: Client = kwargs.get("client")
        try:
            p = client.get_profile(actor=username)
            return NormalizedProfile(
                platform="bluesky", id=p.did, username=p.handle,
                display_name=p.display_name, bio=p.description,
                profile_url=f"https://bsky.app/profile/{p.handle}",
                metrics={
                    "followers": p.followers_count, 
                    "following": p.follows_count, 
                    "posts": p.posts_count,
                    "did": p.did  # Extremely important for OSINT tracking
                }
            )
        except Exception as e: self._handle_api_error(e, username)

    def _fetch_posts(self, username: str, profile: NormalizedProfile, needed_count: int, processed_ids: set, cache: CacheManager = None, **kwargs) -> List[NormalizedPost]:
        client, allow_ext = kwargs.get("client"), kwargs.get("allow_external_media", False)
        new_posts, cursor = [], None
        try:
            while len(new_posts) < needed_count:
                resp = client.get_author_feed(actor=username, cursor=cursor, limit=min(needed_count, 100))
                if not resp.feed: break
                for item in resp.feed:
                    if item.post.uri not in processed_ids:
                        new_posts.append(self._normalize(item.post, cache, client, allow_ext))
                        processed_ids.add(item.post.uri)
                if not resp.cursor: break
                cursor = resp.cursor
        except Exception as e: self._handle_api_error(e, username)
        return new_posts

    def _normalize(self, post: Any, cache: CacheManager, client: Client, allow_ext: bool) -> NormalizedPost:
        media = []
        # Restored deep media parsing for Vision OSINT
        if hasattr(post, 'embed') and post.embed:
            images = getattr(post.embed, 'images', [])
            auth = {"access_jwt": getattr(client._session, 'access_jwt', None)}
            for img in images:
                # Prefer the full-size thumb URL already resolved by the SDK.
                # Fall back to constructing from .cid only if .thumb is missing.
                url = getattr(img, 'thumb', None)
                if not url:
                    cid = getattr(img, 'cid', None)
                    if not cid:
                        self.logger.debug(f"Skipping embed item without .thumb or .cid: {type(img).__name__}")
                        continue
                    # Construct using the blob CID — note: no @jpeg suffix, the CDN infers type
                    url = f"https://cdn.bsky.app/img/feed_fullsize/plain/{post.author.did}/{cid}"
                p = download_media(cache.base_dir, url, cache.is_offline, "bluesky", auth, allow_ext)
                if p: media.append(NormalizedMedia(url=url, local_path=str(p), type="image"))

        return NormalizedPost(
            platform="bluesky", id=post.uri, author_username=post.author.handle,
            text=getattr(post.record, "text", ""), media=media,
            created_at=get_sort_key({"ts": getattr(post.record, "created_at", None)}, "ts"),
            post_url=f"https://bsky.app/profile/{post.author.handle}/post/{post.uri.split('/')[-1]}",
            metrics={"likes": post.like_count, "reposts": post.repost_count, "replies": post.reply_count},
            type="reply" if hasattr(post.record, 'reply') and post.record.reply else "post"
        )

def fetch_data(**kwargs):
    u, c = kwargs.pop("username"), kwargs.pop("cache")
    return BlueskyFetcher().fetch_data(u, c, **kwargs)