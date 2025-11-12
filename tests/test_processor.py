#!/usr/bin/env python3
"""
Tests for MediaJelly Processor
"""

import pytest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock


class TestMediaProcessor:
    """Tests for video compression processor"""

    @pytest.fixture
    def processor(self, tmp_path):
        """Create a processor instance with temporary paths"""
        logs_dir = tmp_path / "scripts" / "logs"
        tmp_dir = tmp_path / "scripts" / "tmp"
        logs_dir.mkdir(parents=True)
        tmp_dir.mkdir(parents=True)

        import sys
        sys.path.insert(0, "/mediajelly/scripts")
        from mediajelly_processor import MediaJellyProcessor

        processor = MediaJellyProcessor()
        processor.logs_dir = logs_dir
        processor.tmp_dir = tmp_dir

        return processor

    def test_get_video_duration(self, processor, tmp_path):
        """Test getting video duration from file"""
        video_file = tmp_path / "test.mkv"
        video_file.touch()

        with patch("subprocess.run") as mock_run:
            # Mock ffprobe output
            mock_run.return_value = Mock(stdout="3600.5", returncode=0)

            duration = processor._get_video_duration(str(video_file))

            assert duration == 3600.5
            mock_run.assert_called_once()

    def test_get_video_duration_error(self, processor, tmp_path):
        """Test handling error when getting duration"""
        video_file = tmp_path / "test.mkv"
        video_file.touch()

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = Exception("ffprobe error")

            duration = processor._get_video_duration(str(video_file))

            assert duration == 0

    def test_check_spanish_audio_with_spanish(self, processor):
        """Test detection of Spanish audio streams"""
        mock_streams = [
            {"codec_type": "audio", "tags": {"language": "spa"}},
            {"codec_type": "audio", "tags": {"language": "eng"}},
        ]

        result = processor._check_spanish_audio(mock_streams)

        assert result is True

    def test_check_spanish_audio_without_spanish(self, processor):
        """Test detection when no Spanish audio"""
        mock_streams = [
            {"codec_type": "audio", "tags": {"language": "eng"}},
            {"codec_type": "audio", "tags": {"language": "jpn"}},
        ]

        result = processor._check_spanish_audio(mock_streams)

        assert result is False

    def test_estimate_compression_time(self, processor):
        """Test compression time estimation"""
        # Mock video with 1 hour duration
        file_size_gb = 5.0
        duration_seconds = 3600

        estimated_time = processor._estimate_compression_time(file_size_gb, duration_seconds)

        # Should return a reasonable estimate (in seconds)
        assert estimated_time > 0
        assert isinstance(estimated_time, (int, float))

    def test_calculate_compression_ratio(self, processor, tmp_path):
        """Test compression ratio calculation"""
        original = tmp_path / "original.mkv"
        compressed = tmp_path / "compressed.mp4"

        # Create files with known sizes
        original.write_bytes(b"x" * 1024 * 1024 * 100)  # 100 MB
        compressed.write_bytes(b"x" * 1024 * 1024 * 50)  # 50 MB

        ratio = processor._calculate_compression_ratio(str(original), str(compressed))

        assert ratio == 50.0  # 50% reduction

    def test_validate_output_file_success(self, processor, tmp_path):
        """Test validation of successfully compressed file"""
        output_file = tmp_path / "output.mp4"
        output_file.write_bytes(b"x" * 1024 * 1024)  # 1 MB file

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=0, stderr="")

            is_valid = processor._validate_output_file(str(output_file), min_size_mb=0.5)

            assert is_valid is True

    def test_validate_output_file_too_small(self, processor, tmp_path):
        """Test validation fails for files that are too small"""
        output_file = tmp_path / "output.mp4"
        output_file.write_bytes(b"x" * 1024 * 100)  # 100 KB file

        is_valid = processor._validate_output_file(str(output_file), min_size_mb=1.0)

        assert is_valid is False

    def test_validate_output_file_corrupted(self, processor, tmp_path):
        """Test validation fails for corrupted files"""
        output_file = tmp_path / "output.mp4"
        output_file.write_bytes(b"x" * 1024 * 1024)  # 1 MB file

        with patch("subprocess.run") as mock_run:
            # Mock ffmpeg error (corrupted file)
            mock_run.return_value = Mock(returncode=0, stderr="Error reading file")

            is_valid = processor._validate_output_file(str(output_file), min_size_mb=0.5)

            assert is_valid is False

    def test_build_ffmpeg_command_basic(self, processor, tmp_path):
        """Test building basic FFmpeg compression command"""
        input_file = tmp_path / "input.mkv"
        output_file = tmp_path / "output.mp4"
        input_file.touch()

        mock_streams = [
            {"codec_type": "video", "index": 0},
            {"codec_type": "audio", "index": 1, "tags": {"language": "spa"}},
        ]

        with patch.object(processor, "_get_stream_info") as mock_info:
            mock_info.return_value = mock_streams

            cmd = processor._build_ffmpeg_command(str(input_file), str(output_file), mock_streams)

            assert isinstance(cmd, list)
            assert "ffmpeg" in cmd
            assert "-i" in cmd
            assert str(input_file) in cmd
            assert str(output_file) in cmd

    def test_load_pending_files(self, processor, tmp_path):
        """Test loading pending files from file"""
        pending_file = tmp_path / "scripts" / "tmp" / "pending-compression.txt"
        pending_file.parent.mkdir(parents=True, exist_ok=True)
        pending_file.write_text("/media/video1.mkv\n/media/video2.mkv\n")

        processor.pending_file = pending_file

        files = processor._load_pending_files()

        assert len(files) == 2
        assert "/media/video1.mkv" in files
        assert "/media/video2.mkv" in files

    def test_remove_from_pending(self, processor, tmp_path):
        """Test removing processed file from pending list"""
        pending_file = tmp_path / "scripts" / "tmp" / "pending-compression.txt"
        pending_file.parent.mkdir(parents=True, exist_ok=True)
        pending_file.write_text("/media/video1.mkv\n/media/video2.mkv\n")

        processor.pending_file = pending_file

        processor._remove_from_pending("/media/video1.mkv")

        # Verify file was removed
        remaining = pending_file.read_text().strip().split("\n")
        assert "/media/video1.mkv" not in remaining
        assert "/media/video2.mkv" in remaining

    def test_add_to_completed(self, processor, tmp_path):
        """Test adding file to completed list"""
        completed_file = tmp_path / "scripts" / "tmp" / "completed.txt"
        completed_file.parent.mkdir(parents=True, exist_ok=True)
        completed_file.touch()

        processor.completed_file = completed_file

        processor._add_to_completed("/media/video1.mkv")

        # Verify file was added
        completed_content = completed_file.read_text()
        assert "/media/video1.mkv" in completed_content


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
