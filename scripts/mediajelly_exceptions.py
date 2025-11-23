"""
MediaJelly Exceptions
Custom exceptions for the MediaJelly project to improve error handling and clarity.
"""

class MediaJellyError(Exception):
    """Base exception for all MediaJelly errors."""
    pass

class CompressionError(MediaJellyError):
    """Raised when a file compression fails."""
    pass

class ValidationError(MediaJellyError):
    """Raised when file validation fails."""
    pass

class ConfigurationError(MediaJellyError):
    """Raised when there is a configuration error."""
    pass

class LanguageDetectionError(MediaJellyError):
    """Raised when language detection fails."""
    pass

class TranslationError(MediaJellyError):
    """Raised when subtitle translation fails."""
    pass

class DatabaseError(MediaJellyError):
    """Raised when a database/cache operation fails."""
    pass
