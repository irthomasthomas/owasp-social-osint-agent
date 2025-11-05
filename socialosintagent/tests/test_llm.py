from unittest.mock import patch
import pytest

from socialosintagent.llm import LLMAnalyzer

@pytest.fixture
def llm_analyzer():
    """Provides an LLMAnalyzer instance for testing."""
    # **THIS IS THE FIX**: We patch the helper function `_load_prompt` that
    # reads prompts from the disk. During the test, any call to it will
    # immediately return "mock prompt template" without touching the filesystem.
    with patch('socialosintagent.llm._load_prompt', return_value="mock prompt template"):
        analyzer = LLMAnalyzer(is_offline=True)
    return analyzer

@pytest.fixture
def mock_twitter_data():
    """Provides a sample twitter data dictionary."""
    return {
        "user_info": {
            "username": "testuser",
            "created_at": "2022-01-01T12:00:00.000Z",
            "public_metrics": {"followers_count": 100, "following_count": 50, "tweet_count": 20}
        },
        "tweets": [
            {"created_at": "2023-10-27T10:00:00.000Z", "text": "This is a test tweet.", "metrics": {"likes": 5}}
        ]
    }

@pytest.fixture
def mock_multi_platform_data_for_links():
    """Provides sample data with URLs for testing link analysis."""
    return {
        "twitter": [{
            "username_key": "user1", "data": {"tweets": [
                {"entities_raw": {"urls": [{"expanded_url": "https://github.com/project"}]}},
                {"entities_raw": {"urls": [{"expanded_url": "https://example.com/article1"}]}},
            ]}
        }],
        "reddit": [{
            "username_key": "user2", "data": {
                "submissions": [{"link_url": "https://github.com/another/repo"}],
                "comments": [{"text": "Check this out: http://example.com/article2"}]
            }
        }]
    }

def test_format_text_data_twitter(llm_analyzer, mock_twitter_data):
    """Tests that Twitter data is formatted into a readable string correctly."""
    # Act
    formatted_string = llm_analyzer._format_text_data("twitter", "testuser", mock_twitter_data)
    
    # Assert
    assert "Twitter Data Summary for: @testuser" in formatted_string
    assert "Account Created: 2022-01-01" in formatted_string
    assert "Followers=100" in formatted_string
    assert "This is a test tweet." in formatted_string

def test_analyze_shared_links(llm_analyzer, mock_multi_platform_data_for_links):
    """Tests the extraction and counting of shared domains, excluding platform domains."""
    # Act
    link_summary = llm_analyzer._analyze_shared_links(mock_multi_platform_data_for_links)

    # Assert
    assert "## Top Shared Domains" in link_summary
    assert "**github.com:** 2 link(s)" in link_summary
    assert "**example.com:** 2 link(s)" in link_summary
    # This was failing before because the regex was wrong in utils.py
    # Now that utils.py is fixed, this assertion should also be correct.
    assert "news.ycombinator.com" not in link_summary