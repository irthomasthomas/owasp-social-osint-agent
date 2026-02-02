"""
Unified base fetcher with consistent rate limit handling and error management.

This module provides a common foundation for all platform fetchers, reducing
code duplication and ensuring consistent behavior across platforms.
"""

import logging
from abc import ABC, abstractmethod
from typing import Dict, List, Optional
from datetime import datetime, timezone

from ..cache import MAX_CACHE_ITEMS, CacheManager
from ..exceptions import (
    AccessForbiddenError,
    RateLimitExceededError,
    UserNotFoundError,
)
from ..utils import UserData, NormalizedProfile, NormalizedPost, get_sort_key

logger = logging.getLogger("SocialOSINTAgent.base_fetcher")


class BaseFetcher(ABC):
    """
    Abstract base class for platform-specific data fetchers.
    
    Provides common functionality for:
    - Cache management
    - Rate limit handling
    - Error standardization
    - Data normalization workflow
    """

    def __init__(self, platform_name: str):
        """
        Initialize the base fetcher.
        
        Args:
            platform_name: The name of the platform (e.g., 'twitter', 'reddit')
        """
        self.platform_name = platform_name
        self.logger = logging.getLogger(f"SocialOSINTAgent.platforms.{platform_name}")

    def fetch_data(
        self,
        username: str,
        cache: CacheManager,
        force_refresh: bool = False,
        fetch_limit: int = 50,
        **kwargs,
    ) -> Optional[UserData]:
        """
        Main entry point for fetching user data.
        
        This method orchestrates the entire fetch process with consistent
        error handling and caching logic.
        
        Args:
            username: The username to fetch data for
            cache: CacheManager instance
            force_refresh: If True, bypass cache and fetch fresh data
            fetch_limit: Maximum number of posts to fetch
            **kwargs: Platform-specific arguments
            
        Returns:
            UserData object or None on failure
        """
        # Handle offline mode
        cached_data = cache.load(self.platform_name, username)
        if cache.is_offline:
            return cached_data

        # Check cache sufficiency
        if not force_refresh and cached_data:
            if len(cached_data.get("posts", [])) >= fetch_limit:
                self.logger.info(
                    f"Cache hit for {self.platform_name}/{username} with sufficient items"
                )
                return cached_data

        self.logger.info(
            f"Fetching {self.platform_name} data for {username} (Limit: {fetch_limit})"
        )

        try:
            # Fetch profile and posts with unified error handling
            user_data = self._fetch_with_retry(
                username, cache, cached_data, force_refresh, fetch_limit, **kwargs
            )
            
            if user_data:
                # Save to cache
                cache.save(self.platform_name, username, user_data)
                return user_data
            
            return None

        except RateLimitExceededError:
            # Let rate limit errors propagate to be handled by the analyzer
            raise
        except (UserNotFoundError, AccessForbiddenError):
            # Let these specific errors propagate
            raise
        except Exception as e:
            # Catch-all for unexpected errors
            self.logger.error(
                f"Unexpected error fetching {self.platform_name} data for {username}: {e}",
                exc_info=True,
            )
            return None

    def _fetch_with_retry(
        self,
        username: str,
        cache: CacheManager,
        cached_data: Optional[UserData],
        force_refresh: bool,
        fetch_limit: int,
        **kwargs,
    ) -> Optional[UserData]:
        """
        Fetch data with platform-specific logic.
        
        Subclasses should override this to implement platform-specific fetching.
        This method handles the orchestration of profile and post fetching.
        
        Args:
            username: The username to fetch
            cache: CacheManager instance
            cached_data: Previously cached data (if any)
            force_refresh: Whether to force refresh
            fetch_limit: Max items to fetch
            **kwargs: Platform-specific arguments
            
        Returns:
            UserData object or None
        """
        # Get or fetch profile
        profile_obj = self._get_or_fetch_profile(
            username, cached_data, force_refresh, **kwargs
        )
        
        if not profile_obj:
            # Raise the exception so the Analyzer/Tests can catch it
            raise UserNotFoundError(f"{self.platform_name} user '{username}' not found")

        # Get existing posts from cache
        all_posts = (
            cached_data.get("posts", [])
            if not force_refresh and cached_data
            else []
        )
        processed_post_ids = {p["id"] for p in all_posts}

        # Fetch new posts
        needed_count = fetch_limit - len(all_posts)
        if force_refresh or needed_count > 0:
            new_posts = self._fetch_posts(
                username, profile_obj, needed_count, processed_post_ids, cache=cache, **kwargs
            )
            all_posts.extend(new_posts)

        # Sort and trim posts
        final_posts = sorted(
            all_posts, key=lambda x: get_sort_key(x, "created_at"), reverse=True
        )[: max(fetch_limit, MAX_CACHE_ITEMS)]

        return UserData(profile=profile_obj, posts=final_posts)

    @abstractmethod
    def _get_or_fetch_profile(
        self,
        username: str,
        cached_data: Optional[UserData],
        force_refresh: bool,
        **kwargs,
    ) -> Optional[NormalizedProfile]:
        """
        Get profile from cache or fetch it fresh.
        
        Platform-specific implementation required.
        
        Args:
            username: The username to fetch profile for
            cached_data: Cached data (if available)
            force_refresh: Whether to force refresh
            **kwargs: Platform-specific arguments
            
        Returns:
            NormalizedProfile or None
        """
        pass

    @abstractmethod
    def _fetch_posts(
        self,
        username: str,
        profile: NormalizedProfile,
        needed_count: int,
        processed_ids: set,
        cache: "CacheManager" = None,
        **kwargs,
    ) -> List[NormalizedPost]:
        """
        Fetch posts for the user.
        
        Platform-specific implementation required.
        
        Args:
            username: The username to fetch posts for
            profile: The user's profile object
            needed_count: Number of new posts needed
            processed_ids: Set of already-processed post IDs
            cache: CacheManager instance (for media downloads, etc.)
            **kwargs: Platform-specific arguments
            
        Returns:
            List of NormalizedPost objects
        """
        pass

    def _handle_api_error(self, error: Exception, username: str) -> None:
        """
        Standardize error handling across platforms.
        
        Converts platform-specific errors to our custom exceptions.
        
        Args:
            error: The caught exception
            username: Username being fetched (for error messages)
            
        Raises:
            RateLimitExceededError, UserNotFoundError, AccessForbiddenError,
            or re-raises the original exception
        """
        error_str = str(error).lower()
        
        # Check for rate limits
        if any(
            phrase in error_str
            for phrase in ["rate limit", "too many requests", "429"]
        ):
            raise RateLimitExceededError(
                f"{self.platform_name} API rate limit exceeded", original_exception=error
            )
        
        # Check for not found
        if any(
            phrase in error_str
            for phrase in ["not found", "404", "does not exist", "user not found"]
        ):
            raise UserNotFoundError(
                f"{self.platform_name} user '{username}' not found"
            ) from error
        
        # Check for access forbidden
        if any(
            phrase in error_str
            for phrase in ["forbidden", "403", "private", "suspended"]
        ):
            raise AccessForbiddenError(
                f"Access to {self.platform_name} user '{username}' is forbidden"
            ) from error
        
        # Re-raise if not a known error type
        raise


