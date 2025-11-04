import argparse
from unittest.mock import MagicMock

import pytest
from rich.console import Console

from socialosintagent.analyzer import SocialOSINTAgent
from socialosintagent.cache import CacheManager
from socialosintagent.llm import LLMAnalyzer

# A fixture creates a reusable object for tests.
@pytest.fixture
def mock_dependencies(mocker):
    """Provides mocked versions of the agent's dependencies."""
    mock_cache = mocker.MagicMock(spec=CacheManager)
    mock_llm = mocker.MagicMock(spec=LLMAnalyzer)
    return mock_cache, mock_llm

@pytest.fixture
def agent(mock_dependencies):
    """Provides a SocialOSINTAgent instance with mocked dependencies for testing."""
    # Create a minimal 'args' object needed for initialization
    args = argparse.Namespace(offline=False, no_auto_save=True, format="markdown")
    mock_cache, mock_llm = mock_dependencies
    
    # Suppress console output during tests
    agent_instance = SocialOSINTAgent(args, mock_cache, mock_llm)
    agent_instance.console = MagicMock(spec=Console)
    return agent_instance


def test_handle_loadmore_command_specific_target(agent):
    """
    Tests the 'loadmore <platform/user> <count>' command format.
    """
    # Arrange
    parts = ["loadmore", "twitter/testuser", "100"]
    platforms = {"twitter": ["testuser"]}
    fetch_options = {"default_count": 50, "targets": {}}
    last_query = "find connections"
    
    # Act
    should_run, query, force_refresh = agent._handle_loadmore_command(
        parts, platforms, fetch_options, last_query
    )

    # Assert
    assert should_run is True
    assert query == "find connections"
    assert force_refresh is True
    assert fetch_options["targets"]["twitter:testuser"]["count"] == 150 # 50 (default) + 100

def test_handle_loadmore_command_invalid_count(agent):
    """
    Tests that the loadmore command handles non-numeric counts gracefully.
    """
    # Arrange
    parts = ["loadmore", "twitter/testuser", "invalid"]
    platforms = {"twitter": ["testuser"]}
    fetch_options = {}
    
    # Act
    should_run, query, force_refresh = agent._handle_loadmore_command(
        parts, platforms, fetch_options, ""
    )
    
    # Assert
    assert should_run is False
    # Check that the console was used to print an error message
    agent.console.print.assert_called_with("[red]Invalid count: 'invalid'. Must be a number.[/red]")