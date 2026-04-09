#!/usr/bin/env python3
"""
MediaJelly Configuration Manager
Carga y gestiona la configuración centralizada desde YAML
"""

import os
import yaml
from pathlib import Path
from typing import Dict, Any, Optional, Union, List
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class TelegramConfig:
    """Configuración de Telegram"""
    bot_token: str = ""
    chat_id: str = ""
    notify_on_success: bool = True
    notify_on_error: bool = True
    notify_on_no_files: bool = False
    emoji_enabled: bool = True


@dataclass
class ProcessingConfig:
    """Configuración de procesamiento"""
    max_concurrent_jobs: int = 1
    max_memory_gb: int = 4
    max_cpu_cores: int = 4
    temp_dir: str = "/tmp/mediajelly"
    log_level: str = "INFO"
    enable_compression: bool = True
    compression_quality: str = "high"
    # Si se habilita, volverá a procesar (recomprimir) un archivo aunque ya esté
    # listado en completed.txt siempre que su sufijo/"etiqueta" (por ejemplo
    # .mp4, .mkv, etc.) sea diferente al que se registró anteriormente. Esto
    # permite actualizar compresiones cuando el tipo de contenedor cambia.
    reprocess_on_label_change: bool = False
    # Antes de descartar archivos ya procesados, intentar etiquetar pistas de
    # audio que no tengan idioma. La detección se realiza con ffprobe/Whisper y
    # si se descubre un idioma válido se aplica como metadata sin recodificar.
    apply_audio_tagging_on_skip: bool = True


@dataclass
class LanguageDetectionConfig:
    """Configuración de detección de idiomas"""
    whisper_model: str = "tiny"
    cache_enabled: bool = True
    redis_host: str = "redis"
    redis_port: int = 6379
    redis_db: int = 0
    cache_ttl_seconds: int = 86400
    max_samples_per_file: int = 15
    sample_duration_seconds: int = 30
    fallback_to_file_cache: bool = True
    language_code_map: Dict[str, str] = field(default_factory=lambda: {
        'spa': 'es', 'eng': 'en', 'fre': 'fr', 'ger': 'de', 'ita': 'it', 'por': 'pt', 'rus': 'ru', 'jpn': 'ja', 'chi': 'zh'
    })


@dataclass
class TranslationConfig:
    """Configuración de traducción"""
    enabled: bool = True
    provider: str = "translatepy"
    target_languages: list = field(default_factory=lambda: ["es", "en"])
    cache_enabled: bool = True
    max_retries: int = 3
    timeout_seconds: int = 30


@dataclass
class PathsConfig:
    """Configuración de rutas"""
    media_paths: List[str] = field(default_factory=lambda: ["/mediajelly/media/anime", "/mediajelly/media/Peliculas", "/mediajelly/media/series"])
    cache_file_path: str = "/mediajelly/scripts/tmp/language_cache.json"


@dataclass
class LoggingConfig:
    """Configuración de logging"""
    level: str = "INFO"
    main_log: str = "/mediajelly/scripts/logs/mediajelly.log"
    language_detection_log: str = "/mediajelly/scripts/logs/language-detection.log"
    subtitle_translator_log: str = "/mediajelly/scripts/logs/subtitle_translator.log"
    processor_log: str = "/mediajelly/scripts/logs/processor.log"


@dataclass
class MetricsConfig:
    """Configuración de métricas"""
    enabled: bool = True
    prometheus_port: int = 9090
    api_port: int = 8000
    collect_system_metrics: bool = True
    collect_processing_metrics: bool = True
    export_interval_seconds: int = 30


