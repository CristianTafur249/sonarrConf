#!/usr/bin/env python3
"""
MediaJelly Python - Sistema de procesamiento multimedia optimizado
"""

import os
import sys
import time
import json
import re
import logging
import logging.handlers
import resource
import subprocess
from pathlib import Path
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor, as_completed  # Volviendo a ProcessPool
from dataclasses import dataclass, asdict
from typing import List, Dict, Optional, Tuple, Union
import signal
import atexit

# Importar psutil para gestión dinámica de recursos
try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    psutil = None
    PSUTIL_AVAILABLE = False

# Importar whisper solo si está disponible
try:
    import whisper

    WHISPER_AVAILABLE = True
except ImportError:
    WHISPER_AVAILABLE = False

# Importar módulo de emojis
from mediajelly_emoji import EmojiGenerator
from mediajelly_exceptions import (
    MediaJellyError,
    CompressionError,
    ValidationError,
    ConfigurationError,
    LanguageDetectionError,
)
from mediajelly_utils import MediaJellyPaths
try:
    from mediajelly_language_detector import set_cached_language, detect_language_with_whisper_for_processor
    LANGUAGE_DETECTOR_AVAILABLE = True
except Exception:
    LANGUAGE_DETECTOR_AVAILABLE = False
    set_cached_language = None
    detect_language_with_whisper_for_processor = None

try:
    from mediajelly_config import get_config
except Exception:
    get_config = None

# Configuración optimizada de constantes
MAX_MEMORY_GB = 4
FFMPEG_TIMEOUT = 14400  # 4 horas por archivo
COMPRESSED_FILE_SUFFIX = ".compressed.mp4"
STREAM_LANGUAGE_QUERY = "stream=index:stream_tags=language"
CSV_FORMAT_PARAM = "csv=p=0"
FIRST_AUDIO_STREAM = "0:a:0"
PROGRESS_FILE_NAME = "progress.json"
FAILED_COMPRESSION_FILE = "failed-compression.txt"
VAAPI_DEVICE_PATH = "/dev/dri/renderD128"
LOG_RETENTION_DAYS = 7  # Reducido para ahorrar espacio
SHORT_TIMEOUT = 10
MEDIUM_TIMEOUT = 300  # Aumentado para probes más largos
PROCESSING_COMPLETED_MESSAGE = "Procesamiento completado"
EXCLUDED_FOLDERS = MediaJellyPaths.EXCLUDED_FOLDERS
MOVFLAGS_FASTSTART = "+faststart"


def get_optimal_workers() -> int:
    """
    Calcula el número óptimo de workers basado en recursos disponibles.

    Returns:
        Número óptimo de workers para procesamiento concurrente
    """
    if not PSUTIL_AVAILABLE or psutil is None:
        # Fallback a configuración conservadora si psutil no está disponible
        return 1

    try:
        # Obtener información del sistema
        available_ram_gb = psutil.virtual_memory().available / (1024**3)
        cpu_count = psutil.cpu_count(logical=True)  # Usar logical cores
        cpu_physical = psutil.cpu_count(logical=False)  # Physical cores

        # Calcular límites basados en RAM (4GB por worker)
        max_by_ram = int((available_ram_gb - 2) / 4)  # Reservar 2GB para sistema

        # Calcular límites basados en CPU (dejar 1 core libre para sistema)
        max_by_cpu = max(1, cpu_physical - 1)

        # Tomar el mínimo de ambos límites
        optimal_workers = max(1, min(max_by_ram, max_by_cpu))

        # Limitar a un máximo razonable para evitar sobrecarga
        optimal_workers = min(optimal_workers, 4)

        return optimal_workers

    except Exception:
        # En caso de error, usar configuración conservadora
        return 1


@dataclass
class ProcessingStats:
    """Estadísticas de procesamiento"""

    files_found: int = 0
    files_new: int = 0
    files_processed: int = 0
    files_compressed: int = 0
    files_renamed: int = 0
    files_skipped: int = 0
    total_original_size: int = 0
    total_compressed_size: int = 0
    errors: Optional[List[str]] = None
    no_spanish: Optional[List[str]] = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = []
        if self.no_spanish is None:
            self.no_spanish = []


