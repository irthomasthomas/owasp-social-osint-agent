import logging
from typing import Any, Dict, List, Optional, cast
from urllib.parse import quote_plus

from atproto import Client
from atproto import exceptions as atproto_exceptions

from ..cache import MAX_CACHE_ITEMS, CacheManager
from ..exceptions import UserNotFoundError
from ..utils import (NormalizedMedia, NormalizedPost, NormalizedProfile,
                     UserData, download_media, extract_and_resolve_urls,
                     get_sort_key)

logger = logging.getLogger("SocialOSINTAgent.platforms.bluesky")

DEFAULT_FETCH_LIMIT = 50

def fetch_data(
    client: Client,
    username: str,
    cache: CacheManager,
    force_refresh: bool = False,
    fetch_limit: int = DEFAULT_FETCH_LIMIT,
) -> Optional[UserData]:
    cached_data = cache.load("bluesky", username)
    if cache.is_offline:
        return cached_data

    if not force_refresh and cached_data and len(cached_data.get("posts", [])) >= fetch_limit:
        return cached_data

    logger.info(f"Fetching Bluesky data for {username} (Limit: {fetch_limit})")

    try:
        profile_obj: Optional[NormalizedProfile] = None
        if not force_refresh and cached_data and "profile" in cached_data:
            profile_obj = cached_data["profile"]

        if not profile_obj:
            profile = client.get_profile(actor=username)
            profile_obj = NormalizedProfile(
                platform="bluesky",
                id=profile.did,
                username=profile.handle,
                display_name=profile.display_name,
                bio=profile.description,
                created_at=None,  # Bluesky profile API doesn't provide creation date
                profile_url=f"https://bsky.app/profile/{profile.handle}",
                metrics={"followers": profile.followers_count, "following": profile.follows_count, "post_count": profile.posts_count}
            )

        existing_posts = cached_data.get("posts", []) if not force_refresh and cached_data else []
        processed_post_ids = {p['id'] for p in existing_posts}
        all_posts: List[NormalizedPost] = list(existing_posts)

        cursor = None
        while len(all_posts) < fetch_limit:
            response = client.get_author_feed(actor=username, cursor=cursor, limit=min(fetch_limit, 100))
            if not response or not response.feed:
                break

            for feed_item in response.feed:
                post = feed_item.post
                if post.uri not in processed_post_ids:
                    all_posts.append(_to_normalized_post(post, cache, client))
                    processed_post_ids.add(post.uri)

            cursor = response.cursor
            if not cursor:
                break

        final_posts = sorted(all_posts, key=lambda x: x['created_at'], reverse=True)[:max(fetch_limit, MAX_CACHE_ITEMS)]
        user_data = UserData(profile=profile_obj, posts=final_posts)
        cache.save("bluesky", username, user_data)
        return user_data

    except atproto_exceptions.AtProtocolError as e:
        if "Profile not found" in str(e):
            raise UserNotFoundError(f"Bluesky user {username} not found.") from e
        raise RuntimeError(f"Bluesky API error for {username}: {e}") from e
    except Exception as e:
        logger.error(f"Unexpected error fetching Bluesky data for {username}: {e}", exc_info=True)
        return None

def _to_normalized_post(post: Any, cache: CacheManager, client: Client) -> NormalizedPost:
    record = cast(Any, post.record)
    media_items: List[NormalizedMedia] = []
    
    if embed := getattr(record, "embed", None):
        images_to_process = []
        if hasattr(embed, "images"): images_to_process.extend(embed.images)
        if (record_media := getattr(embed, 'media', None)) and hasattr(record_media, 'images'):
            images_to_process.extend(record_media.images)
        
        auth_details = {"access_jwt": getattr(client._session, 'access_jwt', None)}
        for image_info in images_to_process:
            if (img_blob := getattr(image_info, "image", None)) and (cid := getattr(img_blob, "cid", None)):
                mime_type = getattr(img_blob, "mime_type", "image/jpeg").split('/')[-1]
                cdn_url = f"https://cdn.bsky.app/img/feed_fullsize/plain/{post.author.did}/{quote_plus(str(cid))}@{mime_type}"
                if path := download_media(cache.base_dir, cdn_url, cache.is_offline, "bluesky", auth_details):
                    media_items.append(NormalizedMedia(url=cdn_url, local_path=str(path), type="image"))

    return NormalizedPost(
        platform="bluesky",
        id=post.uri,
        created_at=get_sort_key({"created_at": getattr(record, "created_at", None)}, "created_at"),
        author_username=post.author.handle,
        text=getattr(record, "text", ""),
        media=media_items,
        external_links=extract_and_resolve_urls(getattr(record, "text", "")),
        post_url=f"https://bsky.app/profile/{post.author.handle}/post/{post.uri.split('/')[-1]}",
        metrics={"likes": post.like_count, "reposts": post.repost_count, "replies": post.reply_count},
        type="reply" if post.record.reply else "post"
    )