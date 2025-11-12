"""
MediaJelly Tests - Configuration and Fixtures
"""

import pytest
import sys
from pathlib import Path

# Add scripts directory to Python path
SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))


@pytest.fixture
def temp_media_dir(tmp_path):
    """Create a temporary media directory structure"""
    media_dir = tmp_path / "media"
    media_dir.mkdir()
    
    # Create subdirectories
    (media_dir / "anime").mkdir()
    (media_dir / "Peliculas").mkdir()
    (media_dir / "series").mkdir()
    
    return media_dir


@pytest.fixture
def sample_video_file(temp_media_dir):
    """Create a sample video file for testing"""
    video_file = temp_media_dir / "series" / "test_video.mkv"
    # Create empty file for testing
    video_file.touch()
    return video_file


@pytest.fixture
def mock_telegram_config(tmp_path):
    """Create a mock telegram configuration file"""
    config_file = tmp_path / "telegram.conf"
    config_file.write_text("""
TELEGRAM_BOT_TOKEN="test_token_123456"
TELEGRAM_CHAT_ID="123456789"
NOTIFY_ON_SUCCESS=true
NOTIFY_ON_ERROR=true
""")
    return config_file


@pytest.fixture
def mock_pending_files(tmp_path):
    """Create a mock pending-compression.txt file"""
    pending_file = tmp_path / "pending-compression.txt"
    pending_file.write_text("""
/mediajelly/media/series/test1.mkv
/mediajelly/media/anime/test2.mp4
/mediajelly/media/Peliculas/test3.avi
""")
    return pending_file


@pytest.fixture
def mock_completed_files(tmp_path):
    """Create a mock completed.txt file"""
    completed_file = tmp_path / "completed.txt"
    completed_file.write_text("""
/mediajelly/media/series/completed1.mkv
/mediajelly/media/anime/completed2.mp4
""")
    return completed_file
