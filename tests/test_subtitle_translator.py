import unittest
from unittest.mock import MagicMock, patch, mock_open
from pathlib import Path
import sys
import os

# Add scripts directory to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../scripts")))

from mediajelly_subtitle_translator import SubtitleTranslator

class TestSubtitleTranslator(unittest.TestCase):
    def setUp(self):
        # Patch __init__ to avoid side effects
        with patch("mediajelly_subtitle_translator.SubtitleTranslator.__init__", return_value=None):
            self.translator = SubtitleTranslator()
            
            # Manually set attributes needed for tests
            self.translator.logger = MagicMock()
            self.translator.tmp_dir = Path("/tmp/mediajelly/tmp")
            self.translator.lock_file = Path("/tmp/mediajelly/tmp/subtitle.lock")
            self.translator.progress_lock = MagicMock()
            self.translator.use_whisper = True
            self.translator.whisper_available = True
            self.translator.allow_retranslate = False
            
            # Mock quality improver
            self.translator.quality_improver = MagicMock()

    def tearDown(self):
        pass

    def test_quick_check_needs_processing_small_file(self):
        # Test file too small
        video_file = MagicMock(spec=Path)
        video_file.stat.return_value.st_size = 100  # Small size
        video_file.name = "small.mp4"
        
        result = self.translator.quick_check_needs_processing(video_file)
        self.assertFalse(result)

    def test_quick_check_needs_processing_wrong_extension(self):
        # Test wrong extension
        video_file = MagicMock(spec=Path)
        video_file.stat.return_value.st_size = 100 * 1024 * 1024
        video_file.suffix = ".txt"
        video_file.name = "video.txt"
        
        result = self.translator.quick_check_needs_processing(video_file)
        self.assertFalse(result)

    def test_quick_check_needs_processing_valid(self):
        # Test valid file
        video_file = MagicMock(spec=Path)
        video_file.stat.return_value.st_size = 100 * 1024 * 1024
        video_file.suffix = ".mp4"
        video_file.name = "video.mp4"
        
        # Mock check_existing_spanish_subtitles
        self.translator.check_existing_spanish_subtitles = MagicMock(return_value=False)
        
        result = self.translator.quick_check_needs_processing(video_file)
        self.assertTrue(result)

    def test_determine_file_status(self):
        # Test various statuses
        self.assertEqual(self.translator._determine_file_status({"error": "some error"}), "error")
        self.assertEqual(self.translator._determine_file_status({"had_spanish_subtitles": True}), "had_spanish_subtitles")
        self.assertEqual(self.translator._determine_file_status({"translated": True}), "translated")
        self.assertEqual(self.translator._determine_file_status({"extracted": True}), "extracted")
        self.assertEqual(self.translator._determine_file_status({}), "skipped")

    @patch("mediajelly_subtitle_translator.subprocess.run")
    def test_extract_subtitles_embedded(self, mock_run):
        # Mock detection of embedded subtitles
        self.translator._detect_embedded_subtitle_streams = MagicMock(return_value=[{"index": 0}])
        self.translator.detect_embedded_subtitle_language = MagicMock(return_value="en")
        self.translator._should_extract_embedded_subtitles = MagicMock(return_value=True)
        self.translator._extract_embedded_subtitles = MagicMock(return_value=Path("subs.srt"))
        
        video_file = Path("video.mp4")
        result = self.translator.extract_subtitles(video_file)
        
        self.assertEqual(result, Path("subs.srt"))
        self.translator._extract_embedded_subtitles.assert_called_once()

    def test_should_extract_embedded_subtitles(self):
        self.assertTrue(self.translator._should_extract_embedded_subtitles("en"))
        self.assertTrue(self.translator._should_extract_embedded_subtitles("es"))
        self.assertFalse(self.translator._should_extract_embedded_subtitles("xx"))
        self.assertFalse(self.translator._should_extract_embedded_subtitles(None))

if __name__ == "__main__":
    unittest.main()
