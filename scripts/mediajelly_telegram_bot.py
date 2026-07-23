#!/usr/bin/env python3
"""
MediaJelly Telegram Bot - Bot interactivo (long-polling)

A diferencia de mediajelly_notifier.py (que sólo envía mensajes salientes),
este script escucha mensajes entrantes de Telegram mediante getUpdates y
responde con el estado real del sistema, leyendo la misma base de datos
SQLite (mediajelly_db.py) y los mismos archivos de progreso que usa el
pipeline de compresión/subtítulos. No modifica ni interfiere con el
procesamiento: es de solo lectura.

Comandos soportados:
    /start, /ayuda   -> lista de comandos
    /status           -> resumen del progreso actual (compresión y subtítulos)
    /pendientes       -> archivos pendientes de comprimir
    /completados      -> cantidad de archivos completados
    /errores          -> últimos fallos de compresión (con motivo)
    /subtitulos       -> estado de la traducción de subtítulos

Ejecución: pensado para correr como proceso de fondo de larga duración
(ver docker-entrypoint-hybrid.sh), no como tarea de cron.
"""

from __future__ import annotations

import logging
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

try:
    import requests

    REQUESTS_AVAILABLE = True
except ImportError:
    requests = None
    REQUESTS_AVAILABLE = False

from mediajelly_emoji import EmojiGenerator
from mediajelly_utils import MediaJellyPaths
from mediajelly_db import get_pending_files, get_all_completed_files, load_progress
from mediajelly_notifier import TelegramNotifier

REQUEST_TIMEOUT = 35  # Debe ser mayor al "timeout" de long-polling (30s)
POLL_TIMEOUT = 30
POLL_ERROR_BACKOFF = 10
MAX_LIST_ITEMS = 15
MAX_ERROR_ITEMS = 8
TELEGRAM_MESSAGE_LIMIT = 3500  # Margen bajo el límite real de Telegram (4096)


