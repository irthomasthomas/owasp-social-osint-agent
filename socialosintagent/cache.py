import json
import logging
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Optional

from .utils import DateTimeEncoder, UserData, get_sort_key

logger = logging.getLogger("SocialOSINTAgent.cache")

MAX_CACHE_ITEMS = 200
CACHE_EXPIRY_HOURS = 24

class CacheManager:
    def __init__(self, base_dir: Path, is_offline: bool):
        self.base_dir = base_dir; self.is_offline = is_offline
        self.cache_dir = self.base_dir / "cache"; self.cache_dir.mkdir(parents=True, exist_ok=True)

    @lru_cache(maxsize=128)
    def get_cache_path(self, platform: str, username: str) -> Path:
        safe_username = "".join(c for c in username if c.isalnum() or c in ["-", "_", ".", "@"])[:100]
        return self.cache_dir / f"{platform}_{safe_username}.json"

    def load(self, platform: str, username: str) -> Optional[UserData]:
        cache_path = self.get_cache_path(platform, username)
        if not cache_path.exists(): return None
        try:
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            
            # Universal validation for the canonical model
            if not all(k in data for k in ["timestamp", "profile", "posts"]):
                logger.warning(f"Cache file for {platform}/{username} is incomplete. Discarding.")
                cache_path.unlink(missing_ok=True); return None

            timestamp = get_sort_key(data, "timestamp")
            if self.is_offline:
                logger.info(f"Offline mode: Using cache for {platform}/{username}.")
                return data

            if (datetime.now(timezone.utc) - timestamp) < timedelta(hours=CACHE_EXPIRY_HOURS):
                logger.info(f"Cache hit and valid for {platform}/{username}")
                return data
            else:
                logger.info(f"Cache expired for {platform}/{username}. Discarding."); return None

        except (json.JSONDecodeError, KeyError, FileNotFoundError) as e:
            logger.warning(f"Failed to load or parse cache for {platform}/{username}: {e}. Discarding.")
            cache_path.unlink(missing_ok=True); return None

    def save(self, platform: str, username: str, data: UserData):
        cache_path = self.get_cache_path(platform, username)
        try:
            # Universal sorting
            if "posts" in data and isinstance(data["posts"], list):
                data["posts"].sort(key=lambda x: get_sort_key(x, 'created_at'), reverse=True)
            
            data["timestamp"] = datetime.now(timezone.utc)
            data["stats"] = {"total_posts_cached": len(data.get("posts", []))}
            cache_path.write_text(json.dumps(data, indent=2, cls=DateTimeEncoder), encoding="utf-8")
            logger.info(f"Saved cache for {platform}/{username} to {cache_path}")
        except Exception as e:
            logger.error(f"Failed to save cache for {platform}/{username}: {e}", exc_info=True)