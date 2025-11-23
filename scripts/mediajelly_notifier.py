#!/usr/bin/env python3
"""
MediaJelly Telegram Notifier Python - Sistema de notificaciones optimizado
"""

import os
import sys
import json
import time
from pathlib import Path
from datetime import datetime
from typing import List, Optional, Dict
import logging

from mediajelly_emoji import EmojiGenerator

# Importar configuración centralizada
from mediajelly_config import get_config

# Constantes
REQUEST_TIMEOUT = 30  # Timeout para requests HTTP (segundos)


class TelegramNotifier:
    """Notificador de Telegram optimizado"""

    def __init__(self):
        # Configura logging ANTES de cualquier cosa
        logging.basicConfig(level=logging.INFO)
        self.logger = logging.getLogger(__name__)
        self.logger.info("Inicializando TelegramNotifier...")
        
        # Cargar configuración centralizada
        try:
            self.config_obj = get_config()
            print(f"{EmojiGenerator.success()} Configuración YAML cargada exitosamente")
        except Exception as e:
            print(f"Error cargando configuración: {e}")
            sys.exit(1)

        # Detecta el entorno
        self.is_container = Path("/mediajelly").exists()
        self.base_dir = Path("/mediajelly" if self.is_container else "/home/tafurc/mediaJelly")
        self.scripts_dir = self.base_dir / "scripts"
        self.state_file = self.scripts_dir / "tmp" / "last_notification_state"
        self.queue_file = self.scripts_dir / "tmp" / "notification_queue.json"

        # Crea directorio tmp
        (self.scripts_dir / "tmp").mkdir(parents=True, exist_ok=True)

        # Cargar configuración de Telegram
        try:
            self.config = self.load_config()
            self.logger.info("Configuración de Telegram cargada exitosamente")
        except Exception as e:
            self.logger.error(f"Error cargando configuración de Telegram: {e}")
            sys.exit(1)

    def load_config(self) -> Dict[str, str]:
        """Cargar configuración de Telegram desde YAML centralizado"""
        config = {}

        try:
            # Extraer configuración de telegram del objeto de configuración
            config["TELEGRAM_BOT_TOKEN"] = self.config_obj.telegram.bot_token
            config["TELEGRAM_CHAT_ID"] = self.config_obj.telegram.chat_id
        except AttributeError as e:
            self.logger.error(f"Error accediendo a configuración de Telegram: {e}")
            self.logger.error("Verifica que la configuración YAML tenga la sección 'telegram' con 'bot_token' y 'chat_id'")
            raise ValueError(f"Configuración de Telegram inválida: {e}")

        # Verifica configuración requerida
        required = ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"]
        for req in required:
            if req not in config or not config[req]:
                self.logger.error(f"Configuración faltante: {req}")
                raise ValueError(f"Configuración requerida faltante: {req}")

        return config

    def _load_queue(self) -> List[Dict]:
        """Cargar cola de mensajes pendientes"""
        if not self.queue_file.exists():
            return []
        try:
            with open(self.queue_file, "r") as f:
                return json.load(f)
        except Exception as e:
            self.logger.warning(f"Error cargando cola de notificaciones: {e}")
            return []

    def _save_queue(self, queue: List[Dict]) -> None:
        """Guardar cola de mensajes pendientes"""
        try:
            with open(self.queue_file, "w") as f:
                json.dump(queue, f, indent=2)
        except Exception as e:
            self.logger.error(f"Error guardando cola de notificaciones: {e}")

    def _add_to_queue(self, message: str, parse_mode: Optional[str] = None) -> None:
        """Agregar mensaje a la cola"""
        queue = self._load_queue()
        queue.append(
            {"message": message, "parse_mode": parse_mode, "timestamp": datetime.now().isoformat(), "attempts": 0}
        )
        self._save_queue(queue)
        self.logger.info(f"Mensaje agregado a la cola (total: {len(queue)})")

    def _process_queue(self) -> None:
        """Procesar cola de mensajes pendientes"""
        queue = self._load_queue()
        if not queue:
            return

        processed = []
        for item in queue:
            if self._send_message_immediate(item["message"], item["parse_mode"]):
                processed.append(item)
                self.logger.info("Mensaje pendiente enviado exitosamente")
            else:
                item["attempts"] += 1
                # Si ha fallado más de 5 veces, descartar
                if item["attempts"] >= 5:
                    self.logger.warning(f"Mensaje descartado después de {item['attempts']} intentos")
                    processed.append(item)
                else:
                    self.logger.warning(f"Mensaje pendiente falló (intento {item['attempts']})")

        # Remover mensajes procesados
        remaining = [item for item in queue if item not in processed]
        self._save_queue(remaining)

        if remaining:
            self.logger.info(f"Quedan {len(remaining)} mensajes en cola")

    def _send_message_immediate(self, message: str, parse_mode: Optional[str] = None) -> bool:
        """Enviar mensaje inmediatamente sin cola"""
        try:
            import requests  # type: ignore
        except ImportError:
            self.logger.error("requests library no disponible")
            return False

        if "TELEGRAM_BOT_TOKEN" not in self.config or "TELEGRAM_CHAT_ID" not in self.config:
            self.logger.error("Credenciales de Telegram no configuradas")
            return False

        url = f"https://api.telegram.org/bot{self.config['TELEGRAM_BOT_TOKEN']}/sendMessage"

        data = {"chat_id": self.config["TELEGRAM_CHAT_ID"], "text": message}

        if parse_mode:
            data["parse_mode"] = parse_mode

        try:
            response = requests.post(url, data=data, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()

            result = response.json()
            if result.get("ok"):
                return True
            else:
                self.logger.error(f"Error en respuesta de Telegram: {result}")
                return False

        except requests.RequestException as e:
            self.logger.error(f"Error enviando mensaje a Telegram: {e}")
            return False

    def send_telegram_message(self, message: str, parse_mode: Optional[str] = None) -> bool:
        """Enviar mensaje a Telegram con cola de respaldo"""
        # Primero procesar mensajes pendientes
        self._process_queue()

        # Intentar enviar el mensaje actual
        if self._send_message_immediate(message, parse_mode):
            return True

        # Si falla, agregar a la cola
        self._add_to_queue(message, parse_mode)
        return False

    def send_long_message(self, message: str) -> bool:
        """Enviar mensaje largo dividiéndolo en partes si es necesario"""
        max_length = 4096
        chunk_size = 3000

        if len(message) <= max_length:
            return self.send_telegram_message(message)

        # Divide mensaje en chunks
        start = 0
        part_num = 1

        while start < len(message):
            end = start + chunk_size
            if end > len(message):
                end = len(message)

            chunk = message[start:end]

            # Agrega indicador de parte si hay múltiples
            if len(message) > chunk_size:
                chunk = f"(Parte {part_num}) {chunk}"

            if not self.send_telegram_message(chunk):
                return False

            start = end
            part_num += 1

            # Pausa para evitar rate limits
            if start < len(message):
                time.sleep(0.5)

        return True

    def _get_progress_info(self) -> Dict:
        """Obtener información del progreso desde progress.json"""
        progress_file = self.scripts_dir / "tmp" / "progress.json"
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
            self.logger.warning(f"Error leyendo progress.json: {e}")

        return {
            "current_file": 0,
            "total_files": 0,
            "current_file_name": "N/A",
            "percentage": 0,
            "status": "unknown",
            "stats": {},
        }

    def _get_subtitle_progress_info(self) -> Dict:
        """Obtener información del progreso de subtítulos desde progress.json"""
        progress_file = self.scripts_dir / "tmp" / "progress.json"
        try:
            if progress_file.exists():
                with open(progress_file, "r") as f:
                    data = json.load(f)

                    # Si existe la sección subtitle_translation, devolver esa información
                    if "subtitle_translation" in data:
                        subtitle_data = data["subtitle_translation"].copy()
                        # Asegura que tenga el campo status
                        if "status" not in subtitle_data:
                            subtitle_data["status"] = "unknown"
                        return subtitle_data
        except Exception as e:
            self.logger.warning(f"Error leyendo progress.json para subtítulos: {e}")

        return {
            "current_file": 0,
            "total_files": 0,
            "current_file_name": "N/A",
            "percentage": 0,
            "status": "unknown",
            "stats": {},
        }

    def _get_compression_summary(self) -> str:
        """Obtener resumen de compresión adicional (solo detalles extras, no duplicar info principal)"""
        # Esta función ahora solo devuelve información adicional que no está en la notificación principal
        # Ya no duplicamos estadísticas básicas
        return ""

    def _get_error_summary(self) -> str:
        """Obtener resumen de errores"""
        error_tmp = self.scripts_dir / "tmp" / "error_files.tmp"
        if not error_tmp.exists():
            return ""

        with open(error_tmp, "r") as f:
            errors = [line.strip() for line in f if line.strip()]

        if not errors:
            return ""

        summary = f"{EmojiGenerator.error()} Errores encontrados: {len(errors)}\n"
        # Muestra hasta 5 errores
        for error in errors[:5]:
            summary += f"• {error}\n"

        if len(errors) > 5:
            summary += f"... y {len(errors) - 5} más\n"

        return summary + "\n"

    def _get_no_spanish_summary(self) -> str:
        """Obtener resumen de archivos sin español"""
        no_spanish_tmp = self.scripts_dir / "tmp" / "no_spanish_files.tmp"
        if not no_spanish_tmp.exists():
            return ""

        with open(no_spanish_tmp, "r") as f:
            no_spanish = [line.strip() for line in f if line.strip()]

        if not no_spanish:
            return ""

        summary = f"{EmojiGenerator.earth()} Archivos sin español detectados: {len(no_spanish)}\n\n"
        # Muestra hasta 10 archivos
        for file_name in no_spanish[:10]:
            summary += f"• {file_name}\n"

        if len(no_spanish) > 10:
            summary += f"... y {len(no_spanish) - 10} más\n"

        return summary

    def create_processing_summary(self) -> str:
        """Crear resumen de procesamiento usando archivos temporales"""
        summary = ""

        # Resumen del log de compresión
        summary += self._get_compression_summary()

        # Errores de la ejecución actual
        summary += self._get_error_summary()

        # Archivos sin español de la ejecución actual
        summary += self._get_no_spanish_summary()

        return summary

    def has_new_processing(self) -> bool:
        """Verificar si hubo procesamiento nuevo"""
        success_log = self.scripts_dir / "logs" / "compression-success.log"
        pending_file = self.scripts_dir / "pending-compression.txt"
        completed_file = self.scripts_dir / "completed.txt"

        # Estado actual
        current_pending = 0
        current_completed = 0
        current_last_log = ""

        if pending_file.exists():
            with open(pending_file, "r") as f:
                current_pending = len([line for line in f if line.strip()])

        if completed_file.exists():
            with open(completed_file, "r") as f:
                current_completed = len([line for line in f if line.strip()])

        if success_log.exists():
            with open(success_log, "r") as f:
                lines = f.readlines()
                if lines:
                    current_last_log = lines[-1].strip()

        # Estado anterior
        previous_state = {"pending": 0, "completed": 0, "last_log": ""}
        if self.state_file.exists():
            try:
                with open(self.state_file, "r") as f:
                    lines = f.readlines()
                    if len(lines) >= 3:
                        previous_state["pending"] = int(lines[0].strip())
                        previous_state["completed"] = int(lines[1].strip())
                        previous_state["last_log"] = lines[2].strip()
            except (ValueError, IndexError):
                pass

        # Guarda estado actual
        try:
            with open(self.state_file, "w") as f:
                f.write(f"{current_pending}\n")
                f.write(f"{current_completed}\n")
                f.write(f"{current_last_log}\n")
        except PermissionError as e:
            self.logger.warning(f"No se pudo guardar el estado de notificación: {e}")

        # Determina si hubo cambios
        has_changes = current_completed != previous_state["completed"] or current_last_log != previous_state["last_log"]

        return has_changes

    def _build_scan_only_message(self, files_found: int, files_new: int, progress: dict) -> str:
        """Construye mensaje para escaneo sin procesamiento"""
        message = f"{EmojiGenerator.info()} MediaJelly - Escaneo Completado\n\n"
        message += f"{EmojiGenerator.folder()} Archivos encontrados: {files_found}\n"
        message += f"{EmojiGenerator.new()} Archivos nuevos: {files_new}\n\n"

        if progress.get("status") == "processing":
            message += f"{EmojiGenerator.gear()} Estado: En procesamiento ({progress.get('percentage', 0):.1f}%)\n"
            if progress.get("current_file_name"):
                message += f"{EmojiGenerator.memo()} Procesando: {progress.get('current_file_name')}\n\n"
        else:
            message += f"{EmojiGenerator.info()} No se encontraron archivos nuevos para procesar\n"
            message += f"{EmojiGenerator.success()} El sistema está al día\n\n"

        message += f"{EmojiGenerator.time()} Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        return message

    def _build_processing_message(
        self,
        status: str,
        files_found: int,
        files_new: int,
        files_processed: int,
        files_compressed: int,
        files_renamed: int,
        files_skipped: int,
        progress: dict,
        subtitles_translated: int = 0,
        subtitles_errors: int = 0,
    ) -> str:
        """Construye mensaje para procesamiento completado"""
        message = self._build_message_header(status)
        message += self._build_file_info_section(files_found, files_new)
        message += self._build_processing_stats_section(
            files_processed, files_compressed, files_renamed, files_skipped, progress
        )
        message += self._build_subtitle_info_section(subtitles_translated, subtitles_errors)
        message += self._build_progress_info_section(progress)
        message += self._build_timestamp_section()
        return message

    def _build_message_header(self, status: str) -> str:
        """Construye el encabezado del mensaje"""
        emoji = EmojiGenerator.success() if status == "success" else EmojiGenerator.error()
        title = (
            "MediaJelly - Procesamiento Completado" if status == "success" else "MediaJelly - Error en Procesamiento"
        )
        return f"{emoji} {title}\n\n"

    def _build_file_info_section(self, files_found: int, files_new: int) -> str:
        """Construye la sección de información básica de archivos"""
        return (
            f"{EmojiGenerator.folder()} Archivos encontrados: {files_found}\n"
            f"{EmojiGenerator.new()} Archivos nuevos: {files_new}\n"
        )

    def _build_processing_stats_section(
        self, files_processed: int, files_compressed: int, files_renamed: int, files_skipped: int, progress: dict
    ) -> str:
        """Construye la sección de estadísticas de procesamiento"""
        if not (files_processed > 0 or files_compressed > 0 or files_renamed > 0 or files_skipped > 0):
            return ""

        message = (
            f"{EmojiGenerator.gear()} Archivos procesados: {files_processed}\n"
            f"{EmojiGenerator.compression()} Comprimidos: {files_compressed}\n"
            f"{EmojiGenerator.memo()} Renombrados: {files_renamed}\n"
            f"{EmojiGenerator.next_track()} Omitidos: {files_skipped}\n"
        )

        # Calcular promedio de espacio ahorrado
        progress_stats = progress.get("stats", {})
        total_original = progress_stats.get("total_original_size", 0)
        total_compressed = progress_stats.get("total_compressed_size", 0)
        compressed_files = progress_stats.get("files_compressed", 0)

        if compressed_files > 0 and total_original > total_compressed:
            space_saved = total_original - total_compressed
            avg_space_saved = space_saved / compressed_files
            avg_space_saved_mb = avg_space_saved / (1024 * 1024)
            message += f"{EmojiGenerator.chart()} Promedio ahorrado: {avg_space_saved_mb:.1f} MB por archivo\n"

        return message

    def _build_subtitle_info_section(self, subtitles_translated: int, subtitles_errors: int) -> str:
        """Construye la sección de información de subtítulos"""
        if subtitles_translated == 0 and subtitles_errors == 0:
            return ""

        message = f"\n{EmojiGenerator.earth()} Subtítulos traducidos: {subtitles_translated}\n"
        if subtitles_errors > 0:
            message += f"{EmojiGenerator.warning()} Errores en traducción: {subtitles_errors}\n"

        # Indicar estado de traducción
        if subtitles_translated > 0 and subtitles_errors == 0:
            message += f"{EmojiGenerator.success()} Traducción completada sin errores\n"
        elif subtitles_translated > 0 and subtitles_errors > 0:
            message += f"{EmojiGenerator.warning()} Traducción parcial (algunos errores)\n"
        elif subtitles_errors > 0:
            message += f"{EmojiGenerator.error()} Traducción falló\n"

        return message

    def _build_progress_info_section(self, progress: dict) -> str:
        """Construye la sección de información de progreso"""
        if progress.get("status") != "processing" or progress.get("percentage", 0) >= 100:
            return ""

        message = f"\n{EmojiGenerator.stats()} Progreso: {progress.get('percentage', 0):.1f}%\n"
        if progress.get("current_file_name"):
            message += f"{EmojiGenerator.gear()} Procesando: {progress.get('current_file_name')}\n"
        return message

    def _build_timestamp_section(self) -> str:
        """Construye la sección de timestamp"""
        return f"\n{EmojiGenerator.time()} Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"

    def _add_error_summaries(self, message: str) -> str:
        """Agrega resúmenes de errores y archivos sin español al mensaje"""
        error_summary = self._get_error_summary()
        no_spanish_summary = self._get_no_spanish_summary()

        if error_summary or no_spanish_summary:
            message += "\n"
            if error_summary:
                message += error_summary
            if no_spanish_summary:
                message += no_spanish_summary

        return message

    def notify_scan_result(
        self,
        status: str,
        files_found: int,
        files_new: int,
        files_processed: int,
        files_compressed: int,
        files_renamed: int,
        files_skipped: int,
        subtitles_translated: int = 0,
        subtitles_errors: int = 0,
    ) -> bool:
        """Notificar resultado del escaneo"""

        # Obtiene información del progreso
        progress = self._get_progress_info()
        progress_stats = progress.get("stats", {})

        # Usa las estadísticas del progress.json si están disponibles
        if progress_stats:
            files_found = progress_stats.get("files_found", files_found)
            files_new = progress_stats.get("files_new", files_new)
            files_processed = progress_stats.get("files_processed", files_processed)
            files_compressed = progress_stats.get("files_compressed", files_compressed)
            files_renamed = progress_stats.get("files_renamed", files_renamed)
            files_skipped = progress_stats.get("files_skipped", files_skipped)

        # Verifica si hubo procesamiento nuevo
        has_processing = self.has_new_processing()

        # Si no hay archivos procesados, es solo un escaneo
        if files_processed == 0 and not has_processing:
            message = self._build_scan_only_message(files_found, files_new, progress)
            return self.send_long_message(message)

        # Hubo procesamiento
        message = self._build_processing_message(
            status,
            files_found,
            files_new,
            files_processed,
            files_compressed,
            files_renamed,
            files_skipped,
            progress,
            subtitles_translated,
            subtitles_errors,
        )

        # Agrega resúmenes de errores
        message = self._add_error_summaries(message)

        return self.send_long_message(message)

    def notify_start_processing(self, files_found: int, files_new: int) -> bool:
        """Notificar inicio de procesamiento"""
        # Obtiene información del progreso actual
        progress = self._get_progress_info()
        progress_stats = progress.get("stats", {})

        # Usa estadísticas del progress.json si están disponibles
        if progress_stats:
            files_found = progress_stats.get("files_found", files_found)
            files_new = progress_stats.get("files_new", files_new)
            files_processed = progress_stats.get("files_processed", 0)
            files_compressed = progress_stats.get("files_compressed", 0)
            files_renamed = progress_stats.get("files_renamed", 0)
            files_skipped = progress_stats.get("files_skipped", 0)

        message = f"{EmojiGenerator.gear()} MediaJelly - Estado Anterior del Procesamiento\n\n"
        message += f"{EmojiGenerator.info()} Estado actual:\n"
        message += f"{EmojiGenerator.folder()} Archivos encontrados: {files_found}\n"
        message += f"{EmojiGenerator.new()} Archivos nuevos: {files_new}\n"

        if progress_stats:
            message += f"{EmojiGenerator.gear()} Archivos procesados: {files_processed}\n"
            message += f"{EmojiGenerator.compression()} Comprimidos: {files_compressed}\n"
            message += f"{EmojiGenerator.memo()} Renombrados: {files_renamed}\n"
            message += f"{EmojiGenerator.next_track()} Omitidos: {files_skipped}\n"

        message += f"\n{EmojiGenerator.time()} Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

        return self.send_long_message(message)

    def notify_night_subtitles(self, translated: int, skipped: int, errors: int) -> bool:
        """Notificar resultado del procesamiento nocturno de subtítulos"""
        total_processed = translated + skipped

        message = f"{EmojiGenerator.moon()} MediaJelly - Procesamiento Nocturno de Subtítulos\n\n"

        if total_processed > 0:
            message += f"{EmojiGenerator.folder()} Archivos procesados: {total_processed}\n"
            if translated > 0:
                message += f"{EmojiGenerator.translate()} Traducidos: {translated}\n"
            if skipped > 0:
                message += f"{EmojiGenerator.next_track()} Omitidos: {skipped}\n"
        else:
            message += f"{EmojiGenerator.info()} No se procesaron subtítulos nuevos\n"

        if errors > 0:
            message += f"{EmojiGenerator.warning()} Errores: {errors}\n"

        message += f"\n{EmojiGenerator.time()} Hora: {datetime.now().strftime('%H:%M')} ({datetime.now().strftime('%Y-%m-%d')})"
        message += f"\n{EmojiGenerator.gear()} Procesamiento automático nocturno completado"

        return self.send_long_message(message)

    def notify_subtitle_translation(self, stats: dict) -> bool:
        """Notificar resultado de traducción de subtítulos"""
        message = f"{EmojiGenerator.memo()} MediaJelly - Traducción de Subtítulos\n\n"

        message += f"{EmojiGenerator.folder()} Archivos analizados: {stats['total_files']}\n"

        if stats["with_spanish_audio"] > 0:
            message += f"{EmojiGenerator.audio()} Con audio español: {stats['with_spanish_audio']}\n"

        if stats["with_spanish_subs"] > 0:
            message += f"{EmojiGenerator.check()} Ya tenían subtítulos español: {stats['with_spanish_subs']}\n"

        if stats["extracted"] > 0:
            message += f"{EmojiGenerator.extract()} Subtítulos extraídos: {stats['extracted']}\n"

        if stats["translated"] > 0:
            message += f"{EmojiGenerator.translate()} Subtítulos traducidos: {stats['translated']}\n"

        if stats["errors"] > 0:
            message += f"{EmojiGenerator.warning()} Errores: {stats['errors']}\n"

        # Información adicional sobre subtítulos existentes procesados
        if "subtitle_check" in stats:
            sub_check = stats["subtitle_check"]
            if sub_check["translated_subtitles"] > 0:
                message += f"{EmojiGenerator.refresh()} Subtítulos existentes traducidos: {sub_check['translated_subtitles']}\n"

        message += f"\n{EmojiGenerator.time()} Completado: {datetime.now().strftime('%H:%M')} ({datetime.now().strftime('%Y-%m-%d')})"

        # Enviar notificación si se procesaron archivos o hubo actividad de traducción
        has_processed_files = stats.get("total_files", 0) > 0
        has_translation_activity = (
            stats.get("extracted", 0) > 0
            or stats.get("translated", 0) > 0
            or (stats.get("subtitle_check", {}).get("translated_subtitles", 0) > 0)
        )

        if has_processed_files or has_translation_activity:
            return self.send_long_message(message)
        else:
            self.logger.info("No se enviaron notificaciones de subtítulos - no hay archivos procesados ni actividad")
            return True

    def notify_no_pending_files(self, total_files: int, completed_files: int) -> bool:
        """Notificar cuando no hay archivos pendientes"""
        message = f"{EmojiGenerator.info()} MediaJelly - Escaneo Completado\n\n"
        message += f"{EmojiGenerator.folder()} Total de archivos: {total_files}\n"
        message += f"{EmojiGenerator.success()} Archivos completados: {completed_files}\n"
        message += f"{EmojiGenerator.clipboard()} Archivos pendientes: 0\n\n"
        message += f"{EmojiGenerator.info()} No hay archivos pendientes para procesar\n"
        message += f"{EmojiGenerator.party()} ¡Todo está actualizado!\n\n"
        message += f"{EmojiGenerator.time()} Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

        return self.send_long_message(message)

    def notify_critical_error(self, error_message: str, log_file: Optional[str] = None) -> bool:
        """Notificar errores críticos"""
        message = f"{EmojiGenerator.siren()} MediaJelly - Error Crítico\n\n"
        message += f"{EmojiGenerator.error()} Error: {error_message}\n"
        message += f"{EmojiGenerator.time()} Fecha: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

        if log_file and Path(log_file).exists():
            message += f"\n\n{EmojiGenerator.scroll()} Últimas líneas del log:\n"
            with open(log_file, "r") as f:
                lines = f.readlines()
                last_lines = lines[-3:] if len(lines) >= 3 else lines
                for line in last_lines:
                    message += f"• {line.strip()}\n"

        return self.send_long_message(message)

    def send_test_message(self) -> bool:
        """Enviar mensaje de prueba"""
        message = f"{EmojiGenerator.test_tube()} MediaJelly - Prueba de notificación\n\n"
        message += "Si recibes este mensaje, las notificaciones están funcionando correctamente.\n\n"
        message += f"{EmojiGenerator.time()} {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

        return self.send_long_message(message)

    def process_pending_notifications(self) -> bool:
        """Procesar notificaciones pendientes manualmente"""
        self._process_queue()
        queue = self._load_queue()
        return len(queue) == 0

    def reset_state(self) -> bool:
        """Resetear el estado de notificaciones"""
        try:
            if self.state_file.exists():
                self.state_file.unlink()
                self.logger.info("Estado de notificaciones reseteado")
            return True
        except Exception as e:
            self.logger.error(f"Error reseteando estado: {e}")
            return False

    def notify_completed_cleanup(self, files_checked: int, files_removed: int, files_kept: int) -> bool:
        """Notificar limpieza del archivo completed.txt"""
        message = f"{EmojiGenerator.cleanup()} MediaJelly - Limpieza de archivos completados\n\n"
        message += f"{EmojiGenerator.stats()} Archivos verificados: {files_checked}\n"
        message += f"{EmojiGenerator.wastebasket()} Archivos eliminados: {files_removed}\n"
        message += f"{EmojiGenerator.success()} Archivos mantenidos: {files_kept}\n\n"
        message += f"{EmojiGenerator.time()} {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

        return self.send_long_message(message)

    def notify_cleanup_result(self, removed_count: int, remaining_count: int, current_files_processed: int) -> bool:
        """Notificar resultado de limpieza semanal de archivos completados"""
        message = f"{EmojiGenerator.cleanup()} MediaJelly - Limpieza semanal de completados\n\n"
        message += f"{EmojiGenerator.stats()} Total archivos procesados: {current_files_processed}\n"
        message += f"{EmojiGenerator.wastebasket()} Entradas inexistentes eliminadas: {removed_count}\n"
        message += f"{EmojiGenerator.success()} Entradas válidas restantes: {remaining_count}\n\n"
        message += f"{EmojiGenerator.time()} {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

        return self.send_long_message(message)


def main():
    """Función principal"""
    if len(sys.argv) < 2:
        print(
            "Uso: mediajelly_notifier.py {start_processing|scan_result|no_pending|critical_error|test|reset_state|completed_cleanup|night_subtitles|subtitle_translation|process_queue} [argumentos...]"
        )
        sys.exit(1)

    notifier = TelegramNotifier()
    command = sys.argv[1]

    # Diccionario de comandos con sus validaciones y ejecuciones
    command_handlers = {
        "start_processing": _handle_start_processing,
        "scan_result": _handle_scan_result,
        "no_pending": _handle_no_pending,
        "critical_error": _handle_critical_error,
        "test": _handle_test,
        "reset_state": _handle_reset_state,
        "completed_cleanup": _handle_completed_cleanup,
        "cleanup_result": _handle_cleanup_result,
        "night_subtitles": _handle_night_subtitles,
        "subtitle_translation": _handle_subtitle_translation,
        "process_queue": _handle_process_queue,
    }

    if command in command_handlers:
        success = command_handlers[command](notifier, sys.argv)
    else:
        print("Comando no válido")
        sys.exit(1)

    sys.exit(0 if success else 1)


def _handle_start_processing(notifier: TelegramNotifier, args: list) -> bool:
    """Maneja el comando start_processing"""
    if len(args) < 4:
        print("Error: start_processing requiere 2 argumentos (files_found, files_new)")
        return False
    files_found = int(args[2])
    files_new = int(args[3])
    return notifier.notify_start_processing(files_found, files_new)


def _handle_scan_result(notifier: TelegramNotifier, args: list) -> bool:
    """Maneja el comando scan_result"""
    if len(args) < 9:
        print("Error: scan_result requiere 7 argumentos")
        return False
    status = args[2]
    files_found = int(args[3])
    files_new = int(args[4])
    files_processed = int(args[5])
    files_compressed = int(args[6])
    files_renamed = int(args[7])
    files_skipped = int(args[8])

    return notifier.notify_scan_result(
        status, files_found, files_new, files_processed, files_compressed, files_renamed, files_skipped
    )


def _handle_no_pending(notifier: TelegramNotifier, args: list) -> bool:
    """Maneja el comando no_pending"""
    if len(args) < 4:
        print("Error: no_pending requiere 2 argumentos (total_files, completed_files)")
        return False
    total_files = int(args[2])
    completed_files = int(args[3])
    return notifier.notify_no_pending_files(total_files, completed_files)


def _handle_critical_error(notifier: TelegramNotifier, args: list) -> bool:
    """Maneja el comando critical_error"""
    if len(args) < 3:
        print("Error: critical_error requiere al menos 1 argumento (error_message)")
        return False
    error_message = args[2]
    log_file = args[3] if len(args) > 3 else None
    return notifier.notify_critical_error(error_message, log_file)


def _handle_test(notifier: TelegramNotifier, args: list) -> bool:
    """Maneja el comando test"""
    return notifier.send_test_message()


def _handle_reset_state(notifier: TelegramNotifier, args: list) -> bool:
    """Maneja el comando reset_state"""
    return notifier.reset_state()


def _handle_completed_cleanup(notifier: TelegramNotifier, args: list) -> bool:
    """Maneja el comando completed_cleanup"""
    if len(args) < 5:
        print("Error: completed_cleanup requiere 3 argumentos")
        return False
    files_checked = int(args[2])
    files_removed = int(args[3])
    files_kept = int(args[4])
    return notifier.notify_completed_cleanup(files_checked, files_removed, files_kept)


def _handle_cleanup_result(notifier: TelegramNotifier, args: list) -> bool:
    """Maneja el comando cleanup_result para limpieza periódica de completados"""
    if len(args) < 5:
        print("Error: cleanup_result requiere 3 argumentos (removed_count, remaining_count, current_files_processed)")
        return False
    removed_count = int(args[2])
    remaining_count = int(args[3])
    current_files_processed = int(args[4])
    return notifier.notify_cleanup_result(removed_count, remaining_count, current_files_processed)


def _handle_night_subtitles(notifier: TelegramNotifier, args: list) -> bool:
    """Maneja el comando night_subtitles"""
    if len(args) < 5:
        print("Error: night_subtitles requiere 3 argumentos: <translated> <skipped> <errors>")
        return False
    try:
        translated = int(args[2])
        skipped = int(args[3])
        errors = int(args[4])
    except ValueError:
        print("Error: Los argumentos deben ser números enteros")
        return False

    return notifier.notify_night_subtitles(translated, skipped, errors)


def _handle_subtitle_translation(notifier: TelegramNotifier, args: list) -> bool:
    """Maneja el comando subtitle_translation"""
    if len(args) < 8:
        print(
            "Error: subtitle_translation requiere 6 argumentos: total_files extracted translated with_spanish_audio with_spanish_subs errors"
        )
        return False

    # Los argumentos son: total_files, extracted, translated, with_spanish_audio, with_spanish_subs, errors
    try:
        stats = {
            "total_files": int(args[2]),  # args[2] porque args[0]=script, args[1]=command
            "extracted": int(args[3]),
            "translated": int(args[4]),
            "with_spanish_audio": int(args[5]),
            "with_spanish_subs": int(args[6]),
            "errors": int(args[7]),
        }
        return notifier.notify_subtitle_translation(stats)
    except (ValueError, IndexError) as e:
        print(f"Error procesando argumentos: {e}")
        return False
        print(f"Error parsing subtitle_translation arguments: {e}")
        return False


def _handle_process_queue(notifier: TelegramNotifier, args: list) -> bool:
    """Maneja el comando process_queue"""
    return notifier.process_pending_notifications()


if __name__ == "__main__":
    main()
