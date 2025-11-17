import argparse
from unittest.mock import MagicMock, create_autospec, patch

import pytest

from socialosintagent.analyzer import SocialOSINTAgent
from socialosintagent.cache import CacheManager
from socialosintagent.client_manager import ClientManager
from socialosintagent.llm import LLMAnalyzer
from socialosintagent.utils import UserData

@pytest.fixture
def mock_dependencies(mocker):
    """Provides mocked versions of the agent's dependencies."""
    mock_cache = create_autospec(CacheManager, instance=True)
    with patch('socialosintagent.llm._load_prompt', return_value="mock prompt"):
        mock_llm = create_autospec(LLMAnalyzer, instance=True)
    mock_client_manager = create_autospec(ClientManager, instance=True)
    return mock_cache, mock_llm, mock_client_manager

@pytest.fixture
def agent(mock_dependencies, monkeypatch):
    """Provides a SocialOSINTAgent instance with mocked dependencies for testing."""
    monkeypatch.setenv("LLM_API_KEY", "test_key")
    monkeypatch.setenv("LLM_API_BASE_URL", "https://test.api/v1")
    monkeypatch.setenv("IMAGE_ANALYSIS_MODEL", "test_vision_model")
    monkeypatch.setenv("ANALYSIS_MODEL", "test_text_model")

    args = argparse.Namespace(offline=False, no_auto_save=True, format="markdown")
    mock_cache, mock_llm, mock_client_manager = mock_dependencies
    
    agent_instance = SocialOSINTAgent(args, mock_cache, mock_llm, mock_client_manager)
    return agent_instance

def test_analyze_method_orchestration(agent, mocker):
    """Tests the main analyze() method's orchestration with the UserData model."""
    # Arrange
    mock_user_data: UserData = {
        "profile": {"platform": "twitter", "username": "testuser", "id": "123"},
        "posts": [{
            "id": "t1", "media": [{"local_path": "/fake/path/image.jpg", "url": "http://example.com/image.jpg"}]
        }]
    }
    
    mock_fetcher = mocker.MagicMock(return_value=mock_user_data)
    mock_twitter_client = MagicMock()
    agent.client_manager.get_platform_client.return_value = mock_twitter_client

    # Mock Path methods used in vision analysis
    mocker.patch('pathlib.Path.exists', return_value=True)
    # Since suffix is a property, we need to configure it on the mock object itself
    path_mock = mocker.patch('socialosintagent.analyzer.Path')
    path_mock.return_value.suffix = '.jpg'
    
    mocker.patch('socialosintagent.analyzer.FETCHERS', {"twitter": mock_fetcher})
    
    agent.llm.analyze_image.return_value = "This is an image analysis."
    agent.llm.run_analysis.return_value = "This is the final report."

    platforms_to_query = {"twitter": ["testuser"]}
    query = "analyze this user"
    
    # Act
    result = agent.analyze(platforms_to_query, query, force_refresh=False)

    # Assert
    agent.client_manager.get_platform_client.assert_called_once_with("twitter")
    mock_fetcher.assert_called_once()
    
    agent.llm.analyze_image.assert_called_once()
    agent.llm.run_analysis.assert_called_once()
    
    assert isinstance(result, dict)
    assert result["report"].endswith("This is the final report.")
    assert not result["error"]