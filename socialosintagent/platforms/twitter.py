import logging
from typing import Any, Dict, Optional

import tweepy

from ..cache import MAX_CACHE_ITEMS, CacheManager
from ..exceptions import (AccessForbiddenError, RateLimitExceededError,
                         UserNotFoundError)
from ..utils import download_media, get_sort_key

logger = logging.getLogger("SocialOSINTAgent.platforms.twitter")

DEFAULT_FETCH_LIMIT = 50

def fetch_data(
    client: tweepy.Client,
    username: str,
    cache: CacheManager,
    force_refresh: bool = False,
    fetch_limit: int = DEFAULT_FETCH_LIMIT,
) -> Optional[Dict[str, Any]]:
    """Fetches tweets and user info for a Twitter user. Downloads media but does not analyze it."""
    
    cached_data = cache.load("twitter", username)

    if cache.is_offline:
        return cached_data

    if not force_refresh and cached_data:
        if len(cached_data.get("tweets", [])) >= fetch_limit:
            logger.info(f"Twitter cache for @{username} is fresh and has enough items ({len(cached_data.get('tweets', []))}/{fetch_limit}). Skipping fetch.")
            return cached_data

    logger.info(f"Fetching Twitter data for @{username} (Force Refresh: {force_refresh}, Limit: {fetch_limit})")
    
    existing_tweets = cached_data.get("tweets", []) if not force_refresh and cached_data else []
    
    is_load_more = not force_refresh and cached_data and fetch_limit > len(existing_tweets)
    is_incremental_update = not force_refresh and cached_data and not is_load_more

    since_id = existing_tweets[0].get("id") if is_incremental_update and existing_tweets else None
    
    existing_media_paths = cached_data.get("media_paths", []) if not force_refresh and cached_data else []
    user_info = cached_data.get("user_info") if not force_refresh and cached_data else None

    try:
        if not user_info or force_refresh:
            user_response = client.get_user(
                username=username,
                user_fields=["created_at", "public_metrics", "profile_image_url", "verified", "description", "location"]
            )
            if not user_response or not user_response.data:
                raise UserNotFoundError(f"Twitter user @{username} not found.")
            user = user_response.data
            user_info = {
                "id": str(user.id), "name": user.name, "username": user.username,
                "created_at": user.created_at.isoformat() if user.created_at else None,
                "public_metrics": user.public_metrics, "profile_image_url": user.profile_image_url,
                "verified": user.verified, "description": user.description, "location": user.location
            }

        user_id = user_info["id"]        
        # Start the final collection of tweets with what's already in the cache.
        processed_tweets = {tweet['id']: tweet for tweet in existing_tweets}
        
        # Fetch ONLY new tweets from the API.
        new_tweets_from_api = []
        pagination_token = None
        new_media_includes = {}
        all_users_from_includes, all_tweets_from_includes = {}, {}
        
        needed_count = fetch_limit - len(existing_tweets)
        
        while needed_count > 0 or is_incremental_update:
            page_limit = min(needed_count, 100) if not is_incremental_update else 100
            
            tweets_response = client.get_users_tweets(
                id=user_id, max_results=page_limit,
                since_id=since_id, pagination_token=pagination_token,
                tweet_fields=["created_at", "public_metrics", "attachments", "entities", "conversation_id", "in_reply_to_user_id", "referenced_tweets"],
                expansions=["attachments.media_keys", "author_id", "in_reply_to_user_id", "referenced_tweets.id", "referenced_tweets.id.author_id"],
                media_fields=["url", "preview_image_url", "type", "media_key"],
                user_fields=["username", "name", "id"]
            )
            
            if tweets_response.data:
                new_tweets_from_api.extend(tweets_response.data)
                needed_count -= len(tweets_response.data)

            if tweets_response.includes:
                if "media" in tweets_response.includes: new_media_includes.update({m.media_key: m for m in tweets_response.includes["media"]})
                if "users" in tweets_response.includes: all_users_from_includes.update({str(u.id): u for u in tweets_response.includes["users"]})
                if "tweets" in tweets_response.includes: all_tweets_from_includes.update({str(t.id): t for t in tweets_response.includes["tweets"]})

            pagination_token = tweets_response.meta.get("next_token")
            if not pagination_token or is_incremental_update:
                break
        
        # Process ONLY the new tweets from the API (which are objects)
        newly_added_media_paths = set()
        auth_details = {"bearer_token": client.bearer_token}

        for tweet_obj in new_tweets_from_api:
            tweet_id = str(tweet_obj.id)
            if tweet_id in processed_tweets: continue

            media_items_for_tweet = []
            if tweet_obj.attachments and "media_keys" in tweet_obj.attachments:
                for media_key in tweet_obj.attachments["media_keys"]:
                    media = new_media_includes.get(media_key)
                    if media:
                        url = media.url if media.type in ["photo", "gif"] and media.url else media.preview_image_url
                        if url:
                            media_path = download_media(cache.base_dir, url, cache.is_offline, "twitter", auth_details)
                            if media_path:
                                media_items_for_tweet.append({"type": media.type, "analysis": None, "url": url, "local_path": str(media_path)})
                                newly_added_media_paths.add(str(media_path))
            
            replied_to_user_info = all_users_from_includes.get(str(tweet_obj.in_reply_to_user_id))
            # Add the newly processed tweet dictionary to our collection
            processed_tweets[tweet_id] = {
                "id": tweet_id, "text": tweet_obj.text, "created_at": tweet_obj.created_at.isoformat(),
                "metrics": tweet_obj.public_metrics, "entities_raw": tweet_obj.entities,
                "replied_to_user_info": {"username": replied_to_user_info.username} if replied_to_user_info else None,
                "referenced_tweets": [{"type": ref.type, "id": str(ref.id)} for ref in tweet_obj.referenced_tweets] if tweet_obj.referenced_tweets else [],
                "media": media_items_for_tweet
            }

        # The final list is created from the values of our de-duplicated dictionary
        final_tweets = sorted(list(processed_tweets.values()), key=lambda x: get_sort_key(x, "created_at"), reverse=True)[:max(fetch_limit, MAX_CACHE_ITEMS)]
        final_media_paths = sorted(list(newly_added_media_paths.union(existing_media_paths)))

        final_data = {
            "user_info": user_info, "tweets": final_tweets, 
            "media_analysis": [], "media_paths": final_media_paths
        }
        cache.save("twitter", username, final_data)
        return final_data

    except (UserNotFoundError, AccessForbiddenError, RateLimitExceededError):
        raise
    except tweepy.TooManyRequests:
        raise RateLimitExceededError("Twitter API rate limit exceeded.")
    except tweepy.errors.NotFound:
        raise UserNotFoundError(f"Twitter user @{username} not found.")
    except tweepy.errors.Forbidden as e:
        raise AccessForbiddenError(f"Access forbidden to @{username}'s tweets. Reason: {e}")
    except Exception as e:
        logger.error(f"Unexpected error fetching Twitter data for @{username}: {e}", exc_info=True)
        return None