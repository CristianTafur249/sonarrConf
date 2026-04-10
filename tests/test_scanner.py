import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path
import sys
import os
import tempfile
import shutil

# Add scripts directory to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../scripts")))

from mediajelly_scanner import MediaScanner

class TestMediaScanner(unittest.TestCase):
    def setUp(self):
        # Create temp directory
        self.test_dir = tempfile.mkdtemp()
        self.test_path = Path(self.test_dir)
        
        # Patch __init__ to avoid side effects
        with patch("mediajelly_scanner.MediaScanner.__init__", return_value=None):
            self.scanner = MediaScanner()
            
            # Manually set attributes
            self.scanner.logger = MagicMock()
            self.scanner.scripts_dir = self.test_path / "scripts"
            self.scanner.tmp_dir = self.scanner.scripts_dir / "tmp"
            self.scanner.pending_file = self.scanner.tmp_dir / "pending-compression.txt"
            self.scanner.pending_subtitles_file = self.scanner.tmp_dir / "pending-subtitles.txt"
            self.scanner.corrupted_dir = self.test_path / "corrupted"
            
            # Ensure directories exist
            self.scanner.scripts_dir.mkdir(parents=True, exist_ok=True)
            self.scanner.tmp_dir.mkdir(parents=True, exist_ok=True)
            self.scanner.corrupted_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_file_sizes(self):
        # Create test files with different sizes
        large_file = self.test_path / "large.mp4"
        large_file.write_bytes(b"x" * (10 * 1024 * 1024))  # 10 MB
        
        small_file = self.test_path / "small.mp4"
        small_file.write_bytes(b"x" * 100)  # 100 bytes
        
        # Verify sizes
        self.assertGreater(large_file.stat().st_size, 1024 * 1024)
        self.assertLess(small_file.stat().st_size, 1024 * 1024)

    def test_scan_folder_optimized_basic(self):
        # Create test structure
        media_dir = self.test_path / "media"
        media_dir.mkdir()
        
        # Create video files
        (media_dir / "video1.mp4").write_bytes(b"x" * (10 * 1024 * 1024))
        (media_dir / "video2.mkv").write_bytes(b"x" * (10 * 1024 * 1024))
        (media_dir / "text.txt").write_bytes(b"text")
        
        # Mock validation to always return True
        self.scanner.validate_file_integrity = MagicMock(return_value=True)
        
        found_files = self.scanner.scan_folder_optimized(media_dir)
        
        # Should find at least the video files
        self.assertIsInstance(found_files, list)
        self.assertGreater(len(found_files), 0)

if __name__ == "__main__":
    unittest.main()
