import logging, os, httpx
from typing import Any, Dict, List, Optional
from .base_fetcher import BaseFetcher, RateLimitHandler
from ..utils import NormalizedPost, NormalizedProfile, UserData, get_sort_key, extract_and_resolve_urls

class GitHubFetcher(BaseFetcher):
    def __init__(self):
        super().__init__(platform_name="github")
        self.base_url = "https://api.github.com"

    def _get_or_fetch_profile(self, username: str, cached_data: Optional[UserData], force_refresh: bool, **kwargs) -> Optional[NormalizedProfile]:
        if not force_refresh and cached_data and cached_data.get("profile"): return cached_data["profile"]
        headers = {"Accept": "application/vnd.github.v3+json", "User-Agent": "SocialOSINTAgent"}
        if tk := os.getenv("GITHUB_TOKEN"): headers["Authorization"] = f"Bearer {tk}"
        try:
            with httpx.Client(headers=headers, timeout=20.0) as client:
                resp = client.get(f"{self.base_url}/users/{username}")
                RateLimitHandler.check_response_headers(resp.headers, "github")
                resp.raise_for_status()
                d = resp.json()
                return NormalizedProfile(
                    platform="github", id=str(d["id"]), username=d["login"],
                    display_name=d.get("name"), bio=d.get("bio"),
                    created_at=get_sort_key(d, "created_at"), profile_url=d["html_url"],
                    metrics={"followers": d.get("followers", 0), "repos": d.get("public_repos", 0)}
                )
        except Exception as e: self._handle_api_error(e, username)

    def _fetch_posts(self, username: str, profile: NormalizedProfile, needed_count: int, processed_ids: set, **kwargs) -> List[NormalizedPost]:
        headers = {"Accept": "application/vnd.github.v3+json", "User-Agent": "SocialOSINTAgent"}
        if tk := os.getenv("GITHUB_TOKEN"): headers["Authorization"] = f"Bearer {tk}"
        new_posts, page = [], 1
        try:
            with httpx.Client(headers=headers, timeout=20.0) as client:
                while len(new_posts) < needed_count:
                    resp = client.get(f"{self.base_url}/users/{username}/events/public", params={"per_page": 100, "page": page})
                    RateLimitHandler.check_response_headers(resp.headers, "github")
                    resp.raise_for_status()
                    events = resp.json()
                    if not events: break
                    for ev in events:
                        if ev["id"] not in processed_ids:
                            new_posts.append(self._normalize_event(ev))
                            processed_ids.add(ev["id"])
                    page += 1
        except Exception as e: self._handle_api_error(e, username)
        return new_posts

    def _normalize_event(self, ev: Dict) -> NormalizedPost:
        etype = ev["type"]
        repo = ev.get("repo", {}).get("name", "unknown")
        payload = ev.get("payload", {})
        text = f"Activity on {repo}"
        
        # Restoration of OSINT logic for GitHub events
        if etype == "PushEvent":
            commits = [f"- {c['sha'][:7]}: {c['message']}" for c in payload.get('commits', [])]
            text = f"Pushed to {repo}:\n" + "\n".join(commits)
        elif etype == "WatchEvent": text = f"Starred {repo}"
        elif etype == "CreateEvent": text = f"Created {payload.get('ref_type')} in {repo}"
        elif etype == "IssueCommentEvent": 
            text = f"Commented on issue in {repo}: {payload.get('issue', {}).get('title')}"

        return NormalizedPost(
            platform="github", id=ev["id"], created_at=get_sort_key(ev, "created_at"),
            author_username=ev["actor"]["login"], text=text, media=[],
            external_links=extract_and_resolve_urls(text),
            post_url=f"https://github.com/{repo}", type=etype, context={"repo": repo}
        )

def fetch_data(**kwargs):
    u, c = kwargs.pop("username"), kwargs.pop("cache")
    return GitHubFetcher().fetch_data(u, c, **kwargs)