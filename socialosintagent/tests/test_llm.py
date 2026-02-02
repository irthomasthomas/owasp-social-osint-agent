"""
Tests for socialosintagent/llm.py

Covers:
- _format_user_data_summary basic Twitter data, _analyze_shared_links domain
  counting.
- _format_user_data_summary with GitHub posts that include repo context (the
  "Repo: ..." info tag), posts with media items (the "Media: N" info tag), empty/missing
  profile returns empty string, _analyze_shared_links excludes platform-internal domains,
  _analyze_shared_links returns empty string when all links are platform-internal.
"""

from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from socialosintagent.llm import LLMAnalyzer
from socialosintagent.utils import UserData

# Fixtures

@pytest.fixture
def llm_analyzer():
    with patch("socialosintagent.llm._load_prompt", return_value="mock prompt template"):
        analyzer = LLMAnalyzer(is_offline=True)
    return analyzer


@pytest.fixture
def mock_twitter_user_data() -> UserData:
    """Provides a sample UserData object for a Twitter user."""
    return {
        "profile": {
            "platform": "twitter",
            "username": "testuser",
            "created_at": datetime(2022, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
            "metrics": {"followers": 100, "following": 50, "post_count": 20},
        },
        "posts": [
            {
                "created_at": datetime(2023, 10, 27, 10, 0, 0, tzinfo=timezone.utc),
                "text": "This is a test tweet.",
                "type": "post",
            }
        ],
    }


@pytest.fixture
def mock_multi_platform_user_data() -> list[UserData]:
    """Provides sample data with URLs for testing link analysis."""
    return [
        {
            "profile": {"platform": "twitter", "username": "user1"},
            "posts": [
                {"external_links": ["https://github.com/project"]},
                {"external_links": ["https://example.com/article1"]},
            ],
        },
        {
            "profile": {"platform": "reddit", "username": "user2"},
            "posts": [
                {"external_links": ["https://github.com/another/repo"]},
                {"external_links": ["http://example.com/article2"]},
            ],
        },
    ]

# TESTS

def test_format_user_data_summary(llm_analyzer, mock_twitter_user_data):
    """
    Tests that a UserData object is formatted into a readable string correctly.
    Note: Testing the NEW function name `_format_user_data_summary`.
    """
    formatted_string = llm_analyzer._format_user_data_summary(mock_twitter_user_data)

    assert "Twitter Data Summary for: testuser" in formatted_string
    assert "Account Created: 2022-01-01" in formatted_string
    assert "Followers=100" in formatted_string
    assert "This is a test tweet." in formatted_string


def test_analyze_shared_links(llm_analyzer, mock_multi_platform_user_data):
    """Tests the extraction and counting of shared domains from UserData objects."""
    link_summary = llm_analyzer._analyze_shared_links(mock_multi_platform_user_data)

    assert "## Top Shared Domains" in link_summary
    assert "**github.com:** 2 link(s)" in link_summary
    assert "**example.com:** 2 link(s)" in link_summary
    assert "news.ycombinator.com" not in link_summary


# _format_user_data_summary edge cases

class TestFormatUserDataSummary:
    def test_github_posts_show_repo_context(self, llm_analyzer):
        """Posts with a 'context.repo' field render a 'Repo: ...' info tag."""
        data: UserData = {
            "profile": {
                "platform": "github",
                "username": "developer",
                "metrics": {"followers": 5, "following": 3, "public_repos": 10},
            },
            "posts": [
                {
                    "created_at": datetime(2024, 3, 10, 8, 0, 0, tzinfo=timezone.utc),
                    "text": "Pushed 1 commit(s) to branch 'main' in acme/backend:\n  - abc1234: fix: null check",
                    "type": "PushEvent",
                    "context": {"repo": "acme/backend"},
                }
            ],
        }
        result = llm_analyzer._format_user_data_summary(data)

        assert "Repo: acme/backend" in result
        assert "fix: null check" in result
        assert "PushEvent" in result

    def test_posts_with_media_show_media_count(self, llm_analyzer):
        """Posts that include media items render a 'Media: N' info tag."""
        data: UserData = {
            "profile": {
                "platform": "twitter",
                "username": "photophile",
                "metrics": {"followers": 200},
            },
            "posts": [
                {
                    "created_at": datetime(2024, 5, 1, 12, 0, 0, tzinfo=timezone.utc),
                    "text": "Check out these pics!",
                    "type": "post",
                    "media": [
                        {"url": "http://example.com/a.jpg", "local_path": "/tmp/a.jpg"},
                        {"url": "http://example.com/b.jpg", "local_path": "/tmp/b.jpg"},
                        {"url": "http://example.com/c.jpg", "local_path": "/tmp/c.jpg"},
                    ],
                }
            ],
        }
        result = llm_analyzer._format_user_data_summary(data)

        assert "Media: 3" in result
        assert "Check out these pics!" in result

    def test_empty_profile_returns_empty_string(self, llm_analyzer):
        """A UserData with no 'profile' key returns an empty string."""
        data: UserData = {"posts": [{"text": "orphan post"}]}
        result = llm_analyzer._format_user_data_summary(data)
        assert result == ""

    def test_none_profile_returns_empty_string(self, llm_analyzer):
        """A UserData where profile is None returns an empty string."""
        data: UserData = {"profile": None, "posts": []}
        result = llm_analyzer._format_user_data_summary(data)
        assert result == ""

    def test_post_text_is_truncated_at_750_chars(self, llm_analyzer):
        """Very long post text is truncated to 750 characters in the summary."""
        long_text = "x" * 2000
        data: UserData = {
            "profile": {"platform": "reddit", "username": "talker"},
            "posts": [
                {
                    "created_at": datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
                    "text": long_text,
                    "type": "comment",
                }
            ],
        }
        result = llm_analyzer._format_user_data_summary(data)

        # The full 2000-char string should NOT appear
        assert long_text not in result
        # But the first 750 chars should
        assert long_text[:750] in result

    def test_only_first_25_posts_are_shown(self, llm_analyzer):
        """Only up to 25 posts appear in the summary regardless of how many exist."""
        data: UserData = {
            "profile": {"platform": "hackernews", "username": "prolific"},
            "posts": [
                {
                    "created_at": datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
                    "text": f"post number {i}",
                    "type": "story",
                }
                for i in range(40)
            ],
        }
        result = llm_analyzer._format_user_data_summary(data)

        # Item 25 should be present, item 26 should not
        assert "post number 24" in result  # 0-indexed → item 25
        assert "post number 25" not in result  # item 26 would be here

# _analyze_shared_links edge cases

class TestAnalyzeSharedLinks:
    def test_platform_domains_are_excluded(self, llm_analyzer):
        """Known social-platform domains (twitter.com, reddit.com, etc.) are filtered out."""
        data = [
            {
                "profile": {"platform": "twitter", "username": "linker"},
                "posts": [
                    {
                        "external_links": [
                            "https://twitter.com/someone",
                            "https://reddit.com/r/test",
                            "https://news.ycombinator.com/item?id=123",
                            "https://bsky.app/profile/user",
                            "https://techcrunch.com/2024/article",
                        ]
                    }
                ],
            }
        ]
        result = llm_analyzer._analyze_shared_links(data)

        # Only techcrunch should appear
        assert "techcrunch.com" in result
        assert "twitter.com" not in result
        assert "reddit.com" not in result
        assert "news.ycombinator.com" not in result
        assert "bsky.app" not in result

    def test_all_platform_links_returns_empty_string(self, llm_analyzer):
        """When every link is a platform-internal one, the result is an empty string."""
        data = [
            {
                "profile": {"platform": "twitter", "username": "internal"},
                "posts": [
                    {
                        "external_links": [
                            "https://twitter.com/a",
                            "https://x.com/b",
                            "https://t.co/abc",
                            "https://reddit.com/r/x",
                            "https://redd.it/xyz",
                        ]
                    }
                ],
            }
        ]
        result = llm_analyzer._analyze_shared_links(data)
        assert result == ""

    def test_no_posts_returns_empty_string(self, llm_analyzer):
        """UserData with no posts (or no external_links) yields empty string."""
        data = [{"profile": {"platform": "twitter", "username": "quiet"}, "posts": []}]
        result = llm_analyzer._analyze_shared_links(data)
        assert result == ""

    def test_www_prefix_is_normalised(self, llm_analyzer):
        """www. prefix is stripped so www.example.com and example.com count together."""
        data = [
            {
                "profile": {"platform": "twitter", "username": "www_user"},
                "posts": [
                    {
                        "external_links": [
                            "https://www.example.com/page1",
                            "https://example.com/page2",
                        ]
                    }
                ],
            }
        ]
        result = llm_analyzer._analyze_shared_links(data)
        assert "**example.com:** 2 link(s)" in result