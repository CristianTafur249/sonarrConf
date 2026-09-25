#!/usr/bin/env python3
"""
MediaJelly Telegram Bot - con cola de archivos pendientes, verificación de
integridad por tamaño y recuperación automática tras reinicios.
Bot interactivo (long-polling) con soporte de descarga de archivos grandes mediante Telethon.

Gestiona la descarga y organización interactiva de archivos.
Almacena archivos pendientes con su título pre-parseado para ser clasificados mediante /organize.
Navegación limpia mediante edición en vivo del mensaje de interfaz (sin mensajes residuales).
Este bot permite recibir archivos de video (documentos o videos) y clasificarlos
interactively, moviéndolos a las carpetas correspondientes del sistema de medios
( /media/Peliculas/ o /media/series/NombreSerie/ ), normalizando su nombre
(eliminación de prefijos fansub, formato SxxExx, limpieza de espacios) y
agregándolos a la cola de pendientes para que el compresor y el traductor de
subtítulos los procesen en la siguiente ejecución del cron.

Soporta cola de descargas asíncrona (FIFO), detección automática de título predeterminado
vía nombre de archivo/caption y cancelación o edición granular por archivo.

A diferencia de mediajelly_notifier.py (que sólo envía mensajes salientes),
este script escucha mensajes entrantes de Telegram mediante getUpdates y
responde con el estado real del sistema, leyendo la misma base de datos
SQLite (mediajelly_db.py) y los mismos archivos de progreso que usa el
pipeline de compresión/subtítulos.

Además, al usar Telethon (MTProto), el bot puede descargar archivos de cualquier
tamaño, superando el límite de 20 MB de la Bot API. Para ello se autentica
como bot utilizando el token existente y las credenciales API_ID/API_HASH
obtenidas en my.telegram.org.

Comandos soportados:
    /start, /ayuda     -> Lista completa de comandos y ayuda
    /status            -> Resumen global del sistema (servidor, colas y compresión)
    /organize          -> Asistente interactivo para clasificar el video subido
    /recibidos         -> Lista de archivos descargados listos para organizar (/organize)
    /pendientes        -> Archivos en cola de compresión (Base de Datos)
    /completados       -> Total y lista de archivos comprimidos con éxito
    /errores           -> Últimos fallos del proceso de compresión
    /subtitulos        -> Estado del pipeline de traducción de subtítulos
    /disco             -> Espacio disponible en las unidades de almacenamiento
    /cancel            -> Cancela la sesión interactiva actual
"""

from __future__ import annotations

import asyncio
import difflib
import json
import logging
import os
import re
import shutil
import signal
import sqlite3
import time
import traceback
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Deque, Dict, List, Optional

from telethon import TelegramClient, events, Button
from telethon.errors import QueryIdInvalidError

from mediajelly_emoji import EmojiGenerator
from mediajelly_utils import MediaJellyPaths, create_compressed_rotating_file_handler
from mediajelly_db import (
    add_pending,
    get_all_completed_files,
    get_pending_files,
    load_progress,
    save_progress,
)
from mediajelly_filename_normalizer import FilenameNormalizerMixin
from mediajelly_notifier import TelegramNotifier

REQUEST_TIMEOUT = 35
MAX_LIST_ITEMS = 15
MAX_ERROR_ITEMS = 8
TELEGRAM_MESSAGE_LIMIT = 3500

UPLOAD_DIR = Path(__file__).parent / "tmp" / "telegram_uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Cantidad de msg_id ya descargados que se recuerdan para no descargar dos veces
# el mismo archivo (p. ej. cola persistida + catch-up de Telegram al reiniciar).
MAX_PROCESSED_MSGS = 500

# Backoff del bucle de reinicio en main() cuando se pierde la conexión.
RESTART_DELAY_MIN = 10
RESTART_DELAY_MAX = 300

logging.getLogger("telethon").setLevel(logging.WARNING)


def load_config():
    api_id = os.getenv("API_ID")
    api_hash = os.getenv("API_HASH")
    if api_id and api_hash:
        return int(api_id), api_hash

    try:
        from mediajelly_config import get_config
        config = get_config()
        if config and config.telegram.api_id and config.telegram.api_hash:
            return config.telegram.api_id, config.telegram.api_hash
    except Exception:
        pass

    config_paths = [
        Path(__file__).parent / "telegram.conf",
        Path(__file__).parent.parent / "config" / "telegram.conf",
    ]

    for config_path in config_paths:
        if config_path.exists():
            with open(config_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if "=" in line:
                        key, value = line.split("=", 1)
                        if key.strip() == "API_ID":
                            api_id = int(value.strip().strip('"').strip("'"))
                        elif key.strip() == "API_HASH":
                            api_hash = value.strip().strip('"').strip("'")
            if api_id and api_hash:
                return api_id, api_hash

    raise ValueError("API_ID y API_HASH no encontrados.")


API_ID, API_HASH = load_config()


@dataclass
class PendingItem:
    """Representa un archivo listo para ser organizado junto con su título parseado."""
    path: Path
    parsed_title: str


@dataclass
class DownloadTask:
    """Representa una tarea de descarga en la cola FIFO."""
    chat_id: int
    msg_id: int
    default_name: str
    caption: str = ""


@dataclass
class UserSession:
    """Estado de la sesión interactiva por usuario."""
    file_path: Optional[Path] = None
    media_type: Optional[str] = None          # movie, series, anime_movie, anime_series
    base_type: Optional[str] = None           # movie o series (para el paso intermedio)
    raw_title: Optional[str] = None
    selected_folder: Optional[str] = None
    pending_matches: List[str] = field(default_factory=list)
    season: Optional[int] = None
    episode: Optional[int] = None
    step: str = "idle"
    msg_id: Optional[int] = None
    created_at: float = field(default_factory=time.time)


class MediaJellyTelegramBot(FilenameNormalizerMixin):

    def __init__(self) -> None:
        self.notifier = TelegramNotifier()
        self.logger = self._setup_logging()

        self.bot_token = self.notifier.config["TELEGRAM_BOT_TOKEN"]
        self.allowed_chat_id = int(self.notifier.config["TELEGRAM_CHAT_ID"])

        self.scripts_dir = self.notifier.scripts_dir
        self.tmp_dir = self.scripts_dir / "tmp"
        self.tmp_dir.mkdir(parents=True, exist_ok=True)

        self.base_dir = MediaJellyPaths.get_base_path()
        self.media_dir = self.base_dir / "media"
        self.db_path = self.base_dir / "db" / "mediajelly.db"

        self.pending_file_db = self.tmp_dir / "pending_telegram_uploads.json"
        self.queue_file_db = self.tmp_dir / "download_queue_persist.json"
        self.processed_msgs_db = self.tmp_dir / "processed_telegram_msgs.json"
        self.processed_msgs: Deque[str] = self._load_processed_msgs()

        self.pending_uploads: Dict[str, Deque[PendingItem]] = {}
        self.processing_chats: set[str] = set()
        self.pending_lock = asyncio.Lock()
        self._cleanup_stale_partial_downloads()
        self._load_pending_uploads()

        self.download_queue: asyncio.Queue[DownloadTask] = asyncio.Queue()
        self.download_queue_list: List[DownloadTask] = []
        self.current_task: Optional[DownloadTask] = None
        self._recovered_already = False

        self.sessions: Dict[str, UserSession] = {}
        self._background_task: Optional[asyncio.Task] = None

        # El cliente se conecta en run_forever(), después de registrar los handlers,
        # para que las actualizaciones recuperadas por catch_up (mensajes enviados
        # mientras el bot estaba apagado, Telegram las guarda ~24 h) no se pierdan.
        # connection_retries=None: reintenta indefinidamente en vez de morir tras 5.
        self.client = TelegramClient(
            'mediajelly_bot', API_ID, API_HASH,
            catch_up=True,
            connection_retries=None,
            retry_delay=5,
            auto_reconnect=True,
            request_retries=10,
        )

    def _setup_logging(self) -> logging.Logger:
        logger = logging.getLogger("mediajelly_telegram_bot")
        logger.setLevel(logging.INFO)
        if not logger.handlers:
            log_dir = MediaJellyPaths.get_base_path() / "scripts" / "tmp" / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            handler = create_compressed_rotating_file_handler(
                log_dir / "telegram_bot.log", max_bytes=10 * 1024 * 1024, backup_count=5
            )
            handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
            logger.addHandler(handler)
            stream_handler = logging.StreamHandler()
            stream_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
            logger.addHandler(stream_handler)
        return logger

    # ------------------------------------------------------------------
    # Persistencia y Cola de Archivos
    # ------------------------------------------------------------------

    def _cleanup_stale_partial_downloads(self) -> None:
        """
        Elimina archivos temporales de descargas incompletas (`.<nombre>.part`)
        que hayan quedado en disco tras un cierre abrupto del bot.

        Estos archivos NUNCA deben considerarse "huérfanos listos para
        organizar": si la tarea de descarga sigue viva, `_recover_pending_queue`
        + `_process_download_task` la relanzará y regenerará el `.part` desde
        cero; si ya no existe una tarea pendiente para ese archivo, el `.part`
        es basura de una descarga que jamás terminó y debe descartarse.
        """
        try:
            stale = [f for f in UPLOAD_DIR.glob("*.part") if f.is_file() and f.name.startswith(".")]
            for f in stale:
                try:
                    f.unlink(missing_ok=True)
                    self.logger.info(f"Descarga parcial huérfana eliminada al iniciar: {f.name}")
                except OSError as e:
                    self.logger.warning(f"No se pudo eliminar el archivo parcial {f.name}: {e}")
        except Exception as e:
            self.logger.warning(f"Error limpiando descargas parciales huérfanas: {e}")

    def _load_pending_uploads(self) -> None:
        """Carga rutas y títulos parseados desde pending_telegram_uploads.json."""
        loaded_paths = set()
        if self.pending_file_db.exists():
            try:
                with open(self.pending_file_db, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    for chat_id, items in data.items():
                        pending_items = deque()
                        for item in items:
                            if isinstance(item, dict):
                                p = Path(item.get("path", ""))
                                parsed_title = item.get("parsed_title", self._clean_title_string(p.stem))
                            else:
                                p = Path(item)
                                parsed_title = self._clean_title_string(p.stem)

                            if p.exists():
                                pending_items.append(PendingItem(path=p, parsed_title=parsed_title))
                                loaded_paths.add(p.resolve())

                        if pending_items:
                            self.pending_uploads[str(chat_id)] = pending_items
            except Exception as e:
                self.logger.warning(f"Error cargando pending_uploads: {e}")

        # IMPORTANTE: los archivos en descarga se escriben con nombre oculto
        # `.<nombre>.part` (ver _process_download_task) precisamente para que
        # nunca puedan confundirse con un huérfano "listo para organizar" aquí,
        # incluso si el bot se reinicia a mitad de una descarga.
        orphans = [
            f for f in UPLOAD_DIR.iterdir()
            if f.is_file() and f.resolve() not in loaded_paths and not f.name.startswith(".")
        ]

        if orphans:
            chat_id_str = str(self.allowed_chat_id)
            current_queue = self.pending_uploads.setdefault(chat_id_str, deque())
            for orphan in orphans:
                clean_title = self._clean_title_string(orphan.stem)
                self.logger.info(f"Archivo huérfano recuperado en disco: {orphan.name}")
                current_queue.append(PendingItem(path=orphan, parsed_title=clean_title))
            self._save_pending_uploads()

    def _save_pending_uploads(self) -> None:
        """Guarda rutas y títulos parseados en el JSON."""
        try:
            data = {
                chat_id: [
                    {"path": str(item.path), "parsed_title": item.parsed_title}
                    for item in items if item.path.exists()
                ]
                for chat_id, items in self.pending_uploads.items()
                if any(item.path.exists() for item in items)
            }
            with open(self.pending_file_db, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.logger.error(f"Error guardando pending_uploads: {e}")

    def _load_processed_msgs(self) -> Deque[str]:
        try:
            if self.processed_msgs_db.exists():
                with open(self.processed_msgs_db, "r", encoding="utf-8") as f:
                    return deque(json.load(f), maxlen=MAX_PROCESSED_MSGS)
        except Exception as e:
            self.logger.warning(f"Error cargando mensajes procesados: {e}")
        return deque(maxlen=MAX_PROCESSED_MSGS)

    def _mark_msg_processed(self, chat_id: int, msg_id: int) -> None:
        key = f"{chat_id}:{msg_id}"
        if key in self.processed_msgs:
            return
        self.processed_msgs.append(key)
        try:
            with open(self.processed_msgs_db, "w", encoding="utf-8") as f:
                json.dump(list(self.processed_msgs), f)
        except Exception as e:
            self.logger.error(f"Error guardando mensajes procesados: {e}")

    def _is_known_msg(self, chat_id: int, msg_id: int) -> bool:
        """True si el mensaje ya se descargó o ya está en la cola de descargas."""
        if f"{chat_id}:{msg_id}" in self.processed_msgs:
            return True
        return any(t.chat_id == chat_id and t.msg_id == msg_id for t in self.download_queue_list)

    def _save_queue_to_disk(self) -> None:
        # Se escribe siempre, incluso la lista vacía: si no, la última tarea
        # completada quedaría persistida y se re-encolaría al reiniciar.
        try:
            data = [
                {
                    "chat_id": task.chat_id,
                    "msg_id": task.msg_id,
                    "default_name": task.default_name,
                    "caption": task.caption
                }
                for task in self.download_queue_list
            ]
            with open(self.queue_file_db, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            self.logger.error(f"Error guardando cola de descargas a disco: {e}")

    async def _recover_pending_queue(self) -> None:
        if self._recovered_already or not self.queue_file_db.exists():
            return

        self._recovered_already = True
        try:
            with open(self.queue_file_db, "r", encoding="utf-8") as f:
                data = json.load(f)

            if not data:
                return

            self.logger.info(f"Cargando {len(data)} descargas pendientes...")
            for item in data:
                task = DownloadTask(
                    chat_id=item["chat_id"],
                    msg_id=item["msg_id"],
                    default_name=item["default_name"],
                    caption=item.get("caption", "")
                )
                if not any(t.msg_id == task.msg_id and t.chat_id == task.chat_id for t in self.download_queue_list):
                    self.download_queue_list.append(task)
                    await self.download_queue.put(task)

        except Exception as e:
            self.logger.error(f"Error restaurando la cola de descargas: {e}")

    def _clean_session(self, chat_id: str) -> None:
        self.sessions.pop(chat_id, None)

    async def _release_processing(self, chat_id: str) -> None:
        async with self.pending_lock:
            self.processing_chats.discard(chat_id)

    async def _pending_items_for_chat(self, chat_id: str) -> List[PendingItem]:
        async with self.pending_lock:
            return list(self.pending_uploads.get(chat_id, ()))

    # ------------------------------------------------------------------
    # Parsing de Nombres y Búsqueda
    # ------------------------------------------------------------------

    # Palabras genéricas que a veces preceden al patrón SxxExx / NxNN en el
    # nombre/caption ("Episodio 1x14 <Serie>...") y que NO deben tomarse como
    # título de la serie si aparecen solas antes del marcador.
    _EPISODE_FILLER_WORDS = {
        "episodio", "episodios", "capitulo", "capítulo", "capitulos", "capítulos",
        "cap", "ep", "episode", "chapter",
    }

    # Coletillas de sitios de streaming/descarga que suelen venir pegadas al
    # título cuando éste se toma del texto posterior al marcador SxxExx.
    _RELEASE_SITE_NOISE_PATTERN = re.compile(
        r'\b(online|cuevana\s*\d*|repelis(?:plus)?|pelisplus|pelismart|gnula|'
        r'hdfull|tvplay|ver\s*peliculas?|ver\s*series)\b',
        flags=re.IGNORECASE,
    )

    def _clean_title_string(self, text: str) -> str:
        text = self._remove_fansub_prefixes(text)
        text = text.replace('_', ' ').replace('.', ' ')

        patterns_to_remove = [
            r'\b\d{3,4}x\d{3,4}\b',
            r'\b(1080p|720p|480p|2160p|4k|hd|webrip|web-dl|hdtv|x264|x265|hevc|aac|bluray)\b',
            r'\b[a-f0-9]{20,}\b',
            r'\b\d{8,}\b',
        ]
        for pattern in patterns_to_remove:
            text = re.sub(pattern, '', text, flags=re.IGNORECASE)

        return self._clean_filename_formatting(text)

    def _is_filler_title(self, text: str) -> bool:
        """True si `text` (ya limpio) no aporta nada como título de serie:
        vacío, o sólo una palabra genérica tipo "Episodio"/"Capitulo"/"Ep"."""
        normalized = text.strip().strip('-.:').strip().lower()
        return normalized == "" or normalized in self._EPISODE_FILLER_WORDS

    def _strip_trailing_filler_word(self, text: str) -> str:
        """Quita una palabra de relleno (Episodio/Capitulo/Ep/...) pegada al
        FINAL del título cuando precede directamente al marcador SxxExx/NxNN,
        ej. "Cowboy Bebop Episodio 1x16" -> raw_title = "Cowboy Bebop Episodio".
        _is_filler_title sólo descarta el título si es ÚNICAMENTE esa palabra;
        esto cubre el caso en que queda pegada al final de un título real, que
        de lo contrario contamina el título (rompiendo el auto-organize por
        similitud) o crea carpetas nuevas con nombre incorrecto."""
        words = text.strip().split()
        if words and words[-1].strip('-.:').lower() in self._EPISODE_FILLER_WORDS:
            words = words[:-1]
        return ' '.join(words).strip(' -.:')

    def _strip_release_site_noise(self, text: str) -> str:
        """Quita coletillas de sitios de streaming (Cuevana, Repelis, etc.)
        y la palabra "Online" que suelen venir pegadas al título real cuando
        éste se toma del texto posterior al marcador SxxExx/NxNN."""
        text = self._RELEASE_SITE_NOISE_PATTERN.sub('', text)
        text = re.sub(r'\s*-\s*$', '', text)
        text = re.sub(r'^\s*-\s*', '', text)
        return text

    def _extract_series_info(self, text: str):
        pattern = r'^(.*?)[\s_\.]+(?:[sS](\d+)[\s_\.]*[eE](\d+)|\b(\d+)[xX](\d+)\b)(.*)$'
        match = re.search(pattern, text)

        if match:
            raw_title = match.group(1)
            after_marker = match.group(6) or ""
            if match.group(2) and match.group(3):
                season = int(match.group(2))
                episode = int(match.group(3))
            else:
                season = int(match.group(4))
                episode = int(match.group(5))

            clean_title = self._clean_title_string(raw_title)
            clean_title = self._strip_trailing_filler_word(clean_title)

            if self._is_filler_title(clean_title):
                # Formato tipo "Episodio 1x14 El Mentalista Online - Cuevana 3":
                # el título antes del marcador es sólo relleno genérico, así
                # que el título real está DESPUÉS del marcador.
                fallback_title = self._clean_title_string(
                    self._strip_release_site_noise(after_marker)
                )
                if fallback_title:
                    clean_title = fallback_title

            return clean_title, season, episode

        return self._clean_title_string(text), None, None

    # ------------------------------------------------------------------
    # Nuevo método: obtener la raíz según el tipo
    # ------------------------------------------------------------------
    def _get_base_folder(self, media_type: str) -> Path:
        """Devuelve la carpeta raíz para el tipo de contenido."""
        if media_type == "movie":
            return self.media_dir / "Peliculas"
        elif media_type == "series":
            return self.media_dir / "series"
        elif media_type in ("anime_series", "anime_movie"):
            # Ambas comparten la misma raíz porque la carpeta anime no está subcarpetada
            return self.media_dir / "anime"
        else:
            return self.media_dir / "series"   # fallback

    # ------------------------------------------------------------------
    # Búsqueda de coincidencias usando la raíz correcta
    # ------------------------------------------------------------------
    def _find_matching_folders(self, media_type: str, query_title: str) -> List[str]:
        base_folder = self._get_base_folder(media_type)
        if not base_folder.exists():
            return []

        existing_folders = [f.name for f in base_folder.iterdir() if f.is_dir()]
        if not existing_folders:
            return []

        clean_query = self._clean_title_string(query_title)
        matches = difflib.get_close_matches(clean_query, existing_folders, n=3, cutoff=0.3)
        return matches

    # ------------------------------------------------------------------
    # Consultas a Base de Datos (SQLite) - AHORA USAN mediajelly_db
    # ------------------------------------------------------------------
    # NOTA: _query_db ha sido eliminada. Se usa load_progress() en su lugar.

    def _get_error_records(self) -> List[dict]:
        records = []
        error_files = (
            (self.tmp_dir / "failed-compression.txt", "compression"),
            (self.tmp_dir / "failed_verification.txt", "verification"),
        )

        for error_file, error_type in error_files:
            if not error_file.exists():
                continue
            try:
                lines = error_file.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError as exc:
                self.logger.warning(f"No se pudo leer {error_file}: {exc}")
                continue

            for line in lines:
                line = line.strip()
                if not line:
                    continue
                if error_type == "verification":
                    parts = line.split("\t", 2)
                    path = parts[1] if len(parts) > 1 else line
                    reason = parts[2] if len(parts) > 2 else "Error de verificación"
                else:
                    parts = line.split(" | ", 2)
                    path = parts[0]
                    reason = parts[2] if len(parts) > 2 else "Error de compresión"
                records.append({"path": path, "reason": reason})

        return records

    # ------------------------------------------------------------------
    # Helper para Edición Limpia de Mensajes de Interfaz
    # ------------------------------------------------------------------

    async def _update_session_message(self, chat_id: int, text: str, buttons=None) -> None:
        """Edita el mensaje activo de la sesión interactiva para no dejar mensajes duplicados."""
        chat_id_str = str(chat_id)
        session = self.sessions.get(chat_id_str)
        if session and session.msg_id:
            try:
                await self.client.edit_message(chat_id, session.msg_id, text, buttons=buttons)
                return
            except Exception as e:
                self.logger.warning(f"No se pudo editar el mensaje {session.msg_id}: {e}")

        new_msg = await self.client.send_message(chat_id, text, buttons=buttons)
        if session:
            session.msg_id = new_msg.id

    # ------------------------------------------------------------------
    # Eventos e Intérprete de Comandos
    # ------------------------------------------------------------------

    def run_forever(self) -> None:
        self.logger.info("MediaJelly Bot iniciado correctamente.")

        @self.client.on(events.NewMessage)
        async def on_message(event):
            await self._handle_message(event)

        @self.client.on(events.CallbackQuery)
        async def on_callback(event):
            await self._handle_callback(event)

        self.client.start(bot_token=self.bot_token)

        loop = asyncio.get_event_loop()
        # Desconexión limpia ante `stop`/`restart` (telegram_bot_ctl.sh): Telethon
        # guarda así el estado de actualizaciones y no se pierden mensajes.
        try:
            loop.add_signal_handler(signal.SIGTERM, lambda: loop.create_task(self.client.disconnect()))
        except (NotImplementedError, RuntimeError):
            pass
        self._background_task = loop.create_task(self._init_bot_background())

        try:
            self.client.run_until_disconnected()
        finally:
            self._background_task.cancel()
            try:
                loop.run_until_complete(self._background_task)
            except BaseException:
                pass

    async def _init_bot_background(self) -> None:
        await self._recover_pending_queue()
        await self._start_queue_worker()

    async def _start_queue_worker(self) -> None:
        while True:
            task = await self.download_queue.get()
            self.current_task = task
            try:
                await self._process_download_task(task)
            except Exception as e:
                self.logger.error(f"Error procesando cola de descargas: {e}\n{traceback.format_exc()}")
            finally:
                if task in self.download_queue_list:
                    self.download_queue_list.remove(task)
                self._save_queue_to_disk()
                self.download_queue.task_done()
                self.current_task = None

    # ============================================================
    # MODIFICACIÓN: _process_download_task (guardar cola ANTES de procesar)
    # ============================================================
    async def _process_download_task(self, task: DownloadTask) -> bool:
        try:
            msg = await self.client.get_messages(task.chat_id, ids=task.msg_id)
            if not msg or not msg.file:
                self.logger.error(f"No se pudo recuperar el mensaje {task.msg_id}.")
                return False
        except Exception as e:
            self.logger.error(f"Error buscando mensaje {task.msg_id}: {e}")
            return False

        chat_id_str = str(task.chat_id)
        expected_size = msg.file.size
        base_file_name = msg.file.name or f"{task.default_name}.mp4"

        final_file = UPLOAD_DIR / base_file_name
        tmp_file = UPLOAD_DIR / f".{base_file_name}.part"

        local_path: Optional[Path] = None

        if final_file.exists() and final_file.stat().st_size == expected_size:
            local_path = final_file
            status_msg = await msg.respond(
                f"ℹ️ El archivo `{final_file.name}` ya se encuentra completo en disco."
            )
        else:
            final_file.unlink(missing_ok=True)
            tmp_file.unlink(missing_ok=True)

            status_msg = await msg.respond(
                f"{EmojiGenerator.gear()} Descargando archivo a disco: `{task.default_name}`..."
            )
            downloaded_tmp = await self._download_file(msg, status_msg, tmp_file)

            if downloaded_tmp and downloaded_tmp.exists() and downloaded_tmp.stat().st_size == expected_size:
                try:
                    downloaded_tmp.rename(final_file)
                    local_path = final_file
                except OSError as e:
                    self.logger.error(f"Error renombrando descarga completa a destino final: {e}")
                    downloaded_tmp.unlink(missing_ok=True)
            else:
                if downloaded_tmp and downloaded_tmp.exists():
                    downloaded_tmp.unlink(missing_ok=True)

        if not (local_path and local_path.exists() and local_path.stat().st_size == expected_size):
            if status_msg:
                await status_msg.edit(f"{EmojiGenerator.error()} Error: El archivo en disco no coincide con el tamaño esperado.")
            return False

        # ========== GUARDAR COLA ANTES DE PROCESAR ==========
        # Descarga verificada: eliminar de la lista y guardar persistencia
        self._mark_msg_processed(task.chat_id, task.msg_id)
        if task in self.download_queue_list:
            self.download_queue_list.remove(task)
        self._save_queue_to_disk()  # Guarda el estado (si queda vacía, solo toca el archivo)
        # ==================================================

        # Intentar auto-organizar (busca en series y anime)
        auto_result = await self._try_auto_organize_series(local_path, task.default_name)
        if auto_result is not None:
            dest_file, matched_folder, season, episode, media_type = auto_result
            type_label = "Serie" if media_type == "series" else "Anime (serie)"
            await status_msg.edit(
                f"{EmojiGenerator.check_mark()} **Auto-organizado automáticamente**\n\n"
                f"📺 **Serie detectada:** `{matched_folder}` ({type_label}, coincidencia de nombre)\n"
                f"🔢 **Episodio:** `S{season:02d}E{episode:02d}`\n"
                f"📂 **Destino:** `{dest_file}`\n"
                f"📌 Agregado a la cola de compresión y subtítulos."
            )
            return True

        async with self.pending_lock:
            current_queue = self.pending_uploads.setdefault(chat_id_str, deque())
            if not any(item.path == local_path for item in current_queue):
                current_queue.append(PendingItem(path=local_path, parsed_title=task.default_name))
            self._save_pending_uploads()

        await status_msg.edit(
            f"{EmojiGenerator.check_mark()} **Guardado en disco:** `{local_path.name}`\n"
            f"📌 **Nombre parseado:** `{task.default_name}`\n\n"
            f"Usa el comando /organize para clasificarlo."
        )
        return True

    async def _try_auto_organize_series(
        self, local_path: Path, default_name: str
    ) -> Optional[tuple]:
        """
        Evalúa si el archivo recién descargado corresponde inequívocamente a
        un episodio de una serie ya existente en `media/series/` o `media/anime/`,
        y de ser así lo mueve automáticamente sin pasar por el flujo interactivo.

        Devuelve `(dest_file, matched_folder, season, episode, media_type)` si se movió
        automáticamente, o `None` si el archivo debe dejarse en la cola de
        `/organize` para clasificación manual.
        """
        try:
            clean_title, season, episode = self._extract_series_info(default_name)
            if season is None or episode is None:
                return None  # No coincide con un patrón reconocible de serie

            # Buscar en ambas raíces: series y anime
            possible_roots = [
                (self.media_dir / "series", "series"),
                (self.media_dir / "anime", "anime_series"),
            ]

            for root, media_type in possible_roots:
                if not root.exists():
                    continue
                existing_folders = [f.name for f in root.iterdir() if f.is_dir()]
                if not existing_folders:
                    continue

                query = clean_title.strip().lower()
                scored = [
                    (folder, difflib.SequenceMatcher(None, query, folder.strip().lower()).ratio())
                    for folder in existing_folders
                ]
                strong_matches = [(folder, ratio) for folder, ratio in scored if ratio >= 0.80]

                if len(strong_matches) == 1:
                    matched_folder, ratio = strong_matches[0]
                    # Determinar la raíz correcta para este tipo
                    dest_base = self._get_base_folder(media_type)
                    dest_dir = dest_base / matched_folder / f"Season {season}"
                    ext = local_path.suffix if local_path.suffix else ".mkv"
                    clean_name = f"{matched_folder} - S{season:02d}E{episode:02d}{ext}"
                    dest_dir.mkdir(parents=True, exist_ok=True)
                    dest_file = dest_dir / clean_name

                    if dest_file.exists():
                        self.logger.warning(
                            f"Auto-organización cancelada, ya existe '{dest_file}'; se deja para clasificación manual."
                        )
                        return None

                    shutil.move(str(local_path), str(dest_file))
                    add_pending(str(dest_file))

                    self.logger.info(
                        f"Auto-organizado '{local_path.name}' -> '{dest_file}' "
                        f"(similitud {ratio:.0%} con carpeta existente '{matched_folder}' en {root})."
                    )
                    return dest_file, matched_folder, season, episode, media_type

            # Si hay más de una coincidencia fuerte en diferentes raíces, no se auto-organiza
            # (sería ambiguo) - se deja para clasificación manual.
            self.logger.info(f"Auto-organización no concluyente para '{clean_title}', se deja para manual.")
            return None

        except PermissionError as e:
            self.logger.warning(
                f"Auto-organización sin permisos ({e}); se deja para /organize. "
                f"Revisa el propietario de la carpeta destino (debe ser uid {os.getuid()})."
            )
            return None
        except Exception as e:
            self.logger.error(f"Error en auto-organización de serie: {e}\n{traceback.format_exc()}")
            return None

    async def _handle_message(self, event):
        chat_id = event.chat_id
        chat_id_str = str(chat_id)

        if chat_id != self.allowed_chat_id:
            return

        text = event.message.text.strip() if event.message.text else ""

        # Enrutador de comandos
        if text.startswith('/'):
            cmd = text.split()[0].lower()
            if cmd in ["/start", "/ayuda", "/help"]:
                await self._cmd_help(event)
            elif cmd == "/status":
                await self._cmd_status(event)
            elif cmd == "/organize":
                await self._start_organize(event, chat_id_str)
            elif cmd in ["/pending_files", "/recibidos", "/retry_organize"]:
                await self._show_pending_files(event, chat_id_str, cmd == "/retry_organize")
            elif cmd in ["/pendientes", "/cola"]:
                await self._cmd_pendientes(event)
            elif cmd == "/completados":
                await self._cmd_completados(event)
            elif cmd == "/errores":
                await self._cmd_errores(event)
            elif cmd == "/subtitulos":
                await self._cmd_subtitulos(event)
            elif cmd in ["/disco", "/espacio"]:
                await self._cmd_disco(event)
            elif cmd == "/cancel":
                self._clean_session(chat_id_str)
                await self._release_processing(chat_id_str)
                await event.respond(f"{EmojiGenerator.check_mark()} Sesión o tarea actual cancelada.")
            else:
                await event.respond(f"{EmojiGenerator.warning()} Comando no reconocido. Usa /ayuda para ver los comandos válidos.")
            return

        # Recepción de archivos multimedia
        if event.message.media:
            if self._is_known_msg(chat_id, event.message.id):
                self.logger.info(f"Mensaje {event.message.id} ya descargado o en cola; se ignora.")
                return

            caption_text = event.message.message.strip() if event.message.message else ""
            file_name = event.message.file.name if event.message.file and event.message.file.name else ""

            if caption_text:
                raw_default = caption_text
            elif file_name and not file_name.startswith("document_"):
                raw_default = Path(file_name).stem
            else:
                raw_default = "video_sin_nombre"

            default_title = self._clean_title_string(raw_default)

            task = DownloadTask(
                chat_id=chat_id,
                msg_id=event.message.id,
                default_name=default_title,
                caption=caption_text
            )

            self.download_queue_list.append(task)
            self._save_queue_to_disk()
            await self.download_queue.put(task)

            queue_pos = self.download_queue.qsize()

            if queue_pos > 1 or self.current_task is not None:
                await event.respond(
                    f"📥 **Archivo en cola de descarga:** `{default_title}`\n"
                    f"📌 Posición en la fila: #{queue_pos + (1 if self.current_task else 0)}"
                )
            else:
                await event.respond(f"⏳ Archivo recibido: `{default_title}`. Preparando descarga...")
            return

        # Entrada de texto en flujos interactivos
        if chat_id_str in self.sessions:
            session = self.sessions[chat_id_str]

            if session.step == "waiting_title":
                session.raw_title = self._clean_title_string(text.strip())
                try:
                    await event.delete()
                except Exception:
                    pass
                await self._show_folder_confirmation(chat_id, session)

            elif session.step == "waiting_season_episode":
                match = re.search(r'(?:S?(\d+))?\s*(?:E?(\d+))', text, re.IGNORECASE)
                parts = text.split()
                if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
                    session.season = int(parts[0])
                    session.episode = int(parts[1])
                    try:
                        await event.delete()
                    except Exception:
                        pass
                    await self._show_final_summary(chat_id, session)
                elif match:
                    session.season = int(match.group(1)) if match.group(1) else 1
                    session.episode = int(match.group(2)) if match.group(2) else 1
                    try:
                        await event.delete()
                    except Exception:
                        pass
                    await self._show_final_summary(chat_id, session)
                else:
                    await event.respond(f"{EmojiGenerator.warning()} Ingrese temporada y episodio (ejemplo: `1 5` o `S01E05`):")

    # ------------------------------------------------------------------
    # Comandos Informativos
    # ------------------------------------------------------------------

    async def _cmd_help(self, event):
        dl_queue_size = self.download_queue.qsize()
        rec_files = len(await self._pending_items_for_chat(str(event.chat_id)))

        msg = (
            f"{EmojiGenerator.rocket()} **MediaJelly Telegram Bot**\n\n"
            "• /status - Resumen global del sistema y procesos\n"
            "• /organize - Clasificar los archivos pendientes en disco\n"
            "• /recibidos - Lista de archivos descargados listos para mover\n"
            "• /pendientes - Cola de compresión en Base de Datos\n"
            "• /completados - Total de archivos comprimidos con éxito\n"
            "• /errores - Últimos errores en el pipeline\n"
            "• /subtitulos - Estado de extracción y traducción\n"
            "• /disco - Espacio libre en disco\n"
            "• /cancel - Cancelar la sesión interactiva actual\n\n"
            f"📥 **Cola de descargas (Telegram):** {dl_queue_size}\n"
            f"📂 **Archivos recibidos por organizar:** {rec_files}"
        )
        await event.respond(msg)

    async def _cmd_status(self, event):
        dl_queue_size = self.download_queue.qsize() + (1 if self.current_task else 0)
        rec_files = len(await self._pending_items_for_chat(str(event.chat_id)))

        num_pending = len(get_pending_files())
        num_completed = len(get_all_completed_files())
        num_errors = len(self._get_error_records())

        media_usage = shutil.disk_usage(self.media_dir)
        free_media_gb = media_usage.free / (1024 ** 3)

        msg = (
            f"📊 **Estado Actual del Sistema MediaJelly**\n\n"
            f"📥 **Descargas de Telegram:** {dl_queue_size} en cola\n"
            f"📂 **Archivos por clasificar (/organize):** {rec_files}\n"
            f"⚙️ **Compresión pendiente (DB):** {num_pending} archivos\n"
            f"✅ **Comprensión completada:** {num_completed} archivos\n"
            f"❌ **Archivos con error:** {num_errors}\n\n"
            f"💾 **Espacio disponible en `/media`:** {free_media_gb:.2f} GB"
        )
        await event.respond(msg)

    async def _cmd_pendientes(self, event):
        pending_files = get_pending_files()
        if not pending_files:
            await event.respond(f"{EmojiGenerator.check_mark()} No hay archivos pendientes en la cola de compresión de la Base de Datos.")
            return

        lines = [f"⚙️ **Archivos pendientes de compresión ({len(pending_files)}):**\n"]
        for idx, file_path in enumerate(pending_files[:MAX_LIST_ITEMS], 1):
            name = file_path.name
            lines.append(f"{idx}. `{name}`")

        await event.respond("\n".join(lines))

    async def _cmd_completados(self, event):
        completed_files = get_all_completed_files()
        total = len(completed_files)
        recent_files = completed_files[-5:]

        lines = [f"✅ **Total de archivos comprimidos:** {total}\n"]
        if recent_files:
            lines.append("**Últimos procesados:**")
            for file_path in reversed(recent_files):
                name = file_path.name
                lines.append(f"• `{name}`")

        await event.respond("\n".join(lines))

    async def _cmd_errores(self, event):
        error_records = self._get_error_records()
        if not error_records:
            await event.respond(f"{EmojiGenerator.check_mark()} No se registraron errores de compresión en la Base de Datos.")
            return

        lines = [f"❌ **Errores registrados ({len(error_records)}):**\n"]
        for record in reversed(error_records[-MAX_ERROR_ITEMS:]):
            name = Path(record["path"]).name
            err = record["reason"][:300]
            lines.append(f"• `{name}`\n  ⚠️ _{err}_")

        await event.respond("\n".join(lines))

    # ============================================================
    # MODIFICACIÓN: _cmd_subtitulos (usa load_progress)
    # ============================================================
    async def _cmd_subtitulos(self, event):
        progress = load_progress()
        subtitle_status = progress.get("subtitle_translation", {})
        if not subtitle_status:
            await event.respond(f"{EmojiGenerator.check_mark()} Todos los subtítulos están procesados o al día.")
            return

        lines = ["🗣️ **Estado de Subtítulos:**\n"]
        items = subtitle_status.get("files", {})
        if not items:
            await event.respond(f"{EmojiGenerator.check_mark()} Todos los subtítulos están procesados o al día.")
            return

        for file_path, status in list(items.items())[:10]:
            name = Path(file_path).name
            lines.append(f"• `{name}` ➔ `{status}`")

        await event.respond("\n".join(lines))

    async def _cmd_disco(self, event):
        media_u = shutil.disk_usage(self.media_dir)
        tmp_u = shutil.disk_usage(self.tmp_dir)

        msg = (
            f"💾 **Espacio en Disco Servidor**\n\n"
            f"📁 **`/media` (Biblioteca final):**\n"
            f"• Libre: {media_u.free / (1024**3):.2f} GB\n"
            f"• Total: {media_u.total / (1024**3):.2f} GB\n\n"
            f"📂 **`/tmp` (Descargas temporales):**\n"
            f"• Libre: {tmp_u.free / (1024**3):.2f} GB\n"
            f"• Total: {tmp_u.total / (1024**3):.2f} GB"
        )
        await event.respond(msg)

    # ------------------------------------------------------------------
    # Flujo Interactivo (/organize)
    # ------------------------------------------------------------------

    async def _start_organize(self, event, chat_id_str: str):
        async with self.pending_lock:
            if chat_id_str in self.processing_chats:
                await event.respond(f"{EmojiGenerator.warning()} Ya hay un movimiento en proceso, espera a que termine.")
                return
            pending_items = self.pending_uploads.get(chat_id_str)
            pending_item = pending_items[0] if pending_items else None
            if pending_item:
                self.processing_chats.add(chat_id_str)

        if not pending_item:
            await event.respond(f"{EmojiGenerator.error()} No hay ningún archivo pendiente por clasificar.")
            return
        if not pending_item.path.exists():
            await self._release_processing(chat_id_str)
            await event.respond(f"{EmojiGenerator.error()} El primer archivo pendiente ya no existe en disco: `{pending_item.path.name}`.")
            return

        default_name = pending_item.parsed_title

        # Primera elección: Película o Serie
        buttons = [
            [Button.inline("🎬 Película", b"type_movie")],
            [Button.inline("📺 Serie", b"type_series")],
            [Button.inline("❌ Cancelar", b"cancel")]
        ]

        msg = await event.respond(
            f"📁 **Archivo:** `{pending_item.path.name}`\n"
            f"📌 **Título parseado sugerido:** `{default_name}`\n\n"
            f"¿Qué tipo de contenido es?",
            buttons=buttons
        )

        self.sessions[chat_id_str] = UserSession(
            file_path=pending_item.path,
            raw_title=default_name,
            step="waiting_type",
            msg_id=msg.id
        )

    async def _show_pending_files(self, event, chat_id_str: str, retry: bool = False) -> None:
        pending_items = await self._pending_items_for_chat(chat_id_str)
        if not pending_items:
            await event.respond(f"{EmojiGenerator.check_mark()} No hay archivos descargados pendientes de organizar.")
            return

        lines = [f"📋 **Archivos recibidos en disco ({len(pending_items)}):**"]
        for index, item in enumerate(pending_items, 1):
            lines.append(f"{index}. `{item.path.name}`\n   └ 🏷️ Título sugerido: *{item.parsed_title}*")

        await event.respond("\n".join(lines[:MAX_LIST_ITEMS + 1]))
        if retry:
            await self._start_organize(event, chat_id_str)

    async def _show_folder_confirmation(self, chat_id: int, session: UserSession):
        matches = self._find_matching_folders(session.media_type, session.raw_title)
        buttons = []

        # Obtener la ruta base para mostrar en el mensaje
        base_path = self._get_base_folder(session.media_type)

        session.pending_matches = matches

        if matches:
            text = (
                f"🔎 **Confirmación de Carpeta**\n\n"
                f"Tipo: `{session.media_type}`\n"
                f"Nombre sugerido: `{session.raw_title}`\n"
                f"Encontramos coincidencias en `{base_path}`. ¿A cuál corresponde?"
            )
            for index, match_name in enumerate(matches):
                cb_data = f"use_exist_{index}".encode('utf-8')
                buttons.append([Button.inline(f"📂 Usar: {match_name}", cb_data)])

            buttons.append([Button.inline(f"🆕 Crear nueva carpeta: \"{session.raw_title}\"", b"use_new")])
        else:
            text = (
                f"📁 **Confirmación de Carpeta**\n\n"
                f"Tipo: `{session.media_type}`\n"
                f"Se creará la siguiente carpeta:\n"
                f"📂 `{base_path / session.raw_title}/`\n\n"
                f"¿Es correcto o deseas cambiar el nombre?"
            )
            buttons.append([Button.inline(f"✅ Sí, crear/usar \"{session.raw_title}\"", b"use_new")])

        buttons.append([Button.inline("✏️ Corregir / Cambiar nombre", b"edit_title")])
        buttons.append([Button.inline("❌ Cancelar este archivo", b"cancel")])

        session.step = "confirming_folder"
        await self._update_session_message(chat_id, text, buttons=buttons)

    async def _show_final_summary(self, chat_id: int, session: UserSession):
        session.step = "final_confirm"

        base_path = self._get_base_folder(session.media_type)
        folder_path = base_path / session.selected_folder

        if session.media_type in ("series", "anime_series") and session.season:
            folder_path = folder_path / f"Season {session.season}"

        file_info = f"📂 **Destino:** `{folder_path}`\n"

        if session.media_type in ("series", "anime_series"):
            file_info += f"🔢 **Episodio:** Temporada {session.season}, Episodio {session.episode} (`S{session.season:02d}E{session.episode:02d}`)\n"

        file_info += f"📄 **Archivo original:** `{session.file_path.name}`\n"

        text = f"📋 **Resumen Final de Organización:**\n\n{file_info}\n¿Mover y agregar a la cola de compresión?"

        buttons = [
            [Button.inline("🚀 Confirmar y Mover", b"confirm_move")],
            [Button.inline("✏️ Cambiar Nombre/Carpeta", b"edit_title")],
            [Button.inline("❌ Cancelar este archivo", b"cancel")]
        ]
        await self._update_session_message(chat_id, text, buttons=buttons)

    async def _safe_answer(self, event, *args, **kwargs) -> None:
        # Si el usuario pulsa un botón y el bot tarda (o estaba caído), Telegram
        # invalida el query_id; no es un error real.
        try:
            await event.answer(*args, **kwargs)
        except QueryIdInvalidError:
            pass
        except Exception as e:
            self.logger.warning(f"No se pudo responder al callback: {e}")

    async def _handle_callback(self, event):
        chat_id_str = str(event.chat_id)
        data = event.data.decode('utf-8')

        session = self.sessions.get(chat_id_str)
        if not session and data != "cancel":
            await self._safe_answer(event, "Sesión expirada o no encontrada.", alert=True)
            return
        # Responder de inmediato: así event.edit() no lanza su propio answer()
        # en segundo plano (origen de los "Task exception was never retrieved").
        await self._safe_answer(event)

        if data == "cancel":
            self._clean_session(chat_id_str)
            await self._release_processing(chat_id_str)
            await event.edit(f"{EmojiGenerator.check_mark()} Omitido este archivo. Pasando al siguiente si existe...")
            return

        # Paso 1: elección de tipo base (película o serie)
        if data in ("type_movie", "type_series"):
            session.base_type = "movie" if data == "type_movie" else "series"
            session.step = "waiting_subtype"

            # Mostrar botones para elegir normal o anime
            if session.base_type == "movie":
                buttons = [
                    [Button.inline("🎞️ Película normal", b"sub_movie_normal")],
                    [Button.inline("🌸 Anime (película)", b"sub_movie_anime")],
                    [Button.inline("❌ Cancelar", b"cancel")]
                ]
                await event.edit("¿Es una película normal o de anime?", buttons=buttons)
            else:  # series
                buttons = [
                    [Button.inline("📺 Serie normal", b"sub_series_normal")],
                    [Button.inline("🌸 Anime (serie)", b"sub_series_anime")],
                    [Button.inline("❌ Cancelar", b"cancel")]
                ]
                await event.edit("¿Es una serie normal o de anime?", buttons=buttons)

        # Paso 2: elección de subtipo (normal o anime)
        elif data in ("sub_movie_normal", "sub_movie_anime", "sub_series_normal", "sub_series_anime"):
            if data == "sub_movie_normal":
                session.media_type = "movie"
            elif data == "sub_movie_anime":
                session.media_type = "anime_movie"
            elif data == "sub_series_normal":
                session.media_type = "series"
            elif data == "sub_series_anime":
                session.media_type = "anime_series"

            # Si ya tenemos título, parsear información de serie si corresponde
            if session.raw_title:
                if session.media_type in ("series", "anime_series"):
                    clean_title, season, episode = self._extract_series_info(session.raw_title)
                    session.raw_title = clean_title
                    if season is not None and episode is not None:
                        session.season = season
                        session.episode = episode

                await self._show_folder_confirmation(event.chat_id, session)
            else:
                session.step = "waiting_title"
                prompt = "🎬 Escribe el TÍTULO de la película:" if session.media_type in ("movie", "anime_movie") else "📺 Escribe el NOMBRE de la serie:"
                await event.edit(prompt)

        # Selección de carpeta existente o nueva
        elif data.startswith("use_exist_") or data == "use_new":
            if data == "use_new":
                selected_folder = session.raw_title
            else:
                index = int(data[len("use_exist_"):])
                selected_folder = session.pending_matches[index]
            session.selected_folder = selected_folder

            if session.media_type in ("movie", "anime_movie"):
                await self._show_final_summary(event.chat_id, session)
            else:  # series o anime_series
                if session.season is not None and session.episode is not None:
                    await self._show_final_summary(event.chat_id, session)
                else:
                    session.step = "waiting_season_episode"
                    await event.edit(
                        f"📂 **Carpeta seleccionada:** `{selected_folder}`\n\n"
                        f"Por favor ingresa la **Temporada y Episodio** (ej. `1 5` o `S01E05`):"
                    )

        elif data == "edit_title":
            session.step = "waiting_title"
            prompt = "✏️ Escribe nuevamente el nombre o título correcto:"
            await event.edit(prompt)

        elif data == "confirm_move":
            await self._finalize_move(event, session, chat_id_str)

    async def _finalize_move(self, event, session: UserSession, chat_id_str: str):
        src = session.file_path
        if not src or not src.exists():
            await event.edit(f"{EmojiGenerator.error()} El archivo origen ya no existe.")
            self._clean_session(chat_id_str)
            await self._release_processing(chat_id_str)
            return

        try:
            dest_base = self._get_base_folder(session.media_type)
            dest_dir = dest_base / session.selected_folder
            ext = src.suffix if src.suffix else ".mkv"

            # Construir nombre según el tipo
            if session.media_type in ("series", "anime_series"):
                if session.season:
                    dest_dir = dest_dir / f"Season {session.season}"
                clean_name = f"{session.selected_folder} - S{session.season:02d}E{session.episode:02d}{ext}"
            else:  # movie o anime_movie
                clean_name = f"{session.selected_folder}{ext}"

            dest_dir.mkdir(parents=True, exist_ok=True)
            dest_file = dest_dir / clean_name

            if dest_file.exists():
                await event.edit(f"{EmojiGenerator.warning()} Ya existe un archivo con ese nombre en el destino (`{dest_file.name}`).")
                await self._release_processing(chat_id_str)
                return

            async with self.pending_lock:
                pending_items = self.pending_uploads.get(chat_id_str)
                if not pending_items or pending_items[0].path != src:
                    raise RuntimeError("La cola de pendientes cambió durante la organización")

                shutil.move(str(src), str(dest_file))
                pending_items.popleft()
                if not pending_items:
                    self.pending_uploads.pop(chat_id_str, None)
                self._save_pending_uploads()

            add_pending(str(dest_file))
            self._clean_session(chat_id_str)
            await self._release_processing(chat_id_str)

            await event.edit(
                f"{EmojiGenerator.success()} **¡Archivo organizado exitosamente!**\n\n"
                f"📂 **Ubicación:** `{dest_file}`\n"
                f"📌 Agregado a la cola de compresión y subtítulos."
            )
        except PermissionError as e:
            blocked = Path(e.filename) if e.filename else None
            self.logger.error(f"Sin permisos moviendo archivo: {e}")
            await self._release_processing(chat_id_str)
            await event.edit(
                f"{EmojiGenerator.error()} **Sin permisos de escritura**\n\n"
                f"`{blocked or e}` pertenece a otro usuario (probablemente se creó como root "
                f"desde Samba u otro servicio).\n\n"
                f"Se corrige automáticamente en ≤15 min, o ejecuta en el servidor:\n"
                f"`sudo chown -R {os.getuid()}:{os.getgid()} \"{blocked.parent if blocked else ''}\"`\n\n"
                f"Luego usa /retry_organize."
            )
        except Exception as e:
            self.logger.error(f"Error moviendo archivo: {e}\n{traceback.format_exc()}")
            await self._release_processing(chat_id_str)
            await event.edit(f"{EmojiGenerator.error()} Error al mover el archivo: {e}")

    async def _download_file(self, msg, status_msg, target_path: Path) -> Optional[Path]:
        last_update = [0.0]

        async def callback(current, total):
            now = time.time()
            if now - last_update[0] >= 3.5 or current == total:
                last_update[0] = now
                pct = (current / total) * 100 if total else 0
                mb_done, mb_total = current / 1048576, total / 1048576
                try:
                    await status_msg.edit(f"⏳ Descargando... {pct:.1f}% ({mb_done:.1f} MB / {mb_total:.1f} MB)")
                except Exception:
                    pass

        try:
            path = await msg.download_media(file=str(target_path), progress_callback=callback)
            return Path(path) if path else None
        except Exception as e:
            self.logger.error(f"Error descargando archivo: {e}")
            if target_path.exists():
                target_path.unlink(missing_ok=True)
            return None


def main():
    # Si la red cae más allá de lo que Telethon reintenta por su cuenta, se
    # recrea el bot con backoff en lugar de terminar el proceso.
    delay = RESTART_DELAY_MIN
    while True:
        bot = None
        started = time.time()
        try:
            bot = MediaJellyTelegramBot()
            bot.run_forever()
            # Retorno limpio: desconexión pedida (SIGTERM desde telegram_bot_ctl.sh).
            logging.getLogger("mediajelly_telegram_bot").info("Bot detenido.")
            return
        except KeyboardInterrupt:
            return
        except (ConnectionError, OSError, asyncio.TimeoutError) as e:
            logger = logging.getLogger("mediajelly_telegram_bot")
            if time.time() - started > 600:
                delay = RESTART_DELAY_MIN
            logger.error(f"Conexión con Telegram perdida ({e}); reintentando en {delay}s...")
            if bot is not None:
                try:
                    bot.client.loop.run_until_complete(bot.client.disconnect())
                except Exception:
                    pass
            time.sleep(delay)
            delay = min(delay * 2, RESTART_DELAY_MAX)


if __name__ == "__main__":
    main()
