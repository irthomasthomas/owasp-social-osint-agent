import pytest
from unittest.mock import MagicMock
from datetime import datetime, timezone

from socialosintagent.platforms import reddit as reddit_fetcher

@pytest.fixture
def mock_praw_client(mocker):
    """Provides a mocked praw.Reddit client."""
    client = mocker.MagicMock()
    mock_redditor = MagicMock()
    mock_redditor.id = 'u123'
    mock_redditor.name = 'reddituser'
    mock_redditor.created_utc = datetime(2020, 1, 1).timestamp()
    mock_redditor.link_karma = 100
    mock_redditor.comment_karma = 1000

    mock_submission = MagicMock()
    mock_submission.id = 's1'
    mock_submission.title = 'My first post'
    mock_submission.selftext = 'Hello Reddit!'
    mock_submission.created_utc = datetime.now(timezone.utc).timestamp()
    mock_submission.subreddit.display_name = 'testing'
    mock_submission.url = 'http://example.com/image.jpg'
    mock_submission.is_self = False
    mock_submission.is_gallery = False # Important for this test case
    mock_submission.media_metadata = None

    client.redditor.return_value = mock_redditor
    mock_redditor.submissions.new.return_value = [mock_submission]
    mock_redditor.comments.new.return_value = []
    
    return client

@pytest.fixture
def mock_cache(mocker):
    """Provides mocked CacheManager."""
    mock_cache = mocker.MagicMock()
    mock_cache.is_offline = False
    mock_cache.load.return_value = None # Cache miss
    return mock_cache

def test_reddit_fetch_data_cache_miss(mock_praw_client, mock_cache, mocker):
    """Test fetching reddit data on a cache miss."""
    # Arrange
    mocker.patch('socialosintagent.platforms.reddit.download_media', return_value=None)

    # Act
    # REFACTOR: The llm argument is removed from the call
    result = reddit_fetcher.fetch_data(
        client=mock_praw_client,
        username='reddituser',
        cache=mock_cache
    )

    # Assert
    mock_praw_client.redditor.assert_called_with('reddituser')
    assert result is not None
    assert len(result['submissions']) == 1
    assert result['submissions'][0]['title'] == 'My first post'
    mock_cache.save.assert_called_once()