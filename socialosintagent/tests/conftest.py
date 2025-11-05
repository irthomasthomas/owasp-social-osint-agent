import os
import sys
from pathlib import Path

import pytest

# Add the project root to the Python path.
# This ensures that `socialosintagent` can be imported by pytest.
project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(project_root))

@pytest.fixture(scope="session", autouse=True)
def change_test_dir(request):
    """
    This fixture automatically changes the working directory to the project root
    before any tests run. This is crucial so that files like the 'prompts/'
    directory can be found consistently.
    """
    os.chdir(project_root)