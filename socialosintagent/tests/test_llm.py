from unittest.mock import patch
import pytest
from datetime import datetime, timezone

from socialosintagent.llm import LLMAnalyzer
from socialosintagent.utils import UserData

@pytest.fixture
def llm_analyzer():
    with patch('socialosintagent.llm._load_prompt', return_value="mock prompt template"):
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
            "metrics": {"followers": 100, "following": 50, "post_count": 20}
        },
        "posts": [
            {
                "created_at": datetime(2023, 10, 27, 10, 0, 0, tzinfo=timezone.utc),
                "text": "This is a test tweet.",
                "type": "post"
            }
        ]
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
            ]
        },
        {
            "profile": {"platform": "reddit", "username": "user2"},
            "posts": [
                {"external_links": ["https://github.com/another/repo"]},
                {"external_links": ["http://example.com/article2"]},
            ]
        }
    ]

def test_format_user_data_summary(llm_analyzer, mock_twitter_user_data):
    """
    Tests that a UserData object is formatted into a readable string correctly.
    Note: Testing the NEW function name `_format_user_data_summary`.
    """
    # Act
    formatted_string = llm_analyzer._format_user_data_summary(mock_twitter_user_data)
    
    # Assert
    assert "Twitter Data Summary for: testuser" in formatted_string
    assert "Account Created: 2022-01-01" in formatted_string
    assert "Followers=100" in formatted_string
    assert "This is a test tweet." in formatted_string

def test_analyze_shared_links(llm_analyzer, mock_multi_platform_user_data):
    """Tests the extraction and counting of shared domains from UserData objects."""
    # Act
    link_summary = llm_analyzer._analyze_shared_links(mock_multi_platform_user_data)

    # Assert
    assert "## Top Shared Domains" in link_summary
    assert "**github.com:** 2 link(s)" in link_summary
    assert "**example.com:** 2 link(s)" in link_summary
    assert "news.ycombinator.com" not in link_summary