class MediaJellyProcessor:
    """Procesador principal de MediaJelly"""

    def __init__(self):
        # Detecta el entorno
        self.is_container = Path("/mediajelly").exists()
        self.base_dir = Path("/mediajelly" if self.is_container else "/home/tafurc/mediaJelly")
        self.media_dir = self.base_dir / "media"
        self.scripts_dir = self.base_dir / "scripts"
        self.logs_dir = self.scripts_dir / "logs"
        self.tmp_dir = self.scripts_dir / "tmp"

        # Archivos
        self.pending_file = self.tmp_dir / "pending-compression.txt"
        self.completed_file = self.tmp_dir / "completed.txt"
        self.failed_file = self.tmp_dir / FAILED_COMPRESSION_FILE

        # Logs
        self.setup_logging()

        # Crea los directorios
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        self.completed_file.touch()

        # Estadísticas
        self.stats = ProcessingStats()

        # Archivos procesados con estado. Cada entrada guarda al menos
        # {'status': 'success', 'timestamp': ..., 'label': '.mp4'}.
        # La etiqueta se usa para detectar cambios en el tipo de archivo.
        self.processed_files = {}

        # Cargar configuración general si está disponible; guardarla para
        # usos posteriores.
        self.config = None
        if get_config:
            try:
                self.config = get_config()
            except Exception:
                self.config = None

        # Mejorador de calidad
        self.quality_improver = VideoQualityImprover(self.logger)

        # Caché de idiomas pre-detectados (llenado por mediajelly_language_detector.py)
        # Determinar ruta del caché, preferir configuración si está disponible
        cache_file_path = None
        if get_config:
            try:
                config = get_config()
                cache_file_path = Path(config.paths.cache_file_path)
            except Exception:
                cache_file_path = None

        if cache_file_path:
            # Si no se puede crear el directorio en la ruta de config, usar tmp local
            try:
                cache_file_path.parent.mkdir(parents=True, exist_ok=True)
                self.language_cache_file = cache_file_path
            except Exception:
                self.language_cache_file = self.tmp_dir / "language_cache.json"
        else:
            self.language_cache_file = self.tmp_dir / "language_cache.json"
        self.language_cache = self._load_language_cache()

        # Modelo Whisper deshabilitado (se usa pre-análisis)
        self.whisper_model = None
        self.whisper_enabled = False
        self.logger.info("🎙️ Detección de idioma: usando caché pre-analizado")

    def setup_logging(self):
        """Configura logging estructurado con rotación automática"""
        # Limpia los handlers existentes
        for handler in logging.root.handlers[:]:
            logging.root.removeHandler(handler)

        # Configuración del logger principal
        self.logger = logging.getLogger("mediajelly")
        self.logger.setLevel(logging.INFO)
        self.logger.handlers.clear()

        # Configura rotación de logs (máximo 10MB por archivo, mantiene 5 archivos)
        max_bytes = 10 * 1024 * 1024  # Define 10MB como límite
        backup_count = 5

        # Configuración del handler para compression-success.log con rotación
        success_handler = logging.handlers.RotatingFileHandler(
            self.logs_dir / "compression-success.log", maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
        )
        success_handler.setLevel(logging.INFO)
        success_formatter = logging.Formatter("[%(asctime)s] %(message)s", "%Y-%m-%d %H:%M:%S")
        success_handler.setFormatter(success_formatter)

        # Configuración del handler para compression-errors.log con rotación
        error_handler = logging.handlers.RotatingFileHandler(
            self.logs_dir / "compression-errors.log", maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
        )
        error_handler.setLevel(logging.ERROR)
        error_formatter = logging.Formatter("[%(asctime)s] ERROR: %(message)s", "%Y-%m-%d %H:%M:%S")
        error_handler.setFormatter(error_formatter)

        # Configuración del handler para no-spanish.log con rotación
        self.no_spanish_logger = logging.getLogger("mediajelly.no_spanish")
        self.no_spanish_logger.setLevel(logging.INFO)
        self.no_spanish_logger.handlers.clear()
        self.no_spanish_logger.propagate = False  # No heredar handlers del padre
        no_spanish_handler = logging.handlers.RotatingFileHandler(
            self.logs_dir / "no-spanish.log", maxBytes=max_bytes, backupCount=backup_count, encoding="utf-8"
        )
        no_spanish_formatter = logging.Formatter("[%(asctime)s] SIN ESPAÑOL: %(message)s", "%Y-%m-%d %H:%M:%S")
        no_spanish_handler.setFormatter(no_spanish_formatter)

        # Adición de los handlers
        self.logger.addHandler(success_handler)
        self.logger.addHandler(error_handler)
        self.no_spanish_logger.addHandler(no_spanish_handler)

        # Limpieza de logs antiguos al inicio (más de 30 días)
        self.cleanup_old_logs()

    def cleanup_old_logs(self):
        """Limpia archivos de log antiguos (más de 30 días)"""
        try:
            import time

            current_time = time.time()
            cutoff_time = current_time - (LOG_RETENTION_DAYS * 24 * 60 * 60)  # Calcula LOG_RETENTION_DAYS días atrás

            # Busca y elimina logs antiguos
            for log_file in self.logs_dir.glob("*.log*"):
                try:
                    if log_file.stat().st_mtime < cutoff_time:
                        self.logger.info(f"Eliminando log antiguo: {log_file.name}")
                        log_file.unlink()
                except Exception as e:
                    # Evitar logging aquí para prevenir recursión
                    print(f"Error eliminando log {log_file}: {e}")

        except Exception as e:
            print(f"Error en limpieza de logs: {e}")

    def _is_file_in_excluded_folder(self, file_path: Path) -> bool:
        """Verifica si un archivo está en una carpeta excluida"""
        return any(part.lower() in EXCLUDED_FOLDERS for part in file_path.parts)

    def set_resource_limits(self):
        """Establecimiento de límites de recursos del proceso"""
        try:
            # Límite de memoria (en bytes)
            memory_limit = MAX_MEMORY_GB * 1024 * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (memory_limit, memory_limit))

            # Límite de tiempo de CPU (en segundos)
            resource.setrlimit(resource.RLIMIT_CPU, (FFMPEG_TIMEOUT, FFMPEG_TIMEOUT))

            self.logger.info(f"Límites de recursos establecidos: {MAX_MEMORY_GB}GB RAM, {FFMPEG_TIMEOUT}s CPU")
        except Exception as e:
            self.logger.warning(f"No se pudieron establecer límites de recursos: {e}")

    def _save_progress_state(
        self, current_file: int, total_files: int, current_file_name: str, status: str = "processing"
    ) -> None:
        """Guarda el estado actual del progreso

        Args:
            current_file: Número de archivos procesados actualmente
            total_files: Total de archivos válidos para procesar (filtrados)
            current_file_name: Nombre del archivo actual o mensaje de estado
            status: Estado del procesamiento (scanning, processing, completed, error)
        """
        progress_file = self.tmp_dir / PROGRESS_FILE_NAME

        # Leer progreso existente para preservar otras secciones
        existing_progress = {}
        if progress_file.exists():
            try:
                with open(progress_file, "r") as f:
                    existing_progress = json.load(f)
            except Exception:
                existing_progress = {}

        # Resetear progreso si está corrupto (porcentaje > 100%)
        if self._reset_progress_if_corrupted(existing_progress.get("processing", {})):
            # Recargar progreso después del reset
            try:
                with open(progress_file, "r") as f:
                    existing_progress = json.load(f)
            except Exception:
                existing_progress = {}

        # Calcular archivos ya procesados (de processed_files)
        num_processed_files = len(existing_progress.get("processing", {}).get("processed_files", {}))

        # Calcular archivos nuevos: archivos escaneados que NO están en processed_files
        files_new = total_files - num_processed_files if total_files > num_processed_files else 0

        # Calcula porcentaje basado en archivos procesados vs total escaneados
        raw_percentage = round((current_file / total_files) * 100, 1) if total_files > 0 else 0
        percentage = min(100.0, raw_percentage)

        # Verificar si el progreso excede 100% y enviar notificación si no se ha enviado
        if raw_percentage > 100 and not existing_progress.get("processing", {}).get("over_100_notified", False):
            self._send_over_100_notification(raw_percentage, current_file, total_files, files_new)
            existing_progress["processing"]["over_100_notified"] = True

        # Asegurar que existe la sección processing con estructura completa
        if "processing" not in existing_progress:
            existing_progress["processing"] = {
                "current_file": 0,
                "total_files": 0,
                "current_file_name": "",
                "percentage": 0.0,
                "last_updated": datetime.now().isoformat(),
                "status": "idle",
                "notified": False,
                "over_100_notified": False,
                "stats": {
                    "files_found": 0,
                    "files_new": 0,
                    "files_processed": 0,
                    "files_compressed": 0,
                    "files_renamed": 0,
                    "files_skipped": 0,
                    "total_original_size": 0,
                    "total_compressed_size": 0,
                    "errors": [],
                    "no_spanish": [],
                },
                "processed_files": {},
                "last_notification": None,
                "last_cleanup_notification": None,
            }

        # Asegurar que existen los campos last_notification si no están
        if "last_notification" not in existing_progress["processing"]:
            existing_progress["processing"]["last_notification"] = None
        if "last_cleanup_notification" not in existing_progress["processing"]:
            existing_progress["processing"]["last_cleanup_notification"] = None

        # Actualizar la sección processing
        existing_progress["processing"].update(
            {
                "current_file": current_file,
                "total_files": total_files,  # Total de archivos escaneados
                "current_file_name": current_file_name,
                "percentage": percentage,
                "last_updated": datetime.now().isoformat(),
                "status": status,  # scanning, processing, completed, error
                "notified": False,  # Indica si ya se envió notificación para esta ejecución
                "over_100_notified": existing_progress["processing"].get("over_100_notified", False),
            }
        )

        # Actualizar files_new y files_found en las estadísticas
        self.stats.files_new = files_new
        self.stats.files_found = total_files

        # Actualizar estadísticas en la sección processing
        existing_progress["processing"]["stats"] = asdict(self.stats)
        existing_progress["processing"]["processed_files"] = self.processed_files

        try:
            with open(progress_file, "w") as f:
                json.dump(existing_progress, f, indent=2)
        except Exception as e:
            self.logger.warning(f"Error guardando progreso: {e}")

    def get_progress_info(self) -> Dict:
        """Obtiene información del progreso actual"""
        progress_file = self.tmp_dir / PROGRESS_FILE_NAME
        try:
            if progress_file.exists():
                with open(progress_file, "r") as f:
                    data = json.load(f)

                    # Si existe la sección processing, devolver esa información
                    if "processing" in data:
                        processing_data = data["processing"].copy()
                        # Asegura que tenga el campo status
                        if "status" not in processing_data:
                            processing_data["status"] = "unknown"
                        return processing_data
                    else:
                        # Fallback para compatibilidad con estructura antigua
                        if "status" not in data:
                            data["status"] = "unknown"
                        return data
        except Exception as e:
            self.logger.warning(f"Error leyendo progreso: {e}")

        # Retornar estructura por defecto para la sección processing
        return {
            "current_file": 0,
            "total_files": 0,
            "current_file_name": "N/A",
            "percentage": 0,
            "last_updated": None,
            "status": "idle",
            "notified": False,
            "stats": asdict(self.stats),
            "processed_files": {},
        }

    def _check_hardware_acceleration_available(self) -> bool:
        """Verifica si la aceleración por hardware está disponible"""
        try:
            # Verifica dispositivo VAAPI
            if not Path(VAAPI_DEVICE_PATH).exists():
                self.logger.warning("Dispositivo VAAPI no encontrado")
                return False

            # Test simplificado: verificación de que ffmpeg pueda abrir el dispositivo VAAPI
            test_cmd = ["ffmpeg", "-hide_banner", "-hwaccels"]

            result = subprocess.run(
                test_cmd, capture_output=True, timeout=SHORT_TIMEOUT, encoding="utf-8", errors="replace"
            )

            if result.returncode == 0 and "vaapi" in result.stdout:
                self.logger.info("Aceleración VAAPI disponible en FFmpeg")
                return True
            else:
                self.logger.warning("VAAPI no disponible en FFmpeg")
                return False

        except Exception as e:
            self.logger.warning(f"Error verificando aceleración de hardware: {e}")
            return False

    def _remove_orphaned_compressed_files(self) -> int:
        """Elimina TODOS los archivos .compressed.mp4 al inicio del procesamiento.
        Estos archivos indican procesos interrumpidos/corruptos del procesamiento anterior.
        Retorna el número de archivos eliminados.
        """
        compressed_files = list(self.media_dir.rglob(f"*{COMPRESSED_FILE_SUFFIX}"))
        files_removed = 0

        for compressed_file in compressed_files:
            try:
                # Excluir archivos en carpetas de backup/eliminación
                if any(exclude in str(compressed_file) for exclude in ["/Bittorrent/", "/.Trash/", "/backup/"]):
                    continue

                file_size_mb = compressed_file.stat().st_size / (1024 * 1024)
                self.logger.warning(
                    f"Eliminando archivo .compressed.mp4 huérfano de proceso anterior: "
                    f"{compressed_file.name} ({file_size_mb:.1f}MB)"
                )
                compressed_file.unlink()
                files_removed += 1

            except Exception as e:
                self.logger.error(f"Error eliminando {compressed_file}: {e}")

        if files_removed > 0:
            self.logger.info(f"Total de archivos .compressed.mp4 huérfanos eliminados: {files_removed}")

        return files_removed

    def _normalize_filename(self, file_path: Path) -> Optional[Path]:
        """Normaliza el nombre del archivo eliminando prefijos fansub y normalizando formato de temporada/episodio"""
        import re

        # DETECTAR SI ES PELÍCULA: no aplicar normalización de temporada/episodio
        is_movie = any(part.lower() in ["peliculas", "movies"] for part in file_path.parts)

        original_name = file_path.stem
        extension = file_path.suffix
        normalized_name = original_name

        # 1. Eliminar prefijos de grupos fansub
        normalized_name = self._remove_fansub_prefixes(normalized_name)

        # 2. Normalizar formato de temporada/episodio - SOLO PARA SERIES
        if not is_movie:
            normalized_name = self._normalize_season_episode_format(normalized_name)

        # 3. Limpiar espacios y guiones
        normalized_name = self._clean_filename_formatting(normalized_name)

        # Si el nombre cambió, renombrar el archivo
        if normalized_name != original_name:
            return self._rename_file_safely(file_path, normalized_name, extension)

        return None

    def _remove_fansub_prefixes(self, filename: str) -> str:
        """Elimina prefijos de grupos fansub del nombre del archivo"""
        import re

        fansub_patterns = [
            r"^\[([^\]]+)\]\s*",  # [Grupo]
            r"^\(([^\)]+)\)\s*",  # (Grupo)
            r"^\{([^\}]+)\}\s*",  # {Grupo}
        ]

        for pattern in fansub_patterns:
            match = re.match(pattern, filename)
            if match:
                group_name = match.group(1)
                rest_of_name = filename[match.end() :]
                if rest_of_name and not rest_of_name.lower().startswith(group_name.lower()):
                    return rest_of_name.strip()

        return filename

    def _normalize_season_episode_format(self, filename: str) -> str:
        """Normaliza el formato de temporada/episodio en nombres de series"""
        import re

        # Intentar diferentes patrones de normalización
        normalized = self._try_normalize_sxe_pattern(filename)
        if normalized != filename:
            return normalized

        normalized = self._try_normalize_season_dash_pattern(filename)
        if normalized != filename:
            return normalized

        normalized = self._try_normalize_season_episode_pattern(filename)
        if normalized != filename:
            return normalized

        normalized = self._try_normalize_xxy_pattern(filename)
        if normalized != filename:
            return normalized

        normalized = self._try_normalize_episode_only_pattern(filename)
        if normalized != filename:
            return normalized

        return filename

    def _try_normalize_sxe_pattern(self, filename: str) -> str:
        """Intenta normalizar patrón S02E10 (ya normalizado)"""
        import re

        pattern = r"\bS(\d+)E(\d+)\b"
        match = re.search(pattern, filename, re.IGNORECASE)
        if match:
            return self._build_normalized_filename(filename, match, match.groups())
        return filename

    def _try_normalize_season_dash_pattern(self, filename: str) -> str:
        """Intenta normalizar patrón Season 2 - 10"""
        import re

        pattern = r"\bSeason\s+(\d+)\s*-\s*(\d+)\b"
        match = re.search(pattern, filename, re.IGNORECASE)
        if match:
            return self._build_normalized_filename(filename, match, match.groups())
        return filename

    def _try_normalize_season_episode_pattern(self, filename: str) -> str:
        """Intenta normalizar patrón Season 2 Episode 10"""
        import re

        pattern = r"\bSeason\s+(\d+)\s+Episode\s+(\d+)\b"
        match = re.search(pattern, filename, re.IGNORECASE)
        if match:
            return self._build_normalized_filename(filename, match, match.groups())
        return filename

    def _try_normalize_xxy_pattern(self, filename: str) -> str:
        """Intenta normalizar patrón 2x10"""
        import re

        pattern = r"\b(\d+)x(\d+)\b"
        match = re.search(pattern, filename, re.IGNORECASE)
        if match:
            return self._build_normalized_filename(filename, match, match.groups())
        return filename

    def _try_normalize_episode_only_pattern(self, filename: str) -> str:
        """Intenta normalizar patrón E10 (sin Season)"""
        import re

        pattern = r"\bE(\d+)\b(?!.*season)(?!.*S\d+)"
        match = re.search(pattern, filename, re.IGNORECASE)
        if match:
            season = "01"
            episode = match.group(1).zfill(2)
            return self._build_normalized_filename(filename, match, (season, episode))
        return filename

    def _build_normalized_filename(self, filename: str, match, groups) -> str:
        """Construye el nombre de archivo normalizado"""
        if len(groups) == 2:
            season = groups[0].zfill(2)
            episode = groups[1].zfill(2)
        elif len(groups) == 1:
            season = "01"
            episode = groups[0].zfill(2)
        else:
            return filename

        # Construir nombre normalizado
        before_match = filename[: match.start()].strip()
        after_match = filename[match.end() :].strip()

        if before_match and after_match:
            return f"{before_match} S{season}E{episode} {after_match}"
        elif before_match:
            return f"{before_match} S{season}E{episode}"
        else:
            return f"S{season}E{episode} {after_match}" if after_match else f"S{season}E{episode}"

    def _clean_filename_formatting(self, filename: str) -> str:
        """Limpia espacios múltiples, guiones redundantes y formato del nombre"""
        import re

        # Limpiar espacios y guiones
        filename = re.sub(r"\s*-\s*(S\d+E\d+)\s*-\s*", r" \1 ", filename)
        filename = re.sub(r"\s+", " ", filename)
        filename = re.sub(r"\s*-\s*-\s*", " - ", filename)
        filename = re.sub(r"\s*-\s*$", "", filename)
        filename = re.sub(r"^\s*-\s*", "", filename)
        return filename.strip()

    def _rename_file_safely(self, file_path: Path, new_name: str, extension: str) -> Optional[Path]:
        """Renombra un archivo de forma segura verificando conflictos"""
        new_path = file_path.parent / f"{new_name}{extension}"

        if new_path.exists():
            self.logger.warning(f"No se puede renombrar a '{new_path.name}': el archivo ya existe")
            return None

        try:
            file_path.rename(new_path)
            self.logger.info(f"✓ Archivo renombrado: '{file_path.name}' -> '{new_path.name}'")
            return new_path
        except Exception as e:
            self.logger.error(f"Error renombrando archivo: {e}")
            return None

    def _clean_incomplete_compressed_files(self) -> Tuple[int, int]:
        """Limpia archivos .compressed.mp4 incompletos/corruptos y retorna (files_cleaned, files_added_to_pending)
        Se ejecuta siempre para mantener el sistema limpio.
        Detecta archivos corruptos mediante: tamaño < 10MB o validación con ffprobe
        """
        # Excluir archivos en carpetas temporales o de eliminación
        compressed_files_found = [
            f for f in self.media_dir.rglob(f"*{COMPRESSED_FILE_SUFFIX}") if not self._is_file_in_excluded_folder(f)
        ]

        if len(compressed_files_found) == 0:
            return 0, 0

        self.logger.info(
            f"{EmojiGenerator.magnifying_glass()} Buscando archivos {COMPRESSED_FILE_SUFFIX} incompletos/corruptos... Encontrados: {len(compressed_files_found)}"
        )

        files_cleaned = 0
        files_added_to_pending = 0
        failed_files_path = self.failed_file

        # Carga archivos que han fallado previamente
        failed_files = self._load_failed_files(failed_files_path)

        for compressed_file in compressed_files_found:
            try:
                # Verifica si el archivo está corrupto
                is_corrupt = False
                corrupt_reason = ""

                # Verificación 1: Tamaño muy pequeño (< 10MB es sospechoso para archivos de video)
                file_size = compressed_file.stat().st_size
                if file_size < 10 * 1024 * 1024:  # 10MB
                    is_corrupt = True
                    corrupt_reason = f"muy pequeño ({file_size / (1024*1024):.1f}MB)"

                # Verificación 2: Validación con ffprobe si no es obviamente pequeño
                if not is_corrupt:
                    try:
                        probe_cmd = [
                            "ffprobe",
                            "-v",
                            "error",
                            "-show_entries",
                            "format=duration",
                            "-of",
                            "default=noprint_wrappers=1:nokey=1",
                            str(compressed_file),
                        ]
                        probe_result = subprocess.run(
                            probe_cmd, capture_output=True, timeout=10, encoding="utf-8", errors="replace"
                        )

                        # Si ffprobe falla o no retorna duración válida, está corrupto
                        if probe_result.returncode != 0:
                            is_corrupt = True
                            corrupt_reason = "no válido para ffprobe"
                        else:
                            duration_str = probe_result.stdout.strip()
                            if not duration_str or duration_str == "N/A":
                                is_corrupt = True
                                corrupt_reason = "sin duración válida"
                    except subprocess.TimeoutExpired:
                        is_corrupt = True
                        corrupt_reason = "timeout en validación"
                    except Exception as e:
                        is_corrupt = True
                        corrupt_reason = f"error de validación: {str(e)}"

                # Si el archivo NO está corrupto, saltarlo
                if not is_corrupt:
                    continue

                # ARCHIVO CORRUPTO DETECTADO - Buscar el original
                self.logger.warning(
                    f"{EmojiGenerator.wastebasket()}  Archivo corrupto detectado ({corrupt_reason}): {compressed_file.name}"
                )

                base_name = compressed_file.name.replace(COMPRESSED_FILE_SUFFIX, "")
                parent_dir = compressed_file.parent

                # Busca archivos con el mismo nombre base pero diferente extensión
                pattern = f"{base_name}.*"
                original_candidates = list(parent_dir.glob(pattern))

                # Filtrado para encontrar el archivo original (no .compressed.mp4)
                original_files = [f for f in original_candidates if not f.name.endswith(COMPRESSED_FILE_SUFFIX)]

                if original_files:
                    original_file = original_files[0]  # Toma el primero encontrado
                    original_file_str = str(original_file)

                    # Verifica si este archivo ya falló previamente
                    if original_file_str in failed_files:
                        self.logger.warning(f"Archivo ya marcado como fallido, omitiendo: {original_file.name}")
                        compressed_file.unlink()  # Elimina el .compressed.mp4 pero NO lo reagrega a pendientes
                        files_cleaned += 1
                        continue

                    self.logger.info(f"{EmojiGenerator.refresh()} Archivo original encontrado: {original_file.name}")

                    # Elimina el archivo corrupto
                    compressed_file.unlink()
                    files_cleaned += 1

                    # SIEMPRE agregar de vuelta a pendientes cuando eliminamos un archivo corrupto
                    # Esto asegura que el archivo original se reprocese
                    if original_file_str not in failed_files:
                        self._add_to_pending(original_file)
                        files_added_to_pending += 1
                        self.logger.info(
                            f"{EmojiGenerator.success()} Archivo agregado de vuelta a pendientes: {original_file.name}"
                        )

                else:
                    # No encuentra archivo original, elimina el temporal huérfano
                    self.logger.warning(
                        f"{EmojiGenerator.wastebasket()}  Archivo corrupto huérfano eliminado: {compressed_file.name}"
                    )
                    compressed_file.unlink()
                    files_cleaned += 1

            except Exception as e:
                self.logger.error(f"Error procesando archivo {compressed_file}: {e}")

        if files_cleaned > 0:
            self.logger.info(
                f"{EmojiGenerator.cleanup()} Limpieza completada: {files_cleaned} archivos corruptos eliminados, {files_added_to_pending} agregados a pendientes"
            )

        return files_cleaned, files_added_to_pending

    def _load_failed_files(self, failed_files_path: Path) -> set:
        """Carga la lista de archivos que han fallado en compresión"""
        failed_files = set()
        if failed_files_path.exists():
            with open(failed_files_path, "r") as f:
                for line in f:
                    file_path = line.strip()
                    if file_path:
                        failed_files.add(file_path)
        return failed_files

    def _load_completed_files(self) -> set:
        """Carga archivos completados para evitar reprocesamiento"""
        completed_files = set()
        if self.completed_file.exists():
            with open(self.completed_file, "r") as f:
                for line in f:
                    file_path = line.strip()
                    if file_path:
                        completed_files.add(file_path)
        return completed_files

    def _get_pending_files(self) -> List[Path]:
        """Obtiene archivos pendientes de procesamiento y normaliza sus nombres"""
        if not self.pending_file.exists():
            return []

        pending_files = []
        files_renamed: List[Tuple[str, str]] = []
        invalid_files: List[str] = []

        # Procesar cada línea del archivo pending
        with open(self.pending_file, "r") as f:
            for line in f:
                file_path = line.strip()
                if not file_path:
                    continue

                processed_file = self._process_pending_file_line(file_path, invalid_files, files_renamed)
                if processed_file:
                    pending_files.append(processed_file)

        # Limpiar archivos inválidos del pending
        if invalid_files:
            self._remove_invalid_files_from_pending(invalid_files)
            self.logger.info(f"Removidos {len(invalid_files)} archivos no-video de pending-compression.txt")

        # Actualizar pending después de renombramientos
        if files_renamed:
            self._update_pending_after_rename(files_renamed)

        return pending_files

    def _process_pending_file_line(
        self, file_path: str, invalid_files: List[str], files_renamed: List[Tuple[str, str]]
    ) -> Optional[Path]:
        """Procesa una línea individual del archivo pending"""
        original_path = Path(file_path)

        # Verificar que el archivo existe
        if not original_path.exists():
            return None

        # Filtrar archivos que no son videos
        if not self._is_video_file(original_path):
            invalid_files.append(str(original_path))
            self.logger.warning(
                f"Archivo no es video, removiendo de pending: {original_path.name} (extensión: {original_path.suffix})"
            )
            return None

        # Normalizar nombre del archivo
        new_path = self._normalize_filename(original_path)

        if new_path and new_path != original_path:
            # El archivo fue renombrado
            files_renamed.append((str(original_path), str(new_path)))
            return new_path
        else:
            # No se renombró, usar el path original
            return original_path

    def _is_video_file(self, file_path: Path) -> bool:
        """Verifica si un archivo es un video válido para procesamiento"""
        VALID_VIDEO_EXTENSIONS = {".mkv", ".avi", ".ts", ".mp4", ".mov", ".wmv", ".flv", ".webm", ".m4v"}
        return file_path.suffix.lower() in VALID_VIDEO_EXTENSIONS

    def _remove_invalid_files_from_pending(self, invalid_files: List[str]) -> None:
        """Remueve archivos inválidos (no-video) del archivo pending-compression.txt"""
        if not self.pending_file.exists():
            return

        # Convertir lista de inválidos a set para búsqueda rápida
        invalid_set = set(invalid_files)

        # Leer todas las líneas válidas
        valid_lines = []
        with open(self.pending_file, "r") as f:
            for line in f:
                line_clean = line.strip()
                if line_clean and line_clean not in invalid_set:
                    valid_lines.append(line_clean)

        # Reescribir el archivo solo con líneas válidas
        with open(self.pending_file, "w") as f:
            for line in valid_lines:
                f.write(f"{line}\n")

    def _update_pending_after_rename(self, renamed_files: List[Tuple[str, str]]) -> None:
        """Actualiza pending-compression.txt después de renombrar archivos"""
        if not self.pending_file.exists():
            return

        # Leer todas las líneas
        with open(self.pending_file, "r") as f:
            lines = [line.strip() for line in f if line.strip()]

        # Crear diccionario de renombramientos
        rename_map = dict(renamed_files)

        # Actualizar las líneas
        updated_lines = []
        for line in lines:
            if line in rename_map:
                updated_lines.append(rename_map[line])
            else:
                updated_lines.append(line)

        # Reescribir el archivo
        with open(self.pending_file, "w") as f:
            for line in updated_lines:
                f.write(f"{line}\n")

        self.logger.info(f"Archivo pending actualizado con {len(renamed_files)} renombramientos")

    def _update_pending_file(self, old_path: str, new_path: str) -> None:
        """Actualiza un archivo individual en pending-compression.txt después de renombrarlo"""
        if not self.pending_file.exists():
            return

        # Leer todas las líneas
        with open(self.pending_file, "r") as f:
            lines = [line.strip() for line in f if line.strip()]

        # Actualizar la línea correspondiente
        updated_lines = []
        for line in lines:
            if line == old_path:
                updated_lines.append(new_path)
                self.logger.debug(f"Actualizado en pending: {old_path} -> {new_path}")
            else:
                updated_lines.append(line)

        # Reescribir el archivo
        with open(self.pending_file, "w") as f:
            for line in updated_lines:
                f.write(f"{line}\n")

    def _clean_pending_duplicates(self) -> int:
        """Limpia duplicados del archivo pending-compression.txt"""
        if not self.pending_file.exists():
            return 0

        unique_files = set()
        duplicates_removed = 0

        # Lee el archivo y mantiene solo rutas únicas
        with open(self.pending_file, "r") as f:
            for line in f:
                line_clean = line.strip()
                if line_clean:
                    # Normaliza la ruta para comparación consistente
                    normalized_path = str(Path(line_clean).resolve())
                    if normalized_path in unique_files:
                        duplicates_removed += 1
                        self.logger.info(f"Duplicado encontrado y eliminado: {line_clean}")
                    else:
                        unique_files.add(normalized_path)

        # Reescribe el archivo sin duplicados
        if duplicates_removed > 0:
            with open(self.pending_file, "w") as f:
                for file_path in sorted(unique_files):
                    f.write(f"{file_path}\n")
            self.logger.info(f"Limpieza completada: {duplicates_removed} duplicados eliminados")

        return duplicates_removed

    def _add_to_pending(self, file_path: Union[str, Path]) -> None:
        """Agrega un archivo a la cola de pendientes de compresión"""
        file_path_str = str(file_path) if isinstance(file_path, Path) else file_path

        # Verificar si ya existe en el archivo
        existing_files = set()
        if self.pending_file.exists():
            with open(self.pending_file, "r") as f:
                existing_files = {line.strip() for line in f if line.strip()}

        # Agregar solo si no existe
        if file_path_str not in existing_files:
            with open(self.pending_file, "a") as f:
                f.write(f"{file_path_str}\n")
            self.logger.debug(f"Archivo agregado a pendientes: {file_path_str}")
        else:
            self.logger.debug(f"Archivo ya existe en pendientes: {file_path_str}")

    def scan_media_files(self) -> List[Path]:
        """Escanea archivos multimedia con pathlib optimizado"""
        self.logger.info("Iniciando escaneo de archivos multimedia...")

        # Verifica si hay progreso anterior incompleto
        existing_progress = self.get_progress_info()
        has_incomplete_progress = self._has_incomplete_progress(existing_progress)

        if has_incomplete_progress:
            self._handle_incomplete_progress(existing_progress)
        else:
            # Actualiza estado de progreso como escaneando
            self._save_progress_state(0, 1, "Escaneando archivos multimedia...", status="scanning")

        # Limpia archivos .compressed.mp4 incompletos
        self._clean_incomplete_compressed_files()

        # Carga de archivos completados para evitar reprocesamiento
        completed_files = self._load_completed_files()
        self.logger.info(f"Archivos completados cargados: {len(completed_files)}")

        # Carga archivos pendientes
        pending_files = self._get_pending_files()

        # Filtra archivos ya completados
        filtered_files = self._filter_completed_files(pending_files, completed_files)

        # Actualizar files_found con los archivos válidos filtrados
        self.stats.files_found = len(filtered_files)

        self.logger.info(f"Escaneo completado: {len(filtered_files)} archivos pendientes para procesar")

        # Actualiza files_found en el progreso
        self._update_progress_files_found(filtered_files, has_incomplete_progress)

        return filtered_files

    def _has_incomplete_progress(self, existing_progress: dict) -> bool:
        """Verifica si hay progreso anterior incompleto"""
        return (
            existing_progress.get("status") in ["processing", "scanning", "interrupted"]
            and existing_progress.get("percentage", 0) < 90
        )

    def _handle_incomplete_progress(self, existing_progress: dict):
        """Maneja el caso de progreso anterior incompleto"""
        self.logger.info(
            f"Detectado progreso anterior incompleto ({existing_progress.get('percentage', 0):.1f}%), preservando estado"
        )
        # No actualizar estado de progreso para no sobrescribir el anterior

    def _filter_completed_files(self, pending_files: List[Path], completed_files: set) -> List[Path]:
        """Filtra archivos que ya han sido completados"""
        filtered_files = []
        for file_path in pending_files:
            if str(file_path) not in completed_files:
                filtered_files.append(file_path)
            else:
                self.logger.info(f"Archivo ya procesado, omito: {file_path.name}")
        return filtered_files

    def _update_progress_files_found(self, filtered_files: List[Path], has_incomplete_progress: bool):
        """Actualiza la cantidad de archivos encontrados en el progreso"""
        if has_incomplete_progress:
            self._update_files_found_in_existing_progress(len(filtered_files))
        else:
            self._set_normal_progress_state(filtered_files)

    def _update_files_found_in_existing_progress(self, files_count: int):
        """Actualiza files_found en progreso existente sin cambiar el estado"""
        progress_file = self.tmp_dir / PROGRESS_FILE_NAME
        try:
            existing_full_progress = {}
            if progress_file.exists():
                with open(progress_file, "r") as f:
                    existing_full_progress = json.load(f)

            # Asegurar que existe la sección processing
            if "processing" not in existing_full_progress:
                existing_full_progress["processing"] = self._create_default_progress_structure()

            existing_full_progress["processing"]["stats"]["files_found"] = files_count

            with open(progress_file, "w") as f:
                json.dump(existing_full_progress, f, indent=2)
        except Exception as e:
            self.logger.warning(f"Error actualizando files_found en progreso: {e}")

    def _set_normal_progress_state(self, filtered_files: List[Path]):
        """Establece el estado de progreso normal para ejecuciones nuevas"""
        # Usar solo los archivos filtrados válidos
        total_files = len(filtered_files)

        if len(filtered_files) > 0:
            self._save_progress_state(0, total_files, f"{len(filtered_files)} archivos encontrados", status="scanned")
        else:
            # Incluso si no hay archivos filtrados, usar total_files para mantener consistencia
            self._save_progress_state(0, total_files, "Escaneo completado", status="scanned")

    def _create_default_progress_structure(self) -> dict:
        """Crea la estructura de progreso por defecto"""
        return {
            "current_file": 0,
            "total_files": 0,
            "current_file_name": "",
            "percentage": 0.0,
            "last_updated": datetime.now().isoformat(),
            "status": "idle",
            "notified": False,
            "stats": {
                "files_found": 0,
                "files_new": 0,
                "files_processed": 0,
                "files_compressed": 0,
                "files_renamed": 0,
                "files_skipped": 0,
                "total_original_size": 0,
                "total_compressed_size": 0,
                "errors": [],
                "no_spanish": [],
            },
            "processed_files": {},
        }

    def _load_whisper_model(self):
        """Carga el modelo Whisper de forma lazy (solo cuando se necesita)"""
        if not self.whisper_enabled:
            return False

        if self.whisper_model is None:
            try:
                self.logger.info(f"{EmojiGenerator.refresh()} Cargando modelo Whisper (base)...")
                # Usar modelo 'base' (ya descargado - 140MB)
                # Se carga ANTES de iniciar compresiones para evitar conflictos de memoria
                self.whisper_model = whisper.load_model("base")
                self.logger.info(f"{EmojiGenerator.success()} Modelo Whisper cargado exitosamente")
                return True
            except Exception as e:
                import traceback

                self.logger.error(f"{EmojiGenerator.error()} Error cargando modelo Whisper: {e}")
                self.logger.error(f"Traceback: {traceback.format_exc()}")
                # Deshabilitar permanentemente para esta sesión si falla
                self.whisper_enabled = False
                self.whisper_model = None
                return False
        return True

    def _load_language_cache(self) -> dict:
        """Carga el caché de idiomas pre-detectados por mediajelly_language_detector.py"""
        if self.language_cache_file.exists():
            try:
                # Check if file is empty before parsing
                if self.language_cache_file.stat().st_size == 0:
                    self.logger.warning(f"{EmojiGenerator.warning_msg()} Caché de idiomas vacío, inicializando...")
                    return {}

                with open(self.language_cache_file, "r", encoding="utf-8") as f:
                    try:
                        cache = json.load(f)
                    except json.JSONDecodeError as e:
                        # Archivo corrupto: mover a backup y reinicializar caché
                        backup_path = self.language_cache_file.with_suffix(".corrupt.json")
                        try:
                            os.replace(str(self.language_cache_file), str(backup_path))
                            self.logger.warning(f"{EmojiGenerator.warning_msg()} Caché corrupto movido a {backup_path}")
                        except Exception:
                            self.logger.warning(f"{EmojiGenerator.warning_msg()} No se pudo mover caché corrupto: {e}")
                        return {}
                self.logger.info(f"{EmojiGenerator.folder()} Caché de idiomas cargado: {len(cache)} archivos")
                return cache
            except Exception as e:
                self.logger.warning(f"{EmojiGenerator.warning_msg()} Error cargando caché de idiomas: {e}")
        return {}

    def _ensure_three_letter_codes(self, audio_languages: Dict[int, str]) -> Dict[int, str]:
        """Asegura que los valores de audio_languages sean códigos ISO 639-2 (tres letras).

        Usa la configuración para invertir el mapeo si se encuentran códigos ISO 639-1 (dos letras).
        Agrega logging que permita depurar si los valores no están en el formato esperado.
        """
        if not audio_languages:
            return {}

        try:
            cfg = get_config() if get_config else None
            inv_map = {}
            if cfg:
                inv_map = {v: k for k, v in cfg.language_detection.language_code_map.items()}
            # Fallback a small default map en caso de no tener configuración
            if not inv_map:
                inv_map = {
                    'en': 'eng',
                    'es': 'spa',
                    'fr': 'fra',
                    'de': 'deu',
                    'it': 'ita',
                    'pt': 'por',
                    'ja': 'jpn',
                    'zh': 'chi',
                    'ru': 'rus',
                    'ar': 'ara',
                }

            new_map = {}
            for idx, val in audio_languages.items():
                if not val:
                    new_map[idx] = val
                    continue
                # val puede ser ya 3 letras (p.ej. 'eng') o 2 letras ('en')
                if len(val) == 3:
                    new_map[idx] = val
                elif len(val) == 2 and val in inv_map:
                    new_map[idx] = inv_map[val]
                    self.logger.info(
                        f"{EmojiGenerator.clipboard()} Mapeando código idioma 2-letras '{val}' a 3-letras '{new_map[idx]}' para stream {idx}"
                    )
                else:
                    # No se reconoce, dejar tal cual y avisar
                    new_map[idx] = val
                    self.logger.warning(
                        f"{EmojiGenerator.warning_msg()} Código idioma inesperado '{val}' para stream {idx}, no se pudo mapear a 3 letras"
                    )
            return new_map
        except Exception as e:
            self.logger.warning(f"{EmojiGenerator.warning_msg()} Error mapeando idiomas a 3-letras: {e}")
            return audio_languages

    def _detect_language_with_whisper(self, audio_file: Path, file_duration: float = 0) -> Tuple[Optional[str], Dict[str, float]]:
        """
        Detecta el idioma usando Whisper analizando el archivo de audio.
        Retorna el código ISO 639-2 del idioma detectado y las probabilidades.
        """
        try:
            if not self._load_whisper_model():
                return None, {}

            # Mapeo de códigos ISO 639-1 a ISO 639-2 (usado por ffmpeg)
            lang_map = {
                "es": "spa",
                "en": "eng",
                "ja": "jpn",
                "fr": "fra",
                "de": "deu",
                "it": "ita",
                "pt": "por",
                "ko": "kor",
                "zh": "chi",
                "ru": "rus",
                "ar": "ara",
            }

            # Cargar audio
            audio = whisper.load_audio(str(audio_file))
            audio = whisper.pad_or_trim(audio)

            # Convertir a mel spectrogram
            mel = whisper.log_mel_spectrogram(audio).to(self.whisper_model.device)

            # Detectar idioma
            _, probs = self.whisper_model.detect_language(mel)

            # Obtener probabilidades de idiomas relevantes
            spanish_prob = probs.get("es", 0.0)

            # Obtener top 5 idiomas
            sorted_langs = sorted(probs.items(), key=lambda x: x[1], reverse=True)[:5]
            detected_lang = sorted_langs[0][0]
            confidence = sorted_langs[0][1]

            # Log solo del resultado principal (no mostrar top-5 para evitar saturar logs)
            lang_code = lang_map.get(detected_lang, detected_lang)
            # self.logger.info(f"   Whisper detectó: {detected_lang} ({lang_code}): {confidence:.2%}")

            # Retornar idioma detectado y probabilidades para votación
            return detected_lang, probs

        except Exception as e:
            self.logger.error(f"Error en detección Whisper: {e}")
            return None, {}

    def _detect_language_with_multiple_samples(
        self, file_path: Path, stream_index: int, total_duration: float
    ) -> Optional[str]:
        """
        Detecta idioma extrayendo 10 muestras aleatorias de 60s y votando por el idioma más detectado.
        Guarda archivos temporales en tmp/ y los limpia al finalizar.
        """
        import random

        # Mapeo de códigos ISO 639-1 a ISO 639-2
        lang_map = {
            "es": "spa",
            "en": "eng",
            "ja": "jpn",
            "fr": "fra",
            "de": "deu",
            "it": "ita",
            "pt": "por",
            "ko": "kor",
            "zh": "chi",
            "ru": "rus",
            "ar": "ara",
        }

        try:
            # Verificar que el archivo tenga suficiente duración
            if total_duration < 120:  # Menos de 2 minutos
                self.logger.info(
                    f"{EmojiGenerator.warning_msg()} Archivo corto ({total_duration:.0f}s), usando muestra única"
                )
                num_samples = 1
                sample_duration = min(30, total_duration - 10)
            else:
                num_samples = min(10, int(total_duration / 60))  # Máximo 10 muestras
                sample_duration = 60

            self.logger.info(
                f"{EmojiGenerator.audio()} Analizando idioma con {num_samples} muestras de {sample_duration}s"
            )

            # Generar timestamps aleatorios (evitando primeros y últimos 30s)
            safe_start = 30
            safe_end = total_duration - 30 - sample_duration

            if safe_end <= safe_start:
                timestamps = [30]
            else:
                timestamps = sorted(
                    random.sample(range(int(safe_start), int(safe_end)), min(num_samples, int(safe_end - safe_start)))
                )

            # Contador de votos por idioma
            language_votes: Dict[str, int] = {}
            temp_files = []

            for i, start_time in enumerate(timestamps):
                # Crear archivo temporal en tmp/
                temp_audio_path = self.tmp_dir / f"whisper_sample_{file_path.stem}_stream{stream_index}_sample{i}.wav"
                temp_files.append(temp_audio_path)

                try:
                    # Extraer muestra de audio
                    extract_cmd = [
                        "ffmpeg",
                        "-hide_banner",
                        "-y",
                        "-ss",
                        str(start_time),
                        "-i",
                        str(file_path),
                        "-map",
                        f"0:a:{stream_index}",
                        "-t",
                        str(sample_duration),
                        "-ac",
                        "1",
                        "-ar",
                        "16000",
                        "-f",
                        "wav",
                        str(temp_audio_path),
                    ]

                    result = subprocess.run(extract_cmd, capture_output=True, timeout=MEDIUM_TIMEOUT)

                    if result.returncode != 0:
                        self.logger.warning(
                            f"{EmojiGenerator.warning_msg()} Error extrayendo muestra {i+1} en segundo {start_time}"
                        )
                        continue

                    # Detectar idioma con Whisper
                    detection_result = self._detect_language_with_whisper(temp_audio_path, total_duration)

                    if detection_result and len(detection_result) == 2:
                        detected_lang, probs = detection_result

                        # Votar por el idioma detectado
                        if detected_lang:
                            language_votes[detected_lang] = language_votes.get(detected_lang, 0) + 1
                            # Log simplificado (solo cuando cambia el idioma dominante o cada 3 muestras)
                            if i == 0 or i % 3 == 0 or detected_lang != list(language_votes.keys())[-1]:
                                self.logger.info(
                                    f"   Muestra {i+1}/{len(timestamps)}: {detected_lang} ({probs.get(detected_lang, 0):.1%})"
                                )

                except Exception as e:
                    self.logger.warning(f"{EmojiGenerator.warning_msg()} Error procesando muestra {i+1}: {e}")
                    continue

            # Limpiar archivos temporales
            for temp_file in temp_files:
                try:
                    if temp_file.exists():
                        temp_file.unlink()
                except Exception as e:
                    self.logger.warning(
                        f"{EmojiGenerator.warning_msg()} Error eliminando archivo temporal {temp_file}: {e}"
                    )

            if not language_votes:
                self.logger.warning(f"{EmojiGenerator.warning_msg()} No se pudo detectar idioma en ninguna muestra")
                return "spa"  # Fallback a español

            # Determinar idioma ganador por votación
            winner_lang = max(language_votes.items(), key=lambda x: x[1])
            winner_code = lang_map.get(winner_lang[0], winner_lang[0])

            # Log resumido de votación (solo top 3)
            top_3_votes = sorted(language_votes.items(), key=lambda x: x[1], reverse=True)[:3]
            votes_summary = ", ".join([f"{lang_map.get(lang, lang)}:{votes}" for lang, votes in top_3_votes])
            self.logger.info(f"🏆 Votación: {votes_summary} ({len(timestamps)} muestras)")

            # Aplicar reglas específicas para contenido latino
            spanish_votes = language_votes.get("es", 0)
            total_votes = len(timestamps)

            # Si español tiene al menos 30% de los votos, usarlo
            if spanish_votes / total_votes >= 0.30:
                self.logger.info(
                    f"{EmojiGenerator.success()} Español con {spanish_votes}/{total_votes} votos ({spanish_votes/total_votes*100:.1f}%) - Usando español"
                )
                return "spa"

            # Si el ganador tiene menos del 50% de votos y español está presente, usar español
            if winner_lang[1] / total_votes < 0.50 and spanish_votes > 0:
                self.logger.info(
                    f"{EmojiGenerator.success()} Confianza baja del ganador ({winner_lang[1]}/{total_votes}), español presente - Usando español"
                )
                return "spa"

            self.logger.info(f"{EmojiGenerator.success()} Idioma final seleccionado: {winner_lang[0]} ({winner_code})")
            # Guardar el resultado en caché si está disponible la función
            try:
                if set_cached_language:
                    set_cached_language(str(file_path), stream_index, winner_code)
            except Exception as e:
                self.logger.warning(f"{EmojiGenerator.warning_msg()} No se pudo cachear idioma desde processor: {e}")
            return winner_code

        except Exception as e:
            self.logger.error(f"Error en detección multi-muestra: {e}")
            return "spa"  # Fallback a español

    def detect_audio_language(self, file_path: Path, stream_index: int) -> Optional[str]:
        """
        Detecta el idioma de una pista de audio usando el caché pre-analizado.
        Si no está en caché, NO asume ningún idioma (retorna None).
        """
        try:
            # Normalizar la ruta para comparación consistente
            file_path_resolved = file_path.resolve()
            file_path_str = str(file_path_resolved)

            # Crear ruta relativa al directorio media para búsqueda en caché
            relative_path = None
            # Aceptar tanto /media/ (host-standard) como /mediajelly/media/ (contract inside container)
            if "/mediajelly/media/" in file_path_str:
                relative_path = file_path_str.split("/mediajelly/media/", 1)[1]
            elif "/media/" in file_path_str:
                relative_path = file_path_str.split("/media/", 1)[1]

            # Paso 1: Verificar caché de idiomas pre-detectados
            cached_lang = None
            if relative_path and relative_path in self.language_cache:
                stream_idx_str = str(stream_index)
                if stream_idx_str in self.language_cache[relative_path]:
                    cached_lang = self.language_cache[relative_path][stream_idx_str]
                    # Normalizar a código ISO 639-2 de 3 letras (ffmpeg espera 3 letras en tags)
                    try:
                        if get_config:
                            cfg = get_config()
                            inv_map = {v: k for k, v in cfg.language_detection.language_code_map.items()}
                            orig_cached = cached_lang
                            if cached_lang in inv_map:
                                cached_lang = inv_map[cached_lang]
                            if orig_cached != cached_lang:
                                self.logger.info(
                                    f"{EmojiGenerator.clipboard()} Caché: mapeado '{orig_cached}' -> '{cached_lang}' para {relative_path}"
                                )
                    except Exception:
                        pass
                    self.logger.info(
                        f"{EmojiGenerator.folder()} Idioma desde caché: {cached_lang} (stream {stream_index}) - key: {relative_path}"
                    )
                    return cached_lang
            elif file_path_str in self.language_cache:  # Fallback a ruta absoluta
                stream_idx_str = str(stream_index)
                if stream_idx_str in self.language_cache[file_path_str]:
                    cached_lang = self.language_cache[file_path_str][stream_idx_str]
                    try:
                        if get_config:
                            cfg = get_config()
                            inv_map = {v: k for k, v in cfg.language_detection.language_code_map.items()}
                            orig_cached = cached_lang
                            if cached_lang in inv_map:
                                cached_lang = inv_map[cached_lang]
                            if orig_cached != cached_lang:
                                self.logger.info(
                                    f"{EmojiGenerator.clipboard()} Caché: mapeado '{orig_cached}' -> '{cached_lang}' para {file_path_str}"
                                )
                    except Exception:
                        pass
                    self.logger.info(
                        f"{EmojiGenerator.folder()} Idioma desde caché: {cached_lang} (stream {stream_index}) - key: {file_path_str}"
                    )
                    return cached_lang
                else:
                    self.logger.debug(
                        f"{EmojiGenerator.warning_msg()} Archivo en caché pero stream {stream_index} no encontrado: {file_path.name}"
                    )
            else:
                # Debug: mostrar algunas claves del caché para verificar formato de rutas
                if self.language_cache:
                    cache_keys = list(self.language_cache.keys())[:3]
                    self.logger.debug(f"{EmojiGenerator.warning_msg()} Archivo no en caché: {file_path_str}")
                    if relative_path:
                        self.logger.debug(f"{EmojiGenerator.warning_msg()} Ruta relativa no en caché: {relative_path}")
                    self.logger.debug(f"{EmojiGenerator.clipboard()} Ejemplos de rutas en caché: {cache_keys}")

            # Paso 2: Intentar detección por metadatos (solo si hay metadatos claros)
            cmd = [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                f"a:{stream_index}",
                "-show_entries",
                "stream=codec_name:stream_tags=title,handler_name",
                "-of",
                "csv=p=0",
                str(file_path),
            ]

            result = subprocess.run(cmd, capture_output=True, timeout=SHORT_TIMEOUT, encoding="utf-8", errors="replace")

            if result.returncode == 0 and result.stdout.strip():
                metadata = result.stdout.strip().lower()

                # Buscar patrones en metadatos que indiquen idioma
                spanish_patterns = ["spanish", "español", "castellano", "spa", "esp", "latino", "lat"]
                english_patterns = ["english", "inglés", "eng"]
                japanese_patterns = ["japanese", "japonés", "jpn", "jap"]

                for pattern in spanish_patterns:
                    if pattern in metadata:
                        self.logger.info(
                            f"{EmojiGenerator.clipboard()} Idioma detectado por metadatos (español) en stream {stream_index}: {file_path.name}"
                        )
                        return "spa"

                for pattern in english_patterns:
                    if pattern in metadata:
                        self.logger.info(
                            f"{EmojiGenerator.clipboard()} Idioma detectado por metadatos (inglés) en stream {stream_index}: {file_path.name}"
                        )
                        return "eng"

                for pattern in japanese_patterns:
                    if pattern in metadata:
                        self.logger.info(
                            f"{EmojiGenerator.clipboard()} Idioma detectado por metadatos (japonés) en stream {stream_index}: {file_path.name}"
                        )
                        return "jpn"

            # Paso 3: Intentar detección con Whisper si está disponible
            if LANGUAGE_DETECTOR_AVAILABLE:
                self.logger.info(
                    f"{EmojiGenerator.magnifying_glass()} Stream {stream_index} sin idioma en caché/metadatos, ejecutando detección con Whisper: {file_path.name}"
                )
                try:
                    detected_lang = detect_language_with_whisper_for_processor(file_path, stream_index)
                    if detected_lang:
                        self.logger.info(
                            f"{EmojiGenerator.success()} Idioma detectado con Whisper: {detected_lang} para stream {stream_index}"
                        )
                        return detected_lang
                    else:
                        self.logger.warning(
                            f"{EmojiGenerator.warning_msg()} Whisper no pudo detectar idioma para stream {stream_index}"
                        )
                except Exception as e:
                    self.logger.error(f"Error ejecutando detector de idiomas con Whisper: {e}")

            # Paso 4: NO asumir ningún idioma por defecto - retornar None
            self.logger.info(
                f"{EmojiGenerator.warning_msg()} Stream {stream_index} sin idioma detectado (no en caché, no metadatos, detección falló): {file_path.name}"
            )
            return None

        except Exception as e:
            self.logger.error(f"Error detectando idioma de audio en stream {stream_index} de {file_path}: {e}")
            return None  # No asumir idioma en caso de error

    def detect_language_streams(self, file_path: Path) -> Tuple[bool, List[int], List[int], Dict[int, str]]:
        """
        Detecta streams de audio y subtítulos en español y devuelve índices.
        También detecta y etiqueta streams sin idioma.
        Retorna: (has_spanish, audio_indices, sub_indices, audio_languages)
        """
        try:
            # Comando para detectar todos los streams (audio y subtítulos)
            cmd = [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "stream=index,codec_type:stream_tags=language",
                "-of",
                "csv=p=0",
                str(file_path),
            ]

            result = subprocess.run(cmd, capture_output=True, timeout=SHORT_TIMEOUT, encoding="utf-8", errors="replace")

            lines = result.stdout.strip().split("\n")
            audio_indices = []
            sub_indices = []
            audio_languages = {}  # Mapeo de índice relativo de audio a idioma
            audio_stream_counter = 0
            subtitle_stream_counter = 0
            seen_indices = set()  # Para evitar duplicados en archivos con múltiples programas

            # Solo loguear si hay streams sin etiquetar
            needs_detection = any(
                "und" in line or line.split(",")[2] == ""
                for line in lines
                if "," in line and len(line.split(",")) >= 3 and line.split(",")[0].isdigit()
            )
            if needs_detection:
                self.logger.info(f"{EmojiGenerator.magnifying_glass()} Detectando idiomas en {file_path.name}")

            for line in lines:
                if line.strip():
                    parts = line.split(",")
                    if len(parts) >= 2 and parts[0].isdigit():  # Verificar que el primer campo sea un número
                        index = int(parts[0])

                        # Evitar procesar el mismo stream múltiples veces (archivos con programas múltiples)
                        if index in seen_indices:
                            continue
                        seen_indices.add(index)

                        codec_type = parts[1] if len(parts) > 1 else ""
                        lang = parts[2].lower() if len(parts) > 2 else ""

                        if codec_type == "audio":
                            # Si no tiene etiqueta de idioma, detectarlo
                            if not lang or lang == "und":
                                self.logger.info(
                                    f"{EmojiGenerator.audio()} Stream audio {audio_stream_counter} (índice absoluto {index}) sin etiqueta ('{lang}'), iniciando detección..."
                                )
                                # Pasar el índice absoluto del stream
                                detected_lang = self.detect_audio_language(file_path, index)
                                if detected_lang:
                                    lang = detected_lang
                                    self.logger.info(
                                        f"{EmojiGenerator.success()} Idioma detectado: {detected_lang} para stream audio {audio_stream_counter}"
                                    )
                                else:
                                    self.logger.warning(
                                        f"{EmojiGenerator.warning_msg()} No se pudo detectar idioma para stream audio {audio_stream_counter}"
                                    )

                            # Guardar el idioma del stream usando el índice relativo de audio
                            audio_languages[audio_stream_counter] = lang

                            # Verificar si es español
                            if any(pattern in lang for pattern in ["spa", "esp", "es", "lat"]):
                                audio_indices.append(audio_stream_counter)

                            audio_stream_counter += 1

                        elif codec_type == "subtitle":
                            # Usar un contador relativo para subtítulos (0:s:N espera índices relativos)
                            current_sub_index = subtitle_stream_counter
                            if any(pattern in lang for pattern in ["spa", "esp", "es", "lat"]):
                                sub_indices.append(current_sub_index)
                            # Incrementar contador relativo de subtítulos siempre
                            subtitle_stream_counter += 1

            has_spanish = len(audio_indices) > 0 or len(sub_indices) > 0

            return has_spanish, audio_indices, sub_indices, audio_languages

        except Exception as e:
            self.logger.error(f"Error detectando idiomas en {file_path}: {e}")
            return False, [], [], {}

    def _validate_file_for_compression(self, file_path: Path) -> None:
        """
        Valida si el archivo necesita compresión con validaciones rápidas primero.

        Args:
            file_path: Ruta al archivo a validar.

        Raises:
            ValidationError: Si el archivo no es válido para compresión.
            FileNotFoundError: Si el archivo no existe.
        """
        # Validaciones rápidas primero
        if not file_path.exists():
            raise FileNotFoundError(f"Archivo no encontrado: {file_path}")

        try:
            stat = file_path.stat()
            file_size = stat.st_size

            # Verifica tamaño mínimo para compresión (archivos muy pequeños no necesitan compresión)
            if file_path.suffix.lower() == ".mp4" and file_size < 50 * 1024 * 1024:  # Menos de 50MB
                raise ValidationError("archivo_pequeno")

            # Verifica que no sea un archivo vacío o corrupto básicamente
            if file_size == 0:
                raise ValidationError(f"Archivo vacío: {file_path}")

        except OSError as e:
            raise ValidationError(f"Error accediendo al archivo: {file_path} - {str(e)}")

        # Validación con ffprobe (más costosa, pero necesaria)
        try:
            probe_cmd = [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                CSV_FORMAT_PARAM,
                str(file_path),
            ]
            probe_result = subprocess.run(
                probe_cmd, capture_output=True, timeout=MEDIUM_TIMEOUT, encoding="utf-8", errors="replace"
            )

            if probe_result.returncode != 0:
                raise ValidationError(f"Archivo corrupto o no válido: {file_path}")

            # Verifica que tenga duración
            duration_str = probe_result.stdout.strip()
            if not duration_str or duration_str == "N/A":
                raise ValidationError(f"Archivo sin duración válida: {file_path}")

            # Verifica que la duración sea razonable (más de 10 segundos)
            try:
                duration = float(duration_str)
                if duration < 10.0:
                    raise ValidationError(f"Archivo demasiado corto ({duration:.1f}s): {file_path}")
            except ValueError:
                raise ValidationError(f"Duración no válida: {file_path}")

        except subprocess.TimeoutExpired:
            raise ValidationError(f"Timeout validando archivo: {file_path}")
        except Exception as e:
            if isinstance(e, ValidationError):
                raise
            raise ValidationError(f"Error validando archivo: {file_path} - {str(e)}")

    def _get_video_codec(self, file_path: Path) -> str:
        """Detecta el codec de video del archivo"""
        try:
            probe_cmd = [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=codec_name",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(file_path),
            ]
            result = subprocess.run(
                probe_cmd, capture_output=True, timeout=SHORT_TIMEOUT, encoding="utf-8", errors="replace"
            )

            if result.returncode == 0:
                return result.stdout.strip().lower()
        except Exception as e:
            self.logger.warning(f"Error detectando codec: {e}")

        return ""

    def _get_video_bitrate(self, file_path: Path) -> Optional[Dict[str, int]]:
        """Obtiene información de bitrate del video"""
        try:
            probe_cmd = [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=bit_rate",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(file_path),
            ]
            result = subprocess.run(
                probe_cmd, capture_output=True, timeout=SHORT_TIMEOUT, encoding="utf-8", errors="replace"
            )

            if result.returncode == 0:
                bitrate_str = result.stdout.strip()
                if bitrate_str and bitrate_str != "N/A":
                    try:
                        bitrate_bps = int(bitrate_str)
                        return {"bitrate_kbps": bitrate_bps // 1000}
                    except ValueError:
                        pass
        except Exception as e:
            self.logger.warning(f"Error obteniendo bitrate: {e}")

        return None

    def _build_remux_command(
        self,
        file_path: Path,
        temp_output: Path,
        audio_indices: List[int],
        sub_indices: List[int],
        audio_languages: Optional[Dict[int, str]] = None,
    ) -> List[str]:
        """Construye comando ffmpeg para remux (sin recodificación) - Para archivos AV1"""
        if audio_languages is None:
            audio_languages = {}

        # Asegurarnos de que los códigos de idioma sean ISO 639-2 (tres letras) para MP4
        audio_languages = self._ensure_three_letter_codes(audio_languages)

        ffmpeg_cmd = [
            "ffmpeg",
            "-hide_banner",
            "-y",
            "-probesize",
            "100M",
            "-analyzeduration",
            "100M",
            "-i",
            str(file_path),
            "-c:v",
            "copy",  # Copiar video sin recodificar
            "-max_muxing_queue_size",
            "1024",
        ]

        # Mapeo de streams de video
        ffmpeg_cmd.extend(["-map", "0:v:0"])

        # Audio: mapear español si existe, sino el primero
        audio_map_count = 0
        # Logging de idiomas que se aplicarán a metadatos
        if audio_languages:
            self.logger.debug(f"{EmojiGenerator.clipboard()} Remux: idiomas de audio para metadata: {audio_languages}")

        # Intentar detectar códec y canales del primer stream de audio mapeado
        detected_codec = ""
        detected_channels = 2
        try:
            probe_idx = audio_indices[0] if audio_indices else 0
            probe_cmd = [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                f"a:{probe_idx}",
                "-show_entries",
                "stream=codec_name,channels",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(file_path),
            ]
            probe_res = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=SHORT_TIMEOUT)
            probe_lines = [l.strip() for l in probe_res.stdout.splitlines() if l.strip()]
            if len(probe_lines) >= 1:
                detected_codec = probe_lines[0].lower()
            if len(probe_lines) >= 2 and probe_lines[1].isdigit():
                detected_channels = int(probe_lines[1])
            self.logger.debug(f"Detected audio codec={detected_codec} channels={detected_channels} for {file_path.name}")
        except Exception:
            detected_codec = ""
            detected_channels = 2

        for idx in audio_indices:
            ffmpeg_cmd.extend(["-map", f"0:a:{idx}"])
            # Aplicar etiqueta de idioma si está disponible
            if idx in audio_languages:
                ffmpeg_cmd.extend([f"-metadata:s:a:{audio_map_count}", f"language={audio_languages[idx]}"])
            audio_map_count += 1

        if not audio_indices:
            ffmpeg_cmd.extend(["-map", "0:a:0"])
            if 0 in audio_languages:
                lang_code = audio_languages[0]
                self.logger.info(f"  Aplicando metadata language={lang_code} al audio stream 0")
                ffmpeg_cmd.extend(["-metadata:s:a:0", f"language={lang_code}"])
            else:
                self.logger.warning(f"  No hay idioma disponible para audio stream 0")

        # Decidir si copiar audio o recodificar a AAC para compatibilidad MP4
        # Copiar solo si ya es AAC; en otros casos recodificar a AAC (preservando canales cuando sea posible)
        if detected_codec == "aac":
            ffmpeg_cmd.extend(["-c:a", "copy"])
        else:
            target_channels = str(detected_channels if detected_channels and detected_channels <= 6 else 2)
            target_bitrate = "384k" if detected_channels >= 6 else "256k"
            ffmpeg_cmd.extend(["-c:a", "aac", "-b:a", target_bitrate, "-ac", target_channels, "-ar", "48000"])

        # Subtítulos: mapear SOLO subtítulos textuales (subrip/ass/etc.).
        # Evitar PGS (hdmv_pgs_subtitle) ya que no se pueden convertir a mov_text.
        mapped_subs = 0
        try:
            probe_subs_cmd = [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "s",
                "-show_entries",
                "stream=codec_name",
                "-of",
                "csv=p=0",
                str(file_path),
            ]
            probe_subs = subprocess.run(probe_subs_cmd, capture_output=True, text=True, timeout=SHORT_TIMEOUT)
            subtitle_codecs = [l.strip() for l in probe_subs.stdout.splitlines() if l.strip()]
        except Exception:
            subtitle_codecs = []

        # Definir codecs textuales que podemos convertir a mov_text
        text_sub_codecs = {"subrip", "srt", "ass", "ssa", "webvtt", "mov_text"}

        if sub_indices:
            for idx in sub_indices:
                codec = subtitle_codecs[idx] if idx < len(subtitle_codecs) else None
                if codec and codec.lower() in text_sub_codecs:
                    ffmpeg_cmd.extend(["-map", f"0:s:{idx}?"])
                    mapped_subs += 1
                else:
                    self.logger.warning(f"Omitiendo subtítulo 0:s:{idx} (codec={codec}) - no apto para remux a mov_text")
        else:
            # Intentar mapear el primer subtítulo textual disponible
            mapped = False
            for i, codec in enumerate(subtitle_codecs):
                if codec and codec.lower() in text_sub_codecs:
                    ffmpeg_cmd.extend(["-map", f"0:s:{i}?"])
                    mapped_subs += 1
                    mapped = True
                    break
            if not mapped:
                self.logger.debug("No se encontraron subtítulos textuales a mapear; omitiendo subtítulos")

        # Añadir conversión solo si se mapearon subtítulos
        if mapped_subs > 0:
            ffmpeg_cmd.extend(["-c:s", "mov_text"])

        # Copiar metadatos
        ffmpeg_cmd.extend(["-map_metadata", "0"])

        # Configuración final
        ffmpeg_cmd.extend(["-movflags", MOVFLAGS_FASTSTART, str(temp_output)])

        return ffmpeg_cmd

    def _build_ffmpeg_command(
        self,
        file_path: Path,
        temp_output: Path,
        audio_indices: List[int],
        sub_indices: List[int],
        audio_languages: Optional[Dict[int, str]] = None,
    ) -> List[str]:
        """Construye comando ffmpeg optimizado"""
        if audio_languages is None:
            audio_languages = {}

        # Asegurarnos de que los códigos de idioma sean ISO 639-2 (tres letras) para MP4
        audio_languages = self._ensure_three_letter_codes(audio_languages)

        ffmpeg_cmd = [
            "ffmpeg",
            "-hide_banner",
            "-y",
            "-probesize",
            "100M",
            "-analyzeduration",
            "100M",
            "-hwaccel",
            "vaapi",
            "-hwaccel_device",
            VAAPI_DEVICE_PATH,
            "-hwaccel_output_format",
            "vaapi",
            "-i",
            str(file_path),
            "-c:v",
            "h264_vaapi",
            "-qp",
            "28",
            "-low_power",
            "1",
            "-max_muxing_queue_size",
            "1024",
        ]

        # Mapeo de streams
        # Logging de idiomas que se aplicarán a metadatos
        if audio_languages:
            self.logger.debug(f"{EmojiGenerator.clipboard()} FFmpeg: idiomas de audio para metadata: {audio_languages}")
        ffmpeg_cmd.extend(["-map", "0:v:0"])  # Video

        # Audio: mapear español si existe, sino el primero
        audio_map_count = 0
        if audio_indices:
            for idx in audio_indices:
                ffmpeg_cmd.extend(["-map", f"0:a:{idx}"])
                # Aplicar etiqueta de idioma si está disponible
                if idx in audio_languages:
                    ffmpeg_cmd.extend([f"-metadata:s:a:{audio_map_count}", f"language={audio_languages[idx]}"])
                audio_map_count += 1
        else:
            ffmpeg_cmd.extend(["-map", "0:a:0"])  # Primer audio
            # Aplicar etiqueta de idioma al primer audio si está disponible
            if 0 in audio_languages:
                lang_code = audio_languages[0]
                self.logger.info(f"  Aplicando metadata language={lang_code} al audio stream 0")
                ffmpeg_cmd.extend(["-metadata:s:a:0", f"language={lang_code}"])
            else:
                self.logger.warning(f"  No hay idioma disponible para audio stream 0")

        # Subtítulos: mapear SOLO subtítulos textuales (subrip/ass/etc.). Evitar PGS.
        mapped_subs = 0
        try:
            probe_subs_cmd = [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "s",
                "-show_entries",
                "stream=codec_name",
                "-of",
                "csv=p=0",
                str(file_path),
            ]
            probe_subs = subprocess.run(probe_subs_cmd, capture_output=True, text=True, timeout=SHORT_TIMEOUT)
            subtitle_codecs = [l.strip() for l in probe_subs.stdout.splitlines() if l.strip()]
        except Exception:
            subtitle_codecs = []

        text_sub_codecs = {"subrip", "srt", "ass", "ssa", "webvtt", "mov_text"}

        if sub_indices:
            for idx in sub_indices:
                codec = subtitle_codecs[idx] if idx < len(subtitle_codecs) else None
                if codec and codec.lower() in text_sub_codecs:
                    ffmpeg_cmd.extend(["-map", f"0:s:{idx}?"])
                    mapped_subs += 1
                else:
                    self.logger.warning(f"Omitiendo subtítulo 0:s:{idx} (codec={codec}) - no apto para remux a mov_text")
        else:
            mapped = False
            for i, codec in enumerate(subtitle_codecs):
                if codec and codec.lower() in text_sub_codecs:
                    ffmpeg_cmd.extend(["-map", f"0:s:{i}?"])
                    mapped_subs += 1
                    mapped = True
                    break
            if not mapped:
                self.logger.debug("No se encontraron subtítulos textuales a mapear; omitiendo subtítulos")

        # Audio config
        ffmpeg_cmd.extend(["-c:a", "aac", "-b:a", "256k", "-ac", "2", "-ar", "48000"])

        # Subtítulos config: convertir a mov_text solo si se mapearon subtítulos textuales
        if mapped_subs > 0:
            ffmpeg_cmd.extend(["-c:s", "mov_text"])

        # Copiar metadatos principales
        ffmpeg_cmd.extend(["-map_metadata", "0"])

        # Configuración final
        ffmpeg_cmd.extend(["-movflags", MOVFLAGS_FASTSTART, "-avoid_negative_ts", "make_zero", str(temp_output)])

        return ffmpeg_cmd

    def _build_fallback_ffmpeg_command(
        self,
        file_path: Path,
        temp_output: Path,
        audio_indices: List[int],
        sub_indices: List[int],
        audio_languages: Optional[Dict[int, str]] = None,
    ) -> List[str]:
        """Construye comando ffmpeg fallback sin aceleración de hardware"""
        if audio_languages is None:
            audio_languages = {}

        # Asegurarnos de que los códigos de idioma sean ISO 639-2 (tres letras) para MP4
        audio_languages = self._ensure_three_letter_codes(audio_languages)

        ffmpeg_cmd = [
            "ffmpeg",
            "-hide_banner",
            "-y",
            "-probesize",
            "100M",
            "-analyzeduration",
            "100M",
            "-i",
            str(file_path),
        ]

        # Video config
        ffmpeg_cmd.extend(["-c:v", "libx264", "-preset", "fast", "-crf", "23", "-map", "0:v:0"])  # Más rápido

        # Audio: mapear español si existe, sino el primero
        audio_map_count = 0
        # Logging de idiomas que se aplicarán a metadatos
        if audio_languages:
            self.logger.debug(f"{EmojiGenerator.clipboard()} Fallback FFmpeg: idiomas de audio para metadata: {audio_languages}")
        if audio_indices:
            for idx in audio_indices:
                ffmpeg_cmd.extend(["-map", f"0:a:{idx}"])
                # Aplicar etiqueta de idioma si está disponible
                if idx in audio_languages:
                    ffmpeg_cmd.extend([f"-metadata:s:a:{audio_map_count}", f"language={audio_languages[idx]}"])
                audio_map_count += 1
        else:
            ffmpeg_cmd.extend(["-map", "0:a:0"])  # Primer audio
            # Aplicar etiqueta de idioma al primer audio si está disponible
            if 0 in audio_languages:
                lang_code = audio_languages[0]
                self.logger.info(f"  Aplicando metadata language={lang_code} al audio stream 0")
                ffmpeg_cmd.extend(["-metadata:s:a:0", f"language={lang_code}"])
            else:
                self.logger.warning(f"  No hay idioma disponible para audio stream 0")

        # Subtítulos: mapear solo subtítulos textuales (evitar PGS)
        try:
            probe_subs_cmd = [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "s",
                "-show_entries",
                "stream=codec_name",
                "-of",
                "csv=p=0",
                str(file_path),
            ]
            probe_subs = subprocess.run(probe_subs_cmd, capture_output=True, text=True, timeout=SHORT_TIMEOUT)
            subtitle_codecs = [l.strip() for l in probe_subs.stdout.splitlines() if l.strip()]
        except Exception:
            subtitle_codecs = []

        text_sub_codecs = {"subrip", "srt", "ass", "ssa", "webvtt", "mov_text"}
        mapped_subs = 0

        if sub_indices:
            for idx in sub_indices:
                codec = subtitle_codecs[idx] if idx < len(subtitle_codecs) else None
                if codec and codec.lower() in text_sub_codecs:
                    ffmpeg_cmd.extend(["-map", f"0:s:{idx}?"])
                    mapped_subs += 1
                else:
                    self.logger.warning(f"Omitiendo subtítulo 0:s:{idx} (codec={codec}) - no apto para remux a mov_text")
        else:
            mapped = False
            for i, codec in enumerate(subtitle_codecs):
                if codec and codec.lower() in text_sub_codecs:
                    ffmpeg_cmd.extend(["-map", f"0:s:{i}?"])
                    mapped_subs += 1
                    mapped = True
                    break
            if not mapped:
                self.logger.debug("No se encontraron subtítulos textuales a mapear; omitiendo subtítulos")

        # Audio config
        ffmpeg_cmd.extend(["-c:a", "aac", "-b:a", "256k", "-ac", "2", "-ar", "48000"])

        # Subtítulos config: convertir a mov_text solo si se mapearon subtítulos textuales
        if mapped_subs > 0:
            ffmpeg_cmd.extend(["-c:s", "mov_text"])

        # Copiar metadatos principales
        ffmpeg_cmd.extend(["-map_metadata", "0"])

        # Final config
        ffmpeg_cmd.extend(["-movflags", MOVFLAGS_FASTSTART, "-avoid_negative_ts", "make_zero", str(temp_output)])

        return ffmpeg_cmd

    def _build_ffmpeg_command_no_subtitles(
        self,
        file_path: Path,
        temp_output: Path,
        audio_indices: List[int],
        audio_languages: Optional[Dict[int, str]] = None,
    ) -> List[str]:
        """Construye comando ffmpeg sin subtítulos para archivos con subtítulos corruptos"""
        if audio_languages is None:
            audio_languages = {}

        audio_languages = self._ensure_three_letter_codes(audio_languages)

        ffmpeg_cmd = [
            "ffmpeg",
            "-hide_banner",
            "-y",
            "-probesize",
            "100M",
            "-analyzeduration",
            "100M",
            "-i",
            str(file_path),
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "23",
            "-map",
            "0:v:0",
        ]

        audio_map_count = 0
        if audio_languages:
            self.logger.debug(f"{EmojiGenerator.clipboard()} No-subs FFmpeg: idiomas de audio para metadata: {audio_languages}")

        if audio_indices:
            for idx in audio_indices:
                ffmpeg_cmd.extend(["-map", f"0:a:{idx}"])
                if idx in audio_languages:
                    ffmpeg_cmd.extend([f"-metadata:s:a:{audio_map_count}", f"language={audio_languages[idx]}"])
                audio_map_count += 1
        else:
            ffmpeg_cmd.extend(["-map", "0:a:0"])
            if 0 in audio_languages:
                lang_code = audio_languages[0]
                self.logger.info(f"  Aplicando metadata language={lang_code} al audio stream 0")
                ffmpeg_cmd.extend(["-metadata:s:a:0", f"language={lang_code}"])
            else:
                self.logger.warning(f"  No hay idioma disponible para audio stream 0")

        ffmpeg_cmd.extend(["-c:a", "aac", "-b:a", "256k", "-ac", "2", "-ar", "48000"])
        ffmpeg_cmd.extend(["-map_metadata", "0"])
        ffmpeg_cmd.extend(["-movflags", MOVFLAGS_FASTSTART, "-avoid_negative_ts", "make_zero", str(temp_output)])

        return ffmpeg_cmd

    def _validate_compressed_file(self, compressed_file: Path) -> Tuple[bool, str]:
        """Valida la integridad del archivo comprimido de forma simplificada"""
        try:
            # Verifica que el archivo exista y tenga tamaño
            if not compressed_file.exists() or compressed_file.stat().st_size == 0:
                return False, "Archivo no existe o está vacío"

            # Verifica integridad básica con ffprobe
            probe_cmd = ["ffprobe", "-v", "quiet", "-show_format", str(compressed_file)]

            result = subprocess.run(
                probe_cmd, capture_output=True, timeout=SHORT_TIMEOUT, encoding="utf-8", errors="replace"
            )

            if result.returncode != 0:
                return False, "FFprobe falló: archivo corrupto"

            # Verifica que tenga duración razonable
            duration_cmd = [
                "ffprobe",
                "-v",
                "quiet",
                "-show_entries",
                "format=duration",
                "-of",
                CSV_FORMAT_PARAM,
                str(compressed_file),
            ]

            duration_result = subprocess.run(
                duration_cmd, capture_output=True, timeout=SHORT_TIMEOUT, encoding="utf-8", errors="replace"
            )

            if duration_result.returncode == 0:
                duration_str = duration_result.stdout.strip()
                try:
                    duration = float(duration_str)
                    if duration < 10.0:
                        return False, f"Archivo demasiado corto ({duration:.1f}s)"
                except ValueError:
                    pass  # No crítico si no se puede leer la duración

            return True, "Archivo válido"

        except subprocess.TimeoutExpired:
            return False, "Timeout validando archivo"
        except Exception as e:
            return False, f"Error validando: {str(e)}"

    def _process_compression_result(
        self,
        process: subprocess.CompletedProcess,
        file_path: Path,
        temp_output: Path,
        elapsed_time: float,
        used_gpu: bool = True,
    ) -> Dict:
        """
        Procesa el resultado del intento de compresión.

        Evalúa el código de retorno y el archivo de salida para determinar éxito o fallo.

        Args:
            process: Resultado del proceso subprocess.run.
            file_path: Ruta del archivo original.
            temp_output: Ruta del archivo temporal de salida.
            elapsed_time: Tiempo transcurrido en segundos.
            used_gpu: Si se usó GPU en el intento.

        Returns:
            Dict con el resultado del procesamiento.
        """
        if process.returncode == 0 and temp_output.exists() and temp_output.stat().st_size > 1024:  # Mínimo 1KB
            return self._handle_successful_compression(temp_output, file_path, elapsed_time, used_gpu)
        else:
            return self._handle_failed_compression(process, file_path, temp_output)

    def _handle_successful_compression(
        self, temp_output: Path, file_path: Path, elapsed_time: float, used_gpu: bool
    ) -> Dict:
        """
        Maneja el caso de una compresión exitosa.

        Orquesta la validación y finalización del proceso de compresión.

        Args:
            temp_output: Ruta del archivo comprimido temporal.
            file_path: Ruta del archivo original.
            elapsed_time: Tiempo de procesamiento en segundos.
            used_gpu: Si se usó GPU.

        Returns:
            Dict con el resultado final y estadísticas.
        """
        # Validación
        is_valid, validation_msg = self._validate_compressed_output(temp_output, file_path)

        if not is_valid:
            return self._handle_validation_failure(temp_output, file_path, validation_msg)

        self.logger.info(f"Validación exitosa para {file_path.name}: {validation_msg}")

        # Comparación de tamaños
        original_size = file_path.stat().st_size
        compressed_size = temp_output.stat().st_size

        if compressed_size >= original_size:
            return self._finalize_compression_larger(
                temp_output, file_path, used_gpu, original_size, compressed_size
            )
        else:
            return self._finalize_compression_smaller(
                temp_output, file_path, used_gpu, original_size, compressed_size, elapsed_time
            )

    def _validate_compressed_output(self, temp_output: Path, file_path: Path) -> Tuple[bool, str]:
        """
        Valida el archivo comprimido generado.

        Args:
            temp_output: Ruta del archivo temporal.
            file_path: Ruta del archivo original (para contexto).

        Returns:
            Tuple[bool, str]: (Es válido, Mensaje de validación/error).
        """
        return self._validate_compressed_file(temp_output)

    def _handle_validation_failure(self, temp_output: Path, file_path: Path, validation_msg: str) -> Dict:
        """
        Maneja el fallo de validación del archivo comprimido.

        Args:
            temp_output: Ruta del archivo temporal.
            file_path: Ruta del archivo original.
            validation_msg: Mensaje de error de validación.

        Returns:
            Dict con el resultado de error.
        """
        self.logger.error(f"Archivo comprimido inválido para {file_path.name}: {validation_msg}")

        if temp_output.exists():
            temp_output.unlink()

        # Marcar como fallido para evitar reintentos
        failed_files_path = self.failed_file
        self._add_to_failed_files(str(file_path), failed_files_path, f"Validación fallida: {validation_msg}")

        return {
            "compressed": False,
            "renamed": False,
            "success": True, # El proceso terminó "bien" pero la validación falló
            "error": f"Archivo comprimido corrupto: {validation_msg}",
            "no_spanish": False,
            "skipped": False,
        }

    def _finalize_compression_larger(
        self, temp_output: Path, file_path: Path, used_gpu: bool, original_size: int, compressed_size: int
    ) -> Dict:
        """
        Finaliza el proceso cuando el archivo comprimido es más grande que el original.

        En este caso, se renombra a .mp4 (contenedor estándar) pero se mantiene la calidad/tamaño.
        """
        # Calcular la diferencia porcentual
        size_difference_percent = ((compressed_size - original_size) / original_size) * 100

        # Si la diferencia es mínima (< 2%), considerar como exitoso
        if size_difference_percent < 2.0:
            method_str = "GPU" if used_gpu else "CPU"
            self.logger.info(
                f"Compresión efectiva con diferencia mínima ({method_str}): {file_path.name} (+{size_difference_percent:.1f}%)"
            )

            # Renombrar a .mp4
            final_name = file_path.with_suffix(".mp4")
            temp_output.replace(final_name)

            if file_path != final_name and file_path.exists():
                file_path.unlink()

            # Actualizar pending si cambió extensión
            self._update_pending_file(str(file_path), str(final_name))

            # Marcar como completado
            self._mark_as_completed(final_name)

            return {
                "compressed": True,  # Considerar como comprimido exitosamente
                "renamed": True,
                "success": True,
                "error": "",
                "no_spanish": False,
                "skipped": False,
                "original_size": original_size,
                "compressed_size": compressed_size
            }
        else:
            # Diferencia significativa, proceder con lógica original
            method_str = "GPU" if used_gpu else "CPU"
            self.logger.info(
                f"Archivo comprimido es mayor, pero renombrando a MP4 con metadatos ({method_str}): {file_path.name} (+{size_difference_percent:.1f}%)"
            )

            # Renombrar a .mp4 con metadatos aplicados
            final_name = file_path.with_suffix(".mp4")
            temp_output.replace(final_name)

            if file_path != final_name and file_path.exists():
                file_path.unlink()  # Eliminar el original si el nombre cambió

            # Actualizar pending si cambió extensión
            self._update_pending_file(str(file_path), str(final_name))

            # Marcar como completado
            self._mark_as_completed(final_name)

            return {
                "compressed": True, # Contar como procesado
                "renamed": True,
                "success": True,
                "error": "",
                "no_spanish": False,
                "skipped": False,
                "original_size": original_size,
                "compressed_size": compressed_size
            }

    def _finalize_compression_smaller(
        self, temp_output: Path, file_path: Path, used_gpu: bool, original_size: int, compressed_size: int, elapsed_time: float
    ) -> Dict:
        """
        Finaliza el proceso cuando la compresión fue efectiva (tamaño reducido).
        """
        reduction_percent = ((original_size - compressed_size) / original_size) * 100
        method_str = "GPU" if used_gpu else "CPU"
        self.logger.info(
            f"Compresión {method_str} exitosa: {file_path.name} ({elapsed_time:.1f}s, -{reduction_percent:.1f}%)"
        )

        # Crear el nombre final con extensión .mp4
        final_name = file_path.with_suffix(".mp4")

        # Renombrar el archivo comprimido
        temp_output.rename(final_name)

        # Si el archivo original no es .mp4, eliminarlo
        if file_path.suffix.lower() != ".mp4":
            self._delete_original_file(file_path)
        else:
            self.logger.info(f"Archivo original es .mp4, manteniendo: {file_path.name}")

        # Si se comprimió exitosamente y cambió la extensión, actualizar pending-compression.txt
        if final_name != file_path:
            self._update_pending_file(str(file_path), str(final_name))

        # Marcar como completado
        self._mark_as_completed(final_name)

        return {
            "compressed": True,
            "renamed": False,
            "success": True,
            "error": "",
            "no_spanish": False,
            "skipped": False,
            "original_size": original_size,
            "compressed_size": compressed_size
        }

    def _delete_original_file(self, file_path: Path):
        """Elimina el archivo original de forma segura."""
        self.logger.info(f"Eliminando archivo original: {file_path.name}")
        try:
            file_path.unlink()
            self.logger.info(f"Archivo original eliminado exitosamente: {file_path.name}")
        except Exception as e:
            self.logger.error(f"Error eliminando archivo original {file_path}: {e}")
            # Marcar como fallido si no se puede eliminar el original
            failed_files_path = self.failed_file
            self._add_to_failed_files(str(file_path), failed_files_path, f"Error eliminando original: {e}")

    def _mark_as_completed(self, final_name: Path):
        """Marca el archivo como completado en el registro.

        Se guarda además una etiqueta ligera (el sufijo) en la estructura de
        `processed_files` para facilitar detecciones de cambios posteriores.
        """
        with open(self.completed_file, "a") as f:
            f.write(f"{final_name}\n")

        # Actualizar memoria si ya está cargada
        self.processed_files[str(final_name)] = {
            "status": "success",
            "timestamp": datetime.now().isoformat(),
            "label": final_name.suffix.lower(),
        }

    def _handle_failed_compression(
        self, process: subprocess.CompletedProcess, file_path: Path, temp_output: Path
    ) -> Dict:
        """
        Maneja el caso de una compresión fallida.

        Registra el error, limpia archivos temporales y actualiza la lista de fallos.

        Args:
            process: Resultado del proceso subprocess.run.
            file_path: Ruta del archivo original.
            temp_output: Ruta del archivo temporal de salida.

        Returns:
            Dict con detalles del error.
        """
        error_msg = self._generate_compression_error_message(process, file_path, temp_output)

        # Adjuntar un snippet del stderr de ffmpeg para diagnóstico
        stderr_snippet = ""
        try:
            if process and getattr(process, "stderr", None):
                stderr_snippet = process.stderr.strip().splitlines()
                stderr_snippet = " | ".join(stderr_snippet[:10])
                error_msg = f"{error_msg} | ffmpeg-stderr: {stderr_snippet}"
                self.logger.debug(f"FFmpeg stderr completo para {file_path.name}:\n{process.stderr}")
        except Exception:
            pass

        if temp_output.exists():
            temp_output.unlink()

        # NOTA: No marcar como fallido aquí para permitir fallbacks (CPU / SAFE).
        # El marcado persistente a failed-compression.txt se realiza sólo si
        # todos los fallbacks fallan (_handle_fallback_logic se encarga de ello).

        return {
            "compressed": False,
            "renamed": False,
            "success": False,
            "error": error_msg,
            "no_spanish": False,
            "skipped": False,
        }

    def _generate_compression_error_message(
        self, process: subprocess.CompletedProcess, file_path: Path, temp_output: Path
    ) -> str:
        """Genera un mensaje de error descriptivo para fallos de compresión."""
        if process.returncode == 0:
            if temp_output.exists():
                return f"Compresión aparentemente exitosa pero archivo muy pequeño o corrupto: {file_path} (tamaño: {temp_output.stat().st_size} bytes)"
            else:
                return f"Compresión aparentemente exitosa pero archivo no creado: {file_path}"

        if process.returncode == 124:
            return f"Timeout alcanzado ({FFMPEG_TIMEOUT/3600:.1f}h): {file_path}"
        # Detectar si el proceso fue terminado por una señal (retcode negativo)
        if process.returncode < 0:
            sig = -process.returncode
            if sig == 9:
                return f"Falló compresión (terminado por señal SIGKILL - posible OOM o kill): {file_path}"
            return f"Falló compresión (terminado por señal {sig}): {file_path}"

        return f"Falló compresión (código {process.returncode}): {file_path}"

    def _handle_timeout_error(self, result: Dict, file_path: Path, temp_output: Optional[Path]) -> None:
        """Maneja errores de timeout en la compresión"""
        error_msg = f"Timeout alcanzado ({FFMPEG_TIMEOUT/3600:.1f}h): {file_path}"
        result.update(
            {
                "success": False,
                "error": error_msg,
                "compressed": False,
                "renamed": False,
                "no_spanish": False,
                "skipped": False,
            }
        )

        if temp_output and temp_output.exists():
            temp_output.unlink()

        # Marcar como fallido
        failed_files_path = self.failed_file
        self._add_to_failed_files(str(file_path), failed_files_path, error_msg)

        self.logger.error(error_msg)

    def _handle_unexpected_error(self, result: Dict, file_path: Path, exception: Exception, temp_output: Optional[Path]) -> None:
        """Maneja errores inesperados durante la compresión"""
        error_msg = f"Error inesperado procesando {file_path}: {str(exception)}"
        result.update(
            {
                "success": False,
                "error": error_msg,
                "compressed": False,
                "renamed": False,
                "no_spanish": False,
                "skipped": False,
            }
        )

        if temp_output and temp_output.exists():
            temp_output.unlink()

        # Marcar como fallido
        failed_files_path = self.failed_file
        self._add_to_failed_files(str(file_path), failed_files_path, error_msg)

        self.logger.error(error_msg)
        self.logger.error(f"Traceback completo: {exception}", exc_info=True)

    def _log_no_spanish_info(self, file_path: Path, audio_indices: List[int], sub_indices: List[int]) -> None:
        """Loggea información cuando no se detecta español en el archivo"""
        self.logger.warning(f"No se detectó audio/subtítulos en español: {file_path.name}")
        if audio_indices:
            self.logger.info(f"  Pistas de audio detectadas: {', '.join(map(str, audio_indices))}")
        else:
            self.logger.info("  No se detectaron pistas de audio")
        if sub_indices:
            self.logger.info(f"  Pistas de subtítulos detectadas: {', '.join(map(str, sub_indices))}")
        else:
            self.logger.info("  No se detectaron pistas de subtítulos")

    def compress_single_file(self, file_path_str: str) -> Dict:
        """
        Comprime un solo archivo con selección inteligente de método de compresión.
        Completamente independiente - errores en este archivo no afectan otros procesos.

        Args:
            file_path_str: Ruta del archivo a comprimir como string.

        Returns:
            Dict con el resultado del procesamiento.
        """
        file_path = Path(file_path_str)
        result = {
            "file": str(file_path),
            "success": False,
            "error": "",
            "no_spanish": False,
            "compressed": False,
            "renamed": False,
            "skipped": False,
        }

        temp_output = None

        try:
            # Establece límites para este proceso (aislado)
            self.set_resource_limits()

            # Prepara compresión: validación, detección de idiomas, selección de método
            is_prepared, compression_info, temp_output, audio_indices, sub_indices, audio_languages = (
                self._prepare_compression(file_path)
            )
            if not is_prepared:
                # Si se skippeó, considerarlo exitoso
                if compression_info.get("skipped", False):
                    result["success"] = True
                return {**result, **compression_info}

            if temp_output is None:
                result["error"] = "Error interno: temp_output es None"
                return result

            use_gpu = compression_info["use_gpu"]
            has_spanish = compression_info["has_spanish"]
            use_remux = compression_info.get("use_remux", False)

            # Log de método seleccionado (independiente por archivo)
            if use_remux:
                method_str = "REMUX (AV1→MP4)"
            else:
                method_str = "GPU (VAAPI)" if use_gpu else "CPU (libx264)"
            self.logger.info(f"Procesando [{method_str}]: {file_path.name}")

            if not has_spanish:
                result["no_spanish"] = True
                self._log_no_spanish_info(file_path, audio_indices, sub_indices)

            # Ejecuta compresión (aislada) - ahora pasa audio_languages también
            process, elapsed_time = self._execute_compression_attempt(
                file_path, temp_output, use_gpu, audio_indices, sub_indices, use_remux, audio_languages
            )

            # Procesa resultado inicial
            compression_result = self._process_compression_result(
                process, file_path, temp_output, elapsed_time, use_gpu
            )

            # Lógica de fallback: manejar GPU, remux y reintentos sin subtítulos según sea necesario
            compression_result = self._handle_fallback_logic(
                compression_result,
                process,
                file_path,
                temp_output,
                use_gpu,
                audio_indices,
                sub_indices,
                audio_languages,
                use_remux=use_remux,
            )

            result.update(compression_result)

        except subprocess.TimeoutExpired:
            self._handle_timeout_error(result, file_path, temp_output)
        except MediaJellyError as e:
             result["error"] = str(e)
             if temp_output and temp_output.exists():
                 temp_output.unlink()
        except Exception as e:
            self._handle_unexpected_error(result, file_path, e, temp_output)

        return result

    def _ensure_audio_tags(self, file_path: Path) -> bool:
        """Remuxea el archivo para aplicar etiquetas de idioma en pistas de
        audio que no las tuviesen.

        Se ejecuta antes de saltar un archivo ya procesado si la configuración lo
        permite. Devuelve `True` si se hizo una acción (re-muxeado), `False`
        si no había nada que actualizar.
        """
        # obtener idiomas de cada pista (vacío en caso de desconocido)
        _, audio_indices, _, audio_languages = self.detect_language_streams(file_path)
        # comprobar si hay alguna pista sin etiqueta válida
        needs_update = any(lang in ("", "und") for lang in audio_languages.values())
        if not needs_update:
            return False

        # reconstruir el comando ffmpeg para copiar todo y escribir metadata
        cmd = ["ffmpeg", "-hide_banner", "-y", "-i", str(file_path)]
        for idx, lang in audio_languages.items():
            if lang and lang != "und":
                cmd.extend([f"-metadata:s:a:{idx}", f"language={lang}"])
        cmd.extend(["-c", "copy"])
        temp = file_path.with_suffix(file_path.suffix + ".tag")
        cmd.append(str(temp))

        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=LONG_TIMEOUT)
            if proc.returncode == 0 and temp.exists():
                temp.replace(file_path)
                self.logger.info(f"Etiquetas de audio actualizadas en {file_path.name}")
                return True
            else:
                self.logger.warning(
                    f"No se pudo aplicar etiquetas de audio a {file_path.name}: {proc.stderr}"
                )
        except Exception as e:
            self.logger.error(f"Error ejecutando ffmpeg para etiquetar audio: {e}")
        finally:
            if temp.exists():
                try:
                    temp.unlink()
                except Exception:
                    pass
        return False

    def _prepare_compression(self, file_path: Path) -> Tuple[bool, Dict, Optional[Path], List[int], List[int], Dict[int, str]]:
        """
        Prepara la compresión: validación, idiomas, método.

        Args:
            file_path: Ruta al archivo a procesar.

        Returns:
            Tuple conteniendo:
            - bool: Si la preparación fue exitosa.
            - Dict: Información de resultado (error, skipped, etc.) o configuración.
            - Path: Ruta temporal de salida (o None si falla).
            - List[int]: Índices de streams de audio.
            - List[int]: Índices de streams de subtítulos.
            - Dict[int, str]: Idiomas de streams de audio.
        """
        # Verificar si el archivo ya falló anteriormente (evitar reintentos infinitos)
        failed_files_path = self.failed_file
        if failed_files_path.exists():
            with open(failed_files_path, 'r', encoding='utf-8') as f:
                failed_content = f.read()
                if str(file_path) in failed_content:
                    # No saltamos automáticamente; permitimos reintentos y registramos que ya falló antes
                    self.logger.warning(f"{EmojiGenerator.warning_msg()} Archivo previamente fallido (se intentará nuevamente): {file_path.name}")

        # Validación
        try:
            self._validate_file_for_compression(file_path)
        except ValidationError as e:
            error_msg = str(e)
            result = {
                "skipped": error_msg == "archivo_pequeno",
                "error": error_msg if error_msg != "archivo_pequeno" else None,
            }
            return False, result, None, [], [], {}
        except FileNotFoundError as e:
            result = {"skipped": False, "error": str(e)}
            return False, result, None, [], [], {}

        # Idiomas - ahora retorna también audio_languages
        has_spanish, audio_indices, sub_indices, audio_languages = self.detect_language_streams(file_path)

        # Temporal
        temp_output = file_path.with_suffix(COMPRESSED_FILE_SUFFIX)

        # Detectar codec de video
        video_codec = self._get_video_codec(file_path)
        is_av1 = video_codec == "av1"

        # Verificar si ya está en codec eficiente
        efficient_codecs = {"h264", "hevc", "av1"}
        if video_codec in efficient_codecs and not is_av1:  # AV1 usa remux, otros eficientes se procesan normalmente
            # Para h264/hevc, verificar si el bitrate es razonable
            bitrate_info = self._get_video_bitrate(file_path)
            if bitrate_info and bitrate_info.get("bitrate_kbps", 0) < 9000:  # Menos de 9 Mbps (aumentado de 8)
                self.logger.info(
                    f"{EmojiGenerator.check()} Archivo ya optimizado ({video_codec}, {bitrate_info['bitrate_kbps']}kbps), usando remux para conversión de contenedor: {file_path.name}"
                )
                use_gpu = False
                use_remux = True  # Usar remux en lugar de compresión
            else:
                use_gpu = self._should_use_gpu_compression(file_path)
                use_remux = False
        elif is_av1:
            use_gpu = False  # No usar GPU para remux
            use_remux = True
            self.logger.info(
                f"{EmojiGenerator.refresh()} Archivo AV1 detectado, usando remux (sin recodificación): {file_path.name}"
            )
        else:
            use_gpu = self._should_use_gpu_compression(file_path)
            use_remux = False

        return (
            True,
            {"use_gpu": use_gpu, "has_spanish": has_spanish, "use_remux": use_remux},
            temp_output,
            audio_indices,
            sub_indices,
            audio_languages,
        )

    def _execute_compression_attempt(
        self,
        file_path: Path,
        temp_output: Path,
        use_gpu: bool,
        audio_indices: List[int],
        sub_indices: List[int],
        use_remux: bool = False,
        audio_languages: Optional[Dict[int, str]] = None,
        skip_subtitles: bool = False,
    ) -> Tuple[subprocess.CompletedProcess, float]:
        """
        Ejecuta un intento de compresión o remux usando ffmpeg.

        Args:
            file_path: Ruta del archivo original.
            temp_output: Ruta del archivo temporal de salida.
            use_gpu: Si True, intenta usar aceleración por hardware (VAAPI).
            audio_indices: Lista de índices de streams de audio a conservar.
            sub_indices: Lista de índices de streams de subtítulos a conservar.
            use_remux: Si True, usa modo remux (copia de streams) en lugar de recodificar.
            audio_languages: Diccionario de idiomas de audio {index: lang_code}.

        Returns:
            Tuple conteniendo:
            - subprocess.CompletedProcess: Resultado del proceso ffmpeg.
            - float: Tiempo transcurrido en segundos.
        """
        if audio_languages is None:
            audio_languages = {}

        if skip_subtitles:
            ffmpeg_cmd = self._build_ffmpeg_command_no_subtitles(file_path, temp_output, audio_indices, audio_languages)
            cmd_str = " ".join(ffmpeg_cmd[:10]) + " ... [CPU/audio/no-subs]"
            self.logger.info(f"Comando ffmpeg NO-SUBS: {cmd_str}")
        elif use_remux:
            # Usar remux para archivos AV1 (sin recodificación)
            ffmpeg_cmd = self._build_remux_command(file_path, temp_output, audio_indices, sub_indices, audio_languages)
            cmd_str = " ".join(ffmpeg_cmd[:8]) + " ... [REMUX/copy/output]"
            self.logger.info(f"Comando ffmpeg REMUX: {cmd_str}")
        elif use_gpu:
            ffmpeg_cmd = self._build_ffmpeg_command(file_path, temp_output, audio_indices, sub_indices, audio_languages)
            cmd_str = " ".join(ffmpeg_cmd[:10]) + " ... [GPU/audio/subs/output]"
            self.logger.info(f"Comando ffmpeg GPU: {cmd_str}")
        else:
            ffmpeg_cmd = self._build_fallback_ffmpeg_command(
                file_path, temp_output, audio_indices, sub_indices, audio_languages
            )
            cmd_str = " ".join(ffmpeg_cmd[:8]) + " ... [CPU/audio/output]"
            self.logger.info(f"Comando ffmpeg CPU: {cmd_str}")

        start_time = time.time()
        process = subprocess.run(
            ffmpeg_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,  # Capturar stderr para obtener errores de FFmpeg
            text=True,
            timeout=FFMPEG_TIMEOUT,
        )
        elapsed_time = time.time() - start_time

        # Logear stderr corto en debug para facilitar diagnóstico sin llenar logs
        if process.stderr:
            short_err = process.stderr.strip().splitlines()[:6]
            self.logger.debug(f"FFmpeg stderr (short): {' | '.join(short_err)}")

        return process, elapsed_time

    def _handle_fallback_logic(
        self,
        compression_result: Dict,
        process: subprocess.CompletedProcess,
        file_path: Path,
        temp_output: Path,
        use_gpu: bool,
        audio_indices: List[int],
        sub_indices: List[int],
        audio_languages: Optional[Dict[int, str]] = None,
        use_remux: bool = False,
    ) -> Dict:
        """Maneja la lógica de fallback CPU"""
        if audio_languages is None:
            audio_languages = {}
        # Si hubo fallo y se intentó con GPU, intentar recodificar por CPU
        if not compression_result.get("success", False) and use_gpu:
            if "corrupto" in str(compression_result.get("error", "")):
                self.logger.warning(f"Archivo corrupto con GPU, intentando fallback CPU: {file_path.name}")
            else:
                self.logger.warning(f"Compresión GPU falló, intentando fallback CPU: {file_path.name}")

            if temp_output.exists():
                temp_output.unlink()
            process, new_elapsed_time = self._execute_compression_attempt(
                file_path, temp_output, False, audio_indices, sub_indices, False, audio_languages
            )
            compression_result = self._process_compression_result(
                process, file_path, temp_output, new_elapsed_time, False
            )

        # Si se intentó remux y falló, intentar una recodificación completa por CPU
        if not compression_result.get("success", False) and use_remux:
            self.logger.warning(f"Remux falló, intentando recodificación por CPU: {file_path.name}")
            if temp_output.exists():
                temp_output.unlink()
            process, new_elapsed_time = self._execute_compression_attempt(
                file_path, temp_output, False, audio_indices, sub_indices, False, audio_languages
            )
            compression_result = self._process_compression_result(
                process, file_path, temp_output, new_elapsed_time, False
            )

            # Si el fallo fue por subtítulos PGS/HDMV, reintentar sin subtítulos
            if (
                not compression_result.get("success", False)
                and process is not None
                and getattr(process, "stderr", "")
                and "Could not find codec parameters" in process.stderr
            ):
                self.logger.warning(f"Remux->CPU reencode falló por subtítulos, reintentando sin subtítulos: {file_path.name}")
                if temp_output.exists():
                    temp_output.unlink()
                process, new_elapsed_time = self._execute_compression_attempt(
                    file_path, temp_output, False, audio_indices, [], False, audio_languages, skip_subtitles=True
                )
                compression_result = self._process_compression_result(
                    process, file_path, temp_output, new_elapsed_time, False
                )

        # Error específico de subtítulos PGS / hdmv, intentar recodificar SIN subtítulos
        if (
            not compression_result.get("success", False)
            and process is not None
            and getattr(process, "stderr", "")
            and "Could not find codec parameters" in process.stderr
        ):
            self.logger.warning(f"Error de subtítulos detectado, reintentando sin subtítulos: {file_path.name}")
            if temp_output.exists():
                temp_output.unlink()
            process, new_elapsed_time = self._execute_compression_attempt(
                file_path,
                temp_output,
                False,
                audio_indices,
                [],
                False,
                audio_languages,
                skip_subtitles=True,
            )
            compression_result = self._process_compression_result(
                process, file_path, temp_output, new_elapsed_time, False
            )

        # Si después de todos los fallbacks el resultado sigue siendo fallo, registrarlo persistentemente
        if not compression_result.get("success", False):
            try:
                failed_files_path = self.failed_file
                reason = compression_result.get("error", "Fallo en compresión sin mensaje")
                self._add_to_failed_files(str(file_path), failed_files_path, reason)
                self.logger.warning(f"Archivo marcado como fallido después de fallback: {file_path.name}")
            except Exception as e:
                self.logger.error(f"Error marcando archivo como fallido: {e}")

        return compression_result

    def _wait_for_ffmpeg_children(self, timeout: int = 30) -> None:
        """Espera cortamente a procesos `ffmpeg` relacionados con este workspace antes de marcar
        el procesamiento como completado. Esto evita escribir 'completed' si aún quedan
        procesos externos/huérfanos que siguen comprimiendo archivos.

        Nota: hace un escaneo simple de `pgrep -af ffmpeg` y filtra por rutas del
        `media_dir` / `scripts_dir` o por el sufijo temporal usado (COMPRESSED_FILE_SUFFIX).
        No es 100% a prueba de falsos positivos, pero reduce la mayoría de casos donde
        se ve 100% mientras hay ffmpeg en segundo plano.
        """
        try:
            end_time = time.time() + timeout
            while time.time() < end_time:
                # Obtiene lista de procesos ffmpeg
                result = subprocess.run(["pgrep", "-af", "ffmpeg"], capture_output=True, text=True)
                if result.returncode != 0 or not result.stdout.strip():
                    # No hay ffmpeg activos
                    return

                lines = [l.strip() for l in result.stdout.splitlines() if l.strip()]
                # Filtra por procesos relacionados con las rutas del proyecto o sufijo temporal
                related = [
                    l
                    for l in lines
                    if str(self.media_dir) in l or str(self.scripts_dir) in l or COMPRESSED_FILE_SUFFIX in l
                ]
                if not related:
                    return

                # Si hay procesos relacionados, esperamos un poco y reintentamos
                self.logger.info(
                    f"Esperando {len(related)} proceso(s) ffmpeg relacionados a finalizar antes de marcar 'completed'..."
                )
                time.sleep(2)

        except Exception as e:
            self.logger.warning(f"Error comprobando procesos ffmpeg: {e}")
            return

    def _should_use_gpu_compression(self, file_path: Path) -> bool:
        """Decide si usar compresión GPU basado en historial y disponibilidad de hardware"""
        # Verifica si el hardware está disponible (test más completo)
        if not self._check_hardware_acceleration_available():
            return False

        # Archivos .ts (Transport Stream) tienen problemas con VAAPI, usar CPU
        if file_path.suffix.lower() == ".ts":
            self.logger.info(f"Usando CPU para {file_path.name}: formato .ts incompatible con VAAPI")
            return False

        # Archivos pequeños o con calidad ya baja van a CPU por eficiencia
        file_size_mb = file_path.stat().st_size / (1024 * 1024)
        if file_size_mb < 100:  # Archivos menores a 100MB van a CPU
            return False

        # Verifica si este archivo específico ha fallado antes con GPU
        # (Implementación de lógica de historial si es necesario)

        # Por defecto usa GPU para archivos grandes
        return True

    def _process_result(
        self, result: Dict, file_path: Path, current_errors: List[str], current_no_spanish: List[str]
    ) -> None:
        """Procesa el resultado de compresión de un archivo individual"""
        # Incremento de files_processed solo si el archivo realmente se procesó (no omitido por estar ya procesado)
        if not result.get("already_processed", False):
            self.stats.files_processed += 1

        if result["success"]:
            if result["compressed"]:
                self.stats.files_compressed += 1
            elif result["renamed"]:
                self.stats.files_renamed += 1
            elif result["skipped"]:
                self.stats.files_skipped += 1

        if result["error"]:
            self.logger.error(result["error"])
            current_errors.append(file_path.name)
            # Agrega el error a las estadísticas también
            if file_path.name not in self.stats.errors:
                self.stats.errors.append(file_path.name)

        if result["no_spanish"]:
            current_no_spanish.append(file_path.name)
            # Agrega el archivo sin español a las estadísticas también
            if file_path.name not in self.stats.no_spanish:
                self.stats.no_spanish.append(file_path.name)

    def _save_temporary_results(self, current_errors: List[str], current_no_spanish: List[str]) -> None:
        """Guarda errores y archivos sin español en archivos temporales"""
        if current_errors:
            with open(self.tmp_dir / "error_files.tmp", "w") as f:
                f.write("\n".join(current_errors))

        if current_no_spanish:
            with open(self.tmp_dir / "no_spanish_files.tmp", "w") as f:
                f.write("\n".join(current_no_spanish))

    def _initialize_processing(self, files: List[Path]) -> tuple[bool, int]:
        """Inicializa el procesamiento verificando aceleración y limpiando temporales"""
        if not files:
            self.logger.info("No hay archivos pendientes para comprimir")
            self._save_progress_state(0, 0, "Sin archivos pendientes", status="completed")
            return False, 0

        # PRIMERO: Eliminar archivos .compressed.mp4 huérfanos de procesos interrumpidos
        orphaned_count = self._remove_orphaned_compressed_files()
        if orphaned_count > 0:
            self.logger.info(f"Limpieza inicial: {orphaned_count} archivo(s) .compressed.mp4 huérfano(s) eliminado(s)")

        # Verifica disponibilidad de aceleración por hardware
        hw_acceleration_available = self._check_hardware_acceleration_available()
        if not hw_acceleration_available:
            self.logger.warning("Aceleración por hardware no disponible, usando solo CPU")
        else:
            self.logger.info("Aceleración por hardware disponible")

        total_files = len(files)
        optimal_workers = get_optimal_workers()
        self.logger.info(f"Iniciando procesamiento de {total_files} archivos con {optimal_workers} workers")

        # NOTA: No pre-cargar Whisper - cada worker lo cargará cuando necesite (archivo ya descargado)
        # Con 1 worker y modelo descargado, no habrá problemas de memoria

        # Limpia archivos temporales de ejecución anterior
        (self.tmp_dir / "error_files.tmp").unlink(missing_ok=True)
        (self.tmp_dir / "no_spanish_files.tmp").unlink(missing_ok=True)

        return True, total_files

    def _load_previous_progress(self, progress_info: dict) -> int:
        """Carga estadísticas y archivos procesados de progreso anterior"""
        current_file = 0

        # Carga estadísticas previas
        current_file = self._load_previous_stats(progress_info, current_file)

        # Carga archivos procesados previamente
        self._load_previous_processed_files(progress_info)

        return current_file

    def _reset_progress_if_corrupted(self, progress_info: dict) -> bool:
        """Resetea el progreso si está corrupto (porcentaje > 100%)"""
        current_percentage = progress_info.get("percentage", 0)
        if current_percentage > 100:
            self.logger.warning(f"Progreso corrupto detectado ({current_percentage:.1f}%) - Reseteando completamente")

            # Crear nuevo estado de progreso limpio
            clean_progress = {
                "processing": {
                    "current_file": 0,
                    "total_files": 0,
                    "current_file_name": "",
                    "percentage": 0.0,
                    "last_updated": datetime.now().isoformat(),
                    "status": "idle",
                    "notified": False,
                    "over_100_notified": False,
                    "stats": {
                        "files_found": 0,
                        "files_new": 0,
                        "files_processed": 0,
                        "files_compressed": 0,
                        "files_renamed": 0,
                        "files_skipped": 0,
                        "total_original_size": 0,
                        "total_compressed_size": 0,
                        "errors": [],
                        "no_spanish": [],
                    },
                    "processed_files": {},
                    "last_notification": None,
                    "last_cleanup_notification": None,
                    "last_cleanup_files_processed": 0,
                    "last_cleanup_timestamp": datetime.now().isoformat(),
                }
            }

            # Si existe sección de subtítulos, mantenerla
            if "subtitle_translation" in progress_info:
                clean_progress["subtitle_translation"] = progress_info["subtitle_translation"]

            # Guardar progreso limpio
            try:
                with open(self.tmp_dir / PROGRESS_FILE_NAME, "w") as f:
                    json.dump(clean_progress, f, indent=2)
                self.logger.info("Progreso reseteado completamente")
                return True
            except Exception as e:
                self.logger.error(f"Error reseteando progreso: {e}")
                return False

        return False

    def _load_previous_stats(self, progress_info: dict, current_file: int) -> int:
        """Carga estadísticas previas del progreso"""
        prev_stats = progress_info.get("stats", {})

        self.stats.files_found = prev_stats.get("files_found", 0)
        self.stats.files_new = prev_stats.get("files_new", 0)
        self.stats.files_processed = prev_stats.get("files_processed", 0)
        self.stats.files_compressed = prev_stats.get("files_compressed", 0)
        self.stats.files_renamed = prev_stats.get("files_renamed", 0)
        self.stats.files_skipped = prev_stats.get("files_skipped", 0)
        self.stats.total_original_size = prev_stats.get("total_original_size", 0)
        self.stats.total_compressed_size = prev_stats.get("total_compressed_size", 0)

        if "errors" in prev_stats:
            self.stats.errors = prev_stats["errors"]
        if "no_spanish" in prev_stats:
            self.stats.no_spanish = prev_stats["no_spanish"]

        return progress_info.get("current_file", 0)

    def _load_previous_processed_files(self, progress_info: dict) -> None:
        """Carga archivos procesados previamente"""
        if "processed_files" in progress_info and progress_info["processed_files"]:
            self.processed_files = progress_info["processed_files"]
        elif (
            len(progress_info.get("processed_files", {})) == 0
            and progress_info.get("stats", {}).get("files_processed", 0) > 0
        ):
            # Si processed_files vacío pero hay archivos procesados, reconstruir de completed.txt
            self._rebuild_processed_files_from_completed()

    def _rebuild_processed_files_from_completed(self) -> None:
        """Reconstruye processed_files desde completed.txt"""
        completed_files = set()
        if self.completed_file.exists():
            with open(self.completed_file, "r") as f:
                for line in f:
                    file_path = line.strip()
                    if file_path:
                        completed_files.add(file_path)

        self.processed_files = {
            path: {
                "status": "success",
                "timestamp": datetime.now().isoformat(),
                "label": Path(path).suffix.lower(),
            }
            for path in completed_files
        }
        self.logger.info(f"Reconstruyendo processed_files de completed.txt: {len(self.processed_files)} archivos")

    def _filter_already_processed_files(self, files: List[Path]) -> List[Path]:
        """Filtra archivos ya procesados exitosamente.

        - Si `reprocess_on_label_change` está habilitado y la etiqueta (sufijo)
          del archivo actual difiere de la guardada, se volverá a procesar.
        - Antes de descartar un archivo, si `apply_audio_tagging_on_skip` está
          activado, se intentará etiquetar pistas de audio sin idioma.
        """
        files_before_filter = len(files)
        remaining = []

        for f in files:
            key = str(f)
            skip = False

            if key in self.processed_files and self.processed_files[key]["status"] == "success":
                # comprobar si debemos volver a procesar por cambio de etiqueta
                if (
                    self.config
                    and self.config.processing.reprocess_on_label_change
                ):
                    prev_label = self.processed_files[key].get("label")
                    if prev_label and f.suffix.lower() != prev_label:
                        # etiqueta diferente -> no omitir
                        skip = False
                    else:
                        # mismo sufijo, aplicar lógica normal
                        if f.exists() and f.suffix.lower() == ".mp4":
                            skip = True
                else:
                    if f.exists() and f.suffix.lower() == ".mp4":
                        skip = True

                # intentar etiquetar audio aunque se vaya a omitir
                if skip and self.config and self.config.processing.apply_audio_tagging_on_skip:
                    try:
                        self._ensure_audio_tags(f)
                    except Exception as e:
                        self.logger.error(f"Error etiquetando audio de {f}: {e}")

            if not skip:
                remaining.append(f)

        files_filtered = files_before_filter - len(remaining)
        if files_filtered > 0:
            self.logger.info(f"Archivos ya procesados exitosamente omitidos: {files_filtered}")
        return remaining

    def _process_files_with_executor(
        self, files: List[Path], total_files: int, current_file: int
    ) -> tuple[int, list[str], list[str]]:
        """Procesa archivos con executor y actualiza progreso después de cada compresión"""
        current_errors: List[str] = []
        current_no_spanish: List[str] = []
        processed_count = current_file

        optimal_workers = get_optimal_workers()
        with ProcessPoolExecutor(max_workers=optimal_workers) as executor:
            future_to_file = {
                executor.submit(self.compress_single_file, str(file_path)): file_path for file_path in files
            }
            files_in_progress = [f.name for f in files]

            # Procesa resultados conforme van completando
            for future in as_completed(future_to_file):
                file_path = future_to_file[future]
                self._process_single_future_result(future, file_path, current_errors, current_no_spanish)
                processed_count += 1
                self._update_progress_after_processing(file_path, processed_count, total_files, files_in_progress)

        return processed_count, current_errors, current_no_spanish

    def process_files_concurrent(self, files: List[Path]) -> ProcessingStats:
        """Procesa archivos con concurrencia limitada y seguimiento de progreso"""
        start_time = time.time()

        # Inicializa procesamiento
        initialized, total_files_found = self._initialize_processing(files)
        if not initialized:
            return self.stats

        # Carga progreso anterior si existe
        progress_info = self.get_progress_info()

        # Carga estadísticas y archivos procesados de progreso anterior
        current_file = self._load_previous_progress(progress_info)

        # Filtra archivos ya procesados exitosamente
        files = self._filter_already_processed_files(files)

        # Guarda el total de archivos encontrados (escaneados) para cálculos de progreso
        files_pending = len(files)
        if files_pending == 0:
            self.logger.info("Todos los archivos ya han sido procesados exitosamente")
            # Espera procesos ffmpeg relacionados antes de marcar completado
            try:
                self._wait_for_ffmpeg_children(timeout=20)
            except Exception:
                pass
            # Envía notificación de completado y marca como completado
            self._send_completion_notification()
            self._save_progress_state(0, total_files_found, PROCESSING_COMPLETED_MESSAGE, status="completed")
            return self.stats

        optimal_workers = get_optimal_workers()
        self.logger.info(
            f"Procesando {files_pending} archivos pendientes de {total_files_found} encontrados con {optimal_workers} workers"
        )

        # Procesa archivos con executor (actualiza progreso después de cada compresión)
        current_file, current_errors, current_no_spanish = self._process_files_with_executor(
            files, total_files_found, current_file
        )

        # Guarda errores y archivos sin español de esta ejecución
        self._save_temporary_results(current_errors, current_no_spanish)

        # Actualiza progreso como completado
        # Espera procesos ffmpeg relacionados antes de marcar completado (si quedan)
        try:
            self._wait_for_ffmpeg_children(timeout=60)
        except Exception:
            pass
        # Envía notificación de completado y marca como completado
        self._send_completion_notification()
        self._save_progress_state(current_file, total_files_found, PROCESSING_COMPLETED_MESSAGE, status="completed")

        # Log de resumen
        self.logger.info(
            f"RESUMEN: Procesados: {self.stats.files_processed}, "
            f"Comprimidos: {self.stats.files_compressed}, "
            f"Renombrados: {self.stats.files_renamed}, "
            f"Omitidos: {self.stats.files_skipped}"
        )

        return self.stats

    def _process_single_future_result(
        self, future, file_path: Path, current_errors: list, current_no_spanish: list
    ) -> None:
        """Procesa el resultado de un futuro individual"""
        try:
            result = future.result()
            self._update_stats_from_result(result, file_path, current_errors, current_no_spanish)

        except Exception as e:
            error_msg = f"Error procesando {file_path}: {str(e)}"
            self.logger.error(error_msg)
            current_errors.append(file_path.name)
            # Agregar a stats.errors también
            if file_path.name not in self.stats.errors:
                self.stats.errors.append(file_path.name)
            self.processed_files[str(file_path)] = {
                "status": "failed",
                "timestamp": datetime.now().isoformat(),
                "error": str(e),
            }

    def _update_size_stats(self, result: dict) -> None:
        """Actualiza estadísticas de tamaño"""
        if "original_size" in result and "compressed_size" in result:
            self.stats.total_original_size += result["original_size"]
            self.stats.total_compressed_size += result["compressed_size"]

    def _update_stats_from_result(
        self, result: dict, file_path: Path, current_errors: list, current_no_spanish: list
    ) -> None:
        """Actualiza estadísticas desde el resultado de procesamiento"""
        # Incrementa contador de archivos procesados
        self.stats.files_processed += 1

        # Actualiza contadores específicos según el resultado
        if result.get("compressed", False):
            self.stats.files_compressed += 1
        if result.get("renamed", False):
            self.stats.files_renamed += 1
        if result.get("skipped", False):
            self.stats.files_skipped += 1

        # Maneja errores
        if result.get("error"):
            current_errors.append(file_path.name)
            # Agregar a stats.errors también
            if file_path.name not in self.stats.errors:
                self.stats.errors.append(file_path.name)
            self.processed_files[str(file_path)] = {
                "status": "failed",
                "timestamp": datetime.now().isoformat(),
                "error": result["error"],
            }
        else:
            self.processed_files[str(file_path)] = {"status": "success", "timestamp": datetime.now().isoformat()}

        # Maneja archivos sin español
        if result.get("no_spanish", False):
            current_no_spanish.append(file_path.name)
            # Agregar a stats.no_spanish también
            if file_path.name not in self.stats.no_spanish:
                self.stats.no_spanish.append(file_path.name)

        # Actualiza estadísticas de tamaño
        self._update_size_stats(result)

    def _update_progress_after_processing(
        self, file_path: Path, processed_count: int, total_files: int, files_in_progress: list
    ) -> None:
        """Actualiza progreso después del procesamiento de un archivo"""
        # Remueve el archivo de la lista de procesamiento
        if file_path.name in files_in_progress:
            files_in_progress.remove(file_path.name)

        # Construye mensaje de archivos en procesamiento
        current_file_name = self._build_progress_message(files_in_progress)

        # Actualiza progreso cada archivo procesado para mejor precisión
        if processed_count % 1 == 0 or processed_count == total_files:
            self._save_progress_state(processed_count, total_files, current_file_name, status="processing")

        # Log progreso cada archivo o al final
        if processed_count % 1 == 0 or processed_count == total_files:
            percentage = min(100.0, (processed_count / total_files) * 100)
            self.logger.info(
                f"Progreso: {processed_count}/{total_files} ({percentage:.1f}%) - Archivo procesado: {file_path.name}"
            )

    def _build_progress_message(self, files_in_progress: list) -> str:
        """Construye mensaje de archivos actualmente en procesamiento"""
        if not files_in_progress:
            return "Procesamiento completado"

        if len(files_in_progress) == 1:
            return f"Procesando: {files_in_progress[0]}"

        # Muestra hasta 3 archivos
        files_to_show = files_in_progress[:3]
        message = f"Procesando {len(files_in_progress)} archivos: {', '.join(files_to_show)}"

        if len(files_in_progress) > 3:
            message += f" y {len(files_in_progress) - 3} más"

        return message

    def run(self) -> ProcessingStats:
        """Ejecuta procesamiento completo"""
        try:
            self.logger.info("=== Iniciando MediaJelly Python ===")

            # Al iniciar el procesamiento, marca notified=False en la sección processing
            progress_file = self.tmp_dir / PROGRESS_FILE_NAME
            existing_progress = {}
            if progress_file.exists():
                try:
                    with open(progress_file, "r") as f:
                        existing_progress = json.load(f)
                except Exception:
                    existing_progress = {}

            # Asegurar que existe la sección processing
            if "processing" not in existing_progress:
                existing_progress["processing"] = {
                    "current_file": 0,
                    "total_files": 0,
                    "current_file_name": "",
                    "percentage": 0.0,
                    "last_updated": datetime.now().isoformat(),
                    "status": "idle",
                    "notified": False,
                    "stats": {
                        "files_found": 0,
                        "files_new": 0,
                        "files_processed": 0,
                        "files_compressed": 0,
                        "files_renamed": 0,
                        "files_skipped": 0,
                        "total_original_size": 0,
                        "total_compressed_size": 0,
                        "errors": [],
                        "no_spanish": [],
                    },
                    "processed_files": {},
                }

            existing_progress["processing"]["notified"] = False
            existing_progress["processing"]["status"] = "processing"

            with open(progress_file, "w") as f:
                json.dump(existing_progress, f, indent=2)
            self.logger.info("Estado de notificación reseteado (notified=False)")

            # Limpia archivos pendientes procesados antes de escanear
            self.cleanup_processed_files()

            # Escanea archivos
            files = self.scan_media_files()

            if not files:
                self.logger.info("No hay archivos pendientes para procesar")
                # Verifica si todos los archivos pendientes ya están procesados
                self._check_all_pending_processed()
                return self.stats

            # Procesa archivos
            self.stats = self.process_files_concurrent(files)

            # Limpia archivos pendientes procesados exitosamente (de nuevo por si acaso)
            self.cleanup_processed_files()

            # Verifica si todos los archivos pendientes ya están procesados
            self._check_all_pending_processed()

            self.logger.info("=== MediaJelly Python completado ===")

        except Exception as e:
            self.logger.error(f"Error fatal en MediaJelly: {e}")
        finally:
            # Siempre libera el bloqueo
            pass

        return self.stats

    def cleanup_processed_files(self):
        """Limpia archivos procesados exitosamente del archivo pending"""
        if not self.pending_file.exists():
            return

        completed_files = self._load_completed_files()

        # Lee archivos pendientes actuales y filtra
        remaining_pending, orphaned_files, files_removed = self._filter_pending_files(completed_files)

        # Reescribe el archivo con solo los archivos válidos pendientes
        self._rewrite_pending_file(remaining_pending)

        orphaned_count = len(orphaned_files)
        files_kept = len(remaining_pending)

        self._log_cleanup_results(files_removed, orphaned_count, files_kept, orphaned_files)

    def _load_completed_base_paths(self) -> set:
        """Carga las rutas base de archivos completados"""
        completed_base_paths = set()
        if self.completed_file.exists():
            with open(self.completed_file, "r") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        path_obj = Path(line)
                        base_path = str(path_obj.parent / path_obj.stem)
                        completed_base_paths.add(base_path)
        return completed_base_paths

    def _filter_pending_files(self, completed_files: set) -> Tuple[List[str], List[str], int]:
        """Filtra archivos pendientes, separando válidos y huérfanos"""
        remaining_pending = []
        orphaned_files = []
        files_removed = 0

        with open(self.pending_file, "r") as f:
            for line in f:
                file_path = line.strip()
                if not file_path:
                    continue

                result = self._get_pending_file_action(file_path, completed_files)
                if result == "remove":
                    files_removed += 1
                elif result == "orphan":
                    orphaned_files.append(file_path)
                    files_removed += 1
                else:
                    remaining_pending.append(file_path)

        return remaining_pending, orphaned_files, files_removed

    def _get_pending_file_action(self, file_path: str, completed_files: set) -> str:
        """Procesa una línea individual del archivo pending y retorna la acción a tomar"""
        pending_path_obj = Path(file_path)

        # 1. Si ya está completado (procesado exitosamente), remover de pending
        # Verificar tanto la ruta original como la ruta con extensión .mp4 (por si fue comprimido)
        if file_path in completed_files:
            return "remove"

        # También verificar si existe una versión .mp4 del archivo en completed
        mp4_version = str(pending_path_obj.with_suffix(".mp4"))
        if mp4_version in completed_files:
            return "remove"

        # 2. Si el archivo original no existe (huérfano), remover
        if not pending_path_obj.exists():
            self.logger.warning(f"Archivo huérfano removido de pending (no existe): {pending_path_obj.name}")
            return "orphan"

        # 3. Remover archivos fallidos persistentemente
        if self._should_remove_failed_file(file_path, pending_path_obj):
            return "remove"

        # 4. Archivo válido, mantener en pending
        return "keep"

    def _should_remove_failed_file(self, file_path: str, pending_path_obj: Path) -> bool:
        """Determina si un archivo fallido debe ser removido de pending"""
        if file_path not in self.processed_files or self.processed_files[file_path].get("status") != "failed":
            return False

        failed_files_path = self.failed_file

        # Para archivos .ts fallidos, remover inmediatamente
        if pending_path_obj.suffix.lower() == ".ts":
            self.logger.warning(f"Removiendo archivo .ts fallido de pendientes: {file_path}")
            self._add_to_failed_files(file_path, failed_files_path, "Formato .ts incompatible con VAAPI")
            return True

        # Para otros formatos, verificar si han fallado recientemente
        failed_info = self.processed_files[file_path]
        if "timestamp" in failed_info:
            try:
                failed_time = datetime.fromisoformat(failed_info["timestamp"])
                if (datetime.now() - failed_time).total_seconds() > 86400:  # 24 horas
                    self.logger.warning(f"Removiendo archivo fallido antiguo de pendientes: {file_path}")
                    self._add_to_failed_files(file_path, failed_files_path, "Falló múltiples veces")
                    return True
            except (ValueError, TypeError):
                self.logger.warning(f"Removiendo archivo fallido con timestamp inválido de pendientes: {file_path}")
                self._add_to_failed_files(file_path, failed_files_path, "Timestamp inválido")
                return True

        return False

    def _add_to_failed_files(self, file_path: str, failed_files_path: Path, reason: str) -> None:
        """Agrega un archivo a la lista de archivos fallidos con su razón"""
        try:
            # Leer archivos fallidos existentes para evitar duplicados
            existing_failed = set()
            if failed_files_path.exists():
                with open(failed_files_path, "r") as f:
                    for line in f:
                        if line.strip():
                            # Extraer solo la ruta del archivo (sin timestamp ni razón)
                            parts = line.strip().split(" | ")
                            if parts:
                                existing_failed.add(parts[0])

            # Agregar solo si no existe
            if file_path not in existing_failed:
                with open(failed_files_path, "a") as f:
                    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    f.write(f"{file_path} | {timestamp} | {reason}\n")
                self.logger.info(f"Archivo agregado a failed-compression.txt: {Path(file_path).name}")
        except Exception as e:
            self.logger.error(f"Error agregando archivo a failed-compression.txt: {e}")

    def _rewrite_pending_file(self, remaining_pending: List[str]) -> None:
        """Reescribe el archivo de pendientes con archivos válidos"""
        with open(self.pending_file, "w") as f:
            for file_path in remaining_pending:
                f.write(f"{file_path}\n")

    def _log_cleanup_results(
        self, files_removed: int, orphaned_count: int, files_kept: int, orphaned_files: List[str]
    ) -> None:
        """Loggea los resultados de la limpieza"""
        self.logger.info(f"Archivos procesados removidos de pendientes: {files_removed}")
        if orphaned_count > 0:
            self.logger.warning(f"Archivos huérfanos encontrados y removidos: {orphaned_count}")
            for orphaned in orphaned_files:
                self.logger.warning(f"  Huérfano: {orphaned}")
        self.logger.info(f"Archivos mantenidos en pendientes: {files_kept}")

        if files_removed > 0 or orphaned_count > 0:
            self.logger.info("Limpieza de lista de pendientes completada")

    def _check_all_pending_processed(self) -> None:
        """Verifica si todos los archivos pendientes ya están procesados y termina si es así"""
        if not self.pending_file.exists():
            self.logger.info("No existe archivo de pendientes")
            return

        # Lee archivos pendientes actuales
        with open(self.pending_file, "r") as f:
            pending_lines = [line.strip() for line in f if line.strip()]

        if not pending_lines:
            self.logger.info("No hay archivos pendientes - todos procesados")
            return

        # Carga archivos completados para comparación
        completed_files = self._load_completed_files()

        # Normaliza rutas para comparación (convierte rutas absolutas a relativas desde media_dir)
        def normalize_path(path_str: str) -> str:
            """Normaliza una ruta para comparación"""
            path_obj = Path(path_str)

            # Reemplaza diferentes prefijos de ruta para normalizar
            path_str_normalized = str(path_obj)
            path_str_normalized = path_str_normalized.replace("/home/tafurc/mediaJelly/", "")
            path_str_normalized = path_str_normalized.replace("/mediajelly/", "")

            return path_str_normalized

        # Verifica cada archivo pendiente
        all_processed = True
        for pending_path in pending_lines:
            # Normaliza la ruta pendiente
            normalized_pending = normalize_path(pending_path)

            # Verifica si está en archivos completados (comparando rutas normalizadas)
            found_in_completed = False
            for completed_path in completed_files:
                normalized_completed = normalize_path(completed_path)
                if normalized_pending == normalized_completed:
                    found_in_completed = True
                    break

            if not found_in_completed:
                all_processed = False
                break

        if all_processed:
            self.logger.info(f"Todos los {len(pending_lines)} archivos pendientes ya están procesados - terminando")
            # Limpia el archivo de pendientes ya que todos están procesados
            self.pending_file.unlink(missing_ok=True)
        else:
            remaining_count = len(pending_lines)
            self.logger.info(f"Aún quedan {remaining_count} archivos pendientes por procesar")

    def _send_completion_notification(self) -> None:
        """Envía notificación de completado cuando llega al 100%"""
        try:
            notifier_script = self.scripts_dir / "mediajelly_notifier.py"
            if not notifier_script.exists():
                self.logger.warning("Script de notificaciones no encontrado, omitiendo notificación")
                return

            # Determinar status basado en si hubo errores
            status = "error" if self.stats.errors else "success"

            # Preparar comando de notificación
            cmd = [
                sys.executable,
                str(notifier_script),
                "scan_result",
                status,
                str(self.stats.files_found),
                str(self.stats.files_new),
                str(self.stats.files_processed),
                str(self.stats.files_compressed),
                str(self.stats.files_renamed),
                str(self.stats.files_skipped),
                "0",  # subtitles_translated (no aplicable en processor)
                "0",  # subtitles_errors (no aplicable en processor)
            ]

            self.logger.info("Enviando notificación de completado...")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)

            if result.returncode == 0:
                self.logger.info("Notificación de completado enviada correctamente")
            else:
                self.logger.error(f"Error enviando notificación de completado: {result.stderr}")

        except Exception as e:
            self.logger.error(f"Error enviando notificación de completado: {e}")

    def _send_over_100_notification(
        self, raw_percentage: float, current_file: int, total_files: int, files_new: int
    ) -> None:
        """Envía notificación de aviso cuando el progreso excede 100%"""
        try:
            notifier_script = self.scripts_dir / "mediajelly_notifier.py"
            if not notifier_script.exists():
                self.logger.warning("Script de notificaciones no encontrado, omitiendo notificación de progreso >100%")
                return

            error_message = f"{EmojiGenerator.warning_msg()} AVISO: Progreso excedió 100% ({raw_percentage:.1f}%) - Revisar estado del sistema"

            # Preparar comando de notificación de procesamiento con información real
            cmd = [
                sys.executable,
                str(notifier_script),
                "scan_result",
                "warning",
                str(total_files),  # files_found
                str(files_new),  # files_new
                str(current_file),  # files_processed
                str(self.stats.files_compressed),  # files_compressed
                str(self.stats.files_renamed),  # files_renamed
                str(self.stats.files_skipped),  # files_skipped
            ]

            self.logger.warning(f"Enviando notificación de progreso >100%: {raw_percentage:.1f}%")
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)

            if result.returncode == 0:
                self.logger.info("Notificación de progreso >100% enviada correctamente")
            else:
                self.logger.error(f"Error enviando notificación de progreso >100%: {result.stderr}")

        except Exception as e:
            self.logger.error(f"Error enviando notificación de progreso >100%: {e}")


class VideoQualityImprover:
    """Clase para mejorar la calidad de videos sin aumentar significativamente el tamaño"""

    def __init__(self, logger):
        self.logger = logger

    def improve_video_quality(self, input_file: Path, output_file: Path, has_spanish: bool) -> bool:
        """Aplica mejoras de calidad al video sin aumentar el tamaño significativamente

        Mejoras aplicadas:
        - Normalización de audio (mejor volumen consistente)
        - Mejora de metadatos
        - Optimización leve de colores/contraste
        - Mejora de subtítulos embebidos (si los hay)
        """
        try:
            self.logger.info(f"Aplicando mejoras de calidad a: {input_file.name}")

            # Verificar que el archivo de entrada existe
            if not input_file.exists():
                self.logger.error(f"Archivo de entrada no existe: {input_file}")
                return False

            # Comando ffmpeg para mejoras de calidad
            ffmpeg_cmd = self._build_quality_improvement_command(input_file, output_file, has_spanish)

            self.logger.info("Ejecutando mejoras de calidad...")
            start_time = time.time()

            result = subprocess.run(ffmpeg_cmd, capture_output=True, text=True, timeout=FFMPEG_TIMEOUT)

            elapsed_time = time.time() - start_time

            if result.returncode == 0 and output_file.exists():
                self.logger.info(f"Mejoras de calidad aplicadas exitosamente en {elapsed_time:.1f}s")
                return True
            else:
                self.logger.error(f"Error aplicando mejoras de calidad: código {result.returncode}")
                if result.stderr:
                    self.logger.error(f"FFmpeg stderr: {result.stderr[:500]}...")
                return False

        except subprocess.TimeoutExpired:
            self.logger.error(f"Timeout aplicando mejoras de calidad ({FFMPEG_TIMEOUT/3600:.1f}h): {input_file.name}")
            return False
        except Exception as e:
            self.logger.error(f"Error aplicando mejoras de calidad: {e}")
            return False

    def _build_quality_improvement_command(self, input_file: Path, output_file: Path, has_spanish: bool) -> List[str]:
        """Construye comando ffmpeg para mejoras de calidad"""
        cmd = ["ffmpeg", "-hide_banner", "-y"]

        # Input
        cmd.extend(["-i", str(input_file)])

        # Filtros de video más simples para no aumentar significativamente el tamaño
        video_filters = []

        # Solo mejora leve de contraste/color (muy sutil)
        video_filters.append("eq=contrast=1.02:brightness=0.01:saturation=1.05")

        # Reducción muy leve de ruido
        video_filters.append("hqdn3d=1:1:3:3")

        # Aplicar filtros de video con codec especificado
        if video_filters:
            cmd.extend(["-vf", ",".join(video_filters)])
            cmd.extend(["-c:v", "libx264", "-preset", "ultrafast", "-crf", "18"])

        # Audio: normalización simple
        cmd.extend(["-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-c:a", "aac", "-b:a", "128k"])

        # Subtítulos si tiene español
        if has_spanish:
            cmd.extend(
                [
                    "-map",
                    "0:s:m:language:spa?",
                    "-map",
                    "0:s:m:language:esp?",
                    "-map",
                    "0:s:m:language:es?",
                    "-map",
                    "0:s:m:language:lat?",
                    "-c:s",
                    "mov_text",
                ]
            )

        # Metadatos mejorados
        cmd.extend(
            [
                "-metadata",
                "title=" + input_file.stem,
                "-metadata",
                "artist=MediaJelly",
                "-metadata",
                "comment=Procesado con mejoras de calidad",
                "-metadata",
                "creation_time=" + datetime.now().isoformat(),
                "-movflags",
                MOVFLAGS_FASTSTART,
                "-avoid_negative_ts",
                "make_zero",
                str(output_file),
            ]
        )

        return cmd