@dataclass
class MediaJellyConfig:
    """Configuración centralizada de MediaJelly"""
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    processing: ProcessingConfig = field(default_factory=ProcessingConfig)
    language_detection: LanguageDetectionConfig = field(default_factory=LanguageDetectionConfig)
    translation: TranslationConfig = field(default_factory=TranslationConfig)
    paths: PathsConfig = field(default_factory=PathsConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    metrics: MetricsConfig = field(default_factory=MetricsConfig)

    # Atributos adicionales
    config_file: Optional[Path] = None
    loaded_at: Optional[datetime] = None

    @classmethod
    def load_from_file(cls, config_path: Union[str, Path]) -> 'MediaJellyConfig':
        """Carga configuración desde archivo YAML"""
        config_path = Path(config_path)

        if not config_path.exists():
            raise FileNotFoundError(f"Archivo de configuración no encontrado: {config_path}")

        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                data = yaml.safe_load(f) or {}
        except yaml.YAMLError as e:
            raise ValueError(f"Error al parsear YAML: {e}")

        # Resolver variables de entorno
        data = cls._resolve_env_vars(data)

        # Crear instancia de configuración
        config = cls.from_dict(data)
        config.config_file = config_path
        config.loaded_at = datetime.now()

        return config

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'MediaJellyConfig':
        """Crea configuración desde diccionario"""
        config = cls()

        # Configuración de Telegram
        if 'telegram' in data:
            telegram_data = data['telegram']
            config.telegram = TelegramConfig(
                bot_token=telegram_data.get('bot_token', ''),
                chat_id=telegram_data.get('chat_id', ''),
                notify_on_success=telegram_data.get('notify_on_success', True),
                notify_on_error=telegram_data.get('notify_on_error', True),
                notify_on_no_files=telegram_data.get('notify_on_no_files', False),
                emoji_enabled=telegram_data.get('emoji_enabled', True)
            )

        # Configuración de procesamiento
        if 'processing' in data:
            processing_data = data['processing']
            config.processing = ProcessingConfig(
                max_concurrent_jobs=processing_data.get('max_concurrent_jobs', 1),
                max_memory_gb=processing_data.get('max_memory_gb', 4),
                max_cpu_cores=processing_data.get('max_cpu_cores', 4),
                temp_dir=processing_data.get('temp_dir', '/tmp/mediajelly'),
                log_level=processing_data.get('log_level', 'INFO'),
                enable_compression=processing_data.get('enable_compression', True),
                compression_quality=processing_data.get('compression_quality', 'high'),
                reprocess_on_label_change=processing_data.get('reprocess_on_label_change', False),
                apply_audio_tagging_on_skip=processing_data.get('apply_audio_tagging_on_skip', True),
            )

        # Configuración de detección de idiomas
        if 'language_detection' in data:
            lang_data = data['language_detection']
            config.language_detection = LanguageDetectionConfig(
                whisper_model=lang_data.get('whisper_model', 'tiny'),
                cache_enabled=lang_data.get('cache_enabled', True),
                redis_host=lang_data.get('redis_host', 'localhost'),
                redis_port=lang_data.get('redis_port', 6379),
                redis_db=lang_data.get('redis_db', 0),
                cache_ttl_seconds=lang_data.get('cache_ttl_seconds', 86400),
                max_samples_per_file=lang_data.get('max_samples_per_file', 15),
                sample_duration_seconds=lang_data.get('sample_duration_seconds', 30),
                fallback_to_file_cache=lang_data.get('fallback_to_file_cache', True),
                language_code_map=lang_data.get('language_code_map', {
                    'spa': 'es', 'eng': 'en', 'fre': 'fr', 'ger': 'de', 'ita': 'it', 'por': 'pt', 'rus': 'ru', 'jpn': 'ja', 'chi': 'zh'
                })
            )

        # Configuración de traducción
        if 'translation' in data:
            trans_data = data['translation']
            config.translation = TranslationConfig(
                enabled=trans_data.get('enabled', True),
                provider=trans_data.get('provider', 'translatepy'),
                target_languages=trans_data.get('target_languages', ['es', 'en']),
                cache_enabled=trans_data.get('cache_enabled', True),
                max_retries=trans_data.get('max_retries', 3),
                timeout_seconds=trans_data.get('timeout_seconds', 30)
            )

        # Configuración de métricas
        if 'metrics' in data:
            metrics_data = data['metrics']
            config.metrics = MetricsConfig(
                enabled=metrics_data.get('enabled', True),
                prometheus_port=metrics_data.get('prometheus_port', 9090),
                api_port=metrics_data.get('api_port', 8000),
                collect_system_metrics=metrics_data.get('collect_system_metrics', True),
                collect_processing_metrics=metrics_data.get('collect_processing_metrics', True),
                export_interval_seconds=metrics_data.get('export_interval_seconds', 30)
            )

        # Configuración de rutas
        if 'paths' in data:
            paths_data = data['paths']
            config.paths = PathsConfig(
                media_paths=paths_data.get('media_paths', ["/mediajelly/media/anime", "/mediajelly/media/Peliculas", "/mediajelly/media/series"]),
                cache_file_path=paths_data.get('cache_file_path', "/mediajelly/scripts/tmp/language_cache.json")
            )

        # Configuración de logging
        if 'logging' in data:
            logging_data = data['logging']
            config.logging = LoggingConfig(
                level=logging_data.get('level', 'INFO'),
                main_log=logging_data.get('main_log', '/mediajelly/scripts/logs/mediajelly.log'),
                language_detection_log=logging_data.get('language_detection_log', '/mediajelly/scripts/logs/language-detection.log'),
                subtitle_translator_log=logging_data.get('subtitle_translator_log', '/mediajelly/scripts/logs/subtitle_translator.log'),
                processor_log=logging_data.get('processor_log', '/mediajelly/scripts/logs/processor.log')
            )

        return config

    @staticmethod
    def _resolve_env_vars(data: Dict[str, Any]) -> Dict[str, Any]:
        """Resuelve variables de entorno en los valores de configuración"""
        if isinstance(data, dict):
            resolved = {}
            for key, value in data.items():
                if isinstance(value, str) and value.startswith('${') and value.endswith('}'):
                    # Formato: ${VAR_NAME:-default}
                    env_part = value[2:-1]  # Remover ${}
                    if ':-' in env_part:
                        var_name, default = env_part.split(':-', 1)
                        resolved[key] = os.getenv(var_name, default)
                    else:
                        resolved[key] = os.getenv(env_part, '')
                elif isinstance(value, (dict, list)):
                    resolved[key] = MediaJellyConfig._resolve_env_vars(value)
                else:
                    resolved[key] = value
            return resolved
        elif isinstance(data, list):
            return [MediaJellyConfig._resolve_env_vars(item) for item in data]
        else:
            return data

    def save_to_file(self, config_path: Union[str, Path]) -> None:
        """Guarda configuración actual a archivo YAML"""
        config_path = Path(config_path)

        # Convertir a diccionario
        data = {
            'telegram': {
                'bot_token': self.telegram.bot_token,
                'chat_id': self.telegram.chat_id,
                'notify_on_success': self.telegram.notify_on_success,
                'notify_on_error': self.telegram.notify_on_error,
                'notify_on_no_files': self.telegram.notify_on_no_files,
                'emoji_enabled': self.telegram.emoji_enabled
            },
            'processing': {
                'max_concurrent_jobs': self.processing.max_concurrent_jobs,
                'max_memory_gb': self.processing.max_memory_gb,
                'max_cpu_cores': self.processing.max_cpu_cores,
                'temp_dir': self.processing.temp_dir,
                'log_level': self.processing.log_level,
                'enable_compression': self.processing.enable_compression,
                'compression_quality': self.processing.compression_quality
            },
            'language_detection': {
                'whisper_model': self.language_detection.whisper_model,
                'cache_enabled': self.language_detection.cache_enabled,
                'redis_host': self.language_detection.redis_host,
                'redis_port': self.language_detection.redis_port,
                'redis_db': self.language_detection.redis_db,
                'cache_ttl_seconds': self.language_detection.cache_ttl_seconds,
                'max_samples_per_file': self.language_detection.max_samples_per_file,
                'sample_duration_seconds': self.language_detection.sample_duration_seconds,
                'fallback_to_file_cache': self.language_detection.fallback_to_file_cache
            },
            'translation': {
                'enabled': self.translation.enabled,
                'provider': self.translation.provider,
                'target_languages': self.translation.target_languages,
                'cache_enabled': self.translation.cache_enabled,
                'max_retries': self.translation.max_retries,
                'timeout_seconds': self.translation.timeout_seconds
            },
            'metrics': {
                'enabled': self.metrics.enabled,
                'prometheus_port': self.metrics.prometheus_port,
                'api_port': self.metrics.api_port,
                'collect_system_metrics': self.metrics.collect_system_metrics,
                'collect_processing_metrics': self.metrics.collect_processing_metrics,
                'export_interval_seconds': self.metrics.export_interval_seconds
            }
        }

        # Crear directorio si no existe
        config_path.parent.mkdir(parents=True, exist_ok=True)

        # Guardar a YAML
        with open(config_path, 'w', encoding='utf-8') as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True, indent=2)

    def validate(self) -> list:
        """Valida la configuración y retorna lista de errores"""
        errors = []

        # Validar Telegram
        if not self.telegram.bot_token:
            errors.append("telegram.bot_token es requerido")
        if not self.telegram.chat_id:
            errors.append("telegram.chat_id es requerido")

        # Validar procesamiento
        if self.processing.max_memory_gb < 1:
            errors.append("processing.max_memory_gb debe ser al menos 1GB")
        if self.processing.max_cpu_cores < 1:
            errors.append("processing.max_cpu_cores debe ser al menos 1")

        # Validar modelo Whisper
        valid_models = ['tiny', 'tiny.en', 'base', 'small', 'medium', 'large']
        if self.language_detection.whisper_model not in valid_models:
            errors.append(f"language_detection.whisper_model debe ser uno de: {valid_models}")

        # Validar calidad de compresión
        valid_qualities = ['low', 'medium', 'high']
        if self.processing.compression_quality not in valid_qualities:
            errors.append(f"processing.compression_quality debe ser uno de: {valid_qualities}")

        return errors


def load_config(config_path: Optional[Union[str, Path]] = None) -> MediaJellyConfig:
    """Función de conveniencia para cargar configuración"""
    if config_path is None:
        # Buscar en ubicaciones estándar
        search_paths = [
            Path("/mediajelly/config/mediajelly.yaml"),
            Path("/home/tafurc/mediaJelly/config/mediajelly.yaml"),
            Path("config/mediajelly.yaml"),
            Path("mediajelly.yaml")
        ]

        for path in search_paths:
            if path.exists():
                config_path = path
                break
        else:
            raise FileNotFoundError("No se encontró archivo de configuración mediajelly.yaml")

    return MediaJellyConfig.load_from_file(config_path)


# Configuración global (lazy loading)
_config_instance: Optional[MediaJellyConfig] = None

def get_config() -> MediaJellyConfig:
    """Obtiene la instancia global de configuración"""
    global _config_instance
    if _config_instance is None:
        _config_instance = load_config()
    return _config_instance
