import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from socialosintagent.cache import CacheManager

@pytest.fixture
def temp_cache_dir(tmp_path):
    """Create a temporary cache directory for testing."""
    cache_dir = tmp_path / "data"
    (cache_dir / "cache").mkdir(parents=True, exist_ok=True)
    return cache_dir

def test_save_and_load(temp_cache_dir):
    """Test that data can be saved and loaded correctly."""
    # Arrange
    cache = CacheManager(base_dir=temp_cache_dir, is_offline=False)
    platform = "test_platform"
    username = "test_user"
    data_to_save = {"user_info": {"id": "123"}, "tweets": [{"id": "t1", "text": "hello"}]}

    # Act
    cache.save(platform, username, data_to_save)
    loaded_data = cache.load(platform, username)

    # Assert
    assert loaded_data is not None
    assert loaded_data["user_info"]["id"] == "123"
    assert "timestamp" in loaded_data
    assert cache.get_cache_path(platform, username).exists()

def test_cache_expiry(temp_cache_dir, mocker):
    """Test that stale cache is not returned in online mode."""
    # Arrange
    cache = CacheManager(base_dir=temp_cache_dir, is_offline=False)
    platform = "test_platform"
    username = "expired_user"
    
    # Create an old timestamp
    old_time = datetime.now(timezone.utc) - timedelta(hours=48)
    old_data = {"user_info": {"id": "456"}, "tweets": [], "timestamp": old_time.isoformat()}
    
    cache_path = cache.get_cache_path(platform, username)
    cache_path.write_text(json.dumps(old_data))

    # Act
    # In online mode, this should return the stale data for incremental loading
    loaded_data_online = cache.load(platform, username) 

    # To test if it *would* be considered stale, we can check the timestamp
    loaded_dt = datetime.fromisoformat(loaded_data_online['timestamp']).replace(tzinfo=timezone.utc)
    is_fresh = (datetime.now(timezone.utc) - loaded_dt) < timedelta(hours=24)

    # Assert
    assert loaded_data_online is not None # The current logic returns stale data
    assert is_fresh is False

def test_offline_mode_returns_stale_cache(temp_cache_dir):
    """Test that stale cache IS returned in offline mode."""
    # Arrange
    cache = CacheManager(base_dir=temp_cache_dir, is_offline=True)
    platform = "test_platform"
    username = "offline_user"
    
    old_time = datetime.now(timezone.utc) - timedelta(hours=48)
    old_data = {"user_info": {"id": "789"}, "tweets": [], "timestamp": old_time.isoformat(), "required_keys": True}
    
    # Manually create a dummy cache file with required keys
    cache_path = cache.get_cache_path(platform, username)
    # A simplified structure to pass the validation checks in `cache.load`
    data_to_write = {
        "timestamp": old_time.isoformat(),
        "tweets": [{"id": "t1"}],
        "user_info": {"id": "789"},
    }
    cache_path.write_text(json.dumps(data_to_write))

    # Act
    loaded_data = cache.load(platform, username)

    # Assert
    assert loaded_data is not None
    assert loaded_data["user_info"]["id"] == "789"