class RateLimitHandler:
    """
    Centralized rate limit detection and handling.
    
    Provides utilities to detect rate limits from various sources
    (HTTP headers, exception types, response bodies) and convert them
    to our unified RateLimitExceededError.
    """

    @staticmethod
    def check_response_headers(headers: Dict[str, str], platform: str) -> None:
        """
        Check HTTP response headers for rate limit indicators.
        
        Args:
            headers: HTTP response headers
            platform: Platform name for error messages
            
        Raises:
            RateLimitExceededError if rate limit is detected
        """
        # Check X-RateLimit-Remaining (common pattern)
        if "x-ratelimit-remaining" in headers:
            remaining = headers.get("x-ratelimit-remaining", "1")
            if remaining.isdigit() and int(remaining) == 0:
                reset_time = headers.get("x-ratelimit-reset", "unknown")
                if reset_time.isdigit():
                    reset_dt = datetime.fromtimestamp(
                        int(reset_time), tz=timezone.utc
                    )
                    raise RateLimitExceededError(
                        f"{platform} API rate limit exceeded. Resets at {reset_dt.isoformat()}"
                    )
                raise RateLimitExceededError(f"{platform} API rate limit exceeded")

        # Check Retry-After header
        if "retry-after" in headers:
            retry_after = headers["retry-after"]
            raise RateLimitExceededError(
                f"{platform} API rate limit exceeded. Retry after {retry_after} seconds"
            )