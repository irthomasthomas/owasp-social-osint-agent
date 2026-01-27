import logging
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup
from mastodon import (Mastodon, MastodonError, MastodonNotFoundError,
                    MastodonRatelimitError)

from ..cache import MAX_CACHE_ITEMS, CacheManager
from ..exceptions import (AccessForbiddenError, RateLimitExceededError,
                         UserNotFoundError)
from ..utils import (NormalizedMedia, NormalizedPost, NormalizedProfile,
                     UserData, download_media, extract_and_resolve_urls,
                     get_sort_key)

logger = logging.getLogger("SocialOSINTAgent.platforms.mastodon")

DEFAULT_FETCH_LIMIT = 40

def fetch_data(
    clients: Dict[str, Mastodon],
    default_client: Optional[Mastodon],
    username: str,
    cache: CacheManager,
    force_refresh: bool = False,
    fetch_limit: int = DEFAULT_FETCH_LIMIT,
    allow_external_media: bool = False,
) -> Optional[UserData]:
    """Fetches statuses and user info for a Mastodon user and normalizes them."""
    
    if "@" not in username or len(username.split('@', 1)) != 2:
        raise ValueError(f"Invalid Mastodon username format: '{username}'. Must be 'user@instance.domain'.")

    cached_data = cache.load("mastodon", username)
    if cache.is_offline:
        return cached_data

    if not force_refresh and cached_data and len(cached_data.get("posts", [])) >= fetch_limit:
        return cached_data

    logger.info(f"Fetching Mastodon data for {username} (Limit: {fetch_limit})")
    
    instance_domain = username.split('@')[1]
    client_to_use = clients.get(f"https://{instance_domain}") or default_client
    if not client_to_use:
        raise RuntimeError(f"No suitable Mastodon client found for instance {instance_domain} or for default lookup.")

    all_posts = cached_data.get("posts", []) if not force_refresh and cached_data else []
    post_ids = {p['id'] for p in all_posts}
    profile_obj = cached_data.get("profile") if not force_refresh and cached_data else None

    try:
        if not profile_obj or force_refresh:
            account = client_to_use.account_lookup(acct=username)
            profile_obj = NormalizedProfile(
                platform="mastodon",
                id=str(account["id"]),
                username=account["acct"],
                display_name=account["display_name"],
                bio=BeautifulSoup(account.get("note",""), "html.parser").get_text(separator=" ", strip=True),
                created_at=account["created_at"],
                profile_url=account["url"],
                metrics={
                    "followers": account["followers_count"],
                    "following": account["following_count"],
                    "post_count": account["statuses_count"]
                }
            )
        
        user_id = profile_obj["id"]
        
        # Fetch new content
        needed_items = fetch_limit - len(all_posts)
        if needed_items > 0 or force_refresh:
            # Mastodon API pagination is tricky; for simplicity, we'll just fetch a block
            statuses = client_to_use.account_statuses(id=user_id, limit=min(fetch_limit, 40))
            for status in statuses:
                if str(status['id']) not in post_ids:
                    all_posts.append(_to_normalized_post(status, cache, profile_obj['username'], allow_external_media))
                    post_ids.add(str(status['id']))

        final_posts = sorted(all_posts, key=lambda x: get_sort_key(x, "created_at"), reverse=True)[:max(fetch_limit, MAX_CACHE_ITEMS)]
        
        user_data = UserData(profile=profile_obj, posts=final_posts)
        cache.save("mastodon", username, user_data)
        return user_data

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

def _to_normalized_post(status: Dict[str, Any], cache: CacheManager, author_username: str, allow_external: bool) -> NormalizedPost:
    cleaned_text = BeautifulSoup(status["content"], "html.parser").get_text(separator=" ", strip=True)
    media_items: List[NormalizedMedia] = []
    for att in status.get("media_attachments", []):
        if path := download_media(cache.base_dir, att["url"], cache.is_offline, "mastodon", allow_external=allow_external):
            media_items.append(NormalizedMedia(url=att["url"], local_path=str(path), type=att["type"]))

    return NormalizedPost(
        platform="mastodon",
        id=str(status["id"]),
        created_at=status["created_at"],
        author_username=author_username,
        text=cleaned_text,
        media=media_items,
        external_links=extract_and_resolve_urls(cleaned_text),
        post_url=status["url"],
        metrics={"replies": status["replies_count"], "reposts": status["reblogs_count"], "likes": status["favourites_count"]},
        type="repost" if status.get("reblog") else "post"
    )