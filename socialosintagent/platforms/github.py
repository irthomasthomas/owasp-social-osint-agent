import logging
import os
import re
from typing import Any, Dict, List, Optional, Set
import httpx

from .base_fetcher import BaseFetcher, RateLimitHandler
from ..utils import NormalizedPost, NormalizedProfile, UserData, get_sort_key, extract_and_resolve_urls

logger = logging.getLogger("SocialOSINTAgent.platforms.github")


class GitHubFetcher(BaseFetcher):
    def __init__(self):
        super().__init__(platform_name="github")
        self.base_url = "https://api.github.com"

    def _get_or_fetch_profile(
        self, 
        username: str, 
        cached_data: Optional[UserData], 
        force_refresh: bool, 
        **kwargs
    ) -> Optional[NormalizedProfile]:
        if not force_refresh and cached_data and cached_data.get("profile"):
            return cached_data["profile"]
        
        headers = {"Accept": "application/vnd.github.v3+json", "User-Agent": "SocialOSINTAgent"}
        if tk := os.getenv("GITHUB_TOKEN"):
            headers["Authorization"] = f"Bearer {tk}"
        
        try:
            with httpx.Client(headers=headers, timeout=20.0) as client:
                resp = client.get(f"{self.base_url}/users/{username}")
                RateLimitHandler.check_response_headers(resp.headers, "github")
                resp.raise_for_status()
                d = resp.json()
                return NormalizedProfile(
                    platform="github", 
                    id=str(d["id"]), 
                    username=d["login"],
                    display_name=d.get("name"), 
                    bio=d.get("bio"),
                    created_at=get_sort_key(d, "created_at"), 
                    profile_url=d["html_url"],
                    metrics={"followers": d.get("followers", 0), "repos": d.get("public_repos", 0)}
                )
        except Exception as e:
            self._handle_api_error(e, username)

    def _fetch_posts(
        self, 
        username: str, 
        profile: NormalizedProfile, 
        needed_count: int, 
        processed_ids: set, 
        **kwargs
    ) -> List[NormalizedPost]:
        headers = {"Accept": "application/vnd.github.v3+json", "User-Agent": "SocialOSINTAgent"}
        if tk := os.getenv("GITHUB_TOKEN"):
            headers["Authorization"] = f"Bearer {tk}"
        
        # Deep analysis options
        deep_analysis = kwargs.get("github_deep_analysis", False)
        max_patches = kwargs.get("github_max_patches", 20)
        
        new_posts = []
        page = 1
        patches_fetched = 0
        
        try:
            with httpx.Client(headers=headers, timeout=20.0) as client:
                while len(new_posts) < needed_count:
                    resp = client.get(
                        f"{self.base_url}/users/{username}/events/public", 
                        params={"per_page": 100, "page": page}
                    )
                    RateLimitHandler.check_response_headers(resp.headers, "github")
                    resp.raise_for_status()
                    events = resp.json()
                    if not events:
                        break
                    
                    for ev in events:
                        if ev["id"] not in processed_ids:
                            normalized_post = self._normalize_event(ev)
                            
                            # Optionally analyze patches for PushEvents
                            if (deep_analysis and 
                                ev["type"] == "PushEvent" and 
                                patches_fetched < max_patches and
                                self._is_interesting_commit(ev)):
                                
                                patch_data = self._analyze_patch(ev, client)
                                if patch_data:
                                    if "context" not in normalized_post:
                                        normalized_post["context"] = {}
                                    normalized_post["context"]["patch_analysis"] = patch_data
                                    patches_fetched += 1
                            
                            new_posts.append(normalized_post)
                            processed_ids.add(ev["id"])
                    
                    page += 1
                
                if deep_analysis and patches_fetched > 0:
                    logger.info(f"Analyzed {patches_fetched} patches for {username}")
        
        except Exception as e:
            self._handle_api_error(e, username)
        
        return new_posts

    def _normalize_event(self, ev: Dict) -> NormalizedPost:
        etype = ev["type"]
        repo = ev.get("repo", {}).get("name", "unknown")
        payload = ev.get("payload", {})
        text = f"Activity on {repo}"
        
        if etype == "PushEvent":
            commits = [f"- {c['sha'][:7]}: {c['message']}" for c in payload.get('commits', [])]
            text = f"Pushed to {repo}:\n" + "\n".join(commits)
        elif etype == "WatchEvent":
            text = f"Starred {repo}"
        elif etype == "CreateEvent":
            text = f"Created {payload.get('ref_type')} in {repo}"
        elif etype == "IssueCommentEvent":
            text = f"Commented on issue in {repo}: {payload.get('issue', {}).get('title')}"

        return NormalizedPost(
            platform="github", 
            id=ev["id"], 
            created_at=get_sort_key(ev, "created_at"),
            author_username=ev["actor"]["login"], 
            text=text, 
            media=[],
            external_links=extract_and_resolve_urls(text),
            post_url=f"https://github.com/{repo}", 
            type=etype, 
            context={"repo": repo}
        )

    def _is_interesting_commit(self, event: Dict) -> bool:
        """Quick heuristic to decide if commit is worth analyzing."""
        import random
        payload = event.get("payload", {})
        commits = payload.get("commits", [])
        
        if not commits:
            return False
        
        # Check for security keywords or large commits
        for commit in commits:
            msg = commit.get("message", "").lower()
            if len(msg) > 200 or any(kw in msg for kw in ["security", "cve", "auth", "crypto"]):
                return True
        
        # 10% random sampling
        return random.random() < 0.10

    def _analyze_patch(self, event: Dict, client: httpx.Client) -> Optional[Dict[str, Any]]:
        """Fetch patch and extract key OSINT signals."""
        payload = event.get("payload", {})
        commits = payload.get("commits", [])
        repo_name = event.get("repo", {}).get("name", "unknown")
        
        if not commits:
            return None
        
        commit = commits[0]
        commit_sha = commit.get("sha")
        if not commit_sha:
            return None
        
        patch_url = f"https://github.com/{repo_name}/commit/{commit_sha}.patch"
        
        try:
            resp = client.get(patch_url, timeout=15.0)
            resp.raise_for_status()
            patch = resp.text
            
            # Extract key signals
            return {
                "commit_sha": commit_sha[:7],
                "commit_url": f"https://github.com/{repo_name}/commit/{commit_sha}",
                "author_email": self._extract_email(patch, "From:"),
                "committer_email": self._extract_email(patch, "Committer:"),
                "languages": self._detect_languages(patch),
                "lines_changed": self._count_changes(patch),
                "has_tests": "test" in patch.lower(),
                "security_related": any(kw in patch.lower() for kw in ["security", "auth", "crypto", "password"]),
            }
        
        except Exception as e:
            logger.debug(f"Could not analyze patch for {commit_sha[:7]}: {e}")
            return None

    def _extract_email(self, patch: str, prefix: str) -> Optional[str]:
        """Extract email from patch header."""
        match = re.search(f'^{prefix}.*?<(.+?)>', patch, re.MULTILINE)
        return match.group(1) if match else None

    def _detect_languages(self, patch: str) -> Set[str]:
        """Detect languages from file extensions."""
        paths = re.findall(r'diff --git a/(.+?) b/', patch)
        ext_map = {
            '.py': 'Python', '.js': 'JavaScript', '.ts': 'TypeScript', 
            '.java': 'Java', '.go': 'Go', '.rs': 'Rust', '.cpp': 'C++',
            '.rb': 'Ruby', '.php': 'PHP', '.swift': 'Swift'
        }
        langs = set()
        for path in paths[:20]:
            for ext, lang in ext_map.items():
                if path.lower().endswith(ext):
                    langs.add(lang)
        return langs

    def _count_changes(self, patch: str) -> Dict[str, int]:
        """Count added/removed lines."""
        lines = patch.split('\n')
        return {
            "added": sum(1 for l in lines if l.startswith('+') and not l.startswith('+++')),
            "removed": sum(1 for l in lines if l.startswith('-') and not l.startswith('---'))
        }


def fetch_data(**kwargs):
    u, c = kwargs.pop("username"), kwargs.pop("cache")
    return GitHubFetcher().fetch_data(u, c, **kwargs)
