import argparse
from unittest.mock import MagicMock, create_autospec, patch

import pytest

from socialosintagent.analyzer import SocialOSINTAgent
from socialosintagent.cache import CacheManager
from socialosintagent.client_manager import ClientManager
from socialosintagent.llm import LLMAnalyzer

@pytest.fixture
def mock_dependencies(mocker):
    """Provides mocked versions of the agent's dependencies."""
    mock_cache = create_autospec(CacheManager, instance=True)
    # Mock the LLM to prevent actual file reads for prompts
    with patch('socialosintagent.llm._load_prompt', return_value="mock prompt"):
        mock_llm = create_autospec(LLMAnalyzer, instance=True)
    mock_client_manager = create_autospec(ClientManager, instance=True)
    return mock_cache, mock_llm, mock_client_manager

@pytest.fixture
def agent(mock_dependencies, monkeypatch):
    """Provides a SocialOSINTAgent instance with mocked dependencies for testing."""
    # Use monkeypatch to set fake environment variables for the test
    monkeypatch.setenv("LLM_API_KEY", "test_key")
    monkeypatch.setenv("LLM_API_BASE_URL", "https://test.api/v1")
    monkeypatch.setenv("IMAGE_ANALYSIS_MODEL", "test_vision_model")
    monkeypatch.setenv("ANALYSIS_MODEL", "test_text_model")

    args = argparse.Namespace(offline=False, no_auto_save=True, format="markdown")
    mock_cache, mock_llm, mock_client_manager = mock_dependencies
    
    # This call will now succeed because the environment variables are set
    agent_instance = SocialOSINTAgent(args, mock_cache, mock_llm, mock_client_manager)
    return agent_instance

def test_analyze_method_orchestration(agent, mocker):
    """
    Tests the main analyze() method's two-phase process: fetch then vision analysis.
    """
    # Arrange
    # The fetcher now returns data with a media path but no analysis yet.
    mock_platform_data = {
        "user_info": {"id": "123"},
        "tweets": [{
            "media": [{
                "local_path": "/fake/path/image.jpg",
                "url": "http://example.com/image.jpg",
                "analysis": None
            }]
        }],
        "media_analysis": [], # Starts empty
        "media_paths": ["/fake/path/image.jpg"]
    }
    mock_fetcher = mocker.MagicMock(return_value=mock_platform_data)
    
    mock_twitter_client = MagicMock()
    agent.client_manager.get_platform_client.return_value = mock_twitter_client

    # Mock the Path object to simulate the image file existing
    mocker.patch('pathlib.Path.exists', return_value=True)
    mocker.patch('pathlib.Path.suffix', '.jpg')

    # Patch the global FETCHERS dictionary
    mocker.patch('socialosintagent.analyzer.FETCHERS', {"twitter": mock_fetcher})
    
    # Mock the two distinct LLM calls
    agent.llm.analyze_image.return_value = "This is an image analysis."
    agent.llm.run_analysis.return_value = "This is the final report."

    platforms_to_query = {"twitter": ["testuser"]}
    query = "analyze this user"
    
    # Act
    result = agent.analyze(platforms_to_query, query, force_refresh=False)

    # Assert
    # Client manager was called
    agent.client_manager.get_platform_client.assert_called_once_with("twitter")

    # Fetcher was called correctly (without LLM object)
    mock_fetcher.assert_called_once_with(
        username='testuser', 
        cache=agent.cache, 
        force_refresh=False, 
        fetch_limit=50,
        client=mock_twitter_client
    )

    # Vision analysis was called in the second phase
    agent.llm.analyze_image.assert_called_once()

    # Final text analysis was called with the now-populated data
    agent.llm.run_analysis.assert_called_once()
    
    # The final return value is a structured dictionary
    assert isinstance(result, dict)
    assert "metadata" in result
    assert "report" in result
    assert result["report"].endswith("This is the final report.")
    assert result["error"] is False