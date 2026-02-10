import logging, os, httpx
from typing import Any, List, Optional, Tuple
from .base_fetcher import BaseFetcher, RateLimitHandler
from ..utils import NormalizedPost, NormalizedProfile, get_sort_key, extract_and_resolve_urls

logger = logging.getLogger("SocialOSINTAgent.platforms.github")

class GitHubFetcher(BaseFetcher):
    def __init__(self):
        super().__init__(platform_name="github")
        self.base_url = "https://api.github.com"

    def _fetch_profile(self, username: str, **kwargs) -> Optional[NormalizedProfile]:
        headers = {"Accept": "application/vnd.github.v3+json", "User-Agent": "SocialOSINTAgent"}
        if tk := os.getenv("GITHUB_TOKEN"): headers["Authorization"] = f"Bearer {tk}"
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

    def _fetch_batch(self, username, profile, needed, state, **kwargs) -> Tuple[List[Any], Any]:
        page = state or 1
        headers = {"Accept": "application/vnd.github.v3+json", "User-Agent": "SocialOSINTAgent"}
        if tk := os.getenv("GITHUB_TOKEN"): headers["Authorization"] = f"Bearer {tk}"
        with httpx.Client(headers=headers, timeout=20.0) as client:
            resp = client.get(f"{self.base_url}/users/{username}/events/public", params={"per_page": 100, "page": page})
            RateLimitHandler.check_response_headers(resp.headers, "github")
            events = resp.json()
            return events, (page + 1 if len(events) >= 100 else None)

    def _normalize(self, ev: Any, profile: NormalizedProfile, **kwargs) -> NormalizedPost:
        etype, repo = ev["type"], ev.get("repo", {}).get("name", "unknown")
        payload = ev.get("payload", {})
        text = f"Activity on {repo}"
        if etype == "PushEvent":
            commits = payload.get('commits', [])
            text = f"Pushed to {repo}:\n" + "\n".join([f"- {c['sha'][:7]}: {c['message']}" for c in commits])
        elif etype == "WatchEvent": text = f"Starred {repo}"

        return NormalizedPost(
            platform="github", id=ev["id"], created_at=get_sort_key(ev, "created_at"),
            author_username=profile["username"], text=text, media=[],
            external_links=extract_and_resolve_urls(text), post_url=f"https://github.com/{repo}", 
            type=etype, context={"repo": repo}
        )

def fetch_data(**kwargs):
    return GitHubFetcher().fetch_data(kwargs.pop("username"), kwargs.pop("cache"), **kwargs)