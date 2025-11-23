#!/usr/bin/env python3
"""
MediaJelly Subtitle Translator
Extrae y traduce subtítulos al español automáticamente
Soporta extracción desde audio usando Whisper
"""

import os
import sys
import subprocess
import json
import logging
import re
import fcntl
import time
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, asdict
from typing import Optional, List, Tuple, Dict, Any, Union
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

from mediajelly_emoji import EmojiGenerator

# Importar configuración centralizada
try:
    sys.path.append(str(Path(__file__).parent))
    from mediajelly_config import get_config
    CONFIG_AVAILABLE = True
except ImportError:
    MediaJellyConfig = None
    CONFIG_AVAILABLE = False

# Importaciones opcionales con manejo de errores
try:
    import whisper  # type: ignore

    WHISPER_AVAILABLE = True
except ImportError:
    whisper = None
    WHISPER_AVAILABLE = False

try:
    from langdetect import detect as langdetect_detect  # type: ignore

    LANGDETECT_AVAILABLE = True
except ImportError:
    langdetect_detect = None
    LANGDETECT_AVAILABLE = False

try:
    from translatepy.translators.yandex import YandexTranslate  # type: ignore

    YANDEX_AVAILABLE = True
except ImportError:
    YandexTranslate = None
    YANDEX_AVAILABLE = False

# Lock global para Whisper (solo un proceso a la vez)
whisper_lock = threading.Lock()

# Constantes globales
CONTAINER_PATH = "/mediajelly"
SPANISH_SUBTITLE_SUFFIX = ".es.srt"

# Constantes de estado de progreso
STATUS_PROCESSING = "processing"
STATUS_COMPLETED = "completed"

# Constantes de rutas
MEDIAJELLY_PATH = "/mediajelly"
HOST_MEDIAJELLY_PATH = "/home/tafurc/mediaJelly"

# Configuración de timeouts
FFPROBE_TIMEOUT = 120  # segundos
FFPROBE_QUICK_TIMEOUT = 30  # segundos para verificación rápida
EXCLUDED_FOLDERS = {".delete", ".deleted", ".tmp", ".temp", ".trash", ".recycle"}


class SubtitleQualityImprover:
    """Mejora la calidad de los subtítulos extraídos y traducidos"""

    def __init__(self, logger=None):
        self.logger = logger

    def improve_subtitle_quality(self, srt_content: str, source_language: str = "auto") -> str:
        """
        Aplica mejoras de calidad al contenido de subtítulos.

        Args:
            srt_content: Contenido del archivo SRT.
            source_language: Idioma de origen ('auto' para detectar automáticamente).

        Returns:
            str: Contenido SRT mejorado.
        """
        if not srt_content.strip():
            return srt_content

        try:
            # Parsear el contenido SRT
            lines = srt_content.split("\n")
            improved_lines = []

            for line in lines:
                line = line.strip()
                if not line:
                    improved_lines.append("")
                    continue

                # Identificar líneas de diálogo (no números de secuencia ni timestamps)
                if not self._is_sequence_number(line) and not self._is_timestamp(line):
                    # Aplicar mejoras de calidad al texto
                    improved_text = self._improve_text_quality(line, source_language)
                    improved_lines.append(improved_text)
                else:
                    improved_lines.append(line)

            return "\n".join(improved_lines)

        except Exception as e:
            if self.logger:
                self.logger.warning(f"Error mejorando calidad de subtítulos: {e}")
            return srt_content

    def _improve_text_quality(self, text: str, source_language: str = "auto") -> str:
        """
        Aplica mejoras específicas al texto de los subtítulos.

        Args:
            text: Texto a mejorar.
            source_language: Idioma del texto.

        Returns:
            str: Texto mejorado.
        """
        if not text:
            return text

        # Detectar idioma si es automático
        if source_language == "auto":
            source_language = self._detect_language(text)

        # Aplicar mejoras según el idioma
        if source_language in ["en", "english"]:
            text = self._improve_english_text(text)
        elif source_language in ["es", "spanish"]:
            text = self._improve_spanish_text(text)

        # Mejoras generales aplicables a todos los idiomas
        text = self._apply_general_improvements(text)

        return text

    def _detect_language(self, text: str) -> str:
        """
        Detecta el idioma del texto.

        Args:
            text: Texto a analizar.

        Returns:
            str: Código de idioma detectado ('en' o 'es'), por defecto 'en'.
        """
        if not LANGDETECT_AVAILABLE:
            return "en"  # Default to English

        try:
            # Limpiar texto para mejor detección
            clean_text = re.sub(r"[^\w\s]", "", text).strip()
            if len(clean_text) < 3:
                return "en"

            detected = langdetect_detect(clean_text)
            return detected if detected in ["en", "es"] else "en"
        except Exception:
            return "en"

    def _improve_english_text(self, text: str) -> str:
        """Mejoras específicas para texto en inglés"""
        # Correcciones comunes en subtítulos generados por Whisper
        corrections = {
            # Errores comunes de pronunciación/transcripción
            r"\b(i|I)\s+(am|AM)\b": "I am",
            r"\b(im|Im|IM)\b": "I'm",
            r"\b(you|You)\s+(are|ARE)\b": "you are",
            r"\b(we|We)\s+(are|ARE)\b": "we are",
            r"\b(they|They)\s+(are|ARE)\b": "they are",
            r"\b(it|It)\s+(is|IS)\b": "it is",
            r"\b(thats|thats|THATS)\b": "that's",
            r"\b(dont|DONT)\b": "don't",
            r"\b(cant|CANT)\b": "can't",
            r"\b(wont|WONT)\b": "won't",
            r"\b(isnt|ISNT)\b": "isn't",
            r"\b(arent|ARENT)\b": "aren't",
            r"\b(wasnt|WASNT)\b": "wasn't",
            r"\b(werent|WERENT)\b": "weren't",
            # Nombres propios comunes que se transcriben mal
            r"\b(whisper|WHISPER)\b": "Whisper",
            r"\b(ffmpeg|FFMPEG)\b": "FFmpeg",
            # Corregir contracciones mal formateadas
            r"\b(I|I)\s+ve\b": "I've",
            r"\b(you|You)\s+ve\b": "you've",
            r"\b(we|We)\s+ve\b": "we've",
            r"\b(they|They)\s+ve\b": "they've",
            r"\b(it|It)\s+s\b": "it's",
        }

        for pattern, replacement in corrections.items():
            text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

        return text

    def _improve_spanish_text(self, text: str) -> str:
        """Mejoras específicas para texto en español"""
        # Correcciones comunes en español
        corrections = {
            # Tildes y caracteres especiales
            r"\b(que|QUE)\b": "qué",
            r"\b(como|COMO)\b": "cómo",
            r"\b(donde|DONDE)\b": "dónde",
            r"\b(cuando|CUANDO)\b": "cuándo",
            r"\b(porque|PORQUE)\b": "porque",
            # Errores comunes de traducción automática
            r"\b(el|EL)\s+(is|IS)\b": "es",
            r"\b(la|LA)\s+(is|IS)\b": "es",
            r"\b(yes|YES)\b": "sí",
            r"\b(no|NO)\b": "no",
            # Corregir artículos
            r"\b(un|UN)\s+(a|A)\b": "una",
            r"\b(una|UNA)\s+(a|A)\b": "una",
        }

        for pattern, replacement in corrections.items():
            text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

        return text

    def _apply_general_improvements(self, text: str) -> str:
        """Mejoras generales aplicables a cualquier idioma"""
        # Eliminar espacios múltiples
        text = re.sub(r"\s+", " ", text)

        # Corregir puntuación
        text = re.sub(r"\s+([,.!?;:])", r"\1", text)  # Quitar espacios antes de puntuación
        text = re.sub(r"([,.!?;:])\s*([,.!?;:])", r"\1\2", text)  # Corregir puntuación duplicada

        # Corregir comillas y paréntesis
        text = re.sub(r'"\s+', '"', text)  # Quitar espacios después de comillas de apertura
        text = re.sub(r'\s+"', '"', text)  # Quitar espacios antes de comillas de cierre
        text = re.sub(r"\(\s+", "(", text)  # Quitar espacios después de paréntesis de apertura
        text = re.sub(r"\s+\)", ")", text)  # Quitar espacios antes de paréntesis de cierre

        # Capitalizar primera letra de oraciones
        sentences = re.split(r"([.!?]+\s*)", text)
        improved_sentences = []
        for i, sentence in enumerate(sentences):
            if i % 2 == 0 and sentence.strip():  # Contenido de oración
                sentence = sentence.strip()
                if sentence:
                    # Solo capitalizar si la oración no comienza con un número (para listas)
                    if not sentence[0].isdigit():
                        sentence = sentence[0].upper() + sentence[1:]
            improved_sentences.append(sentence)

        text = "".join(improved_sentences)

        # Limpiar espacios al inicio y final
        text = text.strip()

        return text

    def _is_sequence_number(self, line: str) -> bool:
        """Verifica si una línea es un número de secuencia"""
        return bool(re.match(r"^\d+$", line.strip()))

    def _is_timestamp(self, line: str) -> bool:
        """Verifica si una línea es un timestamp SRT"""
        # Formato: 00:00:00,000 --> 00:00:00,000
        return bool(re.match(r"^\d{2}:\d{2}:\d{2},\d{3}\s*-->\s*\d{2}:\d{2}:\d{2},\d{3}$", line.strip()))


@dataclass
class TranslationStats:
    """Estadísticas de traducción de subtítulos"""
    subtitles_extracted: int = 0
    subtitles_translated: int = 0
    translation_errors: int = 0
    total_translation_time: float = 0.0
    files_processed: int = 0


@dataclass
class TranslationMetrics:
    """Métricas de traducción para Prometheus"""
    subtitles_extracted: int
    subtitles_translated: int
    translation_errors: int
    avg_translation_time: float
    timestamp: str


class TranslationMetricsCollector:
    """Colector de métricas de traducción de subtítulos"""

    def __init__(self, logs_dir: Path):
        self.logs_dir = logs_dir
        self.tmp_dir = logs_dir.parent / "tmp"  # Cambiar a tmp_dir
        self.metrics_file = self.tmp_dir / "translation_metrics.json"

    def calculate_metrics(self, stats: TranslationStats, execution_time: float) -> TranslationMetrics:
        """
        Calcula métricas detalladas desde estadísticas de traducción.

        Args:
            stats: Estadísticas de traducción.
            execution_time: Tiempo de ejecución en segundos.

        Returns:
            TranslationMetrics: Métricas calculadas.
        """

        # Calcular tiempo promedio de traducción
        avg_translation_time = 0.0
        if stats.subtitles_translated > 0:
            avg_translation_time = stats.total_translation_time / stats.subtitles_translated

        return TranslationMetrics(
            subtitles_extracted=stats.subtitles_extracted,
            subtitles_translated=stats.subtitles_translated,
            translation_errors=stats.translation_errors,
            avg_translation_time=round(avg_translation_time, 2),
            timestamp=datetime.now().isoformat()
        )

    def save_metrics(self, metrics: TranslationMetrics) -> None:
        """
        Guarda métricas en archivo JSON.

        Args:
            metrics: Métricas a guardar.
        """
        try:
            # Leer métricas existentes
            existing_metrics = []
            if self.metrics_file.exists():
                with open(self.metrics_file, 'r', encoding='utf-8') as f:
                    existing_metrics = json.load(f)
                    if not isinstance(existing_metrics, list):
                        existing_metrics = [existing_metrics]

            # Agregar nuevas métricas
            existing_metrics.append(asdict(metrics))

            # Mantener solo las últimas 100 entradas
            if len(existing_metrics) > 100:
                existing_metrics = existing_metrics[-100:]

            # Guardar archivo
            with open(self.metrics_file, 'w', encoding='utf-8') as f:
                json.dump(existing_metrics, f, indent=2, ensure_ascii=False)

        except Exception as e:
            print(f"Error guardando métricas de traducción: {e}")


