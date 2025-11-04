import tweepy
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock

import pytest

from socialosintagent.platforms import twitter as twitter_fetcher
from socialosintagent.exceptions import UserNotFoundError


@pytest.fixture
def mock_tweepy_client(mocker):
    """Provides a mocked tweepy.Client."""
    client = mocker.MagicMock()

    # Mock user object
    mock_user = MagicMock()
    mock_user.id = 12345
    mock_user.name = "Test User"
    mock_user.username = "testuser"
    mock_user.created_at = datetime(2022, 1, 1, tzinfo=timezone.utc)
    mock_user.public_metrics = {"followers": 100}
    
    # Mock tweet object
    mock_tweet = mocker.MagicMock(spec=tweepy.Tweet)
    mock_tweet.id = 54321
    mock_tweet.text = "Hello world!"
    mock_tweet.created_at = datetime.now(timezone.utc) - timedelta(hours=1)
    mock_tweet.public_metrics = {}
    mock_tweet.attachments = None
    mock_tweet.entities = {}
    mock_tweet.referenced_tweets = None
    mock_tweet.in_reply_to_user_id = None
    
    # Configure mock responses
    client.get_user.return_value = MagicMock(data=mock_user)
    client.get_users_tweets.return_value = MagicMock(
        data=[mock_tweet], includes={}, meta={"next_token": None}
    )
    return client

@pytest.fixture
def mock_dependencies(mocker):
    """Provides mocked CacheManager and LLMAnalyzer."""
    mock_cache = mocker.MagicMock()
    mock_llm = mocker.MagicMock()
    return mock_cache, mock_llm


def test_fetch_data_cache_miss(mock_tweepy_client, mock_dependencies):
    """Test fetch_data when no cache exists (cache miss)."""
    # Arrange
    mock_cache, mock_llm = mock_dependencies
    mock_cache.load.return_value = None  # Simulate cache miss
    mock_cache.is_offline = False  # <<< --- ADD THIS LINE
    username = "testuser"

    # Act
    result = twitter_fetcher.fetch_data(
        client=mock_tweepy_client,
        username=username,
        cache=mock_cache,
        llm=mock_llm,
        force_refresh=False,
        fetch_limit=50,
    )

    # Assert
    mock_cache.load.assert_called_once_with("twitter", username)
    mock_tweepy_client.get_user.assert_called_once()
    mock_tweepy_client.get_users_tweets.assert_called_once()
    mock_cache.save.assert_called_once()
    assert result is not None
    assert len(result["tweets"]) == 1
    assert result["tweets"][0]["text"] == "Hello world!"

def test_fetch_data_cache_hit_fresh(mock_tweepy_client, mock_dependencies):
    """Test fetch_data when a fresh cache exists."""
    # Arrange
    mock_cache, mock_llm = mock_dependencies
    fresh_data = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "user_info": {"id": "123"},
        "tweets": [{"id": "t1"}] * 50, # Sufficient items
    }
    mock_cache.load.return_value = fresh_data
    # No need to set is_offline here, as the function should return before checking it
    username = "testuser"

    # Act
    result = twitter_fetcher.fetch_data(
        client=mock_tweepy_client,
        username=username,
        cache=mock_cache,
        llm=mock_llm,
        force_refresh=False,
        fetch_limit=50,
    )

    # Assert
    mock_cache.load.assert_called_once_with("twitter", username)
    # API calls should NOT be made
    mock_tweepy_client.get_user.assert_not_called()
    mock_tweepy_client.get_users_tweets.assert_not_called()
    mock_cache.save.assert_not_called()
    assert result == fresh_data

def test_user_not_found(mock_tweepy_client, mock_dependencies):
    """Test that UserNotFoundError is raised for a non-existent user."""
    # Arrange
    mock_cache, mock_llm = mock_dependencies
    mock_cache.load.return_value = None
    mock_cache.is_offline = False  # <<< --- ADD THIS LINE
    mock_tweepy_client.get_user.return_value = MagicMock(data=None) # Simulate user not found
    username = "nonexistent"

    # Act & Assert
    with pytest.raises(UserNotFoundError):
        twitter_fetcher.fetch_data(
            client=mock_tweepy_client,
            username=username,
            cache=mock_cache,
            llm=mock_llm
        )