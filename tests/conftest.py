import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path
import sys
import os

# Add scripts directory to path to allow imports
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../scripts")))

from mediajelly_processor import MediaJellyProcessor
from mediajelly_config import MediaJellyConfig

@pytest.fixture
def mock_config():
    config = MagicMock(spec=MediaJellyConfig)
    config.paths = MagicMock()
    config.paths.base_dir = Path("/tmp/mediajelly/base")
    config.paths.output_dir = Path("/tmp/mediajelly/output")
    config.paths.temp_dir = Path("/tmp/mediajelly/temp")
    config.processing = MagicMock()
    config.processing.max_workers = 1
    config.processing.gpu_enabled = False
    return config

@pytest.fixture
def mock_processor(mock_config):
    with patch("mediajelly_processor.MediaJellyConfig", return_value=mock_config):
        processor = MediaJellyProcessor()
        processor.config = mock_config
        processor.logger = MagicMock()
        processor.scripts_dir = Path("/tmp/mediajelly/scripts")
        processor.completed_file = processor.scripts_dir / "completed.txt"
        processor.failed_file = processor.scripts_dir / "failed.txt"
        return processor

@pytest.fixture
def temp_video_file(tmp_path):
    video_file = tmp_path / "test_video.mp4"
    video_file.write_bytes(b"fake video content" * 100)  # Create a dummy file
    return video_file

@pytest.fixture
def temp_compressed_file(tmp_path):
    compressed_file = tmp_path / "test_video_compressed.mp4"
    compressed_file.write_bytes(b"fake compressed content" * 50)  # Smaller content
    return compressed_file
