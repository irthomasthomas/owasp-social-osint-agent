import logging
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from mastodon import Mastodon

from .base_fetcher import BaseFetcher
from ..cache import CacheManager
from ..utils import (NormalizedMedia, NormalizedPost, NormalizedProfile, 
                   UserData, download_media, extract_and_resolve_urls)

logger = logging.getLogger("SocialOSINTAgent.platforms.mastodon")

class MastodonFetcher(BaseFetcher):
    def __init__(self):
        super().__init__(platform_name="mastodon")

    def _get_or_fetch_profile(
        self, 
        username: str, 
        cached_data: Optional[UserData], 
        force_refresh: bool, 
        **kwargs
    ) -> Optional[NormalizedProfile]:
        # Check cache first (consistent with other fetchers)
        if not force_refresh and cached_data and cached_data.get("profile"):
            return cached_data["profile"]
        
        clients: Dict[str, Mastodon] = kwargs.get("clients")
        default: Optional[Mastodon] = kwargs.get("default_client")
        
        # Validate username format
        parts = username.split('@')
        if len(parts) != 2:
            self.logger.error(
                f"Invalid Mastodon username format: '{username}'. "
                f"Expected format: user@instance.domain"
            )
            return None
        
        instance = parts[1]
        instance_url = f"https://{instance}"
        
        # Get appropriate client
        client = clients.get(instance_url) or default
        
        # Warn if using default instance (incomplete results possible)
        if instance_url not in clients:
            self.logger.warning(
                f"No credentials for {instance}. Using default instance to query {username}. "
                f"Results may be incomplete. Consider adding {instance_url} credentials."
            )
        
        try:
            acc = client.account_lookup(acct=username)
            return NormalizedProfile(
                platform="mastodon", 
                id=str(acc["id"]), 
                username=acc["acct"],
                display_name=acc["display_name"], 
                bio=BeautifulSoup(acc.get("note",""), "html.parser").get_text(),
                created_at=acc["created_at"], 
                profile_url=acc["url"],
                metrics={"followers": acc["followers_count"], "statuses": acc["statuses_count"]}
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
        clients: Dict[str, Mastodon] = kwargs.get("clients")
        default: Optional[Mastodon] = kwargs.get("default_client")
        allow_ext = kwargs.get("allow_external_media", False)
        
        # Extract the instance domain to trust it dynamically
        user_instance_domain = username.split('@')[1].lower()
        
        instance_url = f"https://{user_instance_domain}"
        client = clients.get(instance_url) or default
        cache = kwargs.get("cache")
        
        new_posts = []
        try:
            statuses = client.account_statuses(id=profile["id"], limit=needed_count)
            for s in statuses:
                if str(s['id']) not in processed_ids:
                    new_posts.append(
                        self._normalize_status(s, cache, profile["username"], allow_ext, user_instance_domain)
                    )
        except Exception as e: 
            self._handle_api_error(e, username)
        return new_posts

    def _normalize_status(
        self, 
        s: Dict, 
        cache: CacheManager, 
        author: str, 
        allow_ext: bool, 
        host_domain: str
    ) -> NormalizedPost:
        text = BeautifulSoup(s["content"], "html.parser").get_text()
        media_items = []
        
        # Import the global list from utils
        from ..utils import SAFE_CDN_DOMAINS

        for att in s.get("media_attachments", []):
            media_url = att["url"]
            media_domain = urlparse(media_url).netloc.lower()
            
            # 1. Check Dynamic Trust (Matches the user's home server)
            is_internal_host = media_domain == host_domain or media_domain.endswith(f".{host_domain}")
            
            # 2. Check Static Trust (Matches the list in utils.py)
            is_globally_safe = any(
                media_domain == d or media_domain.endswith(f".{d}") 
                for d in SAFE_CDN_DOMAINS.get("mastodon", [])
            )
            
            # Download is allowed if ANY of these are true:
            effective_allow_external = allow_ext or is_internal_host or is_globally_safe
            
            p = download_media(
                cache.base_dir, 
                media_url, 
                cache.is_offline, 
                "mastodon", 
                allow_external=effective_allow_external
            )
            
            if p:
                media_items.append(
                    NormalizedMedia(url=media_url, local_path=str(p), type=att["type"])
                )

        return NormalizedPost(
            platform="mastodon", 
            id=str(s["id"]), 
            created_at=s["created_at"],
            author_username=author, 
            text=text, 
            media=media_items,
            external_links=extract_and_resolve_urls(text),
            post_url=s["url"], 
            metrics={"likes": s["favourites_count"]},
            type="repost" if s.get("reblog") else "post"
        )

def fetch_data(**kwargs):
    u = kwargs.pop("username")
    c = kwargs.pop("cache")
    return MastodonFetcher().fetch_data(u, c, **kwargs)
