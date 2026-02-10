import logging
from typing import Any, List, Optional, Tuple
from bs4 import BeautifulSoup
from .base_fetcher import BaseFetcher
from ..utils import (NormalizedMedia, NormalizedPost, NormalizedProfile, download_media, extract_and_resolve_urls)

logger = logging.getLogger("SocialOSINTAgent.platforms.mastodon")

class MastodonFetcher(BaseFetcher):
    def __init__(self):
        super().__init__(platform_name="mastodon")

    def _get_client(self, username, **kwargs):
        instance = username.split('@')[1]
        return kwargs.get("clients", {}).get(instance) or kwargs.get("default_client")

    def _fetch_profile(self, username: str, **kwargs) -> Optional[NormalizedProfile]:
        client = self._get_client(username, **kwargs)
        account = client.account_lookup(acct=username)
        return NormalizedProfile(
            platform="mastodon", id=str(account["id"]), username=account["acct"],
            display_name=account["display_name"], profile_url=account["url"],
            bio=BeautifulSoup(account.get("note", ""), "html.parser").get_text(separator=" ", strip=True),
            created_at=account["created_at"],
            metrics={"followers": account["followers_count"], "post_count": account["statuses_count"]}
        )

    def _fetch_batch(self, username, profile, needed, state, **kwargs) -> Tuple[List[Any], Any]:
        client = self._get_client(username, **kwargs)
        statuses = client.account_statuses(id=profile["id"], limit=min(needed, 40), max_id=state)
        next_state = statuses[-1]['id'] if statuses else None
        return statuses, next_state

    def _normalize(self, status: Any, profile: NormalizedProfile, **kwargs) -> NormalizedPost:
        cache = kwargs.get("cache")
        cleaned_text = BeautifulSoup(status["content"], "html.parser").get_text(separator=" ", strip=True)
        media_items = []
        for att in status.get("media_attachments", []):
            if path := download_media(cache.base_dir, att["url"], cache.is_offline, "mastodon"):
                media_items.append(NormalizedMedia(url=att["url"], local_path=str(path), type=att["type"]))

        return NormalizedPost(
            platform="mastodon", id=str(status["id"]), created_at=status["created_at"],
            author_username=profile["username"], text=cleaned_text, media=media_items,
            external_links=extract_and_resolve_urls(cleaned_text), post_url=status["url"],
            metrics={"likes": status["favourites_count"], "reposts": status["reblogs_count"]},
            type="repost" if status.get("reblog") else "post"
        )

def fetch_data(**kwargs):
    return MastodonFetcher().fetch_data(kwargs.pop("username"), kwargs.pop("cache"), **kwargs)