import logging
from typing import Any, Dict, List, Optional

import tweepy

from ..cache import MAX_CACHE_ITEMS, CacheManager
from ..exceptions import (AccessForbiddenError, RateLimitExceededError,
                         UserNotFoundError)
from ..utils import (NormalizedMedia, NormalizedPost, NormalizedProfile,
                     UserData, download_media, extract_and_resolve_urls,
                     get_sort_key)

logger = logging.getLogger("SocialOSINTAgent.platforms.twitter")

DEFAULT_FETCH_LIMIT = 50
MIN_API_FETCH_LIMIT = 5

def fetch_data(
    client: tweepy.Client,
    username: str,
    cache: CacheManager,
    force_refresh: bool = False,
    fetch_limit: int = DEFAULT_FETCH_LIMIT,
) -> Optional[UserData]:
    fetch_limit = max(fetch_limit, MIN_API_FETCH_LIMIT)
    cached_data = cache.load("twitter", username)

    if cache.is_offline: return cached_data
    if not force_refresh and cached_data and len(cached_data.get("posts", [])) >= fetch_limit:
        return cached_data

    logger.info(f"Fetching Twitter data for @{username} (Limit: {fetch_limit})")
    
    try:
        profile_obj = cached_data.get("profile") if not force_refresh and cached_data else None
        
        if not profile_obj:
            user_response = client.get_user(
                username=username,
                user_fields=["created_at", "public_metrics", "profile_image_url", "verified", "description", "location"]
            )
            if not user_response or not user_response.data:
                raise UserNotFoundError(f"Twitter user @{username} not found.")
            user = user_response.data
            profile_obj = NormalizedProfile(
                platform="twitter", id=str(user.id), username=user.username,
                display_name=user.name, bio=user.description, created_at=user.created_at,
                profile_url=f"https://twitter.com/{user.username}",
                metrics={
                    "followers": user.public_metrics.get("followers_count", 0),
                    "following": user.public_metrics.get("following_count", 0),
                    "post_count": user.public_metrics.get("tweet_count", 0)
                }
            )
        
        user_id = profile_obj["id"]
        all_posts = cached_data.get("posts", []) if not force_refresh and cached_data else []
        processed_post_ids = {p['id'] for p in all_posts}
        
        needed_count = fetch_limit - len(all_posts)
        
        if force_refresh or needed_count > 0:
            pagination_token = None
            while needed_count > 0:
                page_limit = max(min(needed_count, 100), MIN_API_FETCH_LIMIT)
                
                tweets_response = client.get_users_tweets(
                    id=user_id, max_results=page_limit, pagination_token=pagination_token,
                    tweet_fields=["created_at", "public_metrics", "attachments", "entities", "in_reply_to_user_id", "referenced_tweets"],
                    expansions=["attachments.media_keys"],
                    media_fields=["url", "preview_image_url", "type", "media_key"],
                )
                
                if not tweets_response.data: break

                media_dict = {m.media_key: m for m in tweets_response.includes.get("media", [])} if tweets_response.includes else {}
                auth_details = {"bearer_token": client.bearer_token}

                new_tweets_found = 0
                for tweet in tweets_response.data:
                    if str(tweet.id) not in processed_post_ids:
                        all_posts.append(_to_normalized_post(tweet, media_dict, cache, auth_details))
                        processed_post_ids.add(str(tweet.id))
                        new_tweets_found += 1

                if new_tweets_found == 0: # Stop if a page returns no new tweets
                    break
                needed_count -= new_tweets_found
                pagination_token = tweets_response.meta.get("next_token")
                if not pagination_token: break
        
        final_posts = sorted(all_posts, key=lambda x: get_sort_key(x, "created_at"), reverse=True)[:max(fetch_limit, MAX_CACHE_ITEMS)]
        user_data = UserData(profile=profile_obj, posts=final_posts)
        cache.save("twitter", username, user_data)
        return user_data

    except tweepy.TooManyRequests as e:
        raise RateLimitExceededError("Twitter API rate limit exceeded.", original_exception=e)
    except tweepy.errors.NotFound:
        raise UserNotFoundError(f"Twitter user @{username} not found.")
    except tweepy.errors.Forbidden as e:
        raise AccessForbiddenError(f"Access forbidden to @{username}'s tweets. Reason: {e}")
    except Exception as e:
        if isinstance(e, (UserNotFoundError, AccessForbiddenError, RateLimitExceededError)):
            raise
        logger.error(f"Unexpected error fetching Twitter data for @{username}: {e}", exc_info=True)
        return None

def _to_normalized_post(tweet: tweepy.Tweet, media_dict: Dict, cache: CacheManager, auth: Dict) -> NormalizedPost:
    media_items: List[NormalizedMedia] = []
    if tweet.attachments and "media_keys" in tweet.attachments:
        for key in tweet.attachments["media_keys"]:
            media = media_dict.get(key)
            if media:
                url = media.url if media.type in ["photo", "gif"] and media.url else media.preview_image_url
                if url:
                    path = download_media(cache.base_dir, url, cache.is_offline, "twitter", auth)
                    if path: media_items.append(NormalizedMedia(url=url, local_path=str(path), type=media.type))

    return NormalizedPost(
        platform="twitter", id=str(tweet.id), created_at=tweet.created_at,
        author_username=str(tweet.author_id), text=tweet.text, media=media_items,
        external_links=extract_and_resolve_urls(tweet.text), post_url=f"https://twitter.com/user/status/{tweet.id}",
        metrics={
            "likes": tweet.public_metrics.get("like_count", 0), "reposts": tweet.public_metrics.get("retweet_count", 0),
            "replies": tweet.public_metrics.get("reply_count", 0), "quotes": tweet.public_metrics.get("quote_count", 0)
        },
        type="reply" if tweet.in_reply_to_user_id else "post",
        context={"in_reply_to_user_id": str(tweet.in_reply_to_user_id) if tweet.in_reply_to_user_id else None}
    )