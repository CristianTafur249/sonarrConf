"""
MediaJelly Notifier Tests
"""

import pytest
from unittest.mock import Mock, patch, MagicMock


class TestTelegramNotifier:
    """Tests for TelegramNotifier class"""
    
    @pytest.fixture
    def notifier(self, tmp_path, mock_telegram_config):
        """Create a notifier instance with mocked config"""
        with patch('mediajelly_notifier.Path') as mock_path:
            mock_path.return_value = tmp_path
            
            from mediajelly_notifier import TelegramNotifier
            notifier = TelegramNotifier()
            
            # Override paths
            notifier.base_dir = tmp_path
            notifier.config_file = mock_telegram_config
            notifier.queue_file = tmp_path / "notification_queue.json"
            
            # Load config
            notifier.config = notifier.load_config()
            
            return notifier
    
    def test_load_config_success(self, notifier):
        """Test successful loading of telegram config"""
        config = notifier.config
        
        assert 'TELEGRAM_BOT_TOKEN' in config
        assert 'TELEGRAM_CHAT_ID' in config
        assert config['TELEGRAM_BOT_TOKEN'] == "test_token_123456"
        assert config['TELEGRAM_CHAT_ID'] == "123456789"
    
    @patch('mediajelly_notifier.requests.post')
    def test_send_message_success(self, mock_post, notifier):
        """Test successful message sending"""
        # Mock successful response
        mock_response = Mock()
        mock_response.json.return_value = {'ok': True}
        mock_response.raise_for_status = Mock()
        mock_post.return_value = mock_response
        
        result = notifier._send_message_immediate("Test message")
        
        assert result is True
        mock_post.assert_called_once()
    
    @patch('mediajelly_notifier.requests.post')
    def test_send_message_failure(self, mock_post, notifier):
        """Test message sending failure"""
        # Mock failed response
        mock_post.side_effect = Exception("Network error")
        
        result = notifier._send_message_immediate("Test message")
        
        assert result is False
    
    def test_queue_management(self, notifier):
        """Test notification queue management"""
        # Add messages to queue
        notifier._add_to_queue("Message 1")
        notifier._add_to_queue("Message 2")
        
        # Load queue
        queue = notifier._load_queue()
        
        assert len(queue) == 2
        assert queue[0]['message'] == "Message 1"
        assert queue[1]['message'] == "Message 2"
    
    def test_long_message_splitting(self, notifier):
        """Test that long messages are properly split"""
        # Create a very long message
        long_message = "A" * 5000  # Exceeds 4096 character limit
        
        with patch.object(notifier, 'send_telegram_message', return_value=True) as mock_send:
            result = notifier.send_long_message(long_message)
            
            # Should be called multiple times due to message length
            assert mock_send.call_count >= 2
    
    @pytest.mark.unit
    def test_config_parsing(self, notifier, mock_telegram_config):
        """Test configuration file parsing"""
        config = notifier.load_config()
        
        # Verify all required fields are present
        assert 'TELEGRAM_BOT_TOKEN' in config
        assert 'TELEGRAM_CHAT_ID' in config
    
    def test_env_variable_expansion(self, notifier, monkeypatch):
        """Test that environment variables in format ${VAR} are expanded correctly"""
        # Configurar variables de entorno
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test_token_expanded")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "987654321")
        
        # Mock config object con valores ${VAR}
        mock_config = Mock()
        mock_config.telegram.bot_token = "${TELEGRAM_BOT_TOKEN}"
        mock_config.telegram.chat_id = "${TELEGRAM_CHAT_ID}"
        
        notifier.config_obj = mock_config
        config = notifier.load_config()
        
        # Verificar que las variables fueron expandidas
        assert config['TELEGRAM_BOT_TOKEN'] == "test_token_expanded"
        assert config['TELEGRAM_CHAT_ID'] == "987654321"
    
    @patch('mediajelly_notifier.REQUESTS_AVAILABLE', False)
    def test_send_message_without_requests(self, notifier):
        """Test that sending message without requests library fails gracefully"""
        result = notifier._send_message_immediate("Test message")
        
        assert result is False
