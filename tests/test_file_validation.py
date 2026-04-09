#!/usr/bin/env python3
"""
Tests for file integrity validation in MediaScanner
"""

import pytest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
import subprocess


class TestFileValidation:
    """Tests for file integrity validation functionality"""
    
    @pytest.fixture
    def scanner(self, tmp_path):
        """Create a scanner instance with temporary paths"""
        # Create required directories
        logs_dir = tmp_path / "scripts" / "logs"
        tmp_dir = tmp_path / "scripts" / "tmp"
        logs_dir.mkdir(parents=True)
        tmp_dir.mkdir(parents=True)
        
        # Import and create scanner
        import sys
        sys.path.insert(0, '/mediajelly/scripts')
        from mediajelly_scanner import MediaScanner
        
        scanner = MediaScanner()
        scanner.logs_dir = logs_dir
        scanner.tmp_dir = tmp_dir
        
        return scanner
    
    def test_validate_file_integrity_valid_file(self, scanner, tmp_path):
        """Test that valid files pass integrity check"""
        test_file = tmp_path / "valid_video.mkv"
        test_file.touch()
        
        with patch('subprocess.run') as mock_run:
            # Mock successful ffmpeg validation (no errors)
            mock_run.return_value = Mock(
                stderr="",
                returncode=0
            )
            
            result = scanner.validate_file_integrity(test_file)
            
            assert result is True
            mock_run.assert_called_once()
            
            # Verify ffmpeg command
            args = mock_run.call_args[0][0]
            assert 'ffmpeg' in args
            assert '-v' in args
            assert 'error' in args
            assert str(test_file) in args
    
    def test_validate_file_integrity_corrupted_file(self, scanner, tmp_path):
        """Test that corrupted files fail integrity check"""
        test_file = tmp_path / "corrupted_video.mkv"
        test_file.touch()
        
        with patch('subprocess.run') as mock_run:
            # Mock ffmpeg validation with errors
            mock_run.return_value = Mock(
                stderr="[matroska,webm @ 0x123] EBML header parsing failed",
                returncode=0
            )
            
            result = scanner.validate_file_integrity(test_file)
            
            assert result is False
    
    def test_validate_file_integrity_timeout(self, scanner, tmp_path):
        """Test that timeout returns False"""
        test_file = tmp_path / "slow_video.mkv"
        test_file.touch()
        
        with patch('subprocess.run') as mock_run:
            # Mock timeout
            mock_run.side_effect = subprocess.TimeoutExpired(
                cmd=['ffmpeg'], timeout=30
            )
            
            result = scanner.validate_file_integrity(test_file)
            
            assert result is False
    
    def test_validate_file_integrity_exception(self, scanner, tmp_path):
        """Test that exceptions return False"""
        test_file = tmp_path / "error_video.mkv"
        test_file.touch()
        
        with patch('subprocess.run') as mock_run:
            # Mock exception
            mock_run.side_effect = Exception("Unexpected error")
            
            result = scanner.validate_file_integrity(test_file)
            
            assert result is False
    
    def test_move_corrupted_file_success(self, scanner, tmp_path):
        """Test successfully moving corrupted file to .delete folder"""
        # Create test file
        video_dir = tmp_path / "videos"
        video_dir.mkdir()
        test_file = video_dir / "corrupted.mkv"
        test_file.write_text("corrupted data")
        
        result = scanner.move_corrupted_file(test_file)
        
        assert result is True
        assert not test_file.exists()
        
        # Check file was moved to .delete
        delete_folder = video_dir / ".delete"
        assert delete_folder.exists()
        moved_file = delete_folder / "corrupted.mkv"
        assert moved_file.exists()
        assert moved_file.read_text() == "corrupted data"
    
    def test_move_corrupted_file_creates_delete_folder(self, scanner, tmp_path):
        """Test that .delete folder is created if it doesn't exist"""
        video_dir = tmp_path / "videos"
        video_dir.mkdir()
        test_file = video_dir / "corrupted.mkv"
        test_file.write_text("data")
        
        # Ensure .delete doesn't exist
        delete_folder = video_dir / ".delete"
        assert not delete_folder.exists()
        
        scanner.move_corrupted_file(test_file)
        
        # Verify .delete was created
        assert delete_folder.exists()
        assert delete_folder.is_dir()
    
    def test_move_corrupted_file_error_handling(self, scanner, tmp_path):
        """Test error handling when moving file fails"""
        test_file = tmp_path / "nonexistent.mkv"
        # File doesn't exist, should cause error
        
        result = scanner.move_corrupted_file(test_file)
        
        assert result is False
    
    def test_process_new_files_with_validation(self, scanner, tmp_path):
        """Test that new files are validated before adding to pending"""
        # Create test video files
        video1 = tmp_path / "valid.mkv"
        video2 = tmp_path / "corrupted.mkv"
        video1.touch()
        video2.touch()
        
        all_found_files = [video1, video2]
        existing_pending = set()
        completed_files = set()
        
        with patch.object(scanner, 'validate_file_integrity') as mock_validate:
            with patch.object(scanner, 'move_corrupted_file') as mock_move:
                with patch.object(scanner, '_append_to_pending_file'):
                    with patch.object(scanner, '_log_new_files_added'):
                        # Mock validation: video1 is valid, video2 is corrupted
                        mock_validate.side_effect = [True, False]
                        mock_move.return_value = True
                        
                        total, new = scanner._process_new_files(
                            all_found_files, existing_pending, completed_files
                        )
                        
                        # Should validate both files
                        assert mock_validate.call_count == 2
                        
                        # Should move corrupted file
                        mock_move.assert_called_once_with(video2)
                        
                        # Should return correct counts
                        assert total == 2
                        assert new == 1  # Only valid file added


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