class MediaJellyTelegramBot:
    """Bot interactivo de solo lectura sobre el estado de MediaJelly."""

    def __init__(self) -> None:
        # Reutiliza toda la carga de configuración/credenciales de TelegramNotifier
        self.notifier = TelegramNotifier()
        self.logger = self._setup_logging()

        self.bot_token = self.notifier.config["TELEGRAM_BOT_TOKEN"]
        self.allowed_chat_id = str(self.notifier.config["TELEGRAM_CHAT_ID"])
        self.api_base = f"https://api.telegram.org/bot{self.bot_token}"

        self.scripts_dir = self.notifier.scripts_dir
        self.tmp_dir = self.scripts_dir / "tmp"
        self.offset_file = self.tmp_dir / "telegram_bot_offset.txt"

        self.commands = {
            "/start": self._cmd_help,
            "/ayuda": self._cmd_help,
            "/help": self._cmd_help,
            "/status": self._cmd_status,
            "/pendientes": self._cmd_pendientes,
            "/completados": self._cmd_completados,
            "/errores": self._cmd_errores,
            "/subtitulos": self._cmd_subtitulos,
        }

    # ------------------------------------------------------------------
    # Infraestructura
    # ------------------------------------------------------------------

    def _setup_logging(self) -> logging.Logger:
        logger = logging.getLogger("mediajelly_telegram_bot")
        logger.setLevel(logging.INFO)
        if not logger.handlers:
            log_dir = MediaJellyPaths.get_base_path() / "scripts" / "tmp" / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            handler = logging.FileHandler(log_dir / "telegram_bot.log", encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
            logger.addHandler(handler)
            stream_handler = logging.StreamHandler()
            stream_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
            logger.addHandler(stream_handler)
        return logger

    def _load_offset(self) -> int:
        try:
            if self.offset_file.exists():
                return int(self.offset_file.read_text().strip() or "0")
        except Exception:
            pass
        return 0

    def _save_offset(self, offset: int) -> None:
        try:
            self.offset_file.write_text(str(offset))
        except Exception as e:
            self.logger.warning(f"No se pudo guardar el offset de Telegram: {e}")

    def _get_updates(self, offset: int) -> List[Dict]:
        url = f"{self.api_base}/getUpdates"
        params = {"timeout": POLL_TIMEOUT, "offset": offset, "allowed_updates": ["message"]}
        response = requests.get(url, params=params, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            self.logger.warning(f"Respuesta no OK de getUpdates: {data}")
            return []
        return data.get("result", [])

    def _send(self, chat_id: str, text: str) -> None:
        # Divide mensajes largos para respetar el límite de Telegram
        for i in range(0, len(text), TELEGRAM_MESSAGE_LIMIT):
            chunk = text[i : i + TELEGRAM_MESSAGE_LIMIT]
            try:
                requests.post(
                    f"{self.api_base}/sendMessage",
                    data={"chat_id": chat_id, "text": chunk},
                    timeout=REQUEST_TIMEOUT,
                )
            except Exception as e:
                self.logger.error(f"Error enviando respuesta a Telegram: {e}")

    # ------------------------------------------------------------------
    # Comandos
    # ------------------------------------------------------------------

    def _cmd_help(self, _args: str) -> str:
        return (
            f"{EmojiGenerator.rocket()} MediaJelly Bot\n\n"
            "Comandos disponibles:\n"
            "/status - Resumen del progreso actual (compresión y subtítulos)\n"
            "/pendientes - Archivos pendientes de comprimir\n"
            "/completados - Cantidad de archivos ya comprimidos\n"
            "/subtitulos - Estado de la traducción de subtítulos\n"
            "/errores - Últimos fallos de compresión, con el motivo\n"
        )

    def _cmd_status(self, _args: str) -> str:
        progress = load_progress()
        proc = progress.get("processing", {})
        subs = progress.get("subtitle_translation", {})

        lines = [f"{EmojiGenerator.gear()} Estado de MediaJelly\n"]

        proc_status = proc.get("status", "desconocido")
        lines.append(f"{EmojiGenerator.clapper()} Compresión: {proc_status}")
        if proc.get("total_files"):
            lines.append(
                f"  {proc.get('current_file', 0)}/{proc.get('total_files', 0)} "
                f"({proc.get('percentage', 0):.1f}%) - {proc.get('current_file_name', '')}"
            )
        proc_stats = proc.get("stats", {})
        if proc_stats:
            lines.append(
                f"  Comprimidos: {proc_stats.get('files_compressed', 0)} | "
                f"Renombrados: {proc_stats.get('files_renamed', 0)} | "
                f"Omitidos: {proc_stats.get('files_skipped', 0)} | "
                f"Errores: {len(proc_stats.get('errors', []))}"
            )

        subs_status = subs.get("status", "desconocido")
        lines.append(f"\n{EmojiGenerator.scroll()} Subtítulos: {subs_status}")
        subs_stats = subs.get("stats", {})
        if subs_stats:
            lines.append(
                f"  Extraídos: {subs_stats.get('files_extracted', 0)} | "
                f"Traducidos: {subs_stats.get('files_translated', 0)} | "
                f"Errores: {subs_stats.get('files_errors', 0)}"
            )

        try:
            pending_count = len(get_pending_files())
        except Exception:
            pending_count = "?"
        try:
            completed_count = len(get_all_completed_files())
        except Exception:
            completed_count = "?"
        lines.append(f"\n{EmojiGenerator.clipboard()} Pendientes: {pending_count} | Completados: {completed_count}")

        return "\n".join(lines)

    def _cmd_pendientes(self, _args: str) -> str:
        try:
            pending = get_pending_files()
        except Exception as e:
            return f"{EmojiGenerator.error()} No se pudo leer la lista de pendientes: {e}"

        if not pending:
            return f"{EmojiGenerator.check_mark()} No hay archivos pendientes de comprimir."

        lines = [f"{EmojiGenerator.clipboard()} Pendientes: {len(pending)}\n"]
        for p in pending[:MAX_LIST_ITEMS]:
            lines.append(f"• {Path(p).name}")
        if len(pending) > MAX_LIST_ITEMS:
            lines.append(f"... y {len(pending) - MAX_LIST_ITEMS} más")
        return "\n".join(lines)

    def _cmd_completados(self, _args: str) -> str:
        try:
            completed = get_all_completed_files()
        except Exception as e:
            return f"{EmojiGenerator.error()} No se pudo leer la lista de completados: {e}"

        lines = [f"{EmojiGenerator.check_mark()} Archivos completados: {len(completed)}\n"]
        for p in completed[-MAX_LIST_ITEMS:]:
            lines.append(f"• {Path(p).name}")
        if len(completed) > MAX_LIST_ITEMS:
            lines.append(f"(mostrando los últimos {MAX_LIST_ITEMS})")
        return "\n".join(lines)

    def _cmd_subtitulos(self, _args: str) -> str:
        progress = load_progress()
        subs = progress.get("subtitle_translation", {})
        stats = subs.get("stats", {})

        lines = [
            f"{EmojiGenerator.scroll()} Traducción de subtítulos\n",
            f"Estado: {subs.get('status', 'desconocido')}",
            f"Última ejecución: {subs.get('last_run', 'N/D')}",
            f"Extraídos: {stats.get('files_extracted', 0)}",
            f"Traducidos: {stats.get('files_translated', 0)}",
            f"Con audio en español: {stats.get('files_with_spanish_audio', 0)}",
            f"Ya tenían subtítulos en español: {stats.get('files_with_spanish_subs', 0)}",
            f"Errores: {stats.get('files_errors', 0)}",
        ]
        errors = stats.get("errors", [])
        if errors:
            lines.append("\nÚltimos errores:")
            for err in errors[-5:]:
                lines.append(f"• {err}")
        return "\n".join(lines)

    def _cmd_errores(self, _args: str) -> str:
        failed_file = self.tmp_dir / "failed-compression.txt"
        if not failed_file.exists():
            return f"{EmojiGenerator.check_mark()} No hay fallos de compresión registrados."

        try:
            lines_raw = [l.strip() for l in failed_file.read_text(encoding="utf-8", errors="ignore").splitlines() if l.strip()]
        except Exception as e:
            return f"{EmojiGenerator.error()} No se pudo leer failed-compression.txt: {e}"

        if not lines_raw:
            return f"{EmojiGenerator.check_mark()} No hay fallos de compresión registrados."

        lines = [f"{EmojiGenerator.error()} Últimos fallos de compresión ({len(lines_raw)} en total):\n"]
        for entry in lines_raw[-MAX_ERROR_ITEMS:]:
            # Formato: "ruta | timestamp | motivo"
            parts = entry.split(" | ", 2)
            if len(parts) == 3:
                file_path, timestamp, reason = parts
                name = Path(file_path).name
                if len(reason) > 250:
                    reason = reason[:250] + "…"
                lines.append(f"• {name} ({timestamp})\n  {reason}")
            else:
                lines.append(f"• {entry}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Loop principal
    # ------------------------------------------------------------------

    def _handle_message(self, message: Dict) -> None:
        chat_id = str(message.get("chat", {}).get("id", ""))
        text = (message.get("text") or "").strip()
        if not text:
            return

        # Seguridad: sólo responder al chat configurado (el dueño del bot)
        if chat_id != self.allowed_chat_id:
            self.logger.warning(f"Mensaje ignorado de chat no autorizado: {chat_id}")
            return

        command = text.split()[0].split("@")[0].lower()
        args = text[len(command):].strip()

        handler = self.commands.get(command)
        if not handler:
            self._send(chat_id, f"{EmojiGenerator.warning_msg()} Comando no reconocido. Usa /ayuda para ver la lista.")
            return

        try:
            response = handler(args)
        except Exception as e:
            self.logger.error(f"Error ejecutando comando {command}: {e}", exc_info=True)
            response = f"{EmojiGenerator.error()} Error interno procesando el comando: {e}"

        self._send(chat_id, response)

    def run_forever(self) -> None:
        if not REQUESTS_AVAILABLE:
            self.logger.error("La librería 'requests' no está disponible; el bot no puede iniciar.")
            return

        self.logger.info(f"{EmojiGenerator.rocket()} MediaJelly Telegram Bot iniciado (long-polling)")
        offset = self._load_offset()

        while True:
            try:
                updates = self._get_updates(offset)
                for update in updates:
                    offset = update["update_id"] + 1
                    message = update.get("message")
                    if message:
                        self._handle_message(message)
                    self._save_offset(offset)
            except requests.exceptions.RequestException as e:
                self.logger.warning(f"Error de red consultando Telegram, reintentando en {POLL_ERROR_BACKOFF}s: {e}")
                time.sleep(POLL_ERROR_BACKOFF)
            except Exception as e:
                self.logger.error(f"Error inesperado en el loop del bot: {e}\n{traceback.format_exc()}")
                time.sleep(POLL_ERROR_BACKOFF)


def main() -> None:
    bot = MediaJellyTelegramBot()
    bot.run_forever()


if __name__ == "__main__":
    main()
