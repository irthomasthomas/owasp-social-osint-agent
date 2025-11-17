import argparse
from unittest.mock import MagicMock, create_autospec

import pytest

from socialosintagent.analyzer import SocialOSINTAgent
from socialosintagent.cli_handler import CliHandler

@pytest.fixture
def mock_agent():
    """Provides a mocked SocialOSINTAgent with its dependencies mocked."""
    # create_autospec ensures the mock has the same methods/attributes as the real class
    return create_autospec(SocialOSINTAgent, instance=True)

@pytest.fixture
def cli(mock_agent):
    """Provides a CliHandler instance with a mocked agent for testing."""
    args = argparse.Namespace(offline=False, no_auto_save=True, format="markdown")
    handler = CliHandler(mock_agent, args)
    # Suppress console output during tests
    handler.console = MagicMock()
    return handler

def test_handle_loadmore_command_specific_target(cli):
    """
    Tests the 'loadmore <platform/user> <count>' command format.
    This test is now correctly testing the CliHandler.
    """
    # Arrange
    parts = ["loadmore", "twitter/testuser", "100"]
    platforms = {"twitter": ["testuser"]}
    fetch_options = {"default_count": 50, "targets": {}}
    last_query = "find connections"
    
    # Act
    should_run, query, force_refresh = cli._handle_loadmore_command(
        parts, platforms, fetch_options, last_query
    )

    # Assert
    assert should_run is True
    assert query == "find connections"
    assert force_refresh is True
    assert fetch_options["targets"]["twitter:testuser"]["count"] == 150 # 50 (default) + 100

def test_handle_loadmore_command_invalid_count(cli):
    """
    Tests that the loadmore command handles non-numeric counts gracefully.
    This test is now correctly testing the CliHandler.
    """
    # Arrange
    parts = ["loadmore", "twitter/testuser", "invalid"]
    platforms = {"twitter": ["testuser"]}
    fetch_options = {}
    
    # Act
    should_run, query, force_refresh = cli._handle_loadmore_command(
        parts, platforms, fetch_options, ""
    )
    
    # Assert
    assert should_run is False
    # Check that the console was used to print an error message
    cli.console.print.assert_called_with("[red]Invalid count: 'invalid'.[/red]")