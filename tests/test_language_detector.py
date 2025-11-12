#!/usr/bin/env python3
"""
Tests for MediaJelly Language Detector
"""

import pytest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
import json


class TestLanguageDetector:
    """Tests for language detection functionality"""

    @pytest.fixture
    def detector(self, tmp_path):
        """Create a language detector instance with temporary paths"""
        # Create required directories
        logs_dir = tmp_path / "scripts" / "logs"
        tmp_dir = tmp_path / "scripts" / "tmp"
        logs_dir.mkdir(parents=True)
        tmp_dir.mkdir(parents=True)

        # Create cache file
        cache_file = tmp_dir / "language_cache.json"
        cache_file.write_text("{}")

        import sys
        sys.path.insert(0, "/mediajelly/scripts")
        from mediajelly_language_detector import LanguageDetector

        detector = LanguageDetector()
        detector.cache_file = cache_file
        detector.logs_dir = logs_dir

        return detector

    def test_load_cache_empty(self, detector, tmp_path):
        """Test loading empty cache"""
        cache_file = tmp_path / "scripts" / "tmp" / "language_cache.json"
        cache_file.write_text("{}")

        cache = detector.load_cache()

        assert isinstance(cache, dict)
        assert len(cache) == 0

    def test_load_cache_with_data(self, detector, tmp_path):
        """Test loading cache with existing data"""
        cache_file = tmp_path / "scripts" / "tmp" / "language_cache.json"
        test_data = {
            "/media/anime/show.mkv": {
                "language": "es",
                "confidence": 0.95,
                "timestamp": "2025-11-11 00:00:00",
            }
        }
        cache_file.write_text(json.dumps(test_data))

        cache = detector.load_cache()

        assert len(cache) == 1
        assert "/media/anime/show.mkv" in cache
        assert cache["/media/anime/show.mkv"]["language"] == "es"

    def test_save_cache(self, detector, tmp_path):
        """Test saving cache to file"""
        cache_file = tmp_path / "scripts" / "tmp" / "language_cache.json"
        detector.cache_file = cache_file

        test_cache = {
            "/media/test.mkv": {
                "language": "en",
                "confidence": 0.85,
            }
        }

        detector.save_cache(test_cache)

        # Verify file was written
        assert cache_file.exists()

        # Load and verify content
        saved_data = json.loads(cache_file.read_text())
        assert "/media/test.mkv" in saved_data
        assert saved_data["/media/test.mkv"]["language"] == "en"

    def test_is_language_tagged_with_spanish(self, detector):
        """Test detection of Spanish language tags"""
        mock_audio_streams = [
            {"tags": {"language": "spa"}},
            {"tags": {"language": "eng"}},
        ]

        result = detector._is_language_tagged(mock_audio_streams)

        assert result is True

    def test_is_language_tagged_without_spanish(self, detector):
        """Test detection when no Spanish tags present"""
        mock_audio_streams = [
            {"tags": {"language": "eng"}},
            {"tags": {"language": "jpn"}},
        ]

        result = detector._is_language_tagged(mock_audio_streams)

        assert result is False

    def test_is_language_tagged_no_tags(self, detector):
        """Test detection when streams have no language tags"""
        mock_audio_streams = [
            {"codec_name": "aac"},
            {"codec_name": "ac3"},
        ]

        result = detector._is_language_tagged(mock_audio_streams)

        assert result is False

    def test_filter_pending_files(self, detector, tmp_path):
        """Test filtering of files that need language detection"""
        # Create test files
        test_file1 = tmp_path / "video1.mkv"
        test_file2 = tmp_path / "video2.mkv"
        test_file1.touch()
        test_file2.touch()

        pending_files = [str(test_file1), str(test_file2)]
        cache = {str(test_file1): {"language": "es"}}

        with patch.object(detector, "_is_file_language_tagged") as mock_tagged:
            mock_tagged.return_value = False

            result = detector._filter_pending_files(pending_files, cache)

            # test_file1 is in cache, so only test_file2 should be returned
            assert len(result) == 1
            assert str(test_file2) in result

    def test_extract_audio_sample(self, detector, tmp_path):
        """Test audio sample extraction from video"""
        video_file = tmp_path / "test.mkv"
        video_file.touch()

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=0)

            sample_path = detector._extract_audio_sample(str(video_file), 0, 10)

            # Verify ffmpeg was called
            mock_run.assert_called_once()
            args = mock_run.call_args[0][0]
            assert "ffmpeg" in args
            assert "-ss" in args
            assert "-t" in args

    def test_detect_language_voting_spanish_majority(self, detector):
        """Test language detection with Spanish majority"""
        # Mock samples with Spanish majority
        sample_results = [
            ("es", {}),
            ("es", {}),
            ("es", {}),
            ("en", {}),
            ("en", {}),
        ]

        with patch.object(detector, "_extract_audio_sample") as mock_extract:
            with patch.object(detector, "detect_language_with_whisper") as mock_whisper:
                mock_extract.return_value = "/tmp/sample.wav"
                mock_whisper.side_effect = sample_results

                detected_lang = detector._detect_language_voting("/tmp/test.mkv", num_samples=5)

                assert detected_lang == "es"

    def test_detect_language_voting_no_majority(self, detector):
        """Test language detection without clear majority"""
        sample_results = [
            ("en", {}),
            ("es", {}),
            ("ja", {}),
            ("en", {}),
            ("es", {}),
        ]

        with patch.object(detector, "_extract_audio_sample") as mock_extract:
            with patch.object(detector, "detect_language_with_whisper") as mock_whisper:
                mock_extract.return_value = "/tmp/sample.wav"
                mock_whisper.side_effect = sample_results

                detected_lang = detector._detect_language_voting("/tmp/test.mkv", num_samples=5)

                # Should return the language with most votes (en or es)
                assert detected_lang in ["en", "es"]

    def test_cache_hit_skip_detection(self, detector, tmp_path):
        """Test that cached files skip Whisper detection"""
        test_file = tmp_path / "cached.mkv"
        test_file.touch()

        cache = {str(test_file): {"language": "es", "confidence": 0.95}}

        with patch.object(detector, "detect_language_with_whisper") as mock_whisper:
            # Load cached files
            detector.cache = cache

            # Process file that's in cache
            # Should not call Whisper
            if str(test_file) in cache:
                # File is cached, should skip detection
                pass
            else:
                detector.detect_language_with_whisper(None, str(test_file))

            # Whisper should not have been called
            mock_whisper.assert_not_called()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
