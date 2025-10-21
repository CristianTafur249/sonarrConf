#!/usr/bin/env python3
"""
MediaJelly Cron Runner Python - Orquestador principal optimizado
"""

import os
import sys
import time
import json
import fcntl
import signal
import subprocess
from pathlib import Path
from datetime import datetime
import logging
from typing import Optional

class MediaJellyCronRunner:
    """Orquestador principal de MediaJelly"""
    
    PROGRESS_FILE_NAME = "progress.json"
    
    def __init__(self):
        # Detecta el entorno
        self.is_container = Path("/mediajelly").exists()
        self.base_dir = Path("/mediajelly" if self.is_container else "/home/tafurc/mediaJelly")
        self.scripts_dir = self.base_dir / "scripts"
        self.media_dir = self.base_dir / "media"
        self.logs_dir = self.scripts_dir / "logs"
        self.tmp_dir = self.scripts_dir / "tmp"
        
        # Archivos
        self.lockfile = self.tmp_dir / "cron_python.lock"
        self.log_file = self.logs_dir / "cron_python.log"
        self.pending_subtitles_file = self.scripts_dir / "pending_subtitles.txt"
        
        # Scripts Python
        self.scanner_script = self.scripts_dir / "mediajelly_scanner.py"
        self.processor_script = self.scripts_dir / "mediajelly_processor.py"
        self.notifier_script = self.scripts_dir / "mediajelly_notifier.py"
        self.subtitle_translator_script = self.scripts_dir / "mediajelly_subtitle_translator.py"
        
        # Crea directorios
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        
    # Configura logging
        self.setup_logging()
        
        # Variables de estado
        self.scan_success = False
        self.stats = {
            'files_found': 0,
            'files_new': 0,
            'files_processed': 0,
            'files_compressed': 0,
            'files_renamed': 0,
            'files_skipped': 0,
            'subtitles_translated': 0,
            'subtitles_errors': 0
        }
    
    def setup_logging(self):
        """Configura el logging estructurado con rotación automática"""
        import time
        
        # Configura logging con zona horaria local
        class LocalTimeFormatter(logging.Formatter):
            def formatTime(self, record, datefmt=None):
                dt = datetime.fromtimestamp(record.created)
                if datefmt:
                    return dt.strftime(datefmt)
                return dt.strftime('%Y-%m-%d %H:%M:%S')
        
        formatter = LocalTimeFormatter('[%(asctime)s] %(message)s', '%Y-%m-%d %H:%M:%S')
        
        # Handler para archivo
        file_handler = logging.FileHandler(self.log_file)
        file_handler.setFormatter(formatter)
        
        # Handler para consola
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        
        # Configura el logger
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.INFO)
        self.logger.handlers.clear()
        self.logger.addHandler(file_handler)
        self.logger.addHandler(console_handler)
    
    def acquire_lock(self) -> bool:
        """Adquiere un lock exclusivo para evitar ejecuciones simultáneas"""
        try:
            # Si el archivo de lock existe, verificar si el proceso está activo
            if self.lockfile.exists():
                try:
                    with open(self.lockfile, 'r') as f:
                        pid = f.read().strip()
                        if pid and pid.isdigit():
                            pid = int(pid)
                            # Verificar si el proceso existe
                            try:
                                os.kill(pid, 0)  # No mata el proceso, solo verifica si existe
                                self.logger.error(f"Ya hay una instancia ejecutándose (PID: {pid})")
                                return False
                            except OSError:
                                # El proceso no existe, el lock está huérfano
                                self.logger.info(f"Lock huérfano detectado (PID {pid} no existe), limpiando...")
                                self.lockfile.unlink()
                except Exception as e:
                    self.logger.warning(f"Error al verificar lock existente: {e}, recreando lock...")
                    self.lockfile.unlink(missing_ok=True)
            
            # Crear el archivo de lock con el PID actual
            self.lock_fd = open(self.lockfile, 'w')
            fcntl.flock(self.lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.lock_fd.write(str(os.getpid()))
            self.lock_fd.flush()
            return True
        except (IOError, OSError) as e:
            self.logger.error(f"Error al adquirir lock: {e}")
            return False
    
    def release_lock(self):
        """Libera el lock si está adquirido"""
        try:
            if hasattr(self, 'lock_fd'):
                fcntl.flock(self.lock_fd.fileno(), fcntl.LOCK_UN)
                self.lock_fd.close()
            if self.lockfile.exists():
                self.lockfile.unlink()
        except Exception as e:
            self.logger.warning(f"Error liberando lock: {e}")
    
    def run_scanner(self) -> bool:
        """Ejecuta el scanner de archivos para detectar nuevos archivos multimedia"""
        try:
            self.logger.info(f"Iniciando escaneo de {self.media_dir}")
            
            # Ejecuta scanner Python
            cmd = [sys.executable, str(self.scanner_script), str(self.media_dir)]
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=14400  # 4 horas timeout
            )
            
            if result.returncode == 0:
                self.logger.info("Escaneo completado exitosamente")
                
                # Extrae estadísticas del output
                for line in result.stdout.strip().split('\n'):
                    if "Archivos encontrados:" in line:
                        self.stats['files_found'] = int(line.split(':')[1].strip())
                    elif "Nuevas rutas detectadas:" in line:
                        self.stats['files_new'] = int(line.split(':')[1].strip())
                
                return True
            else:
                self.logger.error(f"Error en el escaneo (código {result.returncode})")
                if result.stderr:
                    self.logger.error(f"STDERR: {result.stderr}")
                return False
                
        except subprocess.TimeoutExpired:
            self.logger.error("Timeout en el escaneo (4 horas)")
            return False
        except Exception as e:
            self.logger.error(f"Error ejecutando scanner: {e}")
            return False
    
    def run_processor(self) -> bool:
        """Ejecuta el procesador de archivos para comprimir y renombrar archivos multimedia"""
        try:
            self.logger.info("Iniciando procesamiento de archivos")
            
            # Ejecuta procesador Python
            cmd = [sys.executable, str(self.processor_script)]
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=14400  # 4 horas timeout
            )
            
            if result.returncode == 0:
                self.logger.info("Procesamiento completado exitosamente")
                
                # Extrae estadísticas del output
                for line in result.stdout.strip().split('\n'):
                    if "Procesados:" in line:
                        self.stats['files_processed'] = int(line.split(':')[1].strip())
                    elif "Comprimidos:" in line:
                        self.stats['files_compressed'] = int(line.split(':')[1].strip())
                    elif "Renombrados:" in line:
                        self.stats['files_renamed'] = int(line.split(':')[1].strip())
                    elif "Omitidos:" in line:
                        self.stats['files_skipped'] = int(line.split(':')[1].strip())
                
                return True
            else:
                self.logger.error(f"Error en el procesamiento (código {result.returncode})")
                if result.stderr:
                    self.logger.error(f"STDERR: {result.stderr}")
                return False
                
        except subprocess.TimeoutExpired:
            self.logger.error("Timeout en el procesamiento (4 horas)")
            return False
        except Exception as e:
            self.logger.error(f"Error ejecutando procesador: {e}")
            return False
    
    def get_next_pending_file(self) -> Optional[str]:
        """Obtiene el siguiente archivo pendiente de procesamiento"""
        pending_file = self.scripts_dir / "pending-compression.txt"
        if not pending_file.exists():
            return None
        
        with open(pending_file, 'r') as f:
            lines = [line.strip() for line in f if line.strip()]
        
        if not lines:
            return None
        
        return lines[0]  # Retorna el primer archivo
    
    def get_next_pending_subtitle_file(self) -> Optional[str]:
        """Obtiene el siguiente archivo pendiente de traducción de subtítulos"""
        if not self.pending_subtitles_file.exists():
            return None
        
        with open(self.pending_subtitles_file, 'r') as f:
            lines = [line.strip() for line in f if line.strip()]
        
        if not lines:
            return None
        
        return lines[0]  # Retorna el primer archivo
    
    def add_to_pending_subtitles(self, file_path: str):
        """Agrega un archivo a la lista de pendientes de subtítulos"""
        with open(self.pending_subtitles_file, 'a') as f:
            f.write(f"{file_path}\n")
        self.logger.info(f"Archivo agregado a pendientes de subtítulos: {file_path}")
    
    def remove_from_pending_subtitles(self, file_path: str):
        """Remueve un archivo de pending_subtitles.txt"""
        if not self.pending_subtitles_file.exists():
            return
        
        with open(self.pending_subtitles_file, 'r') as f:
            pending_lines = [line.strip() for line in f if line.strip()]
        
        # Remover el archivo procesado
        pending_lines = [line for line in pending_lines if line != file_path]
        
        # Reescribir
        with open(self.pending_subtitles_file, 'w') as f:
            for line in pending_lines:
                f.write(f"{line}\n")
    
    def process_pending_subtitles(self) -> tuple[int, int]:
        """Procesa todos los archivos pendientes de subtítulos (modo nocturno)"""
        self.logger.info("=== Iniciando procesamiento nocturno de subtítulos ===")
        
        files_processed = 0
        files_with_errors = 0
        
        while True:
            # Obtener siguiente archivo pendiente de subtítulos
            next_file = self.get_next_pending_subtitle_file()
            if not next_file:
                break  # No hay más archivos
            
            self.logger.info(f"Procesando subtítulos para archivo {files_processed + 1}: {next_file}")
            
            # Procesar subtítulos para el archivo
            try:
                from mediajelly_subtitle_translator import SubtitleTranslator
                
                translator = SubtitleTranslator(max_workers=1, use_whisper=True)
                video_path = Path(next_file)
                result_dict = translator.process_video(video_path)
                
                if result_dict.get('error'):
                    self.logger.warning(f"Error procesando subtítulos para {next_file}: {result_dict['error']}")
                    files_with_errors += 1
                else:
                    self.logger.info(f"✓ Subtítulos procesados para: {next_file}")
                    files_processed += 1
                    
            except Exception as e:
                self.logger.error(f"Error procesando subtítulos para {next_file}: {e}")
                files_with_errors += 1
            
            # Remover de pendientes de subtítulos
            self.remove_from_pending_subtitles(next_file)
        
        self.logger.info(f"=== Procesamiento nocturno finalizado: {files_processed} subtítulos procesados, {files_with_errors} errores ===")
        return files_processed, files_with_errors
    
    def remove_from_pending_and_add_to_completed(self, file_path: str) -> None:
        """Remueve un archivo de pending-compression.txt y lo agrega a completed.txt"""
        pending_file = self.scripts_dir / "pending-compression.txt"
        completed_file = self.scripts_dir / "completed.txt"
        
        # Leer pending y remover el archivo
        if pending_file.exists():
            with open(pending_file, 'r') as f:
                pending_lines = [line.strip() for line in f if line.strip()]
            
            # Remover el archivo procesado
            pending_lines = [line for line in pending_lines if line != file_path]
            
            # Reescribir pending
            with open(pending_file, 'w') as f:
                for line in pending_lines:
                    f.write(f"{line}\n")
        
        # Agregar a completed
        with open(completed_file, 'a') as f:
            f.write(f"{file_path}\n")
    
    def process_single_file(self, file_path: str, process_subtitles: bool = True) -> bool:
        """Procesa un solo archivo: compresión + traducción de subtítulos (opcional)"""
        try:
            self.logger.info(f"Procesando archivo individual: {file_path}")
            
            # 1. Ejecutar procesador para este archivo específico
            cmd = [sys.executable, str(self.processor_script), file_path]
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=7200  # 2 horas timeout por archivo
            )
            
            if result.returncode != 0:
                self.logger.error(f"Error procesando {file_path} (código {result.returncode})")
                if result.stderr:
                    self.logger.error(f"STDERR: {result.stderr}")
                return False
            
            # 2. Verificar si el procesamiento fue exitoso consultando progress.json
            processed_successfully = self.check_file_processed_successfully(file_path)
            
            if not processed_successfully:
                self.logger.warning(f"Archivo {file_path} no fue procesado exitosamente, omitiendo traducción")
                return True  # No es error, solo no se procesó
            
            # 3. Si se debe procesar subtítulos, traducir; de lo contrario, agregar a pendientes
            if process_subtitles:
                self.logger.info(f"Traduciendo subtítulos para: {file_path}")
                
                # Importar y usar SubtitleTranslator directamente para mejor rendimiento
                try:
                    from mediajelly_subtitle_translator import SubtitleTranslator
                    
                    # Crear instancia con configuración optimizada para un solo archivo
                    translator = SubtitleTranslator(max_workers=1, use_whisper=True)
                    
                    # Procesar el archivo específico
                    video_path = Path(file_path)
                    result_dict = translator.process_video(video_path)
                    
                    if result_dict.get('error'):
                        self.logger.warning(f"Error en traducción de subtítulos para {file_path}: {result_dict['error']}")
                        return True  # No es error fatal, continuar con siguiente archivo
                    else:
                        self.logger.info(f"✓ Subtítulos procesados exitosamente para: {file_path}")
                        
                except ImportError as e:
                    self.logger.error(f"No se pudo importar SubtitleTranslator: {e}")
                    return False
                except Exception as e:
                    self.logger.error(f"Error procesando subtítulos para {file_path}: {e}")
                    return False
            else:
                # Agregar a pendientes de subtítulos para procesamiento nocturno
                self.add_to_pending_subtitles(file_path)
                self.logger.info(f"Archivo agregado a pendientes de subtítulos (procesamiento nocturno): {file_path}")
            
            # 4. Marcar como completado
            self.remove_from_pending_and_add_to_completed(file_path)
            self.logger.info(f"✓ Archivo procesado completamente: {file_path}")
            
            return True
            
        except subprocess.TimeoutExpired:
            self.logger.error(f"Timeout procesando archivo individual: {file_path}")
            return False
        except Exception as e:
            self.logger.error(f"Error procesando archivo individual {file_path}: {e}")
            return False
    
    def check_file_processed_successfully(self, file_path: str) -> bool:
        """Verifica si un archivo específico fue procesado exitosamente"""
        progress_file = self.tmp_dir / self.PROGRESS_FILE_NAME
        
        if not progress_file.exists():
            return False
        
        try:
            with open(progress_file, 'r') as f:
                progress_data = json.load(f)
            
            file_info = progress_data.get('processed_files', {}).get(file_path, {})
            status = file_info.get('status', '')
            
            return status in ['completed', 'renamed', 'success']
            
        except Exception as e:
            self.logger.warning(f"Error verificando procesamiento de {file_path}: {e}")
            return False
    
    def get_processed_files(self) -> list:
        """Obtiene la lista de archivos procesados desde progress.json"""
        processed_files = []
        progress_file = self.tmp_dir / self.PROGRESS_FILE_NAME
        
        if not progress_file.exists():
            return processed_files
        
        try:
            with open(progress_file, 'r') as f:
                progress_data = json.load(f)
            
            # Obtener archivos del diccionario processed_files
            for file_path, file_info in progress_data.get('processed_files', {}).items():
                # Solo incluir archivos que fueron procesados exitosamente o renombrados
                status = file_info.get('status', '')
                if status in ['completed', 'renamed', 'skipped', 'success']:
                    processed_files.append(file_path)
            
            self.logger.info(f"Archivos procesados para traducción: {len(processed_files)}")
            
        except Exception as e:
            self.logger.warning(f"Error obteniendo archivos procesados: {e}")
        
        return processed_files
    
    def run_subtitle_translator(self, file_list: list = None, max_workers: int = 2, use_whisper: bool = True) -> bool:
        """Ejecuta el traductor de subtítulos para archivos sin audio en español
        
        Args:
            file_list: Lista de archivos a procesar. Si es None, procesa toda la carpeta media.
            max_workers: Número de archivos a procesar simultáneamente (se ajusta a 1 con Whisper)
            use_whisper: Si usar Whisper para extraer audio (default: True)
        """
        try:
            # Ajustar max_workers automáticamente cuando se usa Whisper
            if use_whisper:
                max_workers = 1  # Whisper solo procesa 1 por 1
                self.logger.info("Whisper habilitado: Procesamiento secuencial (1 archivo por vez)")
            else:
                self.logger.info(f"Procesamiento concurrente: {max_workers} workers")
            
            self.logger.info("Iniciando traducción de subtítulos")
            
            # Construir comando
            cmd = [sys.executable, str(self.subtitle_translator_script)]
            cmd.extend(['--max-workers', str(max_workers)])
            if not use_whisper:
                cmd.append('--no-whisper')
            
            if file_list and len(file_list) > 0:
                # Procesar lista específica de archivos
                cmd.extend(file_list)
                self.logger.info(f"Procesando {len(file_list)} archivos específicos")
            else:
                # Procesar toda la carpeta
                cmd.append(str(self.media_dir))
                self.logger.info("Procesando toda la carpeta de medios")
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=7200  # 2 horas timeout
            )
            
            if result.returncode == 0:
                self.logger.info("Traducción de subtítulos completada")
                
                # Extrae estadísticas del output
                for line in result.stdout.strip().split('\n'):
                    if "Subtítulos traducidos:" in line:
                        self.stats['subtitles_translated'] = int(line.split(':')[1].strip())
                    elif "Errores:" in line:
                        self.stats['subtitles_errors'] = int(line.split(':')[1].strip())
                
                return True
            else:
                self.logger.error(f"Error en traducción de subtítulos (código {result.returncode})")
                if result.stderr:
                    self.logger.error(f"STDERR: {result.stderr}")
                # Capturar estadísticas incluso si hay error
                for line in result.stdout.strip().split('\n'):
                    if "Subtítulos traducidos:" in line:
                        self.stats['subtitles_translated'] = int(line.split(':')[1].strip())
                    elif "Errores:" in line:
                        self.stats['subtitles_errors'] = int(line.split(':')[1].strip())
                return False
                
        except subprocess.TimeoutExpired:
            self.logger.error("Timeout en traducción de subtítulos (2 horas)")
            return False
        except Exception as e:
            self.logger.error(f"Error ejecutando traductor de subtítulos: {e}")
            return False
    
    def _is_duplicate_success_notification(self) -> bool:
        """Verifica si la notificación de éxito es duplicada"""
        is_no_processing = (
            self.stats['files_processed'] == 0 and 
            self.stats['files_new'] == 0
        )
        if not is_no_processing:
            return False
        
        progress_file = self.tmp_dir / self.PROGRESS_FILE_NAME
        if not progress_file.exists():
            return False
        
        try:
            with open(progress_file, 'r') as f:
                progress_data = json.load(f)
            
            last_notif = progress_data.get('last_notification', {})
            return (last_notif.get('files_processed') == 0 and 
                    last_notif.get('files_new') == 0)
        except Exception as e:
            self.logger.warning(f"Error verificando última notificación: {e}")
            return False

    def send_notification(self, notification_type: str) -> bool:
        """Envía notificación vía Telegram según el tipo especificado"""
        try:
            # Verifica si es una notificación duplicada de completado sin procesamiento
            is_duplicate = False
            if notification_type == "success" and self._is_duplicate_success_notification():
                self.logger.info("Notificación duplicada de completado sin procesamiento, actualizando timestamp...")
                is_duplicate = True
            
            self.logger.info("Enviando notificación...")
            
            cmd = [
                sys.executable, str(self.notifier_script),
                "scan_result", notification_type,
                str(self.stats['files_found']),
                str(self.stats['files_new']),
                str(self.stats['files_processed']),
                str(self.stats['files_compressed']),
                str(self.stats['files_renamed']),
                str(self.stats['files_skipped']),
                str(self.stats['subtitles_translated']),
                str(self.stats['subtitles_errors'])
            ]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60
            )
            
            if result.returncode == 0:
                self.logger.info("Notificación enviada correctamente")
                
                # Marca como notificado en progress.json
                self._mark_as_notified()
                
                return True
            else:
                self.logger.error("Error al enviar notificación")
                # Aún marca como notificado para evitar reenvíos
                if not is_duplicate:
                    self._mark_as_notified()
                return False
                
        except Exception as e:
            self.logger.error(f"Error enviando notificación: {e}")
            return False
    
    def _mark_as_notified(self):
        """Marca el estado actual como notificado en progress.json"""
        try:
            progress_file = self.tmp_dir / self.PROGRESS_FILE_NAME
            if progress_file.exists():
                with open(progress_file, 'r') as f:
                    progress_data = json.load(f)
                
                progress_data['notified'] = True
                # Guarda la información de la última notificación
                progress_data['last_notification'] = {
                    'type': 'success',
                    'files_found': self.stats['files_found'],
                    'files_new': self.stats['files_new'],
                    'files_processed': self.stats['files_processed'],
                    'timestamp': datetime.now().isoformat()
                }
                
                with open(progress_file, 'w') as f:
                    json.dump(progress_data, f, indent=2)
                
                self.logger.info("Estado marcado como notificado (notified=True)")
        except Exception as e:
            self.logger.warning(f"Error marcando como notificado: {e}")
    
    def _mark_as_notified_night(self, subtitles_processed: int, subtitles_errors: int):
        """Marca el estado como notificado para night_subtitles en progress.json"""
        try:
            progress_file = self.tmp_dir / self.PROGRESS_FILE_NAME
            if progress_file.exists():
                with open(progress_file, 'r') as f:
                    progress_data = json.load(f)
                
                progress_data['notified'] = True
                # Guarda la información de la última notificación night_subtitles
                progress_data['last_notification'] = {
                    'type': 'night_subtitles',
                    'subtitles_processed': subtitles_processed,
                    'subtitles_errors': subtitles_errors,
                    'timestamp': datetime.now().isoformat()
                }
                
                with open(progress_file, 'w') as f:
                    json.dump(progress_data, f, indent=2)
                
                self.logger.info("Estado marcado como notificado night_subtitles (notified=True)")
        except Exception as e:
            self.logger.warning(f"Error marcando como notificado night_subtitles: {e}")
    
    def send_night_subtitle_notification(self, subtitles_processed: int, subtitles_errors: int) -> bool:
        """Envía notificación del procesamiento nocturno de subtítulos"""
        try:
            self.logger.info("Enviando notificación de procesamiento nocturno de subtítulos...")
            
            cmd = [
                sys.executable, str(self.notifier_script),
                "night_subtitles",
                str(subtitles_processed),
                str(subtitles_errors)
            ]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60
            )
            
            if result.returncode == 0:
                self.logger.info("Notificación de procesamiento nocturno enviada correctamente")
                self._mark_as_notified_night(subtitles_processed, subtitles_errors)
                return True
            else:
                self.logger.error("Error al enviar notificación de procesamiento nocturno")
                return False
                
        except Exception as e:
            self.logger.error(f"Error enviando notificación de procesamiento nocturno: {e}")
            return False
    
    def check_pending_files(self) -> bool:
        """Verifica si hay archivos pendientes para procesar"""
        pending_file = self.scripts_dir / "pending-compression.txt"
        if not pending_file.exists():
            return False
        
        with open(pending_file, 'r') as f:
            pending_lines = [line.strip() for line in f if line.strip()]
        
        return len(pending_lines) > 0
    
    def cleanup_completed_files(self) -> tuple[int, int, int]:
        """Limpia archivos que ya no existen del archivo completed.txt"""
        completed_file = self.scripts_dir / "completed.txt"
        if not completed_file.exists():
            self.logger.warning("Archivo completed.txt no encontrado")
            return 0, 0, 0
        
        self.logger.info("Iniciando limpieza del archivo completed.txt")
        
        # Lee todas las líneas del archivo
        with open(completed_file, 'r') as f:
            lines = [line.strip() for line in f if line.strip()]
        
        files_checked = len(lines)
        files_kept = 0
        files_removed = 0
        
        # Verifica cada archivo
        cleaned_lines = []
        for line in lines:
            if not line:
                continue
                
            file_path = Path(line)
            if file_path.exists():
                cleaned_lines.append(line)
                files_kept += 1
            else:
                self.logger.info(f"Archivo no encontrado, eliminando: {line}")
                files_removed += 1
        
        # Escribe el archivo limpio
        with open(completed_file, 'w') as f:
            for line in cleaned_lines:
                f.write(f"{line}\n")
        
        self.logger.info(f"Limpieza completada: {files_checked} verificados, {files_removed} eliminados, {files_kept} mantenidos")
        return files_checked, files_removed, files_kept
    
    def send_completed_cleanup_notification(self, files_checked: int, files_removed: int, files_kept: int) -> bool:
        """Envía una notificación de limpieza del archivo completed.txt"""
        try:
            self.logger.info("Enviando notificación de limpieza...")
            
            cmd = [
                sys.executable, str(self.notifier_script),
                "completed_cleanup",
                str(files_checked),
                str(files_removed),
                str(files_kept)
            ]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60
            )
            
            if result.returncode == 0:
                self.logger.info("Notificación de limpieza enviada correctamente")
                return True
            else:
                self.logger.error("Error al enviar notificación de limpieza")
                return False
                
        except Exception as e:
            self.logger.error(f"Error enviando notificación de limpieza: {e}")
            return False
                
    def should_cleanup(self) -> bool:
        """Verifica si debe ejecutarse la limpieza basada en la fecha de última ejecución"""
        last_cleanup_file = self.scripts_dir / "last_cleanup.txt"
        
        if not last_cleanup_file.exists():
            self.logger.info("No existe archivo de última limpieza, ejecutando limpieza")
            return True
        
        try:
            with open(last_cleanup_file, 'r') as f:
                last_date_str = f.read().strip()
            
            last_date = datetime.fromisoformat(last_date_str)
            now = datetime.now()
            
            # Verifica si han pasado al menos 60 días (aproximadamente 2 meses)
            days_since_last = (now - last_date).days
            if days_since_last >= 60:
                self.logger.info(f"Han pasado {days_since_last} días desde la última limpieza, ejecutando limpieza")
                return True
            else:
                self.logger.info(f"Solo han pasado {days_since_last} días, no es necesario limpiar aún")
                return False
                
        except Exception as e:
            self.logger.warning(f"Error leyendo fecha de última limpieza: {e}, ejecutando limpieza por seguridad")
            return True
                
    def update_last_cleanup_date(self):
        """Actualiza la fecha de la última limpieza"""
        last_cleanup_file = self.scripts_dir / "last_cleanup.txt"
        now_str = datetime.now().isoformat()
        try:
            with open(last_cleanup_file, 'w') as f:
                f.write(now_str)
            self.logger.info(f"Fecha de última limpieza actualizada: {now_str}")
        except Exception as e:
            self.logger.error(f"Error actualizando fecha de última limpieza: {e}")
                
    def check_and_cleanup_if_needed(self):
        """Verifica y ejecuta limpieza si es necesario"""
        if self.should_cleanup():
            files_checked, files_removed, files_kept = self.cleanup_completed_files()
            self.send_completed_cleanup_notification(files_checked, files_removed, files_kept)
            self.update_last_cleanup_date()
                
    def run_cleanup_only(self) -> bool:
        """Ejecuta solo la limpieza mensual de archivos completados"""
        if not self.acquire_lock():
            return False
        
        try:
            self.logger.info("=== Iniciando limpieza mensual de archivos completados ===")
            
            # Ejecuta la limpieza
            files_checked, files_removed, files_kept = self.cleanup_completed_files()
            
            # Envía la notificación
            self.send_completed_cleanup_notification(files_checked, files_removed, files_kept)
            
            # Actualiza la fecha de última limpieza
            self.update_last_cleanup_date()
            
            self.logger.info("=== Limpieza mensual finalizada ===")
            return True
            
        except Exception as e:
            self.logger.error(f"Error en limpieza mensual: {e}")
            self.send_error_notification(f"Error en limpieza mensual: {str(e)}")
            return False
        finally:
            self.release_lock()
    
    def _handle_no_pending_files(self):
        """Maneja el caso cuando no hay archivos pendientes para procesar"""
        self.logger.info("No hay archivos pendientes para procesar")
        # Envía notificación de sistema al día
        completed_file = self.scripts_dir / "completed.txt"
        completed_count = 0
        if completed_file.exists():
            with open(completed_file, 'r') as f:
                completed_count = len([line for line in f if line.strip()])
        
        cmd = [
            sys.executable, str(self.notifier_script),
            "no_pending", str(self.stats['files_found']), str(completed_count)
        ]
        
        # Verifica si es duplicada
        if not self._is_duplicate_no_pending_notification(completed_count):
            subprocess.run(cmd, timeout=60)
            # Marca como notificado
            self._mark_as_notified_no_pending(self.stats['files_processed'])
        
        # Verifica y ejecuta limpieza si es necesario
        self.check_and_cleanup_if_needed()

    def _is_duplicate_no_pending_notification(self, completed_count: int) -> bool:
        """Verifica si la notificación no_pending es duplicada"""
        progress_file = self.tmp_dir / self.PROGRESS_FILE_NAME
        if not progress_file.exists():
            return False
        
        try:
            with open(progress_file, 'r') as f:
                progress_data = json.load(f)
            
            last_notif = progress_data.get('last_notification', {})
            return (last_notif.get('type') == 'no_pending' and 
                    last_notif.get('completed_count') == self.stats['files_processed'])
        except Exception as e:
            self.logger.warning(f"Error verificando última notificación no_pending: {e}")
            return False

    def is_night_time(self) -> bool:
        """Verifica si es hora nocturna (0:00 - 5:59 AM) para procesamiento de subtítulos"""
        current_hour = datetime.now().hour
        return 0 <= current_hour < 6  # De 12 AM a 5:59 AM
    
    def run(self) -> bool:
        """Ejecuta el flujo completo de escaneo y procesamiento secuencial"""
        if not self.acquire_lock():
            return False
        
        try:
            # Verificar si es hora nocturna para procesamiento de subtítulos
            night_mode = self.is_night_time()
            
            if night_mode:
                self.logger.info("=== Iniciando modo nocturno: procesamiento de subtítulos pendientes ===")
                
                # Procesar subtítulos pendientes
                subtitles_processed, subtitles_errors = self.process_pending_subtitles()
                
                # Notificar procesamiento nocturno solo si se procesaron subtítulos
                if subtitles_processed > 0 or subtitles_errors > 0:
                    self.send_night_subtitle_notification(subtitles_processed, subtitles_errors)
                else:
                    self.logger.info("No hay subtítulos pendientes para procesar, omitiendo notificación nocturna")
                
                # Verifica y ejecuta limpieza si es necesario
                self.check_and_cleanup_if_needed()
                
                self.logger.info("=== Modo nocturno finalizado ===")
                return True
            else:
                self.logger.info("=== Iniciando ejecución diurna: escaneo y procesamiento ===")
                
                # 1. Ejecuta el scanner
                if not self.run_scanner():
                    self.send_error_notification("Falló el escaneo automático")
                    return False
                
                # 2. Procesar archivos uno por uno (sin subtítulos, se agregan a pendientes)
                files_processed = 0
                files_with_errors = 0
                
                while True:
                    # Obtener siguiente archivo pendiente
                    next_file = self.get_next_pending_file()
                    if not next_file:
                        break  # No hay más archivos
                    
                    self.logger.info(f"Procesando archivo {files_processed + 1}: {next_file}")
                    
                    # Procesar el archivo individualmente (sin subtítulos)
                    if self.process_single_file(next_file, process_subtitles=False):
                        files_processed += 1
                    else:
                        files_with_errors += 1
                        self.logger.error(f"Error procesando: {next_file}")
                
                # 3. Determinar tipo de notificación
                if files_processed > 0:
                    notification_type = "success"
                    if files_with_errors > 0:
                        self.logger.info(f"Procesamiento completado con {files_with_errors} errores")
                else:
                    notification_type = "error"
                    self.logger.warning("No se procesó ningún archivo")
                
                # 4. Envía notificación
                self.send_notification(notification_type)
                
                # 5. Verifica y ejecuta limpieza si es necesario
                self.check_and_cleanup_if_needed()
                
                self.logger.info(f"=== Ejecución diurna finalizada: {files_processed} archivos procesados ===")
                return True
            
        except Exception as e:
            self.logger.error(f"Error fatal en ejecución: {e}")
            self.send_error_notification(f"Error fatal: {str(e)}")
            return False
        finally:
            self.release_lock()

def signal_handler(signum, frame):
    """Manejo de señales para terminación limpia"""
    logging.getLogger(__name__).info(f"Recibida señal {signum}, terminando...")
    sys.exit(0)

def main():
    """Función principal"""
    # Configura el manejo de señales
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    # Ejecuta el runner
    runner = MediaJellyCronRunner()
    
    # Verifica si se ejecuta en modo cleanup
    if len(sys.argv) > 1 and sys.argv[1] == "cleanup":
        success = runner.run_cleanup_only()
    else:
        success = runner.run()
    
    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()