def main():
    """Función principal"""
    processor = MediaJellyProcessor()

    # Registrar guardado de progreso al salir
    atexit.register(
        lambda: processor._save_progress_state(
            processor.stats.files_processed, processor.stats.files_found, "Terminado por atexit", status="interrupted"
        )
    )

    # Manejo de señales para terminación limpia
    def signal_handler(signum, frame):
        processor.logger.info(f"Recibida señal {signum}, guardando progreso y terminando...")
        # Usa las estadísticas actuales en memoria para guardar el progreso
        current_file = processor.stats.files_processed
        total_files = processor.stats.files_found
        current_file_name = f"Terminado por señal {signum}"

        # Guarda el progreso con las estadísticas actuales
        processor._save_progress_state(current_file, total_files, current_file_name, status="interrupted")
        # En lugar de sys.exit(0), lanza KeyboardInterrupt para una terminación limpia
        raise KeyboardInterrupt(f"Señal {signum} recibida")

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)

    # Ejecuta procesamiento
    try:
        stats = processor.run()

        # Imprime estadísticas
        print(f"Procesados: {stats.files_processed}")
        print(f"Comprimidos: {stats.files_compressed}")
        print(f"Renombrados: {stats.files_renamed}")
        print(f"Omitidos: {stats.files_skipped}")
    except KeyboardInterrupt:
        processor.logger.info("Procesamiento interrumpido por señal")
        print("Procesamiento interrumpido")


if __name__ == "__main__":
    main()
