import tweepy
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock

import pytest

from socialosintagent.platforms import twitter as twitter_fetcher
from socialosintagent.exceptions import UserNotFoundError


@pytest.fixture
def mock_tweepy_client(mocker):
    client = mocker.MagicMock()
    mock_user = MagicMock(
        id=12345, name="Test User", username="testuser",
        created_at=datetime(2022, 1, 1, tzinfo=timezone.utc),
        public_metrics={"followers": 100}
    )
    mock_tweet = MagicMock(spec=tweepy.Tweet)
    mock_tweet.id = 54321
    mock_tweet.text = "Hello world!"
    mock_tweet.created_at = datetime.now(timezone.utc) - timedelta(hours=1)
    mock_tweet.public_metrics = {}
    mock_tweet.attachments = None
    mock_tweet.entities = {}
    mock_tweet.referenced_tweets = None
    mock_tweet.in_reply_to_user_id = None
    
    client.get_user.return_value = MagicMock(data=mock_user)
    client.get_users_tweets.return_value = MagicMock(
        data=[mock_tweet], includes={}, meta={"next_token": None}
    )
    return client

@pytest.fixture
def mock_cache(mocker):
    return mocker.MagicMock()


def test_fetch_data_cache_miss(mock_tweepy_client, mock_cache):
    """Test fetch_data when no cache exists (cache miss)."""
    # Arrange
    mock_cache.load.return_value = None
    mock_cache.is_offline = False
    username = "testuser"

    # Act
    # REFACTOR: The llm argument is removed from the call
    result = twitter_fetcher.fetch_data(
        client=mock_tweepy_client,
        username=username,
        cache=mock_cache,
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

def test_fetch_data_cache_hit_fresh(mock_tweepy_client, mock_cache):
    """Test fetch_data when a fresh cache exists."""
    # Arrange
    fresh_data = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "user_info": {"id": "123"},
        "tweets": [{"id": "t1"}] * 50,
    }
    mock_cache.load.return_value = fresh_data
    username = "testuser"

    # Act
    # REFACTOR: The llm argument is removed
    result = twitter_fetcher.fetch_data(
        client=mock_tweepy_client,
        username=username,
        cache=mock_cache,
        force_refresh=False,
        fetch_limit=50,
    )

    # Assert
    mock_cache.load.assert_called_once_with("twitter", username)
    mock_tweepy_client.get_user.assert_not_called()
    mock_tweepy_client.get_users_tweets.assert_not_called()
    assert result == fresh_data

def test_user_not_found(mock_tweepy_client, mock_cache):
    """Test that UserNotFoundError is raised for a non-existent user."""
    # Arrange
    mock_cache.load.return_value = None
    mock_cache.is_offline = False
    mock_tweepy_client.get_user.return_value = MagicMock(data=None)
    username = "nonexistent"

    # Act & Assert
    with pytest.raises(UserNotFoundError):
        # REFACTOR: The llm argument is removed
        twitter_fetcher.fetch_data(
            client=mock_tweepy_client,
            username=username,
            cache=mock_cache
        )