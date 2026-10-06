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
import shutil
import tempfile

from mediajelly_emoji import EmojiGenerator
from mediajelly_utils import MediaJellyPaths, create_compressed_rotating_file_handler
from mediajelly_whisper_extractor import WhisperAudioExtractorMixin, WHISPER_AVAILABLE
from mediajelly_db import get_pending_files, load_progress, save_progress

# Importar configuración centralizada
try:
    sys.path.append(str(Path(__file__).parent))
    from mediajelly_config import get_config
    CONFIG_AVAILABLE = True
except ImportError:
    MediaJellyConfig = None
    CONFIG_AVAILABLE = False
try:
    from mediajelly_config import get_config
except Exception:
    get_config = None

# Importaciones opcionales con manejo de errores
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

try:
    import webrtcvad  # type: ignore
    import soundfile as sf  # type: ignore

    WEBRTC_AVAILABLE = True
except Exception:
    webrtcvad = None
    sf = None
    WEBRTC_AVAILABLE = False

# Path to whisper.cpp binary if compiled in the image
WHISPER_CPP_BIN = Path("/usr/local/bin/whisper_cpp")
# Buscar por defecto el modelo en la carpeta montada persistente del proyecto
# Permite sobrescribir vía env WHISPER_CPP_MODEL
WHISPER_CPP_DEFAULT_MODEL = Path(os.environ.get("WHISPER_CPP_MODEL", "/mediajelly/media/models/ggml-small.bin"))

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

# Configuración de timeouts
FFPROBE_TIMEOUT = 120  # segundos
FFPROBE_QUICK_TIMEOUT = 30  # segundos para verificación rápida
EXCLUDED_FOLDERS = {".delete", ".deleted", ".tmp", ".temp", ".trash", ".recycle"}

# Un subtítulo existente/embebido solo se usa como fuente de traducción si cubre el
# video: su último cue llega al menos a esta fracción de la duración...
MIN_SUBTITLE_COVERAGE = 0.6
# ...y tiene al menos esta densidad de cues (descarta pistas de carteles/canciones)
MIN_CUES_PER_MINUTE = 2
MIN_SUBTITLE_CUES = 3

# Tope por petición al servicio de traducción: sin él, una petición sin respuesta
# bloqueaba el proceso toda la noche
TRANSLATE_REQUEST_TIMEOUT = 60
# Fallos o timeouts seguidos tras los que se abandona la traducción de un archivo
MAX_TRANSLATE_FAILURES = 5

SPANISH_LANG_CODES = {"spa", "es", "esp", "spanish"}
ENGLISH_LANG_CODES = {"eng", "en", "english"}
TEXT_SUBTITLE_CODECS = {"subrip", "srt", "ass", "ssa", "webvtt", "mov_text"}
# Títulos de pistas parciales (no contienen el diálogo completo)
PARTIAL_SUBTITLE_TITLE_RE = re.compile(r"sign|song|forced|forzad|commentar|karaoke", re.IGNORECASE)
SDH_SUBTITLE_TITLE_RE = re.compile(r"\bsdh\b|\bcc\b|hearing", re.IGNORECASE)
SRT_TIMESTAMP_RE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[,\.](\d{3})\s*-->\s*(\d{2}):(\d{2}):(\d{2})[,\.](\d{3})"
)


class SubtitleQualityImprover:
    """Mejora la calidad de los subtítulos extraídos y traducidos"""

    def __init__(self, logger=None):
        self.logger = logger
        # Compilar patrones regex frecuentes para mejorar rendimiento
        self.re_spaces = re.compile(r"\s+")
        self.re_space_before_punct = re.compile(r"\s+([,.!?;:])")
        self.re_multi_punct = re.compile(r"([,.!?;:])\s*([,.!?;:])")
        self.re_quote_open = re.compile(r'"\s+')
        self.re_quote_close = re.compile(r'\s+"')
        self.re_paren_open = re.compile(r"\(\s+")
        self.re_paren_close = re.compile(r"\s+\)")

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

            # Importar localmente para evitar llamadas a objetos None en análisis estático
            try:
                    from langdetect import detect as _langdetect_detect
            except Exception:
                return "en"

            detected = _langdetect_detect(clean_text)
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
        # No se tocan que/como/donde/cuando: llevar tilde o no depende de la frase, y
        # forzarla producía "lo qué haces" o "es cómo ser" en todos los subtítulos.
        corrections = {
            # Errores comunes de traducción automática
            r"\b(el|EL)\s+(is|IS)\b": "es",
            r"\b(la|LA)\s+(is|IS)\b": "es",
            r"\b(yes|YES)\b": "sí",
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
        text = self.re_spaces.sub(" ", text)

        # Corregir puntuación
        text = self.re_space_before_punct.sub(r"\1", text)  # Quitar espacios antes de puntuación
        text = self.re_multi_punct.sub(r"\1\2", text)  # Corregir puntuación duplicada

        # Corregir comillas y paréntesis
        text = self.re_quote_open.sub('"', text)  # Quitar espacios después de comillas de apertura
        text = self.re_quote_close.sub('"', text)  # Quitar espacios antes de comillas de cierre
        text = self.re_paren_open.sub("(", text)  # Quitar espacios después de paréntesis de apertura
        text = self.re_paren_close.sub(")", text)  # Quitar espacios antes de paréntesis de cierre

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


class TranslationStalledError(Exception):
    """El servicio de traducción dejó de responder; se abandona el archivo en curso."""


@dataclass
class TranslationStats:
    """Estadísticas de traducción de subtítulos"""
    subtitles_extracted: int = 0
    subtitles_translated: int = 0
    translation_errors: int = 0
    total_translation_time: float = 0.0
    files_processed: int = 0


class SubtitleTranslator(WhisperAudioExtractorMixin):
    """Gestor de extracción y traducción de subtítulos con soporte de Whisper"""

    PROGRESS_FILE_NAME = "progress.json"

    def __init__(self, max_workers: int = 2, use_whisper: bool = True, allow_retranslate: bool = False, lock_file: Optional[Path] = None, force_language: Optional[str] = None) -> None:
        # Configurar rutas desde configuración
        self.is_container = MediaJellyPaths.is_container()
        self.base_dir = MediaJellyPaths.get_base_path()
        self.scripts_dir = self.base_dir / "scripts"
        self.logs_dir = self.scripts_dir / "tmp" / "logs"
        self.tmp_dir = self.scripts_dir / "tmp"

        # Crear directorios
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)

        self.setup_logging()

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

        # Inicializar estadísticas de traducción
        self.translation_stats = TranslationStats()

        # Inicializar modelo Whisper (lazy loading)
        self.whisper_model = None

        self.max_workers = max_workers  # Procesamiento concurrente
        self.use_whisper = use_whisper
        self.allow_retranslate = allow_retranslate  # Permitir re-traducción de archivos .es.srt existentes
        # Forzar idioma de transcripción si se indica (ej: 'ja', 'en', 'es')
        self.force_language = force_language
        self.progress_lock = threading.Lock()  # Lock para sincronizar progreso y estadísticas
        self.file_locks: Dict[str, threading.Lock] = {}  # Lock por archivo para evitar carreras
        self.file_locks_guard = threading.Lock()  # Protege creación de locks por archivo
        self.lock_file = lock_file  # Archivo de lock para sincronización
        self._probe_cache: Dict[str, Dict[str, Any]] = {}  # Un ffprobe por archivo y ejecución
        # Estadísticas de la ejecución en curso, para poder avisar del avance si se pausa
        self.current_stats: Optional[Dict[str, Any]] = None

        # Verificar disponibilidad de dependencias
        self.whisper_available = WHISPER_AVAILABLE if self.use_whisper else False
        if self.use_whisper and not self.whisper_available:
            self.logger.warning("Whisper solicitado pero no disponible, se usarán solo subtítulos embebidos")
            self.use_whisper = False

        # Inicializar traductor Yandex (reutilizable) para batching
        self.yandex_translator = None
        if YANDEX_AVAILABLE:
            try:
                # Importar localmente para evitar warnings de análisis estático
                from translatepy.translators.yandex import YandexTranslate as _YandexTranslate

                self.yandex_translator = _YandexTranslate()
            except Exception:
                self.yandex_translator = None

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

        file_handler = create_compressed_rotating_file_handler(log_file)
        file_handler.setFormatter(formatter)

        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)

        self.logger = logging.getLogger("mediajelly")
        self.logger.setLevel(logging.INFO)
        self.logger.handlers.clear()
        self.logger.addHandler(file_handler)
        self.logger.addHandler(console_handler)
        self.logger.propagate = False

    def _is_file_in_excluded_folder(self, file_path: Path) -> bool:
        """
        Verifica si un archivo está en una carpeta excluida.

        Args:
            file_path: Ruta del archivo.

        Returns:
            bool: True si está en una carpeta excluida.
        """
        return any(part.lower() in EXCLUDED_FOLDERS for part in file_path.parts)

    def _probe_media(self, video_file: Path) -> Dict[str, Any]:
        """
        Ejecuta un único ffprobe por archivo (formato + streams) y cachea el resultado
        durante la ejecución.

        Args:
            video_file: Ruta del archivo de video.

        Returns:
            Dict[str, Any]: `returncode` (None si hubo timeout), `stderr` y `data` (JSON o None).
        """
        key = str(video_file)
        cached = self._probe_cache.get(key)
        if cached is not None:
            return cached

        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=index,codec_type,codec_name,start_time"
            ":stream_tags=language,title:stream_disposition=default,forced",
            "-of",
            "json",
            str(video_file),
        ]

        probe: Dict[str, Any] = {"returncode": None, "stderr": "", "data": None}
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=FFPROBE_TIMEOUT)
            probe["returncode"] = result.returncode
            probe["stderr"] = result.stderr or ""
            try:
                probe["data"] = json.loads(result.stdout)
            except (json.JSONDecodeError, TypeError):
                pass
        except subprocess.TimeoutExpired:
            self.logger.warning(f"Timeout analizando archivo con ffprobe ({FFPROBE_TIMEOUT}s): {video_file}")

        self._probe_cache[key] = probe
        return probe

    def _media_duration(self, video_file: Path) -> Optional[float]:
        """
        Obtiene la duración del video en segundos.

        Args:
            video_file: Ruta del archivo de video.

        Returns:
            Optional[float]: Duración en segundos o None si no se pudo determinar.
        """
        try:
            data = self._probe_media(video_file)["data"] or {}
            return float(data["format"]["duration"])
        except Exception:
            return None

    @staticmethod
    def _stream_language(stream: Dict[str, Any]) -> str:
        """Devuelve el idioma etiquetado de un stream en minúsculas ('und' si no tiene)."""
        tags = stream.get("tags", {})
        return (tags.get("language") or tags.get("LANGUAGE") or "und").lower()

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

            return self._probe_media(video_file)["returncode"] == 0

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
            data = self._probe_media(video_file)["data"]
            if not data:
                self.logger.warning(f"Error al analizar pistas de audio: {video_file}")
                return False, []

            languages = [
                self._stream_language(stream)
                for stream in data.get("streams", [])
                if stream.get("codec_type") == "audio"
            ]
            has_spanish = any(lang in SPANISH_LANG_CODES for lang in languages)

            return has_spanish, languages

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
            if not self._is_valid_srt_file(srt_file):
                continue

            # Debe cubrir el video; si no (pista parcial o extracción incompleta) se ignora
            if not self._subtitle_covers_video(srt_file, self._media_duration(video_file)):
                self.logger.info(f"Subtítulo existente insuficiente para la duración del video: {srt_file.name}")
                if srt_file.name == f"{video_stem}.srt":
                    # La extracción escribe en este mismo nombre: conservar copia del original
                    try:
                        srt_file.rename(srt_file.with_suffix(".srt.bak"))
                    except Exception as e:
                        self.logger.warning(f"No se pudo respaldar {srt_file.name}: {e}")
                continue

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

            # Muestra repartida por todo el archivo: las primeras líneas suelen ser
            # canciones o créditos y pueden estar en otro idioma que el diálogo
            step = max(1, len(text_lines) // 40)
            sample_text = " ".join(text_lines[::step][:40])

            try:
                from langdetect import detect as _langdetect_detect
                detected_lang = _langdetect_detect(sample_text)
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

    def translate_srt(self, srt_file: Path, force_translate: bool = False) -> Optional[Path]:
        """
        Traduce archivo SRT al español usando Yandex Translate con verificación doble.

        Args:
            srt_file: Ruta del archivo SRT.
            force_translate: Forzar traducción incluso si parece innecesaria.

        Returns:
            Optional[Path]: Ruta del archivo traducido o None.
        """
        # Si ya está en español (audio doblado, pista sin etiquetar) no hay nada que
        # traducir: pasarlo por el traductor solo gastaba minutos y peticiones.
        if not force_translate and self.detect_srt_language(srt_file) == "es":
            return self._save_spanish_copy(srt_file)

        if not self._translation_dependencies_available():
            return None

        try:
            import time

            translator = getattr(self, 'yandex_translator', None)
            if translator is None and YANDEX_AVAILABLE:
                try:
                    from translatepy.translators.yandex import YandexTranslate as _YandexTranslate

                    translator = _YandexTranslate()
                except Exception:
                    translator = None

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

        except TranslationStalledError as e:
            # Se conserva el .srt original; el video se reintenta en otra ejecución
            self.logger.error(f"Traducción abandonada para {srt_file.name}: {e}")
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
        translated_lines: List[str] = []
        translations_made = 0
        consecutive_failures = 0

        if translator is None:
            # No translator available: return original lines
            return [line if line.endswith("\n") else line + "\n" for line in lines], 0

        i = 0
        N = len(lines)
        while i < N:
            line = lines[i]
            if self._should_skip_translation(line.strip()):
                translated_lines.append(line)
                i += 1
                continue

            # Agrupar un bloque contiguo de líneas traducibles
            block_lines = []
            j = i
            char_count = 0
            while j < N and not self._should_skip_translation(lines[j].strip()) and char_count < 3000:
                block_lines.append(lines[j].strip())
                char_count += len(lines[j])
                j += 1

            # Traducir el bloque en batch
            joined = " ||| ".join(block_lines)
            block_translated = True
            try:
                translated_obj = self._translate_with_timeout(translator, joined)
                translated_text = translated_obj.result if hasattr(translated_obj, "result") else str(translated_obj)
                parts = translated_text.split(" ||| ")
                if len(parts) != len(block_lines):
                    # Fallback: split por nueva línea
                    parts = translated_text.split("\n")
                consecutive_failures = 0
            except Exception as e:
                self.logger.debug(f"Batch translation failed: {e}")
                consecutive_failures += 1
                if consecutive_failures >= MAX_TRANSLATE_FAILURES:
                    raise TranslationStalledError(
                        f"{consecutive_failures} fallos seguidos del servicio de traducción (último: {e})"
                    ) from e
                parts = [b + "\n" for b in block_lines]
                block_translated = False

            # Añadir resultados preservando saltos
            for p in parts:
                translated_lines.append(p if p.endswith("\n") else p + "\n")
                if block_translated and p.strip():
                    translations_made += 1

            i = j

        return translated_lines, translations_made

    def _translate_with_timeout(self, translator, text: str):
        """
        Traduce un texto al español con tope de tiempo.

        La librería de traducción no fija timeout en sus peticiones; se ejecuta en un
        hilo auxiliar para poder abandonarla si el servicio deja de responder.

        Args:
            translator: Instancia del traductor.
            text: Texto a traducir.

        Returns:
            Resultado devuelto por `translator.translate`.

        Raises:
            TimeoutError: Si no hubo respuesta en TRANSLATE_REQUEST_TIMEOUT segundos.
        """
        outcome: Dict[str, Any] = {}

        def work():
            try:
                outcome["result"] = translator.translate(text, "es")
            except Exception as e:
                outcome["error"] = e

        worker = threading.Thread(target=work, daemon=True)
        worker.start()
        worker.join(TRANSLATE_REQUEST_TIMEOUT)

        if worker.is_alive():
            raise TimeoutError(f"El servicio de traducción no respondió en {TRANSLATE_REQUEST_TIMEOUT}s")
        if "error" in outcome:
            raise outcome["error"]
        return outcome["result"]

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
        lang = None
        if LANGDETECT_AVAILABLE:
            try:
                from langdetect import detect as _langdetect_detect
                lang = _langdetect_detect(text_to_detect)
            except Exception:
                lang = None

        if lang:
            if self._should_translate_language(lang, force_translate):
                return self._perform_translation(text_to_translate, translator)
            else:
                return text_to_translate + "\n", 0

        # Si no se detectó idioma, o no está disponible la librería, intentar traducir
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

            translated = self._translate_with_timeout(translator, text_to_translate)
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
        # Conservar el archivo original; no eliminarlo para mantener ambas versiones (.srt y .es.srt)
        # Anteriormente se borraba aquí, pero ahora preferimos mantener ambos archivos.
        # self._cleanup_original_file(srt_file)

        return output_file

    def _save_spanish_copy(self, srt_file: Path) -> Optional[Path]:
        """
        Publica como .es.srt un subtítulo que ya está en español, sin traducirlo.

        Args:
            srt_file: Ruta del archivo SRT en español.

        Returns:
            Optional[Path]: Ruta del .es.srt o None si no se pudo escribir.
        """
        output_file = srt_file.parent / f"{self._prepare_output_filename(srt_file)}{SPANISH_SUBTITLE_SUFFIX}"
        try:
            if output_file != srt_file:
                shutil.copyfile(srt_file, output_file)
        except OSError as e:
            self.logger.error(f"No se pudo guardar {output_file.name}: {e}")
            return None

        self.logger.info(f"Subtítulo ya en español, se guarda sin traducir: {output_file.name}")
        self._improve_translated_subtitles_quality(output_file)
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

            # Mantener comportamiento no destructivo: no eliminar el archivo original por defecto.
            self.logger.debug(f"_cleanup_original_file: conservar {srt_file.name} (no se elimina)")

    def process_video(self, video_file: Path, force_regenerate: bool = False) -> dict:
        """
        Procesa un único archivo de video: extrae o traduce subtítulos, y devuelve un resumen.

        Args:
            video_file: Ruta del archivo de video.
            force_regenerate: Si True, ignora los subtítulos existentes y vuelve a
                transcribir el audio con Whisper (reemplaza .srt y .es.srt).

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
            if not force_regenerate and not self.quick_check_needs_processing(video_file):
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

            if force_regenerate:
                # Rehacer desde el audio: los archivos anteriores se conservan hasta ser reemplazados
                srt_file = self._try_extract_audio_subtitles(video_file)
            else:
                # 2. Verificar si ya tiene subtítulos en español (sidecar o pista embebida)
                has_spanish_subs = self.check_existing_spanish_subtitles(
                    video_file
                ) or self._has_embedded_spanish_subtitles(video_file)
                if has_spanish_subs and not self.allow_retranslate:
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
        Detecta si un archivo de video está corrupto a partir del análisis de ffprobe.

        Args:
            video_file: Ruta del archivo de video.

        Returns:
            bool: True si el video está corrupto, False si está OK.
        """
        try:
            probe = self._probe_media(video_file)

            if probe["returncode"] is None:
                self.logger.warning(f"{EmojiGenerator.warning_msg()} Timeout verificando {video_file.name}")
                return True

            # Si ffprobe falla, el archivo está corrupto
            if probe["returncode"] != 0:
                stderr = probe["stderr"].lower()

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

            data = probe["data"]
            if data is None:
                self.logger.warning(
                    f"{EmojiGenerator.warning_msg()} No se pudo parsear salida de ffprobe para {video_file.name}"
                )
                return True

            # Debe tener al menos un stream
            if not data.get("streams"):
                self.logger.warning(f"{EmojiGenerator.warning_msg()} No se detectaron streams en {video_file.name}")
                return True

            # Verificar que tenga duración válida
            if "format" in data:
                duration = data["format"].get("duration")
                if not duration or float(duration) < 1.0:
                    self.logger.warning(f"{EmojiGenerator.warning_msg()} Duración inválida en {video_file.name}")
                    return True

            # Si llegó aquí, el video parece estar OK
            return False

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

    def _pending_compression_paths(self) -> set:
        """
        Devuelve las rutas de los videos que aún esperan compresión.

        El procesador corre en paralelo y renombra el video al comprimirlo, así que
        esos archivos se dejan para un ciclo posterior (ya con su nombre definitivo).

        Returns:
            set: Rutas (str) pendientes de compresión; vacío si no se pudo consultar.
        """
        try:
            return {str(path) for path in get_pending_files()}
        except Exception as e:
            self.logger.warning(f"No se pudo consultar la cola de compresión: {e}")
            return set()

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

        pending_compression = self._pending_compression_paths()

        valid_files = []
        seen_files = set()
        missing_files = []

        for video_file in file_paths:
            normalized_path = str(video_file)

            if normalized_path in pending_compression:
                self.logger.info(f"Pendiente de compresión, se deja para un ciclo posterior: {video_file.name}")
                continue

            # Evitar procesamiento duplicado del mismo archivo en el mismo lote
            if normalized_path in seen_files:
                self.logger.debug(f"Archivo duplicado en entrada, se omite repetido: {video_file}")
                continue
            seen_files.add(normalized_path)

            if not video_file.exists():
                self.logger.warning(f"Archivo no encontrado: {video_file}")
                missing_files.append(str(video_file))
                stats["errors"] += 1
            else:
                # Verificar si el archivo ya fue procesado y tiene subtítulos
                file_path_str = normalized_path
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
        try:
            progress_data = load_progress() or {}

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
            save_progress(progress_data)

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
        host_base_prefix = str(MediaJellyPaths.get_base_path()) + "/"
        for file_path in file_paths:
            # Si la ruta ya está en formato /mediajelly, mantenerla
            if file_path.startswith(MEDIAJELLY_PATH):
                normalized_missing_files.append(file_path)
            # Si está en formato absoluto del host, convertirla
            elif file_path.startswith(host_base_prefix):
                # Convertir <base>/... a /mediajelly/...
                normalized_path = file_path.replace(host_base_prefix, MEDIAJELLY_PATH + "/", 1)
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
        self.current_stats = stats
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
            futures = {executor.submit(self._process_video_with_file_lock, vf): vf for vf in valid_files}

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

    def _process_video_with_file_lock(self, video_file: Path) -> dict:
        """
        Procesa un video serializando por path para evitar condiciones de carrera
        cuando el mismo archivo aparece más de una vez.

        Args:
            video_file: Archivo de video a procesar.

        Returns:
            dict: Resultado de `process_video`.
        """
        file_key = str(video_file)
        with self.file_locks_guard:
            file_lock = self.file_locks.get(file_key)
            if file_lock is None:
                file_lock = threading.Lock()
                self.file_locks[file_key] = file_lock

        with file_lock:
            return self.process_video(video_file)

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
            progress_data = load_progress() or {}

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
            with self.progress_lock:
                progress_data = load_progress() or {}

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

                save_progress(progress_data)

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
            # 0. Carpeta marcada para no generar subtítulos (p. ej. ya vienen incrustados)
            if MediaJellyPaths.has_nosubs_marker(video_file):
                self.logger.debug(f"Carpeta marcada con .nosubs, omitiendo: {video_file.name}")
                return False

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
        try:
            progress_data = load_progress() or {}

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
        try:
            progress_data = load_progress() or {}

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
        try:
            progress_data = load_progress() or {}
            if "subtitle_translation" in progress_data:
                progress_data["subtitle_translation"]["last_input_args"] = None
                save_progress(progress_data)

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
        with self.progress_lock:
            try:
                # Leer o inicializar progreso
                progress_data = self._load_or_initialize_progress_data()

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
                save_progress(progress_data)

                self.logger.info("Archivo de progreso actualizado con estadísticas de subtítulos")

            except Exception as e:
                self.logger.error(f"Error actualizando archivo de progreso: {e}")

    def _load_or_initialize_progress_data(self) -> dict:
        """
        Lee progreso existente o inicializa estructura nueva.

        Args:
        Returns:
            dict: Datos de progreso.
        """
        data = load_progress() or {}
        return data if data else self._create_initial_progress_structure()

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
        Intenta extraer la mejor pista de subtítulos embebida del video.

        Las pistas se ordenan por metadatos (ver `_rank_embedded_subtitle_streams`) y
        se extrae solo la primera que cubra el video.

        Args:
            video_file: Ruta del archivo de video.

        Returns:
            Optional[Path]: Ruta del archivo SRT extraído o None.
        """
        ranked = self._rank_embedded_subtitle_streams(self._detect_embedded_subtitle_streams(video_file))
        if not ranked:
            return None

        duration = self._media_duration(video_file)

        for relative_index, stream in ranked:
            lang = self._stream_language(stream)
            extracted = self._extract_embedded_subtitles(video_file, relative_index, None if lang == "und" else lang)
            if not extracted:
                continue

            if not self._subtitle_covers_video(extracted, duration):
                self.logger.info(
                    f"Pista embebida 0:s:{relative_index} ({lang}) insuficiente para la duración del video, descartada"
                )
                try:
                    extracted.unlink()
                except Exception:
                    pass
                continue

            # Mover a nombre base .srt de forma atómica (overwrite)
            base_srt = video_file.parent / f"{video_file.stem}.srt"
            try:
                base_srt_tmp = base_srt.with_suffix(base_srt.suffix + ".tmp")
                if base_srt_tmp.exists():
                    base_srt_tmp.unlink()
                shutil.move(str(extracted), str(base_srt_tmp))
                base_srt_tmp.replace(base_srt)
            except Exception as e:
                self.logger.warning(f"No se pudo mover subtítulo embebido a destino: {e}")
                # En caso de fallo, devolver el path original
                return extracted

            self.logger.info(f"Seleccionado subtítulo embebido: {base_srt.name} (stream 0:s:{relative_index}, {lang})")
            return base_srt

        self.logger.debug("Ninguna pista embebida útil, se intentará Whisper")
        return None

    def _detect_embedded_subtitle_streams(self, video_file: Path) -> list:
        """
        Detecta streams de subtítulos embebidos en el video.

        Args:
            video_file: Ruta del archivo de video.

        Returns:
            list: Lista de streams de subtítulos encontrados, en el orden del contenedor.
        """
        try:
            data = self._probe_media(video_file)["data"] or {}
            return [stream for stream in data.get("streams", []) if stream.get("codec_type") == "subtitle"]
        except Exception as e:
            self.logger.warning(f"Error detectando subtítulos embebidos: {e}")
            return []

    @staticmethod
    def _stream_title(stream: Dict[str, Any]) -> str:
        """Devuelve el título de un stream ('' si no tiene)."""
        tags = stream.get("tags", {})
        return tags.get("title") or tags.get("TITLE") or ""

    def _is_partial_subtitle_stream(self, stream: Dict[str, Any]) -> bool:
        """
        Indica si una pista de subtítulos no contiene el diálogo completo
        (forzada, carteles/canciones, comentarios).

        Args:
            stream: Stream de subtítulos reportado por ffprobe.

        Returns:
            bool: True si es una pista parcial.
        """
        if stream.get("disposition", {}).get("forced"):
            return True
        return bool(PARTIAL_SUBTITLE_TITLE_RE.search(self._stream_title(stream)))

    def _has_embedded_spanish_subtitles(self, video_file: Path) -> bool:
        """
        Verifica si el video ya trae una pista de subtítulos completa en español.

        Args:
            video_file: Ruta del archivo de video.

        Returns:
            bool: True si existe una pista en español que no sea parcial.
        """
        return any(
            self._stream_language(stream) in SPANISH_LANG_CODES and not self._is_partial_subtitle_stream(stream)
            for stream in self._detect_embedded_subtitle_streams(video_file)
        )

    def _rank_embedded_subtitle_streams(self, streams: list) -> List[Tuple[int, Dict[str, Any]]]:
        """
        Ordena las pistas embebidas utilizables como fuente de traducción.

        Solo se consideran pistas de texto completas y no españolas. Prioridad:
        inglés, luego otros idiomas etiquetados, luego sin etiqueta; a igualdad, las
        que no son para sordos (SDH) y el orden del contenedor.

        Args:
            streams: Streams de subtítulos en el orden del contenedor.

        Returns:
            List[Tuple[int, Dict[str, Any]]]: (índice relativo 0:s:N, stream) de mejor a peor.
        """
        candidates = []
        for relative_index, stream in enumerate(streams):
            if (stream.get("codec_name") or "").lower() not in TEXT_SUBTITLE_CODECS:
                continue
            if self._is_partial_subtitle_stream(stream):
                continue

            lang = self._stream_language(stream)
            if lang in SPANISH_LANG_CODES:
                continue

            if lang in ENGLISH_LANG_CODES:
                lang_rank = 0
            elif lang != "und":
                lang_rank = 1
            else:
                lang_rank = 2
            is_sdh = 1 if SDH_SUBTITLE_TITLE_RE.search(self._stream_title(stream)) else 0

            candidates.append(((lang_rank, is_sdh, relative_index), relative_index, stream))

        candidates.sort(key=lambda candidate: candidate[0])
        return [(relative_index, stream) for _, relative_index, stream in candidates]

    def _subtitle_covers_video(self, srt_file: Path, duration: Optional[float]) -> bool:
        """
        Determina si un SRT es lo bastante completo para usarse como subtítulo del video:
        su último cue alcanza MIN_SUBTITLE_COVERAGE de la duración y tiene al menos
        MIN_CUES_PER_MINUTE cues por minuto.

        Args:
            srt_file: Ruta del archivo SRT.
            duration: Duración del video en segundos (None si se desconoce).

        Returns:
            bool: True si el subtítulo cubre el video.
        """
        try:
            with open(srt_file, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
        except Exception:
            return False

        cues = 0
        last_end = 0.0
        for match in SRT_TIMESTAMP_RE.finditer(content):
            cues += 1
            hours, minutes, seconds, millis = (int(value) for value in match.groups()[4:])
            last_end = max(last_end, hours * 3600 + minutes * 60 + seconds + millis / 1000.0)

        if cues < MIN_SUBTITLE_CUES:
            return False

        # Sin duración conocida solo se puede exigir el mínimo de cues
        if not duration or duration <= 0:
            return True

        return last_end >= duration * MIN_SUBTITLE_COVERAGE and cues >= (duration / 60.0) * MIN_CUES_PER_MINUTE

    def _extract_embedded_subtitles(
        self, video_file: Path, relative_index: int, detected_lang: Optional[str]
    ) -> Optional[Path]:
        """
        Extrae una pista de subtítulos embebida del video.

        Args:
            video_file: Ruta del archivo de video.
            relative_index: Índice de la pista entre los streams de subtítulos (0:s:N).
            detected_lang: Idioma etiquetado de la pista.

        Returns:
            Optional[Path]: Ruta del archivo SRT extraído o None.
        """
        # Guardar candidatos en tmp_dir para evitar condiciones de carrera
        try:
            self.tmp_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        output_srt = self.tmp_dir / f"{video_file.stem}.stream{relative_index}.srt"

        cmd = [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(video_file),
            "-map",
            f"0:s:{relative_index}",
            "-c:s",
            "srt",
            str(output_srt),
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)

        if result.returncode == 0 and output_srt.exists():
            if detected_lang:
                self.logger.info(
                    f"{EmojiGenerator.check_mark()} Subtítulos embebidos en '{detected_lang}' extraídos: {output_srt}"
                )
            else:
                self.logger.info(f"{EmojiGenerator.check_mark()} Subtítulos embebidos extraídos: {output_srt}")

            # Aplicar mejora de calidad a los subtítulos extraídos
            quality_lang = "en" if detected_lang in ENGLISH_LANG_CODES else detected_lang
            self._improve_extracted_subtitles_quality(output_srt, quality_lang)

            return output_srt

        # Log stderr for diagnostics if extraction failed
        if result.returncode != 0:
            self.logger.debug(f"ffmpeg stderr (sub extract): {result.stderr[:1000]}")
        return None

    def _improve_extracted_subtitles_quality(self, srt_file: Path, language: Optional[str]):
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
            improved_content = self.quality_improver.improve_subtitle_quality(original_content, language or "und")

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
    # El traductor se crea después; el manejador lo lee de aquí para avisar del avance
    run_state: Dict[str, Any] = {"translator": None, "env_config": env_config}
    signal.signal(
        signal.SIGTERM, lambda signum, frame: _signal_handler(signum, env_config["subtitle_lock_file"], run_state)
    )
    signal.signal(
        signal.SIGINT, lambda signum, frame: _signal_handler(signum, env_config["subtitle_lock_file"], run_state)
    )

    try:
        # Parsear argumentos y configurar traductor
        args, translator = _parse_arguments_and_setup_translator(env_config)
        run_state["translator"] = translator

        # Regeneración con Whisper: bucle aparte, no usa el sistema de progreso/reanudación
        if args.regenerate or args.redo_queue:
            _run_regeneration(args, translator, env_config)
            return

        # Procesar archivos
        stats = _execute_processing(args, translator)

        # Verificar si hubo error
        if isinstance(stats, dict) and "error" in stats:
            print(f"Error: {stats['error']}")
            return

        # Enviar notificaciones y mostrar resumen
        _send_notifications_and_summary(env_config, translator, stats)

    finally:
        # Limpiar lock siempre
        _cleanup_lock_file(env_config["subtitle_lock_file"])


def _run_regeneration(args, translator, env_config: dict):
    """
    Regenera con Whisper los subtítulos de los videos indicados o de la cola de rehacer.

    Con --redo-queue, cada entrada se quita de la cola al terminar (las fallidas pasan
    a whisper_redo_failed.txt), de modo que una interrupción solo pierde el video en curso.
    """
    logger = translator.logger
    queue_file = env_config["tmp_dir"] / "whisper_redo_queue.txt" if args.redo_queue else None

    if not translator.whisper_available:
        logger.error("Whisper no disponible: no se pueden regenerar subtítulos")
        return

    if queue_file is not None:
        if not queue_file.exists():
            logger.info("No hay cola de subtítulos por rehacer")
            return
        raw_paths = [line.strip() for line in queue_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    else:
        raw_paths = list(args.paths)

    video_files: List[Path] = []
    for path_str in _correct_container_paths(raw_paths, translator):
        path = Path(path_str)
        video_files.extend(translator._find_video_files_in_directory(path) if path.is_dir() else [path])

    # Los videos bajo una carpeta marcada con .nosubs salen de la cola sin rehacerse
    video_files = [video_file for video_file in video_files if not MediaJellyPaths.has_nosubs_marker(video_file)]

    # Los que aún esperan compresión se quedan en la cola para otra noche
    remaining = [str(video_file) for video_file in video_files]
    pending_compression = translator._pending_compression_paths()
    video_files = [video_file for video_file in video_files if str(video_file) not in pending_compression]

    logger.info(f"=== Regenerando subtítulos con Whisper para {len(video_files)} archivo(s) ===")
    regenerated = 0
    failed: List[str] = []
    # Mismo formato que las estadísticas normales, para el aviso de pausa
    translator.current_stats = {
        "total_files": len(video_files),
        "processed": 0,
        "with_spanish_audio": 0,
        "with_spanish_subs": 0,
        "extracted": 0,
        "translated": 0,
        "errors": 0,
    }

    for video_file in video_files:
        if not _check_lock_file_exists(env_config["subtitle_lock_file"]):
            logger.warning("Lock file eliminado externamente. Cancelando regeneración...")
            break

        if not video_file.is_file():
            logger.warning(f"Archivo no encontrado, se descarta de la cola: {video_file}")
        else:
            result = translator.process_video(video_file, force_regenerate=True)
            if result["error"]:
                failed.append(str(video_file))
                translator.current_stats["errors"] += 1
                logger.error(f"No se pudo regenerar {video_file.name}: {result['error']}")
            else:
                regenerated += 1
                translator.current_stats["extracted"] += 1
                translator.current_stats["translated"] += 1
        translator.current_stats["processed"] += 1

        remaining.remove(str(video_file))
        if queue_file is not None:
            queue_tmp = queue_file.with_suffix(".tmp")
            queue_tmp.write_text("".join(f"{path}\n" for path in remaining), encoding="utf-8")
            queue_tmp.replace(queue_file)

    if failed and queue_file is not None:
        with open(queue_file.with_name("whisper_redo_failed.txt"), "a", encoding="utf-8") as f:
            f.writelines(f"{path}\n" for path in failed)

    logger.info(
        f"Regeneración finalizada: {regenerated} rehechos, {len(failed)} con error, {len(remaining)} pendientes"
    )


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
    is_container = MediaJellyPaths.is_container()
    base_dir = MediaJellyPaths.get_base_path()
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
    parser.add_argument(
        "--regenerate",
        action="store_true",
        help="Volver a transcribir el audio con Whisper aunque ya existan subtítulos (reemplaza .srt y .es.srt)",
    )
    parser.add_argument(
        "--redo-queue",
        dest="redo_queue",
        action="store_true",
        help="Regenerar los videos listados en tmp/whisper_redo_queue.txt (ver requeue_whisper_subtitles.py)",
    )
    parser.add_argument(
        "--force-language",
        dest="force_language",
        type=str,
        default=None,
        help="Forzar idioma de transcripción (ej: 'ja', 'en', 'es'). Si no se indica, se detecta automáticamente.",
    )

    args = parser.parse_args()

    # Ajustar max_workers automáticamente cuando se usa Whisper
    if not args.no_whisper:
        args.max_workers = 2  # Whisper solo procesa 2 por 2
        print(f"{EmojiGenerator.magnifying_glass()} Whisper habilitado: Procesamiento secuencial (1 archivo por vez)")

    # Crear traductor con parámetros
    translator = SubtitleTranslator(
        max_workers=args.max_workers,
        use_whisper=not args.no_whisper,
        allow_retranslate=args.retranslate,
        lock_file=env_config["subtitle_lock_file"],
        force_language=args.force_language,
    )

    return args, translator


def _reset_subtitle_progress(translator, input_args: dict):
    """Resetea las estadísticas y archivos procesados cuando las rutas cambian"""
    try:
        # Leer estado actual desde DB
        progress_data = load_progress() or {}

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

        # Guardar estado actualizado en DB
        save_progress(progress_data)

        translator.logger.info("Progreso de subtítulos reseteado debido a cambio de rutas")
        translator.logger.info(f"Nueva ruta: {input_args.get('corrected_paths', [''])[0]}")

    except Exception as e:
        translator.logger.error(f"Error reseteando progreso de subtítulos: {e}")


def _resume_incomplete_process(translator) -> dict:
    """Reanuda un proceso incompleto de traducción de subtítulos"""
    try:
        progress_data = load_progress() or {}

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
    try:
        progress_data = load_progress() or {}
        if not progress_data:
            return False

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
    Normaliza rutas del host (<base>/media) a rutas Docker (/mediajelly/media)
    """
    translator.logger.info(f"_correct_container_paths recibió {len(paths)} paths")
    translator.logger.info(f"Primeros 3 paths: {paths[:3]}")
    corrected_paths = []
    for path_str in paths:
        path_str = str(path_str)  # Asegurar que es string

        host_media_prefix = str(MediaJellyPaths.get_media_dir()) + "/"

        # Normalizar rutas del host a Docker
        if host_media_prefix in path_str:
            # Ruta desde host, convertir a Docker
            corrected_path = path_str.replace(host_media_prefix, "/mediajelly/media/")
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
    """Marca la notificación como enviada en el progreso persistido (DB)."""
    try:
        progress_data = load_progress() or {}
        if "subtitle_translation" in progress_data:
            progress_data["subtitle_translation"]["notified"] = True
            save_progress(progress_data)
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
def _send_pause_notification(translator, env_config: dict):
    """Avisa por Telegram del avance alcanzado cuando el proceso se interrumpe antes de terminar."""
    stats = getattr(translator, "current_stats", None)
    if not stats or not stats.get("total_files"):
        return

    try:
        notifier_cmd = [
            sys.executable,
            str(env_config["scripts_dir"] / "mediajelly_notifier.py"),
            "subtitle_paused",
            str(stats["total_files"]),
            str(stats.get("processed", 0)),
            str(stats["extracted"]),
            str(stats["translated"]),
            str(stats["with_spanish_audio"]),
            str(stats["with_spanish_subs"]),
            str(stats["errors"]),
        ]
        # Debe caber en la gracia que da el cron runner antes de SIGKILL
        result = subprocess.run(notifier_cmd, capture_output=True, text=True, timeout=20)
        if result.returncode == 0:
            translator.logger.info("Notificación de pausa de subtítulos enviada")
        else:
            translator.logger.warning(f"Error enviando notificación de pausa: {result.stderr}")
    except Exception as e:
        translator.logger.warning(f"Error al enviar notificación de pausa: {e}")


# Función para limpiar lock al salir (para señales)
def _signal_handler(signum, lock_file: Path, run_state: Optional[Dict[str, Any]] = None):
    """Manejador de señales: avisa del avance, limpia el lock y termina"""
    print(f"\n{EmojiGenerator.signal()} Recibida señal {signum}, limpiando...")
    translator = (run_state or {}).get("translator")
    if translator is not None:
        _send_pause_notification(translator, run_state["env_config"])
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
