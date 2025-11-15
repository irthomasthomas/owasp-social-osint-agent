import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from ..cache import MAX_CACHE_ITEMS, CacheManager
from ..exceptions import (AccessForbiddenError, RateLimitExceededError,
                         UserNotFoundError)
from ..utils import (NormalizedPost, NormalizedProfile, UserData,
                     extract_and_resolve_urls, get_sort_key)

logger = logging.getLogger("SocialOSINTAgent.platforms.github")

DEFAULT_FETCH_LIMIT = 50
GITHUB_API_BASE_URL = "https://api.github.com"

def fetch_data(
    username: str,
    cache: CacheManager,
    force_refresh: bool = False,
    fetch_limit: int = DEFAULT_FETCH_LIMIT,
) -> Optional[UserData]:
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        logger.warning("GITHUB_TOKEN is not set. Using unauthenticated requests with lower rate limits.")

    cached_data = cache.load("github", username)
    if cache.is_offline:
        return cached_data

    if not force_refresh and cached_data and len(cached_data.get("posts", [])) >= fetch_limit:
        return cached_data

    logger.info(f"Fetching GitHub data for {username} (Limit: {fetch_limit})")
    
    headers = {"Accept": "application/vnd.github.v3+json", "User-Agent": "SocialOSINTAgent"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        with httpx.Client(headers=headers, timeout=20.0, follow_redirects=True) as client:
            profile_obj: Optional[NormalizedProfile] = None
            if not force_refresh and cached_data and "profile" in cached_data:
                profile_obj = cached_data["profile"]
            
            if not profile_obj:
                profile_resp = client.get(f"{GITHUB_API_BASE_URL}/users/{username}")
                _check_response_for_errors(profile_resp)
                profile_data = profile_resp.json()
                profile_obj = NormalizedProfile(
                    platform="github", id=str(profile_data["id"]), username=profile_data["login"],
                    display_name=profile_data.get("name"), bio=profile_data.get("bio"),
                    created_at=get_sort_key(profile_data, "created_at"), profile_url=profile_data["html_url"],
                    metrics={
                        "followers": profile_data.get("followers", 0), "following": profile_data.get("following", 0),
                        "public_repos": profile_data.get("public_repos", 0),
                    }
                )
            
            existing_posts = cached_data.get("posts", []) if not force_refresh and cached_data else []
            processed_post_ids = {p['id'] for p in existing_posts}
            all_posts: List[NormalizedPost] = list(existing_posts)
            
            page = 1
            while len(all_posts) < fetch_limit:
                events_resp = client.get(
                    f"{GITHUB_API_BASE_URL}/users/{username}/events/public",
                    params={"per_page": min(fetch_limit, 100), "page": page}
                )
                _check_response_for_errors(events_resp)
                events_data = events_resp.json()
                if not events_data: break

                for event in events_data:
                    if event["id"] not in processed_post_ids:
                        all_posts.append(_to_normalized_post(event))
                        processed_post_ids.add(event["id"])
                page += 1

            final_posts = sorted(all_posts, key=lambda x: x['created_at'], reverse=True)[:max(fetch_limit, MAX_CACHE_ITEMS)]
            user_data = UserData(profile=profile_obj, posts=final_posts)
            cache.save("github", username, user_data)
            return user_data

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404: raise UserNotFoundError(f"GitHub user '{username}' not found.") from e
        elif e.response.status_code == 403: raise AccessForbiddenError(f"Access to GitHub user '{username}' is forbidden or rate-limited.") from e
        else:
            logger.error(f"HTTP error fetching GitHub data for {username}: {e}", exc_info=True)
            return None
    except Exception as e:
        if not isinstance(e, (UserNotFoundError, RateLimitExceededError, AccessForbiddenError)):
             logger.error(f"Unexpected error fetching GitHub data for {username}: {e}", exc_info=True)
        raise

def _check_response_for_errors(response: httpx.Response):
    if 'x-ratelimit-remaining' in response.headers and int(response.headers['x-ratelimit-remaining']) == 0:
        reset_timestamp = int(response.headers['x-ratelimit-reset'])
        raise RateLimitExceededError(f"GitHub API rate limit exceeded. Resets at {datetime.fromtimestamp(reset_timestamp, tz=timezone.utc).isoformat()}")
    response.raise_for_status()

def _to_normalized_post(event: Dict[str, Any]) -> NormalizedPost:
    payload = event.get("payload", {})
    repo_name = event.get("repo", {}).get("name", "")
    text, url = _format_event_details(event, payload, repo_name)
    return NormalizedPost(
        platform="github", id=event["id"], created_at=get_sort_key(event, "created_at"),
        author_username=event["actor"]["login"], text=text, media=[], external_links=extract_and_resolve_urls(text),
        post_url=url, metrics={}, type=event["type"], context={"repo": repo_name}
    )

def _format_event_details(event, payload, repo_name) -> tuple[str, str]:
    event_type = event["type"]
    text, url = f"Performed an event of type {event_type} on {repo_name}", f"https://github.com/{repo_name}"
    if event_type == "PushEvent":
        commit_count = len(payload.get("commits", []))
        text = f"Pushed {commit_count} commit(s) to {repo_name}"
        if commit_count > 0 and "url" in payload["commits"][0]:
            url = payload["commits"][0]["url"].replace("api.", "").replace("/repos", "").replace("/commits", "/commit")
    elif event_type in ["IssueCommentEvent", "IssuesEvent"]:
        action, issue = payload.get("action", "commented on"), payload.get("issue", {})
        text = f"{action.capitalize()} issue #{issue.get('number')} in {repo_name}: {issue.get('title', '')}"
        url = issue.get("html_url", url)
    elif event_type in ["PullRequestEvent", "PullRequestReviewCommentEvent", "PullRequestReviewEvent"]:
        action, pr = payload.get("action", "interacted with"), payload.get("pull_request", {})
        text = f"{action.capitalize()} pull request #{pr.get('number')} in {repo_name}: {pr.get('title', '')}"
        url = pr.get("html_url", url)
    return text, url