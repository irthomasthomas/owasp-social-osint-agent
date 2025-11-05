import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup
from mastodon import (Mastodon, MastodonError, MastodonNotFoundError,
                    MastodonRatelimitError)

from ..cache import CACHE_EXPIRY_HOURS, MAX_CACHE_ITEMS, CacheManager
from ..exceptions import (AccessForbiddenError, RateLimitExceededError,
                         UserNotFoundError)
from ..utils import download_media, get_sort_key

logger = logging.getLogger("SocialOSINTAgent.platforms.mastodon")

DEFAULT_FETCH_LIMIT = 40

def fetch_data(
    clients: Dict[str, Mastodon],
    default_client: Optional[Mastodon],
    username: str,
    cache: CacheManager,
    force_refresh: bool = False,
    fetch_limit: int = DEFAULT_FETCH_LIMIT
) -> Optional[Dict[str, Any]]:
    """Fetches statuses and user info for a Mastodon user."""
    
    if "@" not in username or len(username.split('@', 1)) != 2:
        raise ValueError(f"Invalid Mastodon username format: '{username}'. Must be 'user@instance.domain'.")

    cached_data = cache.load("mastodon", username)
    if cache.is_offline:
        return cached_data

    cached_posts_count = len(cached_data.get("posts", [])) if cached_data else 0
    if not force_refresh and cached_data and (datetime.now(timezone.utc) - get_sort_key(cached_data, "timestamp")) < timedelta(hours=CACHE_EXPIRY_HOURS):
        if cached_posts_count >= fetch_limit:
            return cached_data

    logger.info(f"Fetching Mastodon data for {username} (Force Refresh: {force_refresh}, Limit: {fetch_limit})")
    
    instance_domain = username.split('@')[1]
    client_to_use = clients.get(f"https://{instance_domain}") or default_client
    if not client_to_use:
        raise RuntimeError(f"No suitable Mastodon client found for instance {instance_domain} or for default lookup.")

    existing_posts = cached_data.get("posts", []) if not force_refresh and cached_data else []
    
    is_incremental_update = not force_refresh and cached_data and fetch_limit <= cached_posts_count
    since_id = existing_posts[0].get("id") if is_incremental_update and existing_posts else None
    
    user_info = cached_data.get("user_info") if not force_refresh and cached_data else None
    existing_media_paths = cached_data.get("media_paths", []) if not force_refresh and cached_data else []

    try:
        if not user_info or force_refresh:
            account = client_to_use.account_lookup(acct=username)
            user_info = {
                "id": str(account["id"]), "username": account["username"], "acct": account["acct"],
                "display_name": account["display_name"], "url": account["url"],
                "note_text": BeautifulSoup(account.get("note",""), "html.parser").get_text(separator=" ", strip=True),
                "followers_count": account["followers_count"], "following_count": account["following_count"],
                "statuses_count": account["statuses_count"], "created_at": account["created_at"].isoformat()
            }
        
        user_id = user_info["id"]
        
        all_fetched_posts = list(existing_posts)
        post_ids = {p['id'] for p in all_fetched_posts}
        newly_added_media_paths = set()

        while len(all_fetched_posts) < fetch_limit:
            api_limit = min(fetch_limit - len(all_fetched_posts), 40)
            if api_limit <= 0: break
            
            max_id = all_fetched_posts[-1]['id'] if not is_incremental_update and all_fetched_posts else None
            new_statuses = client_to_use.account_statuses(id=user_id, limit=api_limit, since_id=since_id, max_id=max_id)
            if not new_statuses: break

            for status in new_statuses:
                if status['id'] in post_ids: continue
                post_ids.add(status['id'])
                
                media_items = []
                for att in status.get("media_attachments", []):
                    media_path = download_media(cache.base_dir, att["url"], cache.is_offline, "mastodon")
                    if media_path:
                        # REFACTOR: Set analysis to None. This will be done later.
                        media_items.append({"type": att["type"], "analysis": None, "url": att["url"], "local_path": str(media_path)})
                        newly_added_media_paths.add(str(media_path))
                
                post_data = {
                    "id": str(status["id"]), "created_at": status["created_at"].isoformat(),
                    "text_cleaned": BeautifulSoup(status["content"], "html.parser").get_text(separator=" ", strip=True),
                    "is_reblog": status.get("reblog") is not None, "media": media_items
                }
                all_fetched_posts.append(post_data)

            if is_incremental_update: break

        final_posts = sorted(all_fetched_posts, key=lambda x: get_sort_key(x, "created_at"), reverse=True)[:max(fetch_limit, MAX_CACHE_ITEMS)]
        final_media_paths = sorted(list(newly_added_media_paths.union(existing_media_paths)))

        stats = {"total_posts_cached": len(final_posts)}
        # REFACTOR: Initialize media_analysis as empty list.
        final_data = {"user_info": user_info, "posts": final_posts, "stats": stats, "media_analysis": [], "media_paths": final_media_paths}
        cache.save("mastodon", username, final_data)
        return final_data

    except MastodonRatelimitError:
        raise RateLimitExceededError("Mastodon API rate limit exceeded.")
    except MastodonNotFoundError:
        raise UserNotFoundError(f"Mastodon user {username} not found.")
    except MastodonError as e:
        if "forbidden" in str(e).lower():
            raise AccessForbiddenError(f"Access to Mastodon user {username} is forbidden.") from e
        logger.error(f"Mastodon API error for {username}: {e}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error fetching Mastodon data for {username}: {e}", exc_info=True)
        return None