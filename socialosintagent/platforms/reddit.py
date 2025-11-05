import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import praw
import prawcore

from ..cache import CACHE_EXPIRY_HOURS, MAX_CACHE_ITEMS, CacheManager
from ..exceptions import (AccessForbiddenError, RateLimitExceededError,
                         UserNotFoundError)
from ..utils import SUPPORTED_IMAGE_EXTENSIONS, download_media, get_sort_key

logger = logging.getLogger("SocialOSINTAgent.platforms.reddit")

DEFAULT_FETCH_LIMIT = 50

def _extract_media_from_submission(submission: Any, cache: CacheManager) -> List[Dict[str, Any]]:
    media_items = []
    # Direct media link
    if any(submission.url.lower().endswith(ext) for ext in SUPPORTED_IMAGE_EXTENSIONS + [".mp4", ".webm"]):
        media_path = download_media(cache.base_dir, submission.url, cache.is_offline, "reddit")
        if media_path:
            item_type = "image" if media_path.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS else "video"
            media_items.append({"type": item_type, "analysis": None, "url": submission.url, "local_path": str(media_path)})
    # Gallery
    elif getattr(submission, 'is_gallery', False) and getattr(submission, 'media_metadata', None):
        for _, media_item in submission.media_metadata.items():
            if 's' in media_item and 'u' in media_item['s']:
                url = media_item['s']['u']
                media_path = download_media(cache.base_dir, url, cache.is_offline, "reddit")
                if media_path:
                    media_items.append({"type": "gallery_image", "analysis": None, "url": url, "local_path": str(media_path)})
    return media_items

def fetch_data(
    client: praw.Reddit,
    username: str,
    cache: CacheManager,
    force_refresh: bool = False,
    fetch_limit: int = DEFAULT_FETCH_LIMIT,
) -> Optional[Dict[str, Any]]:
    """Fetches submissions, comments, and user profile for a Reddit user."""
    
    cached_data = cache.load("reddit", username)

    if cache.is_offline:
        return cached_data
    
    cached_subs_count = len(cached_data.get("submissions", [])) if cached_data else 0
    cached_comms_count = len(cached_data.get("comments", [])) if cached_data else 0
    if not force_refresh and cached_data and (datetime.now(timezone.utc) - get_sort_key(cached_data, "timestamp")) < timedelta(hours=CACHE_EXPIRY_HOURS):
        if cached_subs_count >= fetch_limit and cached_comms_count >= fetch_limit:
            return cached_data

    logger.info(f"Fetching Reddit data for u/{username} (Force Refresh: {force_refresh}, Limit: {fetch_limit})")
    
    all_submissions = cached_data.get("submissions", []) if not force_refresh and cached_data else []
    all_comments = cached_data.get("comments", []) if not force_refresh and cached_data else []
    
    sub_ids = {s['id'] for s in all_submissions}
    comment_ids = {c['id'] for c in all_comments}
    
    media_paths_set = set(cached_data.get("media_paths", [])) if cached_data and cached_data.get("media_paths") else set()
    user_profile = cached_data.get("user_profile") if not force_refresh and cached_data else None

    try:
        redditor = client.redditor(username)
        if not user_profile or force_refresh:
            user_profile = {
                "id": redditor.id, "name": redditor.name,
                "created_utc": datetime.fromtimestamp(redditor.created_utc, tz=timezone.utc).isoformat(),
                "link_karma": redditor.link_karma, "comment_karma": redditor.comment_karma,
            }
        
        # Fetch Submissions
        if len(all_submissions) < fetch_limit:
            for s in redditor.submissions.new(limit=min(fetch_limit, 100)):
                if s.id in sub_ids: continue
                sub_ids.add(s.id)
                submission_media = _extract_media_from_submission(s, cache)
                for media_item in submission_media:
                    if media_item.get("local_path"): media_paths_set.add(media_item["local_path"])
                
                all_submissions.append({
                    "id": s.id, "title": s.title, "text": s.selftext, "score": s.score,
                    "subreddit": s.subreddit.display_name,
                    "created_utc": datetime.fromtimestamp(s.created_utc, tz=timezone.utc).isoformat(),
                    "link_url": s.url if not s.is_self else None, "media": submission_media
                })
                if len(all_submissions) >= fetch_limit: break

        # Fetch Comments
        if len(all_comments) < fetch_limit:
            for c in redditor.comments.new(limit=min(fetch_limit, 100)):
                if c.id in comment_ids: continue
                comment_ids.add(c.id)
                all_comments.append({
                    "id": c.id, "text": c.body, "score": c.score,
                    "subreddit": c.subreddit.display_name,
                    "created_utc": datetime.fromtimestamp(c.created_utc, tz=timezone.utc).isoformat(),
                })
                if len(all_comments) >= fetch_limit: break
        
        final_submissions = sorted(all_submissions, key=lambda x: get_sort_key(x, "created_utc"), reverse=True)[:max(fetch_limit, MAX_CACHE_ITEMS)]
        final_comments = sorted(all_comments, key=lambda x: get_sort_key(x, "created_utc"), reverse=True)[:max(fetch_limit, MAX_CACHE_ITEMS)]
        
        stats = {"total_submissions_cached": len(final_submissions), "total_comments_cached": len(final_comments)}

        final_data = {
            "user_profile": user_profile, "submissions": final_submissions, "comments": final_comments,
            "media_analysis": [], "media_paths": sorted(list(media_paths_set)), "stats": stats
        }
        cache.save("reddit", username, final_data)
        return final_data

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