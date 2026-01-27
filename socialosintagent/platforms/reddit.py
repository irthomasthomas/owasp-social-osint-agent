import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import praw
import prawcore

from ..cache import MAX_CACHE_ITEMS, CacheManager
from ..exceptions import (AccessForbiddenError, RateLimitExceededError,
                         UserNotFoundError)
from ..utils import (SUPPORTED_IMAGE_EXTENSIONS, NormalizedMedia,
                     NormalizedPost, NormalizedProfile, UserData,
                     download_media, get_sort_key)

logger = logging.getLogger("SocialOSINTAgent.platforms.reddit")

DEFAULT_FETCH_LIMIT = 50

def _extract_media_from_submission(submission: Any, cache: CacheManager, allow_external: bool) -> List[NormalizedMedia]:
    media_items: List[NormalizedMedia] = []
    # Direct media link
    if any(submission.url.lower().endswith(ext) for ext in SUPPORTED_IMAGE_EXTENSIONS + [".mp4", ".webm"]):
        media_path = download_media(cache.base_dir, submission.url, cache.is_offline, "reddit", allow_external=allow_external)
        if media_path:
            item_type = "image" if media_path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS else "video"
            media_items.append(NormalizedMedia(type=item_type, url=submission.url, local_path=str(media_path)))
    # Gallery
    elif getattr(submission, 'is_gallery', False) and getattr(submission, 'media_metadata', None):
        for _, media_item in submission.media_metadata.items():
            if 's' in media_item and 'u' in media_item['s']:
                url = media_item['s']['u']
                media_path = download_media(cache.base_dir, url, cache.is_offline, "reddit", allow_external=allow_external)
                if media_path:
                    media_items.append(NormalizedMedia(type="image", url=url, local_path=str(media_path)))
    return media_items

def fetch_data(
    client: praw.Reddit,
    username: str,
    cache: CacheManager,
    force_refresh: bool = False,
    fetch_limit: int = DEFAULT_FETCH_LIMIT,
    allow_external_media: bool = False,
) -> Optional[UserData]:
    """Fetches submissions and comments for a Reddit user and normalizes them."""
    
    cached_data = cache.load("reddit", username)
    if cache.is_offline:
        return cached_data
    
    if not force_refresh and cached_data and len(cached_data.get("posts", [])) >= fetch_limit:
        return cached_data

    logger.info(f"Fetching Reddit data for u/{username} (Limit: {fetch_limit})")
    
    all_posts = cached_data.get("posts", []) if not force_refresh and cached_data else []
    post_ids = {p['id'] for p in all_posts}
    profile_obj = cached_data.get("profile") if not force_refresh and cached_data else None

    try:
        redditor = client.redditor(username)
        if not profile_obj or force_refresh:
            # The praw object can be heavy, so we extract what we need
            profile_data = {
                "id": redditor.id, "name": redditor.name,
                "created_utc": redditor.created_utc,
                "link_karma": redditor.link_karma, "comment_karma": redditor.comment_karma,
            }
            profile_obj = NormalizedProfile(
                platform="reddit",
                id=profile_data["id"],
                username=profile_data["name"],
                created_at=datetime.fromtimestamp(profile_data["created_utc"], tz=timezone.utc),
                profile_url=f"https://www.reddit.com/user/{profile_data['name']}",
                metrics={"post_karma": profile_data["link_karma"], "comment_karma": profile_data["comment_karma"]}
            )

        # Fetch new content until the combined total reaches the limit
        needed_items = fetch_limit - len(all_posts)
        if needed_items > 0 or force_refresh:
            # Fetch both submissions and comments
            submissions = redditor.submissions.new(limit=fetch_limit)
            comments = redditor.comments.new(limit=fetch_limit)
            
            # Combine and normalize
            for s in submissions:
                if s.id not in post_ids:
                    all_posts.append(_to_normalized_post(s, "submission", cache, allow_external_media))
                    post_ids.add(s.id)

            for c in comments:
                if c.id not in post_ids:
                    all_posts.append(_to_normalized_post(c, "comment", cache, allow_external_media))
                    post_ids.add(c.id)

        final_posts = sorted(all_posts, key=lambda x: get_sort_key(x, "created_at"), reverse=True)[:max(fetch_limit, MAX_CACHE_ITEMS)]
        
        user_data = UserData(profile=profile_obj, posts=final_posts)
        cache.save("reddit", username, user_data)
        return user_data

    except prawcore.exceptions.NotFound:
        raise UserNotFoundError(f"Reddit user u/{username} not found.")
    except prawcore.exceptions.Forbidden:
        raise AccessForbiddenError(f"Access to Reddit user u/{username} is forbidden.")
    except prawcore.exceptions.RequestException as e:
        if hasattr(e, 'response') and e.response and e.response.status_code == 429:
            raise RateLimitExceededError("Reddit API rate limit exceeded.") from e
        logger.error(f"Reddit request failed for u/{username}: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error fetching Reddit data for u/{username}: {e}", exc_info=True)
        return None

def _to_normalized_post(item: Any, item_type: str, cache: CacheManager, allow_external: bool) -> NormalizedPost:
    """Converts a PRAW submission or comment object to a NormalizedPost."""
    if item_type == "submission":
        return NormalizedPost(
            platform="reddit",
            id=item.id,
            created_at=datetime.fromtimestamp(item.created_utc, tz=timezone.utc),
            author_username=str(item.author),
            text=f"Title: {item.title}\n\n{item.selftext}",
            media=_extract_media_from_submission(item, cache, allow_external),
            external_links=[item.url] if not item.is_self else [],
            post_url=f"https://www.reddit.com{item.permalink}",
            metrics={"score": item.score, "comment_count": item.num_comments},
            type="submission",
            context={"subreddit": str(item.subreddit)}
        )
    elif item_type == "comment":
        return NormalizedPost(
            platform="reddit",
            id=item.id,
            created_at=datetime.fromtimestamp(item.created_utc, tz=timezone.utc),
            author_username=str(item.author),
            text=item.body,
            media=[],
            external_links=[],
            post_url=f"https://www.reddit.com{item.permalink}",
            metrics={"score": item.score},
            type="comment",
            context={"subreddit": str(item.subreddit)}
        )
    raise ValueError(f"Unknown item_type: {item_type}")