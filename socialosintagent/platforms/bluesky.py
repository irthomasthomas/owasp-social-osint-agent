import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, cast
from urllib.parse import quote_plus, urlparse

from atproto import Client, exceptions as atproto_exceptions

from ..cache import CACHE_EXPIRY_HOURS, MAX_CACHE_ITEMS, CacheManager
from ..exceptions import (AccessForbiddenError, RateLimitExceededError,
                         UserNotFoundError)
from ..utils import SUPPORTED_IMAGE_EXTENSIONS, download_media, get_sort_key

logger = logging.getLogger("SocialOSINTAgent.platforms.bluesky")

DEFAULT_FETCH_LIMIT = 50

def fetch_data(
    client: Client,
    username: str,
    cache: CacheManager,
    force_refresh: bool = False,
    fetch_limit: int = DEFAULT_FETCH_LIMIT,
) -> Optional[Dict[str, Any]]:
    """Fetches posts and user profile for a Bluesky user."""
    
    cached_data = cache.load("bluesky", username)
    if cache.is_offline:
        return cached_data

    cached_posts_count = len(cached_data.get("posts", [])) if cached_data else 0
    if not force_refresh and cached_data and (datetime.now(timezone.utc) - get_sort_key(cached_data, "timestamp")) < timedelta(hours=CACHE_EXPIRY_HOURS):
        if cached_posts_count >= fetch_limit:
            return cached_data

    logger.info(f"Fetching Bluesky data for {username} (Force Refresh: {force_refresh}, Limit: {fetch_limit})")
    
    existing_posts = cached_data.get("posts", []) if not force_refresh and cached_data else []
    
    is_incremental_update = not force_refresh and cached_data and fetch_limit <= cached_posts_count
    
    profile_info = cached_data.get("profile_info") if not force_refresh and cached_data else None
    existing_media_paths = cached_data.get("media_paths", []) if not force_refresh and cached_data else []

    try:
        if not profile_info or force_refresh:
            profile = client.get_profile(actor=username)
            profile_info = {
                "did": profile.did, "handle": profile.handle, "display_name": profile.display_name,
                "description": profile.description, "followers_count": profile.followers_count
            }

        all_fetched_posts = list(existing_posts)
        post_uris = {p['uri'] for p in all_fetched_posts}
        newly_added_media_paths = set()
        
        cursor = None
        while len(all_fetched_posts) < fetch_limit:
            response = client.get_author_feed(actor=username, cursor=cursor, limit=min(fetch_limit, 100))
            if not response or not response.feed: break
            
            page_had_new_posts = False
            for feed_item in response.feed:
                post = feed_item.post
                if post.uri in post_uris: continue
                page_had_new_posts = True
                post_uris.add(post.uri)

                record = cast(Any, post.record)
                if not record: continue
                
                auth_details = {"access_jwt": getattr(client._session, 'access_jwt', None)}
                media_items = _process_post_media(record, post, cache, auth_details, newly_added_media_paths)
                
                post_data = {
                    "uri": post.uri, "text": getattr(record, "text", ""),
                    "created_at": get_sort_key({"created_at": getattr(record, "created_at", None)}, "created_at").isoformat(),
                    "media": media_items
                }
                all_fetched_posts.append(post_data)

            if not page_had_new_posts and len(response.feed) > 0: break
            cursor = response.cursor
            if not cursor or is_incremental_update: break

        final_posts = sorted(all_fetched_posts, key=lambda x: get_sort_key(x, "created_at"), reverse=True)[:max(fetch_limit, MAX_CACHE_ITEMS)]
        final_media_paths = sorted(list(newly_added_media_paths.union(existing_media_paths)))

        stats = {"total_posts_cached": len(final_posts)}
        final_data = {"profile_info": profile_info, "posts": final_posts, "stats": stats, "media_analysis": [], "media_paths": final_media_paths}
        cache.save("bluesky", username, final_data)
        return final_data

    except atproto_exceptions.AtProtocolError as e:
        if "Profile not found" in str(e): raise UserNotFoundError(f"Bluesky user {username} not found.") from e
        raise RuntimeError(f"Bluesky API error for {username}: {e}") from e
    except Exception as e:
        logger.error(f"Unexpected error fetching Bluesky data for {username}: {e}", exc_info=True)
        return None

def _process_post_media(record: Any, post: Any, cache: CacheManager, auth: Dict, paths: set) -> list:
    media_items = []
    embed = getattr(record, "embed", None)
    images_to_process = []
    if embed:
        if hasattr(embed, "images"): images_to_process.extend(embed.images)
        record_media = getattr(embed, 'media', None)
        if record_media and hasattr(record_media, 'images'):
            images_to_process.extend(record_media.images)

    for image_info in images_to_process:
        img_blob = getattr(image_info, "image", None)
        if img_blob and (cid := getattr(img_blob, "cid", None)):
            mime_type = getattr(img_blob, "mime_type", "image/jpeg").split('/')[-1]
            cdn_url = f"https://cdn.bsky.app/img/feed_fullsize/plain/{post.author.did}/{quote_plus(str(cid))}@{mime_type}"
            media_path = download_media(cache.base_dir, cdn_url, cache.is_offline, "bluesky", auth)
            if media_path:
                media_items.append({"type": "image", "analysis": None, "url": cdn_url, "local_path": str(media_path)})
                paths.add(str(media_path))
    return media_items