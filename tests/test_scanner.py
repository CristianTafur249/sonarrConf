"""
MediaJelly Scanner Tests
"""

import pytest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock


class TestMediaScanner:
    """Tests for MediaScanner class"""
    
    @pytest.fixture
    def scanner(self, tmp_path):
        """Create a scanner instance with temporary paths"""
        with patch('mediajelly_scanner.Path') as mock_path:
            # Mock the path detection to use tmp_path
            mock_path.return_value = tmp_path
            
            # Import after patching
            from mediajelly_scanner import MediaScanner
            scanner = MediaScanner()
            
            # Override paths to use tmp_path
            scanner.base_dir = tmp_path
            scanner.scripts_dir = tmp_path / "scripts"
            scanner.tmp_dir = tmp_path / "scripts" / "tmp"
            scanner.logs_dir = tmp_path / "scripts" / "logs"
            
            # Create directories
            scanner.tmp_dir.mkdir(parents=True, exist_ok=True)
            scanner.logs_dir.mkdir(parents=True, exist_ok=True)
            
            scanner.pending_file = scanner.tmp_dir / "pending-compression.txt"
            scanner.completed_file = scanner.tmp_dir / "completed.txt"
            scanner.log_file = scanner.logs_dir / "scan.log"
            
            scanner.completed_file.touch()
            
            return scanner
    
    def test_excluded_folders_detection(self, scanner, temp_media_dir):
        """Test that files in excluded folders are properly filtered"""
        # Create test structure with excluded folders
        excluded_dir = temp_media_dir / "series" / ".delete"
        excluded_dir.mkdir()
        excluded_file = excluded_dir / "test.mkv"
        excluded_file.touch()
        
        normal_file = temp_media_dir / "series" / "normal.mkv"
        normal_file.touch()
        
        # Test exclusion
        assert scanner._is_file_in_excluded_folder(excluded_file) is True
        assert scanner._is_file_in_excluded_folder(normal_file) is False
    
    def test_scan_folder_finds_video_files(self, scanner, temp_media_dir):
        """Test that scan_folder finds video files with correct extensions"""
        # Create test video files
        mkv_file = temp_media_dir / "series" / "test.mkv"
        mp4_file = temp_media_dir / "series" / "test.mp4"
        txt_file = temp_media_dir / "series" / "readme.txt"
        
        mkv_file.touch()
        mp4_file.touch()
        txt_file.touch()
        
        # Scan folder
        found_files = scanner.scan_folder_optimized(temp_media_dir / "series")
        
        # Verify only video files are found
        assert len(found_files) == 2
        assert any(f.name == "test.mkv" for f in found_files)
        assert any(f.name == "test.mp4" for f in found_files)
        assert not any(f.name == "readme.txt" for f in found_files)
    
    def test_scan_ignores_compressed_files(self, scanner, temp_media_dir):
        """Test that .compressed.mp4 files are ignored"""
        normal_file = temp_media_dir / "series" / "normal.mp4"
        compressed_file = temp_media_dir / "series" / "video.compressed.mp4"
        
        normal_file.touch()
        compressed_file.touch()
        
        found_files = scanner.scan_folder_optimized(temp_media_dir / "series")
        
        assert len(found_files) == 1
        assert found_files[0].name == "normal.mp4"
    
    def test_load_completed_files(self, scanner, mock_completed_files):
        """Test loading completed files list"""
        scanner.completed_file = mock_completed_files
        
        completed = scanner._load_completed_files()
        
        assert len(completed) == 2
        assert "/mediajelly/media/series/completed1.mkv" in completed
        assert "/mediajelly/media/anime/completed2.mp4" in completed
    
    def test_scan_folder_nonexistent_directory(self, scanner):
        """Test scanning a directory that doesn't exist"""
        nonexistent = Path("/nonexistent/path")
        
        found_files = scanner.scan_folder_optimized(nonexistent)
        
        assert found_files == []
    
    @pytest.mark.unit
    def test_file_extensions_detection(self, scanner):
        """Test that all supported video extensions are recognized"""
        extensions = ['.mkv', '.mp4', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v']
        
        for ext in extensions:
            assert ext in scanner.EXTENSIONS
    
    @pytest.mark.unit
    def test_excluded_folders_list(self, scanner):
        """Test that all expected folders are in excluded list"""
        expected_excluded = {'.delete', '.deleted', '.tmp', '.temp', '.trash', '.recycle'}
        
        assert scanner.EXCLUDED_FOLDERS == expected_excluded