class SubtitleTranslator:
    """Gestor de extracción y traducción de subtítulos con soporte de Whisper"""

    PROGRESS_FILE_NAME = "progress.json"

    def __init__(self, max_workers: int = 2, use_whisper: bool = True, allow_retranslate: bool = False, lock_file: Optional[Path] = None) -> None:
        # Configurar rutas desde configuración
        self.is_container = Path("/mediajelly").exists()
        self.base_dir = Path("/mediajelly" if self.is_container else "/home/tafurc/mediaJelly")
        self.scripts_dir = self.base_dir / "scripts"
        self.logs_dir = self.scripts_dir / "logs"
        self.tmp_dir = self.scripts_dir / "tmp"

        # Crear directorios
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)

        # Configurar logging
        self.logger = logging.getLogger("mediajelly")
        self.logger.setLevel(logging.INFO)
        self.logger.handlers.clear()

        # Configurar handlers para archivo
        log_file = self.logs_dir / "subtitle_translator.log"
        file_handler = logging.FileHandler(log_file, encoding='utf-8')
        file_handler.setLevel(logging.INFO)
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        file_handler.setFormatter(formatter)
        self.logger.addHandler(file_handler)

        # También loggear a consola
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(formatter)
        self.logger.addHandler(console_handler)

        # Cargar configuración centralizada
        if CONFIG_AVAILABLE:
            try:
                self.config = get_config()
                self.logger.info(f"{EmojiGenerator.success()} Configuración YAML cargada exitosamente")
            except Exception as e:
                self.logger.error(f"Error cargando configuración YAML: {e}")
                raise
        else:
            self.logger.error("Configuración YAML no disponible")

        # Inicializar mejora de calidad de subtítulos
        self.quality_improver = SubtitleQualityImprover(self.logger)

        # Inicializar sistema de métricas
        self.metrics_collector = TranslationMetricsCollector(self.tmp_dir)

        # Inicializar estadísticas de traducción
        self.translation_stats = TranslationStats()

        # Inicializar modelo Whisper (lazy loading)
        self.whisper_model = None

        self.max_workers = max_workers  # Procesamiento concurrente
        self.use_whisper = use_whisper
        self.allow_retranslate = allow_retranslate  # Permitir re-traducción de archivos .es.srt existentes
        self.progress_lock = threading.Lock()  # Lock para sincronizar progreso y estadísticas
        self.lock_file = lock_file  # Archivo de lock para sincronización

        # Verificar disponibilidad de dependencias
        self.whisper_available = WHISPER_AVAILABLE if self.use_whisper else False
        if self.use_whisper and not self.whisper_available:
            self.logger.warning("Whisper solicitado pero no disponible, se usarán solo subtítulos embebidos")
            self.use_whisper = False

    def setup_logging(self) -> None:
        """Configura el sistema de logging."""
        log_file = self.logs_dir / "subtitle_translator.log"

        class LocalTimeFormatter(logging.Formatter):
            def formatTime(self, record, datefmt=None):
                dt = datetime.fromtimestamp(record.created)
                if datefmt:
                    return dt.strftime(datefmt)
                return dt.strftime("%Y-%m-%d %H:%M:%S")

        formatter = LocalTimeFormatter("[%(asctime)s] %(levelname)s: %(message)s", "%Y-%m-%d %H:%M:%S")

        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(formatter)

        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)

        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.INFO)
        self.logger.handlers.clear()
        self.logger.addHandler(file_handler)
        self.logger.addHandler(console_handler)

    def _is_file_in_excluded_folder(self, file_path: Path) -> bool:
        """
        Verifica si un archivo está en una carpeta excluida.

        Args:
            file_path: Ruta del archivo.

        Returns:
            bool: True si está en una carpeta excluida.
        """
        return any(part.lower() in EXCLUDED_FOLDERS for part in file_path.parts)

    def _is_file_accessible(self, video_file: Path) -> bool:
        """
        Verifica si el archivo es accesible y no está corrupto.

        Args:
            video_file: Ruta del archivo de video.

        Returns:
            bool: True si el archivo es accesible.
        """
        try:
            # Verificación básica de acceso
            if not video_file.exists():
                return False

            # Verificación de tamaño (archivos muy pequeños pueden estar corruptos)
            if video_file.stat().st_size < 1024:  # Menos de 1KB
                self.logger.warning(f"Archivo muy pequeño, posiblemente corrupto: {video_file}")
                return False

            # Verificación rápida con ffprobe (solo duración)
            cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(video_file)]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=FFPROBE_QUICK_TIMEOUT)
            return result.returncode == 0

        except subprocess.TimeoutExpired:
            self.logger.warning(f"Archivo inaccesible (timeout en verificación rápida): {video_file}")
            return False
        except Exception as e:
            self.logger.warning(f"Error verificando accesibilidad del archivo {video_file}: {e}")
            return False

    def check_audio_tracks(self, video_file: Path) -> Tuple[bool, List[str]]:
        """
        Verifica si el archivo tiene audio en español.

        Args:
            video_file: Ruta del archivo de video.

        Returns:
            Tuple[bool, List[str]]: (Tiene español, Lista de idiomas encontrados).
        """
        # Verificación previa de accesibilidad
        if not self._is_file_accessible(video_file):
            self.logger.warning(f"Saltando archivo inaccesible: {video_file}")
            return False, []

        try:
            # Primero intentamos una verificación rápida (solo duración) para archivos grandes
            quick_cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(video_file)]

            # Timeout más corto para la verificación rápida
            result = subprocess.run(quick_cmd, capture_output=True, text=True, timeout=FFPROBE_QUICK_TIMEOUT)

            if result.returncode != 0:
                self.logger.warning(f"Error rápido verificando archivo: {video_file}")
                # Si falla la verificación rápida, continuamos con la completa

            # Verificación completa de pistas de audio
            cmd = [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a",
                "-show_entries",
                "stream=index:stream_tags=language",
                "-of",
                "json",
                str(video_file),
            ]

            # Timeout aumentado para archivos complejos
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=FFPROBE_TIMEOUT)

            if result.returncode != 0:
                self.logger.warning(f"Error al analizar pistas de audio: {video_file}")
                return False, []

            data = json.loads(result.stdout)
            streams = data.get("streams", [])

            languages = []
            has_spanish = False

            for stream in streams:
                lang = stream.get("tags", {}).get("language", "und")
                languages.append(lang)
                if lang in ["spa", "es", "esp"]:
                    has_spanish = True

            return has_spanish, languages

        except subprocess.TimeoutExpired:
            self.logger.error(f"Timeout verificando audio ({FFPROBE_TIMEOUT}s): {video_file}")
            return False, []
        except Exception as e:
            self.logger.error(f"Error verificando audio: {e}")
            return False, []

    def _find_srt_files(self, video_file: Path, patterns: List[str]) -> List[Path]:
        """Método auxiliar para buscar archivos SRT con patrones específicos"""
        parent_dir = video_file.parent

        found_files = []
        for pattern in patterns:
            matches = list(parent_dir.glob(pattern))
            found_files.extend(matches)

        return found_files

    def check_existing_spanish_subtitles(self, video_file: Path) -> bool:
        """
        Verifica si ya existen subtítulos en español de calidad aceptable.

        Args:
            video_file: Ruta del archivo de video.

        Returns:
            bool: True si existen subtítulos en español.
        """
        video_stem = video_file.stem

        # Patrones de subtítulos en español priorizados
        spanish_patterns = [
            f"{video_stem}.es.srt",  # Español genérico (mejor)
            f"{video_stem}.spa.srt",  # Español ISO
            f"{video_stem}.es-ES.srt",  # Español España
            f"{video_stem}.es-MX.srt",  # Español México
            f"{video_stem}.es-AR.srt",  # Español Argentina
            f"{video_stem}.spanish.srt",  # Nombre completo
            f"{video_stem}.español.srt",  # Acentuado
        ]

        # Buscar subtítulos en español válidos
        for pattern in spanish_patterns:
            subtitle_file = video_file.parent / pattern

            # Verificar que exista y sea válido
            if subtitle_file.exists():
                # Excluir archivos hearing impaired
                if ".hi." in subtitle_file.name:
                    self.logger.debug(f"Ignorando subtítulo hearing impaired: {subtitle_file.name}")
                    continue

                # Verificar tamaño mínimo
                if subtitle_file.stat().st_size < 100:
                    self.logger.debug(f"Ignorando subtítulo vacío: {subtitle_file.name}")
                    continue

                # Subtítulo válido encontrado
                self.logger.debug(f"Subtítulo español válido encontrado: {subtitle_file.name}")
                return True

        return False

    def find_existing_srt_file(self, video_file: Path) -> Optional[Path]:
        """
        Encuentra un archivo SRT existente (no español) para traducir.

        Prioriza archivos en inglés o genéricos.

        Args:
            video_file: Ruta del archivo de video.

        Returns:
            Optional[Path]: Ruta del archivo SRT encontrado o None.
        """
        video_stem = video_file.stem

        # Patrones de subtítulos priorizados por calidad
        # (excluyendo español y hearing impaired)
        srt_priority_patterns = [
            f"{video_stem}.en.srt",  # Inglés explícito (mejor calidad)
            f"{video_stem}.eng.srt",  # Inglés ISO
            f"{video_stem}.srt",  # Genérico (probablemente inglés)
            f"{video_stem}.sub.srt",  # Subtítulos genéricos
        ]

        # Buscar en orden de prioridad
        for pattern in srt_priority_patterns:
            srt_file = video_file.parent / pattern

            if not srt_file.exists():
                continue

            # Excluir archivos hearing impaired (HI)
            if ".hi." in srt_file.name.lower():
                self.logger.debug(f"Ignorando subtítulo HI: {srt_file.name}")
                continue

            # Excluir archivos ya en español (a menos que se permita re-traducción)
            if not self.allow_retranslate:
                if any(marker in srt_file.name.lower() for marker in [".es.", ".spa.", ".spanish.", ".español."]):
                    self.logger.debug(f"Ignorando subtítulo ya en español: {srt_file.name}")
                    continue

            # Verificar que no esté vacío
            if srt_file.stat().st_size < 100:
                self.logger.debug(f"Ignorando subtítulo vacío: {srt_file.name}")
                continue

            # Validar que sea realmente un archivo SRT válido
            if self._is_valid_srt_file(srt_file):
                self.logger.info(f"Subtítulo encontrado para traducir: {srt_file.name}")
                return srt_file

        # Si no encontró nada y hay un .es.srt, permitir re-traducirlo solo si está habilitado
        if self.allow_retranslate:
            es_srt = video_file.parent / f"{video_stem}.es.srt"
            if es_srt.exists() and es_srt.stat().st_size > 100:
                # Renombrar temporalmente para permitir re-traducción
                temp_srt = video_file.parent / f"{video_stem}.temp.srt"
                try:
                    es_srt.rename(temp_srt)
                    self.logger.info(f"Renombrando {es_srt.name} a {temp_srt.name} para re-traducción")
                    return temp_srt
                except Exception as e:
                    self.logger.error(f"Error renombrando {es_srt.name}: {e}")

        return None

    def _is_valid_srt_file(self, srt_file: Path) -> bool:
        """
        Verifica si un archivo tiene formato SRT válido.

        Args:
            srt_file: Ruta del archivo SRT.

        Returns:
            bool: True si es un archivo SRT válido.
        """
        try:
            with open(srt_file, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read(500)  # Leer primeros 500 caracteres

                # Verificar estructura básica de SRT:
                # 1. Número de secuencia
                # 2. Timestamp (00:00:00,000 --> 00:00:00,000)
                # 3. Texto

                # Patrón simple para timestamp SRT
                timestamp_pattern = r"\d{2}:\d{2}:\d{2}[,\.]\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}[,\.]\d{3}"
                import re

                if re.search(timestamp_pattern, content):
                    return True

                return False
        except Exception as e:
            self.logger.debug(f"Error validando SRT {srt_file.name}: {e}")
            return False

    def detect_srt_language(self, srt_file: Path) -> str:
        """
        Detecta el idioma principal de un archivo SRT.

        Args:
            srt_file: Ruta del archivo SRT.

        Returns:
            str: Código de idioma detectado o 'unknown'.
        """
        if not LANGDETECT_AVAILABLE:
            return "unknown"

        try:
            with open(srt_file, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()

            # Extraer solo líneas de texto (no números ni timestamps)
            lines = content.split("\n")
            text_lines = []

            for line in lines:
                line_stripped = line.strip()
                if (
                    line_stripped
                    and not line_stripped.isdigit()
                    and "-->" not in line_stripped
                    and not line_stripped.lower().startswith("http")
                    and "www" not in line_stripped.lower()
                    and "@" not in line_stripped
                ):
                    text_lines.append(line_stripped)

            if not text_lines:
                return "unknown"

            # Tomar una muestra de las primeras líneas de texto para detectar idioma
            sample_text = " ".join(text_lines[:10])  # Primeras 10 líneas

            try:
                detected_lang = langdetect_detect(sample_text)
                return detected_lang
            except Exception:
                return "unknown"

        except Exception as e:
            self.logger.error(f"Error detectando idioma de {srt_file.name}: {e}")
            return "unknown"

    def process_existing_subtitles(self, directory: Path) -> Dict[str, Any]:
        """
        Procesa subtítulos existentes que no estén en español.

        Args:
            directory: Directorio a escanear.

        Returns:
            Dict[str, Any]: Estadísticas del procesamiento.
        """
        stats: Dict[str, Any] = {"total_srt_files": 0, "spanish_subtitles": 0, "translated_subtitles": 0, "errors": 0, "results": []}

        # Buscar todos los archivos SRT en el directorio
        srt_files = list(directory.glob("*.srt"))
        stats["total_srt_files"] = len(srt_files)

        self.logger.info(f"Revisando {stats['total_srt_files']} archivos SRT existentes en {directory}")

        for srt_file in srt_files:
            result = {"file": str(srt_file), "was_spanish": False, "translated": False, "error": None}

            try:
                # Detectar idioma
                detected_lang = self.detect_srt_language(srt_file)

                if detected_lang == "es":
                    # Ya está en español
                    result["was_spanish"] = True
                    stats["spanish_subtitles"] = stats["spanish_subtitles"] + 1
                    self.translation_stats.subtitles_translated += 1  # Ya estaban en español
                    self.logger.debug(f"Subtítulos ya en español: {srt_file.name}")
                else:
                    # No está en español, intentar traducir
                    self.logger.info(f"Traduciendo subtítulos no españoles ({detected_lang}): {srt_file.name}")

                    translated_file = self.translate_srt(srt_file)

                    if translated_file:
                        result["translated"] = True
                        stats["translated_subtitles"] = stats["translated_subtitles"] + 1
                        self.translation_stats.subtitles_translated += 1
                        self.logger.info(f"{EmojiGenerator.check_mark()} Subtítulos traducidos: {srt_file.name}")
                    else:
                        result["error"] = "No se pudo traducir"
                        stats["errors"] = stats["errors"] + 1
                        self.translation_stats.translation_errors += 1

            except Exception as e:
                result["error"] = str(e)
                stats["errors"] = stats["errors"] + 1
                self.translation_stats.translation_errors += 1
                self.logger.error(f"Error procesando subtítulos {srt_file.name}: {e}")

            stats["results"].append(result)

        return stats

    def detect_embedded_subtitle_language(self, video_file: Path) -> Optional[str]:
        """
        Detecta el idioma de los subtítulos embebidos en el video.

        Args:
            video_file: Ruta del archivo de video.

        Returns:
            Optional[str]: Código de idioma detectado o None.
        """
        try:
            # Extraer una muestra más grande de los subtítulos embebidos para detectar idioma
            cmd = [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-i",
                str(video_file),
                "-map",
                "0:s:0",
                "-c:s",
                "srt",
                "-t",
                "360",  # Primeros 6 minutos para tener más texto
                "-f",
                "srt",
                "pipe:1",  # Salida a stdout
            ]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)

            if result.returncode != 0:
                self.logger.debug(f"No se pudieron extraer subtítulos para detección de idioma: {result.stderr[:200]}")
                return None

            subtitle_sample = result.stdout.strip()

            if not subtitle_sample:
                self.logger.debug("No se encontraron subtítulos embebidos")
                return None

            # Usar langdetect para detectar el idioma
            if not LANGDETECT_AVAILABLE:
                self.logger.warning("langdetect no disponible, no se puede detectar idioma de subtítulos embebidos")
                return None

            try:
                # Extraer solo líneas de texto (no números ni timestamps)
                lines = subtitle_sample.split("\n")
                text_lines = []

                for line in lines:
                    line_stripped = line.strip()
                    if (
                        line_stripped
                        and not line_stripped.isdigit()
                        and "-->" not in line_stripped
                        and not line_stripped.lower().startswith("http")
                        and "www" not in line_stripped.lower()
                        and "@" not in line_stripped
                    ):
                        text_lines.append(line_stripped)

                if not text_lines:
                    self.logger.debug("No se encontraron líneas de texto en los subtítulos")
                    return None

                # Tomar una muestra de las primeras líneas de texto
                sample_text = " ".join(text_lines[:10])  # Primeras 10 líneas

                detected_lang = langdetect_detect(sample_text)
                self.logger.debug(
                    f"Idioma detectado en subtítulos embebidos: {detected_lang} (muestra: {sample_text[:100]}...)"
                )
                return detected_lang

            except Exception as e:
                self.logger.debug(f"Error en detección de idioma: {e}")
                return None

        except Exception as e:
            self.logger.debug(f"Error detectando idioma de subtítulos embebidos: {e}")
            return None

    def extract_subtitles_from_audio(self, video_file: Path) -> Optional[Path]:
        """
        Extrae subtítulos del audio usando Whisper (procesamiento secuencial con lock global).

        Args:
            video_file: Ruta del archivo de video.

        Returns:
            Optional[Path]: Ruta del archivo SRT generado o None si falló.
        """
        if not WHISPER_AVAILABLE:
            self.logger.warning("Whisper no disponible, no se pueden extraer subtítulos del audio")
            return None

        audio_file = None
        output_srt = None

        try:
            # Preparar archivos temporales
            temp_files = self._prepare_temp_files(video_file)
            audio_file, output_srt = temp_files

            # Extraer audio del video (sin límite de duración)
            if not self._extract_audio_from_video(video_file, audio_file):
                return None

            # Transcribir audio con Whisper
            transcription_result = self._transcribe_audio_with_whisper(audio_file)
            if not transcription_result:
                return None

            # Generar archivo SRT desde la transcripción
            return self._generate_srt_from_transcription(transcription_result, output_srt)

        except subprocess.TimeoutExpired:
            self.logger.error("Timeout extrayendo audio (15 minutos)")
            return None
        except Exception as e:
            self.logger.error(f"Error extrayendo subtítulos del audio: {e}")
            return None
        finally:
            # LIMPIEZA: Eliminar archivos temporales SIEMPRE, incluso en errores
            self._cleanup_temp_files(audio_file)

    def _prepare_temp_files(self, video_file: Path) -> tuple[Path, Path]:
        """
        Prepara archivos temporales únicos para el procesamiento.

        Args:
            video_file: Ruta del archivo de video.

        Returns:
            tuple[Path, Path]: (Ruta archivo audio temporal, Ruta archivo SRT salida).
        """
        temp_prefix = f"{video_file.stem}_{video_file.stat().st_mtime_ns}"
        audio_file = self.tmp_dir / f"{temp_prefix}_temp.wav"
        output_srt = video_file.parent / f"{video_file.stem}.srt"
        return audio_file, output_srt

    def _extract_audio_from_video(self, video_file: Path, audio_file: Path) -> bool:
        """
        Extrae audio completo del video usando ffmpeg (con fallback para archivos corruptos).

        Args:
            video_file: Ruta del archivo de video.
            audio_file: Ruta del archivo de audio temporal.

        Returns:
            bool: True si se extrajo correctamente.
        """
        self.logger.info(f"Extrayendo audio temporal: {audio_file.name}")

        # Intentar primero con opciones normales
        if self._extract_audio_normal(video_file, audio_file):
            return True

        # Si falla, intentar con opciones tolerantes a corrupción
        self.logger.warning("Extracción normal falló, intentando con opciones tolerantes a corrupción...")
        if self._extract_audio_corruption_tolerant(video_file, audio_file):
            return True

        # Si aún falla, intentar método alternativo con codec copy y re-mux
        self.logger.warning("Extracción tolerante falló, intentando método de re-mux...")
        return self._extract_audio_remux(video_file, audio_file)

    def _extract_audio_normal(self, video_file: Path, audio_file: Path) -> bool:
        """
        Extrae audio con opciones normales de FFmpeg.

        Args:
            video_file: Ruta del archivo de video.
            audio_file: Ruta del archivo de audio temporal.

        Returns:
            bool: True si se extrajo correctamente.
        """
        cmd = [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(video_file),
            "-ar",
            "16000",  # Whisper requiere 16kHz
            "-ac",
            "1",  # Mono
            "-c:a",
            "pcm_s16le",
            str(audio_file),
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=21600)  # 6 horas timeout

        if result.returncode != 0:
            self.logger.error(f"Error extrayendo audio (modo normal): ffmpeg código {result.returncode}")
            if result.stderr:
                self.logger.error(f"FFmpeg stderr: {result.stderr[:500]}...")
            return False

        return self._validate_extracted_audio(audio_file)

    def _extract_audio_corruption_tolerant(self, video_file: Path, audio_file: Path) -> bool:
        """
        Extrae audio con opciones tolerantes a corrupción.

        Args:
            video_file: Ruta del archivo de video.
            audio_file: Ruta del archivo de audio temporal.

        Returns:
            bool: True si se extrajo correctamente.
        """
        cmd = [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-fflags",
            "+discardcorrupt+genpts+igndts",  # Ignorar corrupción y generar timestamps
            "-err_detect",
            "ignore_err",  # Ignorar errores de decodificación
            "-i",
            str(video_file),
            "-ar",
            "16000",  # Whisper requiere 16kHz
            "-ac",
            "1",  # Mono
            "-c:a",
            "pcm_s16le",
            str(audio_file),
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=21600)  # 6 horas timeout

        if result.returncode != 0:
            self.logger.error(f"Error extrayendo audio (modo tolerante): ffmpeg código {result.returncode}")
            if result.stderr:
                self.logger.error(f"FFmpeg stderr: {result.stderr[:500]}...")
            return False

        return self._validate_extracted_audio(audio_file)

    def _extract_audio_remux(self, video_file: Path, audio_file: Path) -> bool:
        """
        Extrae audio usando re-mux para evitar decodificación de streams corruptos.

        Args:
            video_file: Ruta del archivo de video.
            audio_file: Ruta del archivo de audio temporal.

        Returns:
            bool: True si se extrajo correctamente.
        """
        # Crear archivo temporal intermedio
        temp_audio = audio_file.with_suffix(".temp.aac")

        try:
            # Paso 1: Extraer audio sin re-encodear (codec copy)
            cmd1 = [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-fflags",
                "+discardcorrupt",
                "-i",
                str(video_file),
                "-vn",  # Sin video
                "-acodec",
                "copy",  # Copiar codec sin re-encodear
                "-map",
                "0:a:0",  # Solo primera pista de audio
                str(temp_audio),
            ]

            result1 = subprocess.run(cmd1, capture_output=True, text=True, timeout=3600)

            if result1.returncode != 0 or not temp_audio.exists() or temp_audio.stat().st_size == 0:
                self.logger.error(f"Error en re-mux paso 1: ffmpeg código {result1.returncode}")
                return False

            # Paso 2: Convertir a WAV desde el archivo extraído
            cmd2 = [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-fflags",
                "+discardcorrupt+igndts",
                "-err_detect",
                "ignore_err",
                "-i",
                str(temp_audio),
                "-ar",
                "16000",  # Whisper requiere 16kHz
                "-ac",
                "1",  # Mono
                "-c:a",
                "pcm_s16le",
                str(audio_file),
            ]

            result2 = subprocess.run(cmd2, capture_output=True, text=True, timeout=3600)

            if result2.returncode != 0:
                self.logger.error(f"Error en re-mux paso 2: ffmpeg código {result2.returncode}")
                if result2.stderr:
                    self.logger.error(f"FFmpeg stderr: {result2.stderr[:500]}...")
                return False

            return self._validate_extracted_audio(audio_file)

        finally:
            # Limpiar archivo temporal
            if temp_audio.exists():
                temp_audio.unlink()

    def _validate_extracted_audio(self, audio_file: Path) -> bool:
        """
        Valida que el audio extraído sea usable.

        Args:
            audio_file: Ruta del archivo de audio.

        Returns:
            bool: True si el audio es válido.
        """
        if not audio_file.exists() or audio_file.stat().st_size == 0:
            self.logger.error("Archivo de audio temporal no se creó o está vacío")
            return False

        # VALIDACIÓN: Verificar que el archivo de audio no esté corrupto
        if not self._validate_audio_file(audio_file):
            self.logger.error("Archivo de audio temporal está corrupto o vacío")
            return False

        self.logger.info(f"Audio extraído ({audio_file.stat().st_size} bytes), transcribiendo con Whisper...")
        return True

    def _transcribe_audio_with_whisper(self, audio_file: Path) -> Optional[dict]:
        """
        Transcribe audio usando Whisper con lock global.

        Args:
            audio_file: Ruta del archivo de audio.

        Returns:
            Optional[dict]: Resultado de la transcripción o None.
        """
        # LOCK GLOBAL: Solo un proceso puede usar Whisper a la vez
        with whisper_lock:
            self.logger.info(f"{EmojiGenerator.lock()} Adquiriendo lock global de Whisper...")

            # Cargar modelo si no está cargado
            self._ensure_whisper_model_loaded()

            if self.whisper_model is None:
                raise RuntimeError("No se pudo cargar el modelo Whisper")

            # Transcribir con Whisper con manejo específico de errores
            try:
                result = self.whisper_model.transcribe(
                    str(audio_file),
                    language=None,  # Auto-detectar idioma
                    task="transcribe",
                    fp16=False,  # CPU mode
                    verbose=False,
                )
            except Exception as whisper_error:
                return self._handle_whisper_error(whisper_error)

            self.logger.info(f"{EmojiGenerator.unlock()} Liberando lock global de Whisper...")

        # Verificar que tenemos segmentos
        if not result.get("segments"):
            self.logger.warning("Whisper no generó segmentos de transcripción")
            return None

        return result

    def _ensure_whisper_model_loaded(self) -> None:
        """Asegura que el modelo Whisper esté cargado."""
        if self.whisper_model is None:
            self.logger.info("Cargando modelo Whisper (small)...")
            self.whisper_model = whisper.load_model("small")

    def _handle_whisper_error(self, whisper_error: Exception) -> Optional[Dict[str, Any]]:
        """
        Maneja errores específicos de Whisper.

        Args:
            whisper_error: Excepción capturada.

        Returns:
            Optional[Dict[str, Any]]: None (siempre retorna None o lanza excepción).
        """
        error_msg = str(whisper_error).lower()
        if "cannot reshape tensor" in error_msg or "tensor" in error_msg:
            self.logger.error(f"Error de tensor en Whisper (archivo posiblemente corrupto): {whisper_error}")
        elif "nan" in error_msg:
            self.logger.error(f"Error NaN en Whisper (datos de audio inválidos): {whisper_error}")
        else:
            # Re-lanzar otros errores
            raise RuntimeError(f"Error inesperado en Whisper: {whisper_error}") from whisper_error
        return None

    def _generate_srt_from_transcription(self, result: dict, output_srt: Path) -> Optional[Path]:
        """
        Genera archivo SRT desde el resultado de la transcripción.

        Args:
            result: Resultado de Whisper.
            output_srt: Ruta de salida para el SRT.

        Returns:
            Optional[Path]: Ruta del archivo SRT generado o None.
        """
        self.logger.info(f"Generando archivo SRT con {len(result['segments'])} segmentos")

        with open(output_srt, "w", encoding="utf-8") as f:
            for i, segment in enumerate(result["segments"], start=1):
                start_time = self._format_timestamp(segment["start"])
                end_time = self._format_timestamp(segment["end"])
                text = segment["text"].strip()

                # Solo escribir si hay texto
                if text:
                    f.write(f"{i}\n")
                    f.write(f"{start_time} --> {end_time}\n")
                    f.write(f"{text}\n\n")

        # Verificar que el archivo SRT se creó correctamente
        if not output_srt.exists() or output_srt.stat().st_size == 0:
            self.logger.error("Archivo SRT no se creó o está vacío")
            return None

        detected_lang = result.get("language", "unknown")
        self.logger.info(
            f"{EmojiGenerator.check_mark()} Subtítulos extraídos del audio (idioma: {detected_lang}): {output_srt.name}"
        )

        # Aplicar mejora de calidad a los subtítulos generados por Whisper
        self._improve_extracted_subtitles_quality(output_srt, detected_lang)

        return output_srt

    def _cleanup_temp_files(self, *temp_files):
        """
        Limpia archivos temporales de forma segura.

        Args:
            *temp_files: Archivos temporales a eliminar.
        """
        for temp_file in temp_files:
            if temp_file and temp_file.exists():
                try:
                    temp_file.unlink()
                    self.logger.debug(f"Archivo temporal eliminado: {temp_file.name}")
                except Exception as e:
                    self.logger.warning(f"No se pudo eliminar archivo temporal {temp_file}: {e}")

    def _validate_audio_file(self, audio_file: Path) -> bool:
        """
        Valida que el archivo de audio sea válido antes de procesarlo con Whisper.

        Args:
            audio_file: Ruta del archivo de audio.

        Returns:
            bool: True si el archivo es válido.
        """
        try:
            # Verificar tamaño mínimo (al menos header WAV + algo de audio)
            if audio_file.stat().st_size < 1024:  # Al menos 1KB
                self.logger.warning(f"Archivo de audio muy pequeño: {audio_file.stat().st_size} bytes")
                return False

            # Verificar que sea un archivo WAV válido leyendo el header
            with open(audio_file, "rb") as f:
                header = f.read(44)  # Leer header WAV (44 bytes)

                # Verificar firma RIFF
                if header[:4] != b"RIFF":
                    self.logger.error("Archivo de audio no tiene firma RIFF válida")
                    return False

                # Verificar firma WAVE
                if header[8:12] != b"WAVE":
                    self.logger.error("Archivo de audio no tiene firma WAVE válida")
                    return False

                # Buscar la sección de datos (puede estar en diferentes posiciones)
                data_found = False
                data_size = 0

                # Buscar "data" en el archivo
                f.seek(0)
                file_content = f.read()
                data_pos = file_content.find(b"data")
                if data_pos >= 0 and data_pos < 100:  # Encontrado en una posición razonable
                    data_size = int.from_bytes(file_content[data_pos + 4 : data_pos + 8], byteorder="little")
                    data_found = True

                if not data_found or data_size == 0:
                    self.logger.warning(f"No se encontró sección de datos válida en el WAV (data_size: {data_size})")
                    # En lugar de rechazar, ser más permisivo - verificar que haya contenido
                    if len(file_content) > 1024:  # Si el archivo es lo suficientemente grande
                        self.logger.warning(
                            "Archivo WAV tiene formato no estándar pero tamaño aceptable, continuando..."
                        )
                    else:
                        return False

                # Verificar formato de audio (si está disponible)
                try:
                    channels = int.from_bytes(header[22:24], byteorder="little")
                    sample_rate = int.from_bytes(header[24:28], byteorder="little")

                    # Ser más flexible con los formatos
                    if sample_rate not in [8000, 16000, 22050, 44100, 48000]:
                        self.logger.warning(f"Sample rate inusual: {sample_rate}Hz")
                    if channels not in [1, 2]:
                        self.logger.warning(f"Número de canales inusual: {channels}")
                except (ValueError, IndexError) as e:
                    self.logger.warning(f"No se pudo leer información de formato del WAV: {e}")

                # Verificar que no sea solo silencio (bytes cero)
                f.seek(44)  # Ir después del header
                sample_data = f.read(min(2048, len(file_content) - 44))  # Leer hasta 2KB de datos

                # Contar bytes no cero
                non_zero_bytes = sum(1 for b in sample_data if b != 0)
                if non_zero_bytes < len(sample_data) * 0.01:  # Menos del 1% de bytes no cero
                    self.logger.warning("Archivo de audio parece contener solo silencio")
                    return False

            self.logger.debug(f"Archivo de audio validado: {audio_file.stat().st_size} bytes")
            return True

        except Exception as e:
            self.logger.error(f"Error validando archivo de audio {audio_file}: {e}")
            return False

    def _format_timestamp(self, seconds: float) -> str:
        """
        Convierte segundos a formato SRT (HH:MM:SS,mmm).

        Args:
            seconds: Tiempo en segundos.

        Returns:
            str: Timestamp formateado.
        """
        try:
            hours = int(seconds // 3600)
            minutes = int((seconds % 3600) // 60)
            secs = int(seconds % 60)
            millis = int((seconds % 1) * 1000)
            return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"
        except Exception as e:
            self.logger.error(f"Error formateando timestamp {seconds}: {e}")
            return "00:00:00,000"

    def translate_srt(self, srt_file: Path, force_translate: bool = False) -> Optional[Path]:
        """
        Traduce archivo SRT al español usando Yandex Translate con verificación doble.

        Args:
            srt_file: Ruta del archivo SRT.
            force_translate: Forzar traducción incluso si parece innecesaria.

        Returns:
            Optional[Path]: Ruta del archivo traducido o None.
        """
        if not self._translation_dependencies_available():
            return None

        try:
            import time

            translator = YandexTranslate()

            # Primera traducción
            with open(srt_file, "r", encoding="utf-8", errors="ignore") as f:
                original_lines = f.readlines()

            translated_lines, translations_made = self._translate_lines_batch(
                original_lines, translator, force_translate
            )

            # Segunda pasada automática para asegurar traducción completa
            if translations_made > 0:
                translated_lines, additional_translations = self._translate_lines_batch(
                    translated_lines, translator, force_translate=True
                )
                translations_made += additional_translations

            # Solo guardar si se hicieron traducciones
            if translations_made > 0:
                return self._save_translated_file(srt_file, translated_lines, translations_made)
            else:
                self.logger.debug(f"No se encontraron líneas para traducir en: {srt_file.name}")
                return None

        except ImportError as e:
            self.logger.error(f"Faltan dependencias para traducción: {e}")
            return None

    def _translation_dependencies_available(self) -> bool:
        """
        Verifica que las dependencias de traducción estén disponibles.

        Returns:
            bool: True si las dependencias están disponibles.
        """
        if not YANDEX_AVAILABLE or not LANGDETECT_AVAILABLE:
            self.logger.error("Dependencias de traducción no disponibles (translatepy o langdetect)")
            return False
        return True

    def _translate_lines_batch(
        self, lines: List[str], translator, force_translate: bool = False
    ) -> tuple[List[str], int]:
        """
        Traduce un lote de líneas SRT.

        Args:
            lines: Lista de líneas a traducir.
            translator: Instancia del traductor.
            force_translate: Forzar traducción.

        Returns:
            tuple[List[str], int]: (Líneas traducidas, Número de traducciones realizadas).
        """
        translated_lines = []
        translations_made = 0

        for line in lines:
            translated_line, made_translation = self._translate_single_line(line, translator, force_translate)
            translated_lines.append(translated_line)
            translations_made += made_translation

        return translated_lines, translations_made

    def _translate_single_line(self, line: str, translator, force_translate: bool = False) -> tuple[str, int]:
        """
        Traduce una sola línea SRT si es necesario.

        Args:
            line: Línea a traducir.
            translator: Instancia del traductor.
            force_translate: Forzar traducción.

        Returns:
            tuple[str, int]: (Línea traducida, 1 si se tradujo o 0 si no).
        """
        line_stripped = line.strip()

        # No traducir líneas vacías, números, timestamps, URLs, etc.
        if self._should_skip_translation(line_stripped):
            return line, 0

        # Intentar traducir la línea
        return self._attempt_translation(line_stripped, translator, force_translate)

    def _should_skip_translation(self, line_stripped: str) -> bool:
        """
        Determina si una línea debe ser omitida de la traducción.

        Args:
            line_stripped: Línea limpia de espacios.

        Returns:
            bool: True si debe omitirse.
        """
        return (
            not line_stripped
            or line_stripped.isdigit()
            or "-->" in line_stripped
            or line_stripped.lower().startswith("http")
            or "www" in line_stripped.lower()
            or "@" in line_stripped
        )

    def _attempt_translation(self, text_to_translate: str, translator, force_translate: bool) -> tuple[str, int]:
        """
        Intenta traducir el texto detectando el idioma.

        Args:
            text_to_translate: Texto a traducir.
            translator: Instancia del traductor.
            force_translate: Forzar traducción.

        Returns:
            tuple[str, int]: (Texto traducido, 1 si se tradujo o 0 si no).
        """
        # Limpiar texto para mejor detección de idioma
        clean_text = re.sub(r"[^\w\s]", "", text_to_translate).strip()
        text_to_detect = clean_text if clean_text else text_to_translate

        # Detectar idioma y traducir solo si no es español
        try:
            lang = langdetect_detect(text_to_detect)
            if self._should_translate_language(lang, force_translate):
                return self._perform_translation(text_to_translate, translator)
            else:
                return text_to_translate + "\n", 0
        except Exception:
            # Si falla detección de idioma, intentar traducir asumiendo que puede ser inglés
            return self._perform_translation(text_to_translate, translator)

    def _should_translate_language(self, lang: str, force_translate: bool) -> bool:
        """
        Determina si el idioma detectado debe ser traducido.

        Args:
            lang: Código de idioma detectado.
            force_translate: Forzar traducción.

        Returns:
            bool: True si debe traducirse.
        """
        return (
            lang in ["ja", "en", "fr", "de", "it", "pt", "ru", "ko", "zh", "ca", "eu"] or force_translate
        ) and lang != "es"

    def _perform_translation(self, text_to_translate: str, translator) -> tuple[str, int]:
        """
        Realiza la traducción usando Yandex Translate.

        Args:
            text_to_translate: Texto a traducir.
            translator: Instancia del traductor.

        Returns:
            tuple[str, int]: (Texto traducido, 1 si se tradujo o 0 si no).
        """
        try:
            import time

            translated = translator.translate(text_to_translate, "es")
            translated_text = translated.result
            time.sleep(0.1)  # Pequeño delay para evitar límites de rate
            return translated_text + "\n", 1
        except Exception:
            # Si todo falla, mantener original
            return text_to_translate + "\n", 0

    def _save_translated_file(
        self, srt_file: Path, translated_lines: List[str], translations_made: int
    ) -> Optional[Path]:
        """
        Guarda el archivo traducido y elimina el original si corresponde.

        Args:
            srt_file: Ruta del archivo SRT original.
            translated_lines: Lista de líneas traducidas.
            translations_made: Número de traducciones realizadas.

        Returns:
            Optional[Path]: Ruta del archivo guardado o None.
        """
        # Preparar nombre base para el archivo de salida
        base_name = self._prepare_output_filename(srt_file)
        output_file = srt_file.parent / f"{base_name}{SPANISH_SUBTITLE_SUFFIX}"

        with open(output_file, "w", encoding="utf-8") as f:
            f.writelines(translated_lines)

        self.logger.info(f"Archivo traducido ({translations_made} líneas, 2 pasadas): {output_file.name}")

        # Aplicar mejora de calidad a los subtítulos traducidos
        self._improve_translated_subtitles_quality(output_file)

        # Eliminar archivo original si fue extraído (no tiene _ES)
        self._cleanup_original_file(srt_file)

        return output_file

    def _prepare_output_filename(self, srt_file: Path) -> str:
        """
        Prepara el nombre base para el archivo de salida.

        Args:
            srt_file: Ruta del archivo SRT.

        Returns:
            str: Nombre base sin extensiones de idioma.
        """
        base_name = srt_file.stem
        # Quitar extensiones de idioma comunes del nombre base
        for lang_ext in [".en", ".eng", ".es", ".spa", ".fre", ".ger", ".ita", ".por", ".rus", ".jpn", ".kor", ".chi"]:
            if base_name.endswith(lang_ext):
                base_name = base_name[: -len(lang_ext)]
                break
        return base_name

    def _cleanup_original_file(self, srt_file: Path):
        """
        Elimina el archivo original si fue extraído.

        Args:
            srt_file: Ruta del archivo SRT.
        """
        if not srt_file.name.endswith(SPANISH_SUBTITLE_SUFFIX):
            try:
                srt_file.unlink()
                self.logger.debug(f"Archivo original eliminado: {srt_file.name}")
            except Exception:
                pass

    def process_video(self, video_file: Path) -> dict:
        """
        Procesa un archivo de video para subtítulos en español.

        Args:
            video_file: Ruta del archivo de video.

        Returns:
            dict: Resultado del procesamiento.
        """
        result = {
            "file": str(video_file),
            "has_spanish_audio": False,
            "had_spanish_subtitles": False,
            "extracted": False,
            "translated": False,
            "subtitle_file": None,
            "error": None,
        }

        try:
            # 0. Verificación rápida inicial
            if not self.quick_check_needs_processing(video_file):
                result["had_spanish_subtitles"] = True  # Consideramos que ya está "procesado"
                self.logger.debug(f"Verificación rápida: omitiendo {video_file.name}")
                return result

            # 0.5. Verificar si el video está corrupto antes de intentar procesarlo
            if self._is_video_corrupted(video_file):
                result["error"] = "Video corrupto o inaccesible"
                self.logger.error(f"{EmojiGenerator.error()} Video corrupto detectado: {video_file.name}")
                return result

            # 1. Verificar audio en español
            has_spanish, _ = self.check_audio_tracks(video_file)
            result["has_spanish_audio"] = has_spanish

            if has_spanish:
                self.logger.debug(f"Audio en español detectado, pero se procesarán subtítulos: {video_file.name}")
                # No retornamos aquí, continuamos para crear subtítulos incluso con audio en español

            # 2. Verificar si ya tiene subtítulos en español (verificación adicional)
            if self.check_existing_spanish_subtitles(video_file) and not self.allow_retranslate:
                result["had_spanish_subtitles"] = True
                self.logger.debug(f"Ya tiene subtítulos en español: {video_file.name}")
                return result

            # 3. Buscar subtítulos existentes para traducir
            existing_srt = self.find_existing_srt_file(video_file)
            if existing_srt:
                self.logger.debug(f"Encontrados subtítulos existentes para traducir: {existing_srt.name}")
                # Traducir subtítulos existentes
                start_time = time.time()
                translated_file = self.translate_srt(existing_srt, force_translate=self.allow_retranslate)
                translation_time = time.time() - start_time

                if translated_file:
                    result["translated"] = True
                    result["subtitle_file"] = str(translated_file)
                    self.translation_stats.subtitles_translated += 1
                    self.translation_stats.total_translation_time += translation_time
                    self.logger.info(
                        f"{EmojiGenerator.check_mark()} Subtítulos traducidos desde existentes: {video_file.name}"
                    )
                else:
                    result["error"] = "No se pudieron traducir los subtítulos existentes"
                return result

            # 4. Extraer subtítulos si no hay ninguno
            srt_file = self.extract_subtitles(video_file)

            if srt_file:
                result["extracted"] = True
                self.translation_stats.subtitles_extracted += 1

                # 5. Traducir subtítulos extraídos
                start_time = time.time()
                translated_file = self.translate_srt(srt_file, force_translate=self.allow_retranslate)
                translation_time = time.time() - start_time

                if translated_file:
                    result["translated"] = True
                    result["subtitle_file"] = str(translated_file)
                    self.translation_stats.subtitles_translated += 1
                    self.translation_stats.total_translation_time += translation_time
                    self.logger.info(
                        f"{EmojiGenerator.check_mark()} Subtítulos extraídos y traducidos: {video_file.name}"
                    )
                else:
                    result["error"] = "No se pudo traducir"
            else:
                result["error"] = "No se pudieron extraer subtítulos"

        except Exception as e:
            result["error"] = str(e)
            self.logger.error(f"Error procesando {video_file.name}: {e}")

        return result

    def _is_video_corrupted(self, video_file: Path) -> bool:
        """
        Detecta si un archivo de video está corrupto mediante análisis rápido con ffprobe.

        Args:
            video_file: Ruta del archivo de video.

        Returns:
            bool: True si el video está corrupto, False si está OK.
        """
        try:
            # Análisis rápido con ffprobe (timeout corto)
            cmd = [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration:stream=codec_name,codec_type",
                "-of",
                "json",
                str(video_file),
            ]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)  # Timeout rápido

            # Si ffprobe falla, el archivo está corrupto
            if result.returncode != 0:
                stderr = result.stderr.lower()

                # Detectar errores específicos de corrupción
                corruption_indicators = [
                    "invalid nal unit",
                    "channel element",
                    "is not allocated",
                    "error splitting",
                    "invalid data found",
                    "prediction is not allowed",
                    "reserved bit set",
                    "exhausted before end",
                ]

                if any(indicator in stderr for indicator in corruption_indicators):
                    self.logger.warning(
                        f"{EmojiGenerator.warning_msg()} Indicadores de corrupción detectados en {video_file.name}"
                    )
                    return True

            # Verificar que tenga streams válidos
            try:
                import json

                data = json.loads(result.stdout)

                # Debe tener al menos un stream de video y audio
                if "streams" not in data or len(data.get("streams", [])) == 0:
                    self.logger.warning(f"{EmojiGenerator.warning_msg()} No se detectaron streams en {video_file.name}")
                    return True

                # Verificar que tenga duración válida
                if "format" in data:
                    duration = data["format"].get("duration")
                    if not duration or float(duration) < 1.0:
                        self.logger.warning(f"{EmojiGenerator.warning_msg()} Duración inválida en {video_file.name}")
                        return True

            except json.JSONDecodeError:
                self.logger.warning(
                    f"{EmojiGenerator.warning_msg()} No se pudo parsear salida de ffprobe para {video_file.name}"
                )
                return True

            # Si llegó aquí, el video parece estar OK
            return False

        except subprocess.TimeoutExpired:
            self.logger.warning(f"{EmojiGenerator.warning_msg()} Timeout verificando {video_file.name}")
            return True
        except Exception as e:
            self.logger.debug(f"Error verificando corrupción de {video_file.name}: {e}")
            # En caso de error, asumir que no está corrupto para no bloquear
            return False

    def cleanup_incomplete_files(self, video_files: Optional[List[Path]] = None):
        """
        Elimina archivos temporales e incompletos de ejecuciones anteriores.

        Args:
            video_files: Lista opcional de archivos de video para limpiar específicamente.
        """
        try:
            self.logger.info(f"{EmojiGenerator.broom()} Limpiando archivos temporales e incompletos...")

            # Limpiar archivos temporales
            self._cleanup_temp_wav_files()

            # Limpiar archivos SRT incompletos
            if video_files is not None:
                # Si se proporcionan archivos específicos, solo limpiarlos
                self._cleanup_incomplete_srt_files_for_videos(video_files)
            else:
                # Si no se proporcionan, limpiar todo (comportamiento anterior)
                self._cleanup_incomplete_srt_files()

            self.logger.info("[CLEAN] Limpieza completada")

        except Exception as e:
            self.logger.error(f"Error durante limpieza: {e}")

    def _cleanup_temp_wav_files(self):
        """Limpia archivos .wav temporales en el directorio tmp."""
        if not self.tmp_dir.exists():
            return

        temp_wav_files = list(self.tmp_dir.glob("*_temp.wav"))
        for temp_file in temp_wav_files:
            try:
                temp_file.unlink()
                self.logger.debug(f"Eliminado archivo temporal: {temp_file.name}")
            except Exception as e:
                self.logger.warning(f"No se pudo eliminar {temp_file.name}: {e}")

    def _cleanup_incomplete_srt_files_for_videos(self, video_files: list):
        """
        Limpia archivos SRT incompletos solo para los videos especificados.

        Args:
            video_files: Lista de archivos de video.
        """
        for video_file in video_files:
            try:
                self._check_and_cleanup_incomplete_srt(video_file)
            except Exception as e:
                self.logger.warning(f"Error verificando {video_file.name}: {e}")

    def _cleanup_incomplete_srt_files(self):
        """Limpia archivos .srt que parecen incompletos (versión original - escanea todo)."""
        video_extensions = [".mp4", ".mkv", ".avi", ".mov", ".flv", ".wmv"]

        for video_ext in video_extensions:
            for video_file in self.base_dir.rglob(f"*{video_ext}"):
                # Excluir archivos en carpetas temporales o de eliminación
                if self._is_file_in_excluded_folder(video_file):
                    continue
                try:
                    self._check_and_cleanup_incomplete_srt(video_file)
                except Exception as e:
                    self.logger.warning(f"Error verificando {video_file.name}: {e}")

    def _check_and_cleanup_incomplete_srt(self, video_file: Path):
        """
        Verifica y limpia archivos SRT incompletos para un archivo de video.

        Args:
            video_file: Ruta del archivo de video.
        """
        srt_file = video_file.with_suffix(".srt")
        es_srt_file = video_file.with_suffix(SPANISH_SUBTITLE_SUFFIX)

        # Solo procesar si existe .srt pero no .es.srt
        if not srt_file.exists() or es_srt_file.exists():
            return

        # Verificar si el archivo parece incompleto
        if self._is_srt_file_incomplete(srt_file):
            srt_file.unlink()
            self.logger.debug(f"Eliminado archivo SRT incompleto: {srt_file.name}")

    def _is_srt_file_incomplete(self, srt_file: Path) -> bool:
        """
        Determina si un archivo SRT parece incompleto.

        Args:
            srt_file: Ruta del archivo SRT.

        Returns:
            bool: True si el archivo parece incompleto.
        """
        file_size = srt_file.stat().st_size
        file_age_hours = (datetime.now() - datetime.fromtimestamp(srt_file.stat().st_mtime)).total_seconds() / 3600

        # Si es muy pequeño (< 100 bytes) o modificado en las últimas 2 horas (posiblemente en proceso)
        return file_size < 100 or file_age_hours < 2

    def process_directory(self, directory: Path, input_args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Procesa todos los videos en un directorio (concurrente sin Whisper, secuencial con Whisper).

        Args:
            directory: Directorio a procesar.
            input_args: Argumentos de entrada opcionales.

        Returns:
            Dict[str, Any]: Estadísticas del procesamiento.
        """

        # Buscar archivos de video en el directorio primero
        video_files = self._find_video_files_in_directory(directory)

        # Limpiar archivos temporales e incompletos solo para estos archivos
        self.cleanup_incomplete_files(video_files)

        # Cargar información de archivos ya procesados
        processed_files = self._get_processed_files_info()

        # Filtrar archivos que necesitan procesamiento (no procesados o procesados pero sin subtítulos)
        files_needing_processing = []
        for video_file in video_files:
            file_path_str = str(video_file)
            if file_path_str not in processed_files:
                # Archivo no procesado nunca
                files_needing_processing.append(video_file)
            else:
                # Archivo procesado anteriormente, verificar si tiene subtítulos
                processed_info = processed_files[file_path_str]
                if not processed_info.get("has_subtitles", False):
                    # Archivo procesado pero sin subtítulos, necesita reprocesamiento
                    self.logger.debug(
                        f"Archivo procesado anteriormente pero sin subtítulos, reprocesando: {video_file.name}"
                    )
                    files_needing_processing.append(video_file)
                else:
                    self.logger.debug(f"Archivo ya tiene subtítulos, omitiendo: {video_file.name}")

        stats = self._initialize_processing_stats(len(video_files))
        self.logger.info(f"Archivos de video encontrados: {stats['total_files']}")
        self.logger.info(f"Archivos que necesitan procesamiento: {len(files_needing_processing)}")

        # Determinar estrategia de procesamiento
        processing_config = self._determine_processing_strategy()

        # Actualizar progreso inicial con argumentos de entrada
        self.update_progress_file(stats, 0, stats["total_files"], "", "processing", input_args=input_args)

        # Ejecutar procesamiento según la estrategia
        self._execute_processing(files_needing_processing, stats, processing_config)

        # Procesar subtítulos existentes que no estén en español
        self._process_existing_subtitles_in_directory(directory, stats)

        return stats

    def _find_video_files_in_directory(self, directory: Path) -> List[Path]:
        """
        Busca todos los archivos de video en un directorio.

        Args:
            directory: Directorio a buscar.

        Returns:
            List[Path]: Lista de archivos de video encontrados.
        """
        video_extensions = [".mp4", ".mkv", ".avi", ".mov", ".flv", ".wmv"]
        video_files = []

        for ext in video_extensions:
            for video_file in directory.rglob(f"*{ext}"):
                # Excluir archivos en carpetas temporales o de eliminación
                if not self._is_file_in_excluded_folder(video_file):
                    video_files.append(video_file)

        return video_files

    def _process_existing_subtitles_in_directory(self, directory: Path, stats: dict):
        """
        Procesa subtítulos existentes en el directorio.

        Args:
            directory: Directorio a procesar.
            stats: Diccionario de estadísticas para actualizar.
        """
        self.logger.info("Revisando subtítulos existentes...")
        subtitle_stats = self.process_existing_subtitles(directory)

        # Agregar estadísticas de subtítulos a las estadísticas principales
        stats["subtitle_check"] = subtitle_stats

        self.logger.info(
            f"Subtítulos revisados: {subtitle_stats['total_srt_files']} totales, "
            f"{subtitle_stats['spanish_subtitles']} ya en español, "
            f"{subtitle_stats['translated_subtitles']} traducidos"
        )

    def process_file_list(self, file_paths: List[Path], input_args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Procesa una lista específica de archivos de video (concurrente sin Whisper, secuencial con Whisper).

        Args:
            file_paths: Lista de rutas de archivos a procesar.
            input_args: Argumentos de entrada opcionales.

        Returns:
            Dict[str, Any]: Estadísticas del procesamiento.
        """

        # Filtrar archivos válidos primero
        stats = self._initialize_processing_stats(len(file_paths))
        valid_files = self._filter_valid_files(file_paths, stats)

        # Limpiar archivos temporales e incompletos solo para archivos válidos
        self.cleanup_incomplete_files(valid_files)

        # Filtrar input_args para que solo contenga archivos válidos
        if input_args:
            self._filter_input_args_for_valid_files(input_args, valid_files)

        # Determinar estrategia de procesamiento
        processing_config = self._determine_processing_strategy()

        # Actualizar progreso inicial con argumentos de entrada filtrados
        self.update_progress_file(stats, 0, stats["total_files"], "", STATUS_PROCESSING, input_args=input_args)

        # Ejecutar procesamiento según la estrategia
        self._execute_processing(valid_files, stats, processing_config)

        # Actualizar progreso final
        self.update_progress_file(stats, stats["processed"], stats["total_files"], "", STATUS_COMPLETED)

        # Si el procesamiento se completó al 100%, limpiar last_input_args
        if stats["processed"] == stats["total_files"]:
            self._clear_input_args_from_progress()

        return stats

    def _filter_input_args_for_valid_files(self, input_args: dict, valid_files: List[Path]):
        """
        Filtra input_args para que solo contenga archivos válidos.

        Args:
            input_args: Argumentos de entrada.
            valid_files: Lista de archivos válidos.
        """
        valid_file_paths = [str(f) for f in valid_files]
        if "corrected_paths" in input_args:
            input_args["corrected_paths"] = [path for path in input_args["corrected_paths"] if path in valid_file_paths]
        if "file_paths" in input_args:
            input_args["file_paths"] = [path for path in input_args["file_paths"] if path in valid_file_paths]

    def _initialize_processing_stats(self, total_files: int) -> dict:
        """
        Inicializa las estadísticas de procesamiento.

        Args:
            total_files: Número total de archivos.

        Returns:
            dict: Diccionario de estadísticas inicializado.
        """
        stats = {
            "total_files": total_files,
            "processed": 0,
            "with_spanish_audio": 0,
            "with_spanish_subs": 0,
            "extracted": 0,
            "translated": 0,
            "errors": 0,
            "results": [],
        }
        self.logger.info(f"Archivos a procesar: {stats['total_files']}")
        return stats

    def _filter_valid_files(self, file_paths: List[Path], stats: dict) -> List[Path]:
        """
        Filtra archivos que existen, son válidos y necesitan procesamiento.

        Args:
            file_paths: Lista de rutas de archivos.
            stats: Estadísticas para actualizar errores.

        Returns:
            List[Path]: Lista de archivos válidos.
        """
        # Cargar información de archivos ya procesados
        processed_files = self._get_processed_files_info()

        valid_files = []
        missing_files = []

        for video_file in file_paths:
            if not video_file.exists():
                self.logger.warning(f"Archivo no encontrado: {video_file}")
                missing_files.append(str(video_file))
                stats["errors"] += 1
            else:
                # Verificar si el archivo ya fue procesado y tiene subtítulos
                file_path_str = str(video_file)
                if file_path_str in processed_files:
                    processed_info = processed_files[file_path_str]
                    if processed_info.get("has_subtitles", False):
                        self.logger.debug(f"Archivo ya tiene subtítulos, omitiendo: {video_file.name}")
                        continue

                valid_files.append(video_file)

        # Los archivos huérfanos se mencionan en el log y se limpian de pendientes
        if missing_files:
            self.logger.info(
                f"Encontrados {len(missing_files)} archivos huérfanos (no encontrados), eliminándolos de pendientes"
            )
            self._cleanup_missing_files(missing_files)

        return valid_files

    def _cleanup_missing_files(self, missing_files: List[str]):
        """
        Limpia archivos no encontrados de pending_subtitles.txt y last_input_args.

        Args:
            missing_files: Lista de rutas de archivos faltantes.
        """
        try:
            # Limpiar de pending_subtitles.txt
            self._remove_from_pending_subtitles(missing_files)

            # Limpiar de last_input_args en el archivo de progreso
            self._remove_from_last_input_args(missing_files)

            self.logger.info(f"Limpiados {len(missing_files)} archivos no encontrados de listas pendientes")

        except Exception as e:
            self.logger.error(f"Error limpiando archivos no encontrados: {e}")

    def _remove_from_pending_subtitles(self, file_paths: List[str]):
        """
        Remueve archivos específicos de pending_subtitles.txt.

        Args:
            file_paths: Lista de rutas de archivos a remover.
        """
        pending_file = self.scripts_dir / "pending_subtitles.txt"

        if not pending_file.exists():
            return

        try:
            with open(pending_file, "r") as f:
                pending_lines = [line.strip() for line in f if line.strip()]

            # Remover los archivos no encontrados
            original_count = len(pending_lines)
            pending_lines = [line for line in pending_lines if line not in file_paths]

            # Reescribir solo si cambió algo
            if len(pending_lines) != original_count:
                with open(pending_file, "w") as f:
                    for line in pending_lines:
                        f.write(f"{line}\n")

                removed_count = original_count - len(pending_lines)
                self.logger.debug(f"Removidos {removed_count} archivos de pending_subtitles.txt")

        except Exception as e:
            self.logger.error(f"Error removiendo archivos de pending_subtitles.txt: {e}")

    def _remove_from_last_input_args(self, file_paths: List[str]):
        """
        Remueve archivos específicos de last_input_args en el archivo de progreso.

        Args:
            file_paths: Lista de rutas de archivos a remover.
        """
        progress_file = self.tmp_dir / self.PROGRESS_FILE_NAME

        if not progress_file.exists():
            return

        try:
            with open(progress_file, "r") as f:
                progress_data = json.load(f)

            # Verificar si hay last_input_args
            subtitle_translation = progress_data.get("subtitle_translation", {})
            last_input_args = subtitle_translation.get("last_input_args")

            if not last_input_args:
                return

            # Normalizar rutas y remover archivos
            normalized_missing_files = self._normalize_missing_file_paths(file_paths)
            self._remove_files_from_corrected_paths(last_input_args, normalized_missing_files)
            self._remove_files_from_file_paths(last_input_args, normalized_missing_files)

            # Si corrected_paths está vacío después de la limpieza, limpiar completamente last_input_args
            corrected_paths = last_input_args.get("corrected_paths", [])
            original_paths = last_input_args.get("original_paths", [])

            if not corrected_paths and not original_paths:
                self.logger.info("Todos los archivos fueron removidos. Limpiando last_input_args completamente.")
                progress_data["subtitle_translation"]["last_input_args"] = None

            # Guardar cambios
            with open(progress_file, "w") as f:
                json.dump(progress_data, f, indent=2)

        except Exception as e:
            self.logger.error(f"Error removiendo archivos de last_input_args: {e}")

    def _normalize_missing_file_paths(self, file_paths: List[str]) -> List[str]:
        """
        Normaliza las rutas de archivos no encontrados al formato de last_input_args.

        Args:
            file_paths: Lista de rutas de archivos.

        Returns:
            List[str]: Lista de rutas normalizadas.
        """
        normalized_missing_files = []
        for file_path in file_paths:
            # Si la ruta ya está en formato /mediajelly, mantenerla
            if file_path.startswith(MEDIAJELLY_PATH):
                normalized_missing_files.append(file_path)
            # Si está en formato absoluto del host, convertirla
            elif file_path.startswith(HOST_MEDIAJELLY_PATH):
                # Convertir /home/tafurc/mediaJelly/... a /mediajelly/...
                normalized_path = file_path.replace(HOST_MEDIAJELLY_PATH, MEDIAJELLY_PATH, 1)
                normalized_missing_files.append(normalized_path)
            else:
                # Mantener la ruta tal cual si no coincide con ninguno de los formatos conocidos
                normalized_missing_files.append(file_path)

        self.logger.debug(f"DEBUG: Normalizando {len(file_paths)} archivos no encontrados")
        self.logger.debug(f"DEBUG: Primeros 3 archivos normalizados: {normalized_missing_files[:3]}")
        return normalized_missing_files

    def _remove_files_from_corrected_paths(self, last_input_args: dict, normalized_missing_files: List[str]):
        """
        Remover archivos de corrected_paths y original_paths si existen.

        Args:
            last_input_args: Argumentos de entrada.
            normalized_missing_files: Lista de archivos normalizados a remover.
        """
        # Remover de corrected_paths
        corrected_paths = last_input_args.get("corrected_paths", [])
        self.logger.debug(f"DEBUG: corrected_paths tiene {len(corrected_paths)} elementos")
        if corrected_paths:
            self.logger.debug(f"DEBUG: Primeros 3 de corrected_paths: {corrected_paths[:3]}")
            original_count = len(corrected_paths)
            corrected_paths = [path for path in corrected_paths if path not in normalized_missing_files]

            if len(corrected_paths) != original_count:
                last_input_args["corrected_paths"] = corrected_paths
                removed_count = original_count - len(corrected_paths)
                self.logger.info(f"Removidos {removed_count} archivos de last_input_args.corrected_paths")
            else:
                self.logger.debug(
                    f"DEBUG: No se encontraron coincidencias en corrected_paths ({len(corrected_paths)} rutas)"
                )

        # Remover también de original_paths
        original_paths = last_input_args.get("original_paths", [])
        if original_paths:
            original_count = len(original_paths)
            original_paths = [path for path in original_paths if path not in normalized_missing_files]

            if len(original_paths) != original_count:
                last_input_args["original_paths"] = original_paths
                removed_count = original_count - len(original_paths)
                self.logger.info(f"Removidos {removed_count} archivos de last_input_args.original_paths")

    def _remove_files_from_file_paths(self, last_input_args: dict, normalized_missing_files: List[str]):
        """
        Remover archivos de file_paths si existen.

        Args:
            last_input_args: Argumentos de entrada.
            normalized_missing_files: Lista de archivos normalizados a remover.
        """
        file_paths_in_args = last_input_args.get("file_paths", [])
        self.logger.debug(f"DEBUG: file_paths tiene {len(file_paths_in_args)} elementos")
        if file_paths_in_args:
            self.logger.debug(f"DEBUG: Primeros 3 de file_paths: {file_paths_in_args[:3]}")
            original_count = len(file_paths_in_args)
            file_paths_in_args = [path for path in file_paths_in_args if path not in normalized_missing_files]

            if len(file_paths_in_args) != original_count:
                last_input_args["file_paths"] = file_paths_in_args
                removed_count = original_count - len(file_paths_in_args)
                self.logger.info(f"Removidos {removed_count} archivos de last_input_args.file_paths")
            else:
                self.logger.debug(
                    f"DEBUG: No se encontraron coincidencias en file_paths ({len(file_paths_in_args)} rutas)"
                )

    def _determine_processing_strategy(self) -> dict:
        """
        Determina la estrategia de procesamiento (concurrente vs secuencial).

        Returns:
            dict: Configuración de procesamiento.
        """
        # Permitir procesamiento concurrente limitado incluso con Whisper
        # El lock global de Whisper maneja la sincronización
        use_concurrent = True  # Siempre usar concurrente (con límite de workers)
        actual_workers = 2 if self.use_whisper else self.max_workers  # Max 2 con Whisper

        mode_desc = (
            f"concurrent ({actual_workers} workers, Whisper con lock)"
            if self.use_whisper
            else f"concurrent ({actual_workers} workers)"
        )
        self.logger.info(f"Modo de procesamiento: {mode_desc}")

        return {"use_concurrent": use_concurrent, "actual_workers": actual_workers}

    def _execute_processing(self, valid_files: List[Path], stats: dict, config: dict):
        """
        Ejecuta el procesamiento según la estrategia determinada.

        Args:
            valid_files: Lista de archivos a procesar.
            stats: Estadísticas de procesamiento.
            config: Configuración de procesamiento.
        """
        if config["use_concurrent"] and config["actual_workers"] > 1:
            self._process_concurrent(valid_files, stats, config["actual_workers"])
        else:
            self._process_sequential(valid_files, stats)

    def _process_concurrent(self, valid_files: List[Path], stats: dict, max_workers: int = 2):
        """
        Procesa archivos de forma concurrente.

        Args:
            valid_files: Lista de archivos a procesar.
            stats: Estadísticas de procesamiento.
            max_workers: Número máximo de workers.
        """
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(self.process_video, vf): vf for vf in valid_files}

            for future in as_completed(futures):
                # Verificar si el lock file sigue existiendo (para cancelación externa)
                if self.lock_file and not _check_lock_file_exists(self.lock_file):
                    self.logger.warning("Lock file eliminado externamente. Cancelando procesamiento...")
                    # Cancelar futures pendientes
                    for f in futures:
                        f.cancel()
                    break

                result = future.result()
                self._update_stats_with_result(stats, result)

                # Determinar estado del archivo para processed_files
                file_status = self._determine_file_status(result)

                # Actualizar progreso en tiempo real
                self.update_progress_file(
                    stats,
                    stats["processed"],
                    stats["total_files"],
                    result["file"],
                    "processing",
                    result["file"],
                    file_status,
                )
                self.logger.info(f"Progreso: {stats['processed']}/{stats['total_files']}")

    def _process_sequential(self, valid_files: List[Path], stats: dict):
        """
        Procesa archivos de forma secuencial.

        Args:
            valid_files: Lista de archivos a procesar.
            stats: Estadísticas de procesamiento.
        """
        for video_file in valid_files:
            # Verificar si el lock file sigue existiendo (para cancelación externa)
            if self.lock_file and not _check_lock_file_exists(self.lock_file):
                self.logger.warning("Lock file eliminado externamente. Cancelando procesamiento...")
                break

            # Actualizar progreso antes de procesar cada archivo
            self.update_progress_file(
                stats, stats["processed"], stats["total_files"], str(video_file), STATUS_PROCESSING
            )

            result = self.process_video(video_file)
            self._update_stats_with_result(stats, result)

            # Determinar estado del archivo y actualizar processed_files
            file_status = self._determine_file_status(result)
            self.update_progress_file(
                stats,
                stats["processed"],
                stats["total_files"],
                result["file"],
                "processing",
                result["file"],
                file_status,
            )

            self.logger.info(f"Progreso: {stats['processed']}/{stats['total_files']}")

    def _update_stats_with_result(self, stats: dict, result: dict):
        """
        Actualiza las estadísticas con el resultado de un procesamiento.

        Args:
            stats: Estadísticas de procesamiento.
            result: Resultado del procesamiento de un archivo.
        """
        with self.progress_lock:
            stats["results"].append(result)
            stats["processed"] += 1

            if result["has_spanish_audio"]:
                stats["with_spanish_audio"] += 1
            elif result.get("had_spanish_subs") or result.get("had_spanish_subtitles"):
                stats["with_spanish_subs"] += 1
            elif result["extracted"]:
                stats["extracted"] += 1
                if result["translated"]:
                    stats["translated"] += 1

            if result["error"]:
                stats["errors"] += 1
                # Agregar detalles del error al array de errores
                if "files_errors" not in stats:
                    stats["files_errors"] = []
                error_entry = {
                    "file": result.get("file", "unknown"),
                    "error": result.get("error_msg", "Error no especificado"),
                    "timestamp": datetime.now().isoformat(),
                }
                stats["files_errors"].append(error_entry)

    def _is_notification_duplicate(self, stats: dict) -> bool:
        """
        Verifica si la notificación actual es idéntica a la última enviada.

        Args:
            stats: Estadísticas actuales.

        Returns:
            bool: True si es duplicada.
        """
        try:
            progress_file = self.tmp_dir / self.PROGRESS_FILE_NAME
            if not progress_file.exists():
                return False

            with open(progress_file, "r") as f:
                progress_data = json.load(f)

            last_notification = progress_data.get("subtitle_translation", {}).get("last_notification")
            if not last_notification:
                return False

            # Crear signature de la notificación actual
            current_signature = {
                "total_files": stats.get("total_files", 0),
                "with_spanish_audio": stats.get("with_spanish_audio", 0),
                "with_spanish_subs": stats.get("with_spanish_subs", 0),
                "extracted": stats.get("extracted", 0),
                "translated": stats.get("translated", 0),
                "errors": stats.get("errors", 0),
            }

            # Comparar con la última notificación
            last_signature = last_notification.get("signature", {})

            return current_signature == last_signature

        except Exception as e:
            self.logger.warning(f"Error verificando duplicado de notificación: {e}")
            return False

    def _save_notification_signature(self, stats: dict):
        """
        Guarda la signature de la notificación enviada.

        Args:
            stats: Estadísticas enviadas.
        """
        try:
            progress_file = self.tmp_dir / self.PROGRESS_FILE_NAME

            with self.progress_lock:
                if progress_file.exists():
                    with open(progress_file, "r") as f:
                        progress_data = json.load(f)
                else:
                    progress_data = {}

                if "subtitle_translation" not in progress_data:
                    progress_data["subtitle_translation"] = {}

                # Crear signature de la notificación
                notification_signature = {
                    "total_files": stats.get("total_files", 0),
                    "with_spanish_audio": stats.get("with_spanish_audio", 0),
                    "with_spanish_subs": stats.get("with_spanish_subs", 0),
                    "extracted": stats.get("extracted", 0),
                    "translated": stats.get("translated", 0),
                    "errors": stats.get("errors", 0),
                }

                progress_data["subtitle_translation"]["last_notification"] = {
                    "signature": notification_signature,
                    "sent_at": datetime.now().isoformat(),
                }

                with open(progress_file, "w") as f:
                    json.dump(progress_data, f, indent=2)

                self.logger.info("Signature de notificación guardada")

        except Exception as e:
            self.logger.warning(f"Error guardando signature de notificación: {e}")

    def _determine_file_status(self, result: dict) -> str:
        """
        Determina el estado de un archivo basado en el resultado del procesamiento.

        Args:
            result: Resultado del procesamiento.

        Returns:
            str: Estado del archivo (error, translated, extracted, skipped, etc.).
        """
        if result.get("error"):
            return "error"
        elif result.get("had_spanish_subtitles") or result.get("had_spanish_subs"):
            return "had_spanish_subtitles"
        elif result.get("has_spanish_audio"):
            return "has_spanish_audio"
        elif result.get("translated"):
            return "translated"
        elif result.get("extracted"):
            return "extracted"
        else:
            return "skipped"

    def quick_check_needs_processing(self, video_file: Path) -> bool:
        """
        Verificación rápida para determinar si un archivo necesita procesamiento de subtítulos.

        Args:
            video_file: Ruta del archivo de video.

        Returns:
            bool: True si necesita procesamiento.
        """
        try:
            # 1. Verificar si ya tiene subtítulos en español (solo si no se permite re-traducción)
            if not self.allow_retranslate and self.check_existing_spanish_subtitles(video_file):
                return False

            # 2. Verificación rápida de tamaño (archivos muy pequeños probablemente no tienen audio útil)
            if video_file.stat().st_size < 10 * 1024 * 1024:  # Menos de 10MB
                self.logger.debug(f"Archivo muy pequeño, omitiendo: {video_file.name}")
                return False

            # 3. Verificación rápida de extensión
            if video_file.suffix.lower() not in [".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm"]:
                self.logger.debug(f"Formato no soportado: {video_file.suffix}")
                return False

            return True

        except Exception as e:
            self.logger.warning(f"Error en verificación rápida de {video_file.name}: {e}")
            return False

    def process_directory_resuming(self, directory: Path, processed_files: dict, input_args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Reanuda el procesamiento de un directorio, omitiendo archivos ya procesados que tienen subtítulos.

        Args:
            directory: Directorio a procesar.
            processed_files: Diccionario de archivos ya procesados.
            input_args: Argumentos de entrada opcionales.

        Returns:
            Dict[str, Any]: Estadísticas del procesamiento.
        """
        # Buscar archivos de video en el directorio
        all_video_files = self._find_video_files_in_directory(directory)

        # Filtrar archivos que necesitan procesamiento (no procesados o procesados pero sin subtítulos)
        files_needing_processing = []
        for video_file in all_video_files:
            file_path_str = str(video_file)
            if file_path_str not in processed_files:
                # Archivo no procesado nunca
                files_needing_processing.append(video_file)
            else:
                # Archivo procesado anteriormente, verificar si tiene subtítulos
                processed_info = processed_files[file_path_str]
                if not processed_info.get("has_subtitles", False):
                    # Archivo procesado pero sin subtítulos, necesita reprocesamiento
                    self.logger.debug(
                        f"Archivo procesado anteriormente pero sin subtítulos, reprocesando: {video_file.name}"
                    )
                    files_needing_processing.append(video_file)
                else:
                    self.logger.debug(f"Archivo ya tiene subtítulos, omitiendo: {video_file.name}")

        self.logger.info(
            f"Archivos totales: {len(all_video_files)}, ya procesados con subtítulos: {len(all_video_files) - len(files_needing_processing)}, pendientes: {len(files_needing_processing)}"
        )

        if not files_needing_processing:
            self.logger.info("Todos los archivos ya tienen subtítulos o fueron procesados anteriormente")
            # Retornar estadísticas del progreso guardado
            return self._get_stats_from_progress()

        # Procesar solo los archivos que necesitan procesamiento
        return self.process_file_list(files_needing_processing, input_args)

    def process_file_list_resuming(
        self, file_paths: List[Path], processed_files: dict, input_args: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Reanuda el procesamiento de una lista de archivos, procesando archivos sin subtítulos aunque ya fueron analizados.

        Args:
            file_paths: Lista de archivos a procesar.
            processed_files: Diccionario de archivos ya procesados.
            input_args: Argumentos de entrada opcionales.

        Returns:
            Dict[str, Any]: Estadísticas del procesamiento.
        """
        # Filtrar archivos que necesitan procesamiento (no procesados o procesados pero sin subtítulos)
        files_needing_processing = []
        for video_file in file_paths:
            file_path_str = str(video_file)
            if file_path_str not in processed_files:
                # Archivo no procesado nunca
                files_needing_processing.append(video_file)
            else:
                # Archivo procesado anteriormente, verificar si tiene subtítulos
                processed_info = processed_files[file_path_str]
                if not processed_info.get("has_subtitles", False):
                    # Archivo procesado pero sin subtítulos, necesita reprocesamiento
                    self.logger.debug(
                        f"Archivo procesado anteriormente pero sin subtítulos, reprocesando: {video_file.name}"
                    )
                    files_needing_processing.append(video_file)
                else:
                    self.logger.debug(f"Archivo ya tiene subtítulos, omitiendo: {video_file.name}")

        self.logger.info(
            f"Archivos totales: {len(file_paths)}, ya procesados con subtítulos: {len(file_paths) - len(files_needing_processing)}, pendientes: {len(files_needing_processing)}"
        )

        if not files_needing_processing:
            self.logger.info("Todos los archivos ya tienen subtítulos o fueron procesados anteriormente")
            # Retornar estadísticas del progreso guardado
            return self._get_stats_from_progress()

        # Procesar archivos directamente sin re-filtrado (ya están filtrados)
        return self._process_file_list_direct(files_needing_processing, input_args)

    def _process_file_list_direct(self, file_paths: List[Path], input_args: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Procesa una lista de archivos ya filtrada, sin validación adicional.

        Args:
            file_paths: Lista de archivos a procesar.
            input_args: Argumentos de entrada opcionales.

        Returns:
            Dict[str, Any]: Estadísticas del procesamiento.
        """
        # Inicializar estadísticas
        stats = self._initialize_processing_stats(len(file_paths))

        # Limpiar archivos temporales e incompletos
        self.cleanup_incomplete_files(file_paths)

        # Filtrar input_args para que solo contenga archivos válidos
        if input_args:
            self._filter_input_args_for_valid_files(input_args, file_paths)

        # Determinar estrategia de procesamiento
        processing_config = self._determine_processing_strategy()

        # Actualizar progreso inicial con argumentos de entrada filtrados
        self.update_progress_file(stats, 0, stats["total_files"], "", STATUS_PROCESSING, input_args=input_args)

        # Ejecutar procesamiento según la estrategia
        self._execute_processing(file_paths, stats, processing_config)

        # Actualizar progreso final
        self.update_progress_file(stats, stats["processed"], stats["total_files"], "", STATUS_COMPLETED)

        # Si el procesamiento se completó al 100%, limpiar last_input_args
        if stats["processed"] == stats["total_files"]:
            self._clear_input_args_from_progress()

        return stats

    def _get_processed_files_info(self) -> dict:
        """
        Obtiene información de archivos ya procesados desde el archivo de progreso.

        Returns:
            dict: Diccionario de archivos procesados.
        """
        progress_file = self.tmp_dir / self.PROGRESS_FILE_NAME
        if not progress_file.exists():
            return {}

        try:
            with open(progress_file, "r") as f:
                progress_data = json.load(f)

            subtitle_translation = progress_data.get("subtitle_translation", {})
            return subtitle_translation.get("processed_files", {})
        except Exception as e:
            self.logger.warning(f"Error leyendo información de archivos procesados: {e}")
            return {}

    def _get_stats_from_progress(self) -> dict:
        """
        Obtiene estadísticas del archivo de progreso guardado.

        Returns:
            dict: Estadísticas recuperadas.
        """
        progress_file = self.tmp_dir / self.PROGRESS_FILE_NAME
        try:
            with open(progress_file, "r") as f:
                progress_data = json.load(f)

            subtitle_translation = progress_data.get("subtitle_translation", {})
            stats = subtitle_translation.get("stats", {})

            # Convertir las claves de stats para que coincidan con el formato esperado
            return {
                "total_files": stats.get("files_found", 0),
                "processed": stats.get("files_processed", 0),
                "extracted": stats.get("files_extracted", 0),
                "translated": stats.get("files_translated", 0),
                "with_spanish_audio": stats.get("files_with_spanish_audio", 0),
                "with_spanish_subs": stats.get("files_with_spanish_subs", 0),
                "errors": stats.get("files_errors", 0),
            }
        except Exception as e:
            self.logger.error(f"Error obteniendo estadísticas del progreso: {e}")
            return {"error": "Could not retrieve stats from progress"}

    def _clear_input_args_from_progress(self):
        """Limpia los argumentos de entrada guardados cuando el procesamiento se completa."""
        progress_file = self.tmp_dir / self.PROGRESS_FILE_NAME
        try:
            if progress_file.exists():
                with open(progress_file, "r") as f:
                    progress_data = json.load(f)

                if "subtitle_translation" in progress_data:
                    progress_data["subtitle_translation"]["last_input_args"] = None

                    with open(progress_file, "w") as f:
                        json.dump(progress_data, f, indent=2)

        except Exception as e:
            self.logger.warning(f"Error limpiando argumentos de entrada: {e}")

    def update_progress_file(
        self,
        stats: dict,
        current_file: int = 0,
        total_files: int = 0,
        current_file_name: str = "",
        status: str = STATUS_PROCESSING,
        processed_file_path: Optional[str] = None,
        file_status: Optional[str] = None,
        input_args: Optional[Dict[str, Any]] = None,
    ):
        """
        Actualiza el archivo de progreso con estadísticas de subtítulos (thread-safe).

        Args:
            stats: Estadísticas actuales.
            current_file: Índice del archivo actual.
            total_files: Total de archivos.
            current_file_name: Nombre del archivo actual.
            status: Estado general del proceso.
            processed_file_path: Ruta del archivo procesado (opcional).
            file_status: Estado del archivo procesado (opcional).
            input_args: Argumentos de entrada (opcional).
        """
        progress_file = self.tmp_dir / self.PROGRESS_FILE_NAME

        with self.progress_lock:
            try:
                # Leer o inicializar progreso
                progress_data = self._load_or_initialize_progress_data(progress_file)

                # Asegurar estructura completa
                self._ensure_progress_structure(progress_data)

                # Actualizar datos básicos
                self._update_basic_progress_data(progress_data, current_file, total_files, current_file_name, status)

                # Actualizar estadísticas detalladas si es necesario
                self._update_detailed_stats_if_needed(progress_data, stats, current_file, total_files)

                # Actualizar archivo procesado si se proporciona
                if processed_file_path and file_status:
                    self._update_processed_file_status(progress_data, processed_file_path, file_status)

                # Guardar argumentos de entrada si se proporcionan
                if input_args:
                    progress_data["subtitle_translation"]["last_input_args"] = input_args

                # Guardar cambios
                with open(progress_file, "w") as f:
                    json.dump(progress_data, f, indent=2)

                self.logger.info("Archivo de progreso actualizado con estadísticas de subtítulos")

            except Exception as e:
                self.logger.error(f"Error actualizando archivo de progreso: {e}")

    def _load_or_initialize_progress_data(self, progress_file: Path) -> dict:
        """
        Lee progreso existente o inicializa estructura nueva.

        Args:
            progress_file: Ruta del archivo de progreso.

        Returns:
            dict: Datos de progreso.
        """
        if progress_file.exists():
            with open(progress_file, "r") as f:
                return json.load(f)
        else:
            return self._create_initial_progress_structure()

    def _create_initial_progress_structure(self) -> dict:
        """
        Crea la estructura inicial del archivo de progreso.

        Returns:
            dict: Estructura inicial.
        """
        return {
            "processing": {
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
            },
            "subtitle_translation": {
                "current_file": 0,
                "total_files": 0,
                "current_file_name": "",
                "percentage": 0.0,
                "last_updated": datetime.now().isoformat(),
                "last_run": datetime.now().isoformat(),
                "status": "idle",
                "notified": False,
                "stats": {
                    "files_found": 0,
                    "files_processed": 0,
                    "files_extracted": 0,
                    "files_translated": 0,
                    "files_with_spanish_audio": 0,
                    "files_with_spanish_subs": 0,
                    "files_errors": 0,
                    "total_original_size": 0,
                    "total_processed_size": 0,
                    "errors": [],
                    "whisper_used": False,
                },
                "processed_files": {},
                "last_input_args": None,
                "last_notification": None,
            },
        }

    def _ensure_progress_structure(self, progress_data: dict):
        """
        Asegura que la estructura del progreso esté completa.

        Args:
            progress_data: Datos de progreso a verificar/actualizar.
        """
        # Asegurar que existe la sección subtitle_translation con estructura completa
        if "subtitle_translation" not in progress_data:
            progress_data["subtitle_translation"] = {
                "current_file": 0,
                "total_files": 0,
                "current_file_name": "",
                "percentage": 0.0,
                "last_updated": datetime.now().isoformat(),
                "last_run": datetime.now().isoformat(),
                "status": "idle",
                "notified": False,
                "stats": {
                    "files_found": 0,
                    "files_processed": 0,
                    "files_extracted": 0,
                    "files_translated": 0,
                    "files_with_spanish_audio": 0,
                    "files_with_spanish_subs": 0,
                    "files_errors": 0,
                    "total_original_size": 0,
                    "total_processed_size": 0,
                    "errors": [],
                    "whisper_used": False,
                },
                "processed_files": {},
                "last_input_args": None,
                "last_notification": None,
            }

        # Asegurar que el campo last_notification existe
        if "last_notification" not in progress_data["subtitle_translation"]:
            progress_data["subtitle_translation"]["last_notification"] = None

    def _update_basic_progress_data(
        self, progress_data: dict, current_file: int, total_files: int, current_file_name: str, status: str
    ):
        """
        Actualiza los datos básicos de progreso.

        Args:
            progress_data: Datos de progreso.
            current_file: Índice actual.
            total_files: Total de archivos.
            current_file_name: Nombre del archivo actual.
            status: Estado del proceso.
        """
        # Calcular porcentaje
        percentage = (current_file / total_files * 100) if total_files > 0 else 0.0

        # Actualizar estadísticas de subtítulos con la estructura completa
        progress_data["subtitle_translation"].update(
            {
                "current_file": current_file,
                "total_files": total_files,
                "current_file_name": current_file_name,
                "percentage": percentage,
                "last_updated": datetime.now().isoformat(),
                "last_run": datetime.now().isoformat(),
                "status": status,
                "notified": False,
            }
        )

    def _update_detailed_stats_if_needed(self, progress_data: dict, stats: dict, current_file: int, total_files: int):
        """
        Actualiza estadísticas detalladas solo si es necesario.

        Args:
            progress_data: Datos de progreso.
            stats: Estadísticas actuales.
            current_file: Índice actual.
            total_files: Total de archivos.
        """
        percentage = (current_file / total_files * 100) if total_files > 0 else 0.0

        # SOLO actualizar stats detallados si el porcentaje es 100% (completado)
        # o si es la primera vez (stats vacías)
        current_stats = progress_data["subtitle_translation"]["stats"]
        if self._should_update_detailed_stats(percentage, current_stats):
            errors_list = self._process_error_stats(stats)

            self._update_stats_data(progress_data, stats, errors_list)

    def _should_update_detailed_stats(self, percentage: float, current_stats: dict) -> bool:
        """
        Determina si se deben actualizar las estadísticas detalladas.

        Args:
            percentage: Porcentaje de progreso.
            current_stats: Estadísticas actuales.

        Returns:
            bool: True si se debe actualizar.
        """
        return percentage >= 100.0 or current_stats.get("files_found", 0) == 0

    def _process_error_stats(self, stats: dict) -> list:
        """
        Procesa las estadísticas de errores para el formato correcto.

        Args:
            stats: Estadísticas con errores.

        Returns:
            list: Lista de errores formateada.
        """
        errors_list = []
        if "files_errors" in stats and stats.get("files_errors"):
            if isinstance(stats["files_errors"], list):
                # files_errors es un array de objetos con detalles
                errors_list = stats["files_errors"]
            elif isinstance(stats["files_errors"], int) and stats["files_errors"] > 0:
                # Si files_errors es un número, generar entradas genéricas
                for i in range(stats["files_errors"]):
                    errors_list.append(
                        {"error": "Error de procesamiento #" + str(i + 1), "timestamp": datetime.now().isoformat()}
                    )
        return errors_list

    def _update_stats_data(self, progress_data: dict, stats: dict, errors_list: list):
        """
        Actualiza los datos de estadísticas en progress_data.

        Args:
            progress_data: Datos de progreso.
            stats: Estadísticas actuales.
            errors_list: Lista de errores procesada.
        """
        progress_data["subtitle_translation"]["stats"].update(
            {
                "files_found": stats.get("total_files", 0),
                "files_processed": stats.get("processed", 0),
                "files_extracted": stats.get("extracted", 0),
                "files_translated": stats.get("translated", 0),
                "files_with_spanish_audio": stats.get("with_spanish_audio", 0),
                "files_with_spanish_subs": stats.get("with_spanish_subs", 0),
                "files_errors": stats.get("errors", 0),
                "errors": errors_list,
                "whisper_used": self.use_whisper and self.whisper_available,
            }
        )

    def _update_processed_file_status(self, progress_data: dict, processed_file_path: str, file_status: str):
        """
        Actualiza el estado de un archivo procesado.

        Args:
            progress_data: Datos de progreso.
            processed_file_path: Ruta del archivo procesado.
            file_status: Nuevo estado del archivo.
        """
        progress_data["subtitle_translation"]["processed_files"][processed_file_path] = {
            "status": file_status,
            "processed_at": datetime.now().isoformat(),
            "has_subtitles": file_status in ["translated", "had_spanish_subtitles", "had_spanish_subs"],
        }

    def extract_subtitles(self, video_file: Path) -> Optional[Path]:
        """
        Extrae subtítulos del archivo de video (embebidos en cualquier idioma o desde audio).

        Args:
            video_file: Ruta del archivo de video.

        Returns:
            Optional[Path]: Ruta del archivo SRT extraído o None.
        """
        try:
            # Intentar extraer subtítulos embebidos primero
            embedded_srt = self._try_extract_embedded_subtitles(video_file)
            if embedded_srt:
                return embedded_srt

            # Si no hay subtítulos embebidos útiles, intentar extraer del audio con Whisper
            return self._try_extract_audio_subtitles(video_file)

        except subprocess.TimeoutExpired:
            self.logger.error(f"Timeout extrayendo subtítulos (60s): {video_file}")
            return None
        except Exception as e:
            self.logger.error(f"Error extrayendo subtítulos: {e}")
            return None

    def _try_extract_embedded_subtitles(self, video_file: Path) -> Optional[Path]:
        """
        Intenta extraer subtítulos embebidos del video.

        Args:
            video_file: Ruta del archivo de video.

        Returns:
            Optional[Path]: Ruta del archivo SRT extraído o None.
        """
        embedded_streams = self._detect_embedded_subtitle_streams(video_file)
        if not embedded_streams:
            return None

        detected_lang = self.detect_embedded_subtitle_language(video_file)

        if detected_lang and self._should_extract_embedded_subtitles(detected_lang):
            return self._extract_embedded_subtitles(video_file, detected_lang)
        elif detected_lang:
            self._log_embedded_subtitles_info(detected_lang)
            return None
        else:
            return None

    def _detect_embedded_subtitle_streams(self, video_file: Path) -> list:
        """
        Detecta streams de subtítulos embebidos en el video.

        Args:
            video_file: Ruta del archivo de video.

        Returns:
            list: Lista de streams de subtítulos encontrados.
        """
        try:
            cmd = [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "s",
                "-show_entries",
                "stream=index:stream_tags=language",
                "-of",
                "json",
                str(video_file),
            ]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=FFPROBE_TIMEOUT)

            if result.returncode == 0:
                data = json.loads(result.stdout)
                return data.get("streams", [])

            return []
        except subprocess.TimeoutExpired:
            self.logger.warning(f"Timeout detectando subtítulos embebidos ({FFPROBE_TIMEOUT}s): {video_file}")
            return []
        except Exception as e:
            self.logger.warning(f"Error detectando subtítulos embebidos: {e}")
            return []

    def _should_extract_embedded_subtitles(self, detected_lang: str) -> bool:
        """
        Determina si se deben extraer subtítulos embebidos basados en el idioma.

        Args:
            detected_lang: Código de idioma detectado.

        Returns:
            bool: True si se deben extraer.
        """
        supported_langs = ["ja", "en", "fr", "de", "it", "pt", "ru", "ko", "zh", "es", "ca", "eu"]
        return bool(detected_lang and detected_lang in supported_langs)

    def _extract_embedded_subtitles(self, video_file: Path, detected_lang: str) -> Optional[Path]:
        """
        Extrae subtítulos embebidos del video.

        Args:
            video_file: Ruta del archivo de video.
            detected_lang: Idioma detectado.

        Returns:
            Optional[Path]: Ruta del archivo SRT extraído o None.
        """
        output_srt = video_file.parent / f"{video_file.stem}.srt"

        cmd = ["ffmpeg", "-v", "error", "-y", "-i", str(video_file), "-map", "0:s:0", "-c:s", "srt", str(output_srt)]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

        if result.returncode == 0 and output_srt.exists():
            self.logger.info(
                f"{EmojiGenerator.check_mark()} Subtítulos embebidos en '{detected_lang}' extraídos: {output_srt.name}"
            )

            # Aplicar mejora de calidad a los subtítulos extraídos
            self._improve_extracted_subtitles_quality(output_srt, detected_lang)

            return output_srt

        return None

    def _improve_extracted_subtitles_quality(self, srt_file: Path, language: str):
        """
        Aplica mejora de calidad a los subtítulos extraídos.

        Args:
            srt_file: Ruta del archivo SRT.
            language: Idioma de los subtítulos.
        """
        try:
            # Leer contenido del archivo
            with open(srt_file, "r", encoding="utf-8") as f:
                original_content = f.read()

            # Aplicar mejora de calidad
            improved_content = self.quality_improver.improve_subtitle_quality(original_content, language)

            # Solo guardar si hubo cambios
            if improved_content != original_content:
                with open(srt_file, "w", encoding="utf-8") as f:
                    f.write(improved_content)
                self.logger.debug(f"Calidad de subtítulos mejorada: {srt_file.name}")

        except Exception as e:
            self.logger.warning(f"Error mejorando calidad de subtítulos extraídos {srt_file.name}: {e}")

    def _improve_translated_subtitles_quality(self, srt_file: Path):
        """
        Aplica mejora de calidad específica para subtítulos traducidos al español.

        Args:
            srt_file: Ruta del archivo SRT.
        """
        try:
            # Leer contenido del archivo
            with open(srt_file, "r", encoding="utf-8") as f:
                original_content = f.read()

            # Aplicar mejora de calidad enfocada en español
            improved_content = self.quality_improver.improve_subtitle_quality(original_content, "es")

            # Solo guardar si hubo cambios
            if improved_content != original_content:
                with open(srt_file, "w", encoding="utf-8") as f:
                    f.write(improved_content)
                self.logger.debug(f"Calidad de subtítulos traducidos mejorada: {srt_file.name}")

        except Exception as e:
            self.logger.warning(f"Error mejorando calidad de subtítulos traducidos {srt_file.name}: {e}")

    def _log_embedded_subtitles_info(self, detected_lang: str):
        """
        Registra información sobre subtítulos embebidos no utilizables.

        Args:
            detected_lang: Idioma detectado.
        """
        if detected_lang:
            self.logger.info(
                f"Subtítulos embebidos detectados en idioma '{detected_lang}' no soportado, usando Whisper"
            )
        else:
            self.logger.info("No se pudo detectar idioma de subtítulos embebidos, usando Whisper")

    def _try_extract_audio_subtitles(self, video_file: Path) -> Optional[Path]:
        """
        Intenta extraer subtítulos del audio usando Whisper.

        Args:
            video_file: Ruta del archivo de video.

        Returns:
            Optional[Path]: Ruta del archivo SRT extraído o None.
        """
        if self.use_whisper and self.whisper_available:
            self.logger.info("Usando Whisper para extraer subtítulos del audio...")
            return self.extract_subtitles_from_audio(video_file)
        else:
            self.logger.debug(f"No hay subtítulos embebidos útiles y Whisper no disponible: {video_file.name}")
            return None

    def parse_html_subtitle(self, text: str) -> Tuple[str, List[str]]:
        """
        Parsea texto con HTML y extrae el contenido puro y las etiquetas.

        Args:
            text: Texto con posible HTML.

        Returns:
            Tuple[str, List[str]]: (Texto puro, Lista de etiquetas HTML).
        """
        import re

        # Separar texto y etiquetas usando split con captura de grupos
        parts = re.split(r"(<[^>]+>)", text)

        # Separar texto y etiquetas
        text_parts = []
        html_parts = []

        for part in parts:
            if part.strip():
                if part.startswith("<") and part.endswith(">"):
                    html_parts.append(part)
                else:
                    text_parts.append(part)

        # Unir todo el texto puro
        pure_text = " ".join(text_parts).strip()

        return pure_text, html_parts

    def reconstruct_html_subtitle(self, translated_text: str, html_parts: List[str]) -> str:
        """
        Reconstruye el texto con HTML usando el texto traducido.

        Args:
            translated_text: Texto traducido.
            html_parts: Lista de etiquetas HTML originales.

        Returns:
            str: Texto reconstruido con etiquetas HTML.
        """
        if not html_parts:
            return translated_text

        # Para simplificar, si hay HTML, envolvemos todo el texto traducido
        # con las etiquetas más externas
        result = translated_text

        # Si hay etiquetas de apertura y cierre, aplicarlas
        if html_parts:
            # Buscar etiquetas de apertura y cierre
            opening_tags = [tag for tag in html_parts if not tag.startswith("</")]
            closing_tags = [tag for tag in html_parts if tag.startswith("</")]

            # Aplicar etiquetas de apertura al inicio
            for tag in opening_tags:
                result = tag + result

            # Aplicar etiquetas de cierre al final
            for tag in closing_tags:
                result = result + tag

        return result


def _validate_dependencies():
    """Valida que las dependencias críticas estén disponibles."""
    missing_deps = []

    # Verificar FFmpeg
    try:
        result = subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=10)
        if result.returncode != 0:
            missing_deps.append("FFmpeg")
    except (subprocess.TimeoutExpired, FileNotFoundError):
        missing_deps.append("FFmpeg")

    # Verificar FFprobe
    try:
        result = subprocess.run(["ffprobe", "-version"], capture_output=True, timeout=10)
        if result.returncode != 0:
            missing_deps.append("FFprobe")
    except (subprocess.TimeoutExpired, FileNotFoundError):
        missing_deps.append("FFprobe")

    # Verificar dependencias Python críticas
    if not WHISPER_AVAILABLE:
        print("Advertencia: Whisper no está disponible - se usará solo traducción de texto")

    if not LANGDETECT_AVAILABLE:
        missing_deps.append("langdetect (pip install langdetect)")

    if not YANDEX_AVAILABLE:
        print("Advertencia: Yandex Translate no está disponible - se usarán traductores alternativos")

    if missing_deps:
        print(f"Error: Dependencias faltantes: {', '.join(missing_deps)}")
        print("Instale las dependencias faltantes y vuelva a ejecutar")
        sys.exit(1)


def _is_night_time() -> bool:
    """Verifica si es hora de procesar subtítulos (noche: 00:00-05:59)"""
    from datetime import datetime

    current_hour = datetime.now().hour
    # Noche: de 00:00 a 05:59 (0-5)
    return current_hour <= 5


def main():
    """Función principal"""
    import argparse
    import fcntl
    import signal
    import sys

    # Validar dependencias críticas al inicio
    _validate_dependencies()

    # NO verificar restricción de horario cuando se llama directamente
    # La restricción solo aplica cuando es llamado desde mediajelly_cron_runner.py

    # Configurar entorno y paths
    env_config = _setup_environment()

    # Manejar lock para evitar múltiples ejecuciones
    _handle_lock_file(env_config["subtitle_lock_file"])

    # Registrar cleanup para señales de terminación
    signal.signal(signal.SIGTERM, lambda signum, frame: _signal_handler(signum, env_config["subtitle_lock_file"]))
    signal.signal(signal.SIGINT, lambda signum, frame: _signal_handler(signum, env_config["subtitle_lock_file"]))

    try:
        # Parsear argumentos y configurar traductor
        args, translator = _parse_arguments_and_setup_translator(env_config)

        # Procesar archivos
        stats = _execute_processing(args, translator)

        # Verificar si hubo error
        if isinstance(stats, dict) and "error" in stats:
            print(f"Error: {stats['error']}")
            return

        # Guardar métricas de traducción
        try:
            execution_time = time.time()  # Usar tiempo total de ejecución
            metrics = translator.metrics_collector.calculate_metrics(translator.translation_stats, execution_time)
            translator.metrics_collector.save_metrics(metrics)
            translator.logger.info(f"{EmojiGenerator.success()} Métricas de traducción guardadas")
        except Exception as e:
            translator.logger.error(f"Error guardando métricas de traducción: {e}")

        # Enviar notificaciones y mostrar resumen
        _send_notifications_and_summary(env_config, translator, stats)

    finally:
        # Limpiar lock siempre
        _cleanup_lock_file(env_config["subtitle_lock_file"])


def _setup_environment() -> dict:
    """Configura el entorno y paths necesarios"""
    # Cargar configuración centralizada
    if CONFIG_AVAILABLE:
        try:
            config = get_config()
            print(f"{EmojiGenerator.success()} Configuración YAML cargada exitosamente")
        except Exception as e:
            print(f"Error cargando configuración YAML: {e}")
            raise
    else:
        print("Configuración YAML no disponible")
        raise ImportError("MediaJellyConfig no disponible")

    # Detectar entorno para paths
    is_container = Path("/mediajelly").exists()
    base_dir = Path("/mediajelly" if is_container else "/home/tafurc/mediaJelly")
    scripts_dir = base_dir / "scripts"
    tmp_dir = scripts_dir / "tmp"

    # Lock para evitar múltiples ejecuciones simultáneas del traductor
    subtitle_lock_file = tmp_dir / "subtitle_translator.lock"

    return {
        "is_container": is_container,
        "base_dir": base_dir,
        "scripts_dir": scripts_dir,
        "tmp_dir": tmp_dir,
        "subtitle_lock_file": subtitle_lock_file,
        "config": config,
    }


def _handle_lock_file(lock_file: Path):
    """Maneja la creación y verificación del archivo de lock"""
    # Verificar si ya hay otro proceso ejecutándose
    if lock_file.exists():
        _check_existing_lock(lock_file)

    # Crear lock
    _create_lock_file(lock_file)


def _check_existing_lock(lock_file: Path):
    """Verifica si existe un lock de otro proceso usando flock"""
    try:
        # Intentar abrir y adquirir lock exclusivo de forma no bloqueante
        lock_fd = open(lock_file, "a+")
        try:
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            # Lock adquirido, liberar y limpiar para que _create_lock_file lo cree
            fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
            lock_fd.close()
            # Si el archivo existe pero pudimos adquirir el lock, limpiarlo
            lock_file.unlink(missing_ok=True)
        except (IOError, OSError):
            # No se pudo adquirir el lock, hay otro proceso ejecutándose
            lock_fd.seek(0)
            pid = lock_fd.read().strip()
            lock_fd.close()
            print(f"{EmojiGenerator.lock()} Ya hay un proceso de traducción ejecutándose (PID: {pid})")
            print("   Espere a que termine el proceso actual")
            sys.exit(1)
    except FileNotFoundError:
        # El archivo no existe, continuar normalmente
        pass
    except Exception as e:
        print(f"{EmojiGenerator.warning()} Error al verificar lock existente: {e}, continuando...")
        lock_file.unlink(missing_ok=True)


def _create_lock_file(lock_file: Path):
    """Crea el archivo de lock para este proceso"""
    try:
        lock_file.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_file, "w") as f:
            f.write(str(os.getpid()))
        print(f"{EmojiGenerator.lock()} Lock creado: {lock_file}")
    except Exception as e:
        print(f"{EmojiGenerator.cross_mark()} Error al crear lock: {e}")
        sys.exit(1)


def _check_lock_file_exists(lock_file: Path) -> bool:
    """Verifica si el archivo de lock sigue existiendo (para cancelación externa)"""
    return lock_file.exists()


def _parse_arguments_and_setup_translator(env_config: Dict[str, Any]) -> tuple:
    """Parsea argumentos de línea de comandos y configura el traductor"""
    import argparse

    parser = argparse.ArgumentParser(description="MediaJelly Subtitle Translator")
    parser.add_argument(
        "paths", nargs="*", help="Directorio o archivos de video a procesar (opcional si hay proceso incompleto)"
    )
    parser.add_argument(
        "--max-workers",
        type=int,
        default=2,
        help="Máximo de archivos procesados simultáneamente (default: 2, se ajusta a 1 con Whisper)",
    )
    parser.add_argument("--no-whisper", action="store_true", help="Desactivar Whisper (solo subtítulos embebidos)")
    parser.add_argument(
        "--retranslate", action="store_true", help="Permitir re-traducción de archivos .es.srt existentes"
    )

    args = parser.parse_args()

    # Ajustar max_workers automáticamente cuando se usa Whisper
    if not args.no_whisper:
        args.max_workers = 2  # Whisper solo procesa 2 por 2
        print(f"{EmojiGenerator.magnifying_glass()} Whisper habilitado: Procesamiento secuencial (1 archivo por vez)")

    # Crear traductor con parámetros
    translator = SubtitleTranslator(
        max_workers=args.max_workers, use_whisper=not args.no_whisper, allow_retranslate=args.retranslate, lock_file=env_config["subtitle_lock_file"]
    )

    return args, translator


def _reset_subtitle_progress(translator, input_args: dict):
    """Resetea las estadísticas y archivos procesados cuando las rutas cambian"""
    progress_file = translator.tmp_dir / translator.PROGRESS_FILE_NAME

    try:
        # Leer el archivo actual
        if progress_file.exists():
            with open(progress_file, "r") as f:
                progress_data = json.load(f)
        else:
            progress_data = {}

        # Resetear la sección de subtitle_translation
        progress_data["subtitle_translation"] = {
            "current_file": 0,
            "total_files": 0,
            "current_file_name": "",
            "percentage": 0.0,
            "last_updated": datetime.now().isoformat(),
            "last_run": datetime.now().isoformat(),
            "status": "reset",
            "notified": False,
            "stats": {
                "files_found": 0,
                "files_processed": 0,
                "files_extracted": 0,
                "files_translated": 0,
                "files_with_spanish_audio": 0,
                "files_with_spanish_subs": 0,
                "files_errors": 0,
                "total_original_size": 0,
                "total_processed_size": 0,
                "errors": [],
                "whisper_used": not input_args.get("use_whisper", True),
            },
            "processed_files": {},
            "last_input_args": {"args": input_args, "saved_at": datetime.now().isoformat()},
            "last_notification": None,
        }

        # Guardar el archivo actualizado
        with open(progress_file, "w") as f:
            json.dump(progress_data, f, indent=2, ensure_ascii=False)

        translator.logger.info("Progreso de subtítulos reseteado debido a cambio de rutas")
        translator.logger.info(f"Nueva ruta: {input_args.get('corrected_paths', [''])[0]}")

    except Exception as e:
        translator.logger.error(f"Error reseteando progreso de subtítulos: {e}")


def _resume_incomplete_process(translator) -> dict:
    """Reanuda un proceso incompleto de traducción de subtítulos"""
    progress_file = translator.tmp_dir / translator.PROGRESS_FILE_NAME

    try:
        with open(progress_file, "r") as f:
            progress_data = json.load(f)

        subtitle_translation = progress_data.get("subtitle_translation", {})
        last_input_args = subtitle_translation.get("last_input_args", {})
        processed_files = subtitle_translation.get("processed_files", {})

        translator.logger.info("=== Reanudando proceso incompleto de traducción de subtítulos ===")

        # Obtener los argumentos originales
        args_data = last_input_args

        # Manejar estructura anidada (compatibilidad con versiones anteriores)
        if isinstance(args_data, dict) and "args" in args_data:
            args_data = args_data["args"]

        corrected_paths = args_data.get("corrected_paths", [])

        if not corrected_paths:
            translator.logger.error("No se encontraron rutas guardadas para reanudar")
            return {"error": "No saved paths found"}

        # Determinar si es directorio o archivos
        first_arg = Path(corrected_paths[0])

        if first_arg.is_dir():
            # Procesar directorio pero filtrar archivos ya procesados
            translator.logger.info(f"Reanudando procesamiento del directorio: {first_arg}")
            stats = translator.process_directory_resuming(first_arg, processed_files, last_input_args)
        else:
            # Procesar archivos pero filtrar los ya procesados
            file_paths = [Path(p) for p in corrected_paths]
            translator.logger.info(f"Reanudando procesamiento de {len(file_paths)} archivo(s)")
            stats = translator.process_file_list_resuming(file_paths, processed_files, last_input_args)

        return stats

    except Exception as e:
        translator.logger.error(f"Error reanudando proceso incompleto: {e}")
        return {"error": str(e)}


def _should_resume_incomplete_process(translator, current_args=None) -> bool:
    """Verifica si hay un proceso incompleto que se puede reanudar"""
    progress_file = translator.tmp_dir / translator.PROGRESS_FILE_NAME
    if not progress_file.exists():
        return False

    try:
        with open(progress_file, "r") as f:
            progress_data = json.load(f)

        subtitle_translation = progress_data.get("subtitle_translation", {})
        percentage = subtitle_translation.get("percentage", 0)
        last_input_args = subtitle_translation.get("last_input_args")

        # Solo reanudar si el porcentaje es menor al 100% y hay argumentos guardados
        if percentage >= 100.0 or last_input_args is None:
            return False

        # Si se proporcionan argumentos actuales, verificar que coincidan con los guardados
        if current_args:
            saved_args = last_input_args.get("args", {})
            saved_paths = saved_args.get("corrected_paths", [])
            current_paths = current_args.get("corrected_paths", [])

            # Comparar las rutas (ignorar otros parámetros)
            if saved_paths != current_paths:
                translator.logger.info(
                    f"Rutas diferentes detectadas. Guardadas: {saved_paths}, Actuales: {current_paths}. No se reanudará el proceso anterior."
                )
                return False

        return True
    except Exception:
        return False


def _execute_processing(args, translator) -> dict:
    """Ejecuta el procesamiento de archivos o directorio"""
    # Verificar si se deben reanudar procesos incompletos
    resume_check = _handle_resume_logic(args, translator)
    if isinstance(resume_check, dict):
        return resume_check

    # Preparar argumentos de entrada
    input_args = _prepare_input_args(args, translator)

    # Verificar si se debe reanudar proceso incompleto
    if _should_resume_incomplete_process(translator, input_args):
        return _resume_incomplete_process(translator)
    else:
        # Resetear progreso si las rutas son diferentes
        _reset_subtitle_progress(translator, input_args)

    # Ejecutar el procesamiento principal
    return _execute_main_processing(args, translator, input_args)


def _handle_resume_logic(args, translator) -> dict | None:
    """Maneja la lógica de reanudación de procesos incompletos"""
    if not args.paths:
        # Primero intentar leer archivos pendientes de subtítulos para ejecución automática
        pending_file = Path(CONTAINER_PATH) / "scripts" / "tmp" / "pending_subtitles.txt"
        if pending_file.exists():
            try:
                with open(pending_file, "r", encoding="utf-8") as f:
                    pending_paths = [line.strip() for line in f if line.strip()]

                if pending_paths:
                    translator.logger.info(f"Encontrados {len(pending_paths)} archivos pendientes de subtítulos")
                    translator.logger.info(f"Primeros 3 paths: {pending_paths[:3]}")
                    translator.logger.info("Procesando archivos pendientes automáticamente...")

                    # Simular que se pasaron estos paths como argumentos
                    args.paths = pending_paths
                    translator.logger.info(f"args.paths establecido con {len(args.paths)} elementos")
                    return None
                else:
                    translator.logger.info("Archivo pending_subtitles.txt existe pero está vacío")
            except Exception as e:
                translator.logger.error(f"Error leyendo archivo de subtítulos pendientes: {e}")

        # Si no hay archivos pendientes, intentar reanudar proceso incompleto
        if _should_resume_incomplete_process(translator):
            translator.logger.info("No se especificaron rutas. Intentando reanudar proceso incompleto...")
            resume_result = _resume_incomplete_process(translator)
            if isinstance(resume_result, dict) and "error" in resume_result:
                print(f"Error reanudando proceso: {resume_result['error']}")
                return resume_result
            return resume_result

            print(
                "usage: mediajelly_subtitle_translator.py [-h] [--max-workers MAX_WORKERS] [--no-whisper] [--retranslate] paths [paths ...]"
            )
            print("mediajelly_subtitle_translator.py: error: the following arguments are required: paths")
            print("Nota: Si hay un proceso incompleto, puedes ejecutar sin argumentos para reanudarlo")
            return {"error": "No paths specified and no incomplete process to resume"}
    return None


def _prepare_input_args(args, translator) -> dict:
    """Prepara los argumentos de entrada para el procesamiento"""
    translator.logger.info(f"_prepare_input_args: args.paths tiene {len(args.paths)} elementos")
    translator.logger.info(f"_prepare_input_args: primeros 3 args.paths: {args.paths[:3]}")
    corrected_paths = _correct_container_paths(args.paths, translator)

    return {
        "original_paths": corrected_paths,
        "corrected_paths": corrected_paths,
        "max_workers": args.max_workers,
        "use_whisper": not args.no_whisper,
        "allow_retranslate": args.retranslate,
    }


def _execute_main_processing(args, translator, input_args: dict) -> dict:
    """Ejecuta el procesamiento principal según el tipo de entrada"""
    corrected_paths = input_args["corrected_paths"]
    first_arg = Path(corrected_paths[0])

    if first_arg.is_dir():
        translator.logger.info(f"=== Iniciando traducción de subtítulos en directorio {first_arg} ===")
        translator.logger.info(
            f"Modo: {'secuencial (Whisper)' if translator.use_whisper else f'concurrente ({args.max_workers} workers)'}"
        )
        return translator.process_directory(first_arg, input_args)
    else:
        file_paths = [Path(p) for p in corrected_paths]
        translator.logger.info(f"=== Iniciando traducción de subtítulos para {len(file_paths)} archivo(s) ===")
        translator.logger.info(
            f"Modo: {'secuencial (Whisper)' if translator.use_whisper else f'concurrente ({args.max_workers} workers)'}"
        )
        return translator.process_file_list(file_paths, input_args)


def _correct_container_paths(paths: list, translator) -> list:
    """Corrige las rutas para el entorno de contenedor Docker
    Normaliza rutas del host (/home/tafurc/mediaJelly/media) a rutas Docker (/mediajelly/media)
    """
    translator.logger.info(f"_correct_container_paths recibió {len(paths)} paths")
    translator.logger.info(f"Primeros 3 paths: {paths[:3]}")
    corrected_paths = []
    for path_str in paths:
        path_str = str(path_str)  # Asegurar que es string

        # Normalizar rutas del host a Docker
        # Casos a manejar:
        # 1. /home/tafurc/mediaJelly/media/ -> /mediajelly/media/
        # 2. /mediaJelly/media/ -> /mediajelly/media/
        # 3. /mediajelly/media/ -> /mediajelly/media/ (ya correcto)

        if "/home/tafurc/mediaJelly/media/" in path_str:
            # Ruta desde host, convertir a Docker
            corrected_path = path_str.replace("/home/tafurc/mediaJelly/media/", "/mediajelly/media/")
            translator.logger.debug(f"Ruta normalizada (host->docker): {path_str} -> {corrected_path}")
            corrected_paths.append(corrected_path)
        elif path_str.startswith("/mediaJelly"):
            # Caso de mayúscula incorrecta
            corrected_path = path_str.replace("/mediaJelly", "/mediajelly", 1)
            translator.logger.debug(f"Ruta normalizada (mayúscula): {path_str} -> {corrected_path}")
            corrected_paths.append(corrected_path)
        else:
            # Ya está en formato correcto o es ruta relativa
            corrected_paths.append(path_str)

    return corrected_paths


def _send_notifications_and_summary(env_config: dict, translator, stats: dict):
    """Envía notificaciones y muestra el resumen final"""
    # Actualizar archivo de progreso
    translator.update_progress_file(stats)

    # Mostrar resumen
    _display_summary(translator, stats)

    # Enviar notificación de traducción de subtítulos
    _send_subtitle_notification(env_config, translator, stats)


def _display_summary(translator, stats: dict):
    """Muestra el resumen de la traducción"""
    translator.logger.info("=== Resumen de traducción de subtítulos ===")
    translator.logger.info(f"Total archivos analizados: {stats['total_files']}")
    translator.logger.info(f"Con audio en español: {stats['with_spanish_audio']}")
    translator.logger.info(f"Con subtítulos en español: {stats['with_spanish_subs']}")
    translator.logger.info(f"Subtítulos extraídos: {stats['extracted']}")
    translator.logger.info(f"Subtítulos traducidos: {stats['translated']}")
    translator.logger.info(f"Errores: {stats['errors']}")

    # Imprimir para que el runner pueda capturar
    print(f"Subtítulos traducidos: {stats['translated']}")
    print(f"Errores: {stats['errors']}")


def _send_subtitle_notification(env_config: dict, translator, stats: dict):
    """Envía la notificación de traducción de subtítulos"""
    try:
        # Verificar si la notificación es duplicada
        if translator._is_notification_duplicate(stats):
            translator.logger.info("Notificación de subtítulos omitida - es idéntica a la anterior")
            return

        notifier_cmd = [
            sys.executable,
            str(env_config["scripts_dir"] / "mediajelly_notifier.py"),
            "subtitle_translation",
            str(stats["total_files"]),
            str(stats["extracted"]),
            str(stats["translated"]),
            str(stats["with_spanish_audio"]),
            str(stats["with_spanish_subs"]),
            str(stats["errors"]),
        ]

        result = subprocess.run(notifier_cmd, capture_output=True, text=True, timeout=60)
        if result.returncode == 0:
            translator.logger.info("Notificación de traducción de subtítulos enviada correctamente")
            translator._save_notification_signature(stats)
            _mark_notification_sent(translator)
        else:
            translator.logger.warning(f"Error enviando notificación de subtítulos: {result.stderr}")
    except Exception as e:
        translator.logger.warning(f"Error al enviar notificación de subtítulos: {e}")


def _mark_notification_sent(translator):
    """Marca la notificación como enviada en el archivo de progreso"""
    progress_file = translator.tmp_dir / "progress.json"
    try:
        if progress_file.exists():
            with open(progress_file, "r") as f:
                progress_data = json.load(f)
            if "subtitle_translation" in progress_data:
                progress_data["subtitle_translation"]["notified"] = True
                with open(progress_file, "w") as f:
                    json.dump(progress_data, f, indent=2)
    except Exception as e:
        translator.logger.warning(f"Error actualizando estado de notificación: {e}")


def _cleanup_lock_file(lock_file: Path):
    """Limpia el archivo de lock"""
    try:
        if lock_file.exists():
            lock_file.unlink()
            print(f"{EmojiGenerator.unlock()} Lock liberado")
    except Exception as e:
        print(f"{EmojiGenerator.warning()} Error al liberar lock: {e}")


# Función para limpiar lock al salir (para señales)
def _signal_handler(signum, lock_file: Path):
    """Manejador de señales para cleanup"""
    print(f"\n{EmojiGenerator.signal()} Recibida señal {signum}, limpiando...")
    _cleanup_lock_file(lock_file)
    sys.exit(0)


if __name__ == "__main__":
    # Test de la mejora de calidad (solo para desarrollo)
    if len(sys.argv) > 1 and sys.argv[1] == "--test-quality":
        # Crear instancia de prueba
        improver = SubtitleQualityImprover()

        # Texto de prueba con errores comunes
        test_text = """1
00:00:01,000 --> 00:00:05,000
this is a test.  i am speaking now.

2
00:00:05,000 --> 00:00:10,000
whats going on?  im not sure.

3
00:00:10,000 --> 00:00:15,000
thats right!  we are testing."""

        print("Texto original:")
        print(test_text)
        print("\nTexto mejorado:")
        improved = improver.improve_subtitle_quality(test_text, "en")
        print(improved)
        sys.exit(0)

    main()
