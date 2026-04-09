import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path
import sys
import os
import subprocess
import shutil
import tempfile

# Add scripts directory to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../scripts")))

from mediajelly_processor import MediaJellyProcessor
from mediajelly_config import MediaJellyConfig

class TestMediaJellyProcessor(unittest.TestCase):
    def setUp(self):
        # Create temp directory
        self.test_dir = tempfile.mkdtemp()
        self.test_path = Path(self.test_dir)
        
        # Patch __init__ to avoid hardcoded paths and side effects
        with patch("mediajelly_processor.MediaJellyProcessor.__init__", return_value=None):
            self.processor = MediaJellyProcessor()
            
            # Manually set attributes needed for tests
            self.processor.scripts_dir = self.test_path / "scripts"
            self.processor.completed_file = self.processor.scripts_dir / "completed.txt"
            self.processor.failed_file = self.processor.scripts_dir / "failed.txt"
            self.processor.logger = MagicMock()
            # mimic config object
            cfg = MagicMock()
            cfg.processing.reprocess_on_label_change = False
            cfg.processing.apply_audio_tagging_on_skip = True
            self.processor.config = cfg
            
            # Ensure directories exist
            self.processor.scripts_dir.mkdir(parents=True, exist_ok=True)
            
        # Create dummy files
        self.temp_video_file = self.test_path / "test_video.mp4"
        self.temp_video_file.write_bytes(b"fake video content" * 100)
        
        self.temp_compressed_file = self.test_path / "test_video_compressed.mp4"
        self.temp_compressed_file.write_bytes(b"fake compressed content" * 50)

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_validate_compressed_output_success(self):
        # Mock _validate_compressed_file
        self.processor._validate_compressed_file = MagicMock(return_value=(True, "Valid"))
        
        is_valid, msg = self.processor._validate_compressed_output(self.temp_compressed_file, self.temp_video_file)
        
        self.assertTrue(is_valid)
        self.assertEqual(msg, "Valid")
        self.processor._validate_compressed_file.assert_called_once_with(self.temp_compressed_file)

    def test_finalize_compression_larger(self):
        original_size = 1000
        compressed_size = 1200
        self.processor._update_pending_file = MagicMock()
        self.processor._mark_as_completed = MagicMock()
        
        result = self.processor._finalize_compression_larger(
            self.temp_compressed_file, self.temp_video_file, False, original_size, compressed_size
        )
        
        self.assertTrue(result["success"])
        self.assertTrue(result["renamed"])
        self.assertTrue(result["compressed"])
        
        final_file = self.temp_video_file.with_suffix(".mp4")
        self.assertTrue(final_file.exists())
        if self.temp_video_file != final_file:
            self.assertFalse(self.temp_video_file.exists())
            
        self.processor._update_pending_file.assert_called_once()
        self.processor._mark_as_completed.assert_called_once()

    def test_finalize_compression_smaller(self):
        # Use a non-mp4 file to trigger deletion
        temp_mkv_file = self.test_path / "test_video.mkv"
        temp_mkv_file.write_bytes(b"fake video content" * 100)
        
        original_size = 2000
        compressed_size = 1000
        elapsed_time = 10.0
        self.processor._update_pending_file = MagicMock()
        self.processor._mark_as_completed = MagicMock()
        self.processor._delete_original_file = MagicMock()
        
        result = self.processor._finalize_compression_smaller(
            self.temp_compressed_file, temp_mkv_file, False, original_size, compressed_size, elapsed_time
        )
        
        self.assertTrue(result["success"])
        self.assertTrue(result["compressed"])
        
        final_file = temp_mkv_file.with_suffix(".mp4")
        self.assertTrue(final_file.exists())
        
        self.processor._delete_original_file.assert_called_once_with(temp_mkv_file)
        self.processor._update_pending_file.assert_called_once()
        self.processor._mark_as_completed.assert_called_once()

    def test_generate_compression_error_message_timeout(self):
        process = MagicMock(spec=subprocess.CompletedProcess)
        process.returncode = 124
        
        msg = self.processor._generate_compression_error_message(process, self.temp_video_file, self.temp_compressed_file)
        
        self.assertIn("Timeout alcanzado", msg)

    def test_generate_compression_error_message_failure(self):
        process = MagicMock(spec=subprocess.CompletedProcess)
        process.returncode = 1
        
        msg = self.processor._generate_compression_error_message(process, self.temp_video_file, self.temp_compressed_file)
        
        self.assertIn("Falló compresión", msg)
        self.assertIn("código 1", msg)

    def test_handle_successful_compression_integration(self):
        # Mock internal methods
        self.processor._validate_compressed_output = MagicMock(return_value=(True, "Valid"))
        self.processor._finalize_compression_smaller = MagicMock(return_value={"success": True})
        
        result = self.processor._handle_successful_compression(
            self.temp_compressed_file, self.temp_video_file, 10.0, False
        )
        
        self.assertTrue(result["success"])
        self.processor._validate_compressed_output.assert_called_once()
        self.processor._finalize_compression_smaller.assert_called_once()

    def test_handle_successful_compression_validation_failure(self):
        # Mock validation failure
        self.processor._validate_compressed_output = MagicMock(return_value=(False, "Corrupt"))
        self.processor._handle_validation_failure = MagicMock(return_value={"success": True, "error": "Corrupt"})
        
        result = self.processor._handle_successful_compression(
            self.temp_compressed_file, self.temp_video_file, 10.0, False
        )
        
        self.assertEqual(result["error"], "Corrupt")
        self.processor._handle_validation_failure.assert_called_once()

    def test_filter_reprocess_on_label_change(self):
        # configure to reprocess when suffix differs
        self.processor.config.processing.reprocess_on_label_change = True
        # pretend the file was processed previously with a different label
        self.processor.processed_files = {
            str(self.temp_video_file): {"status": "success", "label": ".mkv"}
        }
        files = [self.temp_video_file]
        filtered = self.processor._filter_already_processed_files(files)
        self.assertEqual(filtered, files)

    def test_filter_apply_audio_tagging_on_skip(self):
        # make config allow tagging on skip
        self.processor.config.processing.apply_audio_tagging_on_skip = True
        # mark as processed so it would normally skip
        self.processor.processed_files = {
            str(self.temp_video_file): {"status": "success", "label": ".mp4"}
        }
        # detection returns an unlabeled stream so tagging should run
        self.processor.detect_language_streams = MagicMock(return_value=(False, [], [], {0: "und"}))
        self.processor._ensure_audio_tags = MagicMock(return_value=True)

        filtered = self.processor._filter_already_processed_files([self.temp_video_file])
        self.processor._ensure_audio_tags.assert_called_once_with(self.temp_video_file)
        self.assertEqual(filtered, [])

if __name__ == "__main__":
    unittest.main()
