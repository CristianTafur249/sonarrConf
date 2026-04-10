import unittest
from unittest.mock import MagicMock, patch
import sys
import os
from pathlib import Path

# Add scripts directory to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../scripts")))

from mediajelly_cron_runner import MediaJellyCron

class TestMediaJellyCron(unittest.TestCase):
    def setUp(self):
        # Mock environment
        self.env_patcher = patch.dict(os.environ, {"CONTAINER_PATH": "/tmp/mediajelly"})
        self.env_patcher.start()
        
        # Mock logging
        self.logging_patcher = patch("mediajelly_cron_runner.logging")
        self.mock_logging = self.logging_patcher.start()
        
        # Patch __init__ to avoid side effects (lock file, paths)
        with patch("mediajelly_cron_runner.MediaJellyCron.__init__", return_value=None):
            self.cron = MediaJellyCron()
            
            # Manually set attributes
            self.cron.base_path = Path("/tmp/mediajelly")
            self.cron.scripts_path = self.cron.base_path / "scripts"
            self.cron.tmp_path = self.cron.scripts_path / "tmp"
            self.cron.scanner_script = self.cron.scripts_path / "mediajelly_scanner.py"
            self.cron.language_detector_script = self.cron.scripts_path / "mediajelly_language_detector.py"
            self.cron.processor_script = self.cron.scripts_path / "mediajelly_processor.py"
            self.cron.subtitle_script = self.cron.scripts_path / "mediajelly_subtitle_translator.py"
            self.cron.lock_file = self.cron.tmp_path / "cron_runner.lock"
            self.cron.logger = MagicMock()

    def tearDown(self):
        self.env_patcher.stop()
        self.logging_patcher.stop()

    @patch("mediajelly_cron_runner.MediaScanner")
    def test_run_scanner(self, mock_scanner_class):
        # Setup mock
        mock_scanner = mock_scanner_class.return_value
        mock_scanner.run.return_value = (10, 5)
        
        # Execute
        result = self.cron.run_scanner()
        
        # Verify
        self.assertTrue(result)
        mock_scanner_class.assert_called_once()
        mock_scanner.run.assert_called_once()

    @patch("mediajelly_cron_runner.language_detector_main")
    def test_run_language_detector(self, mock_main):
        # Execute
        result = self.cron.run_language_detector()
        
        # Verify
        self.assertTrue(result)
        mock_main.assert_called_once()

    @patch("mediajelly_cron_runner.MediaJellyProcessor")
    def test_run_processor(self, mock_processor_class):
        # Setup mock
        mock_processor = mock_processor_class.return_value
        mock_processor.run.return_value = MagicMock(files_processed=5)
        
        # Execute
        result = self.cron.run_processor()
        
        # Verify
        self.assertTrue(result)
        mock_processor_class.assert_called_once()
        mock_processor.run.assert_called_once()

    @patch("mediajelly_cron_runner.subtitle_main")
    def test_run_subtitle_translator(self, mock_main):
        # Execute
        result = self.cron.run_subtitle_translator()
        
        # Verify
        self.assertTrue(result)
        mock_main.assert_called_once()

    @patch("mediajelly_cron_runner.MediaJellyCron.run_nfo_translator")
    @patch("mediajelly_cron_runner.MediaJellyCron.run_scanner")
    @patch("mediajelly_cron_runner.MediaJellyCron.run_language_detector")
    @patch("mediajelly_cron_runner.MediaJellyCron.run_processor")
    @patch("mediajelly_cron_runner.MediaJellyCron.run_subtitle_translator")
    def test_run_cycle(self, mock_sub, mock_proc, mock_lang, mock_scan, mock_nfo):
        self.cron.run_cycle()
        
        mock_scan.assert_called_once()
        mock_nfo.assert_called_once()
        mock_lang.assert_called_once()
        mock_proc.assert_called_once()
        mock_sub.assert_called_once()

if __name__ == "__main__":
    unittest.main()
