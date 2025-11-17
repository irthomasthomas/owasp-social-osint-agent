from datetime import datetime, timezone

from socialosintagent import utils

def test_sanitize_username_with_control_chars():
    """Test that Unicode control characters are stripped."""
    dirty_username = "user\u200bname" # Contains a zero-width space
    clean_username = "username"
    assert utils.sanitize_username(dirty_username) == clean_username

def test_sanitize_username_no_change():
    """Test that a clean username is not modified."""
    clean_username = "user-123"
    assert utils.sanitize_username(clean_username) == clean_username

def test_extract_and_resolve_urls():
    """Test URL extraction regex."""
    text = "Check out my site at https://example.com/page?q=1 and also www.anothersite.net."
    urls = utils.extract_and_resolve_urls(text)
    assert "https://example.com/page?q=1" in urls
    assert "www.anothersite.net" in urls
    assert len(urls) == 2

def test_get_sort_key_from_iso_string():
    """Test parsing a standard ISO 8601 string."""
    item = {"created_at": "2023-01-01T12:00:00+00:00"}
    expected_dt = datetime(2023, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    assert utils.get_sort_key(item, "created_at") == expected_dt

def test_get_sort_key_from_timestamp():
    """Test parsing a Unix timestamp."""
    ts = 1672574400 # This is 2023-01-01 12:00:00 UTC
    item = {"created_utc": ts}
    expected_dt = datetime(2023, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    assert utils.get_sort_key(item, "created_utc") == expected_dt