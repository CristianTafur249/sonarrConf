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

from mediajelly_emoji import EmojiGenerator

class MediaJellyCronRunner:
    """Orquestador principal de MediaJelly"""
    
    PROGRESS_FILE_NAME = "progress.json"
    PENDING_COMPRESSION_FILE = "pending-compression.txt"
    COMPLETED_FILE = "completed.txt"
    STATS_FILE = "stats.json"
    
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
        self.stats_path = self.tmp_dir / self.STATS_FILE
        
        # Scripts Python
        self.scanner_script = self.scripts_dir / "mediajelly_scanner.py"
        self.processor_script = self.scripts_dir / "mediajelly_processor.py"
        self.notifier_script = self.scripts_dir / "mediajelly_notifier.py"
        self.subtitle_translator_script = self.scripts_dir / "mediajelly_subtitle_translator.py"
        
        # Crea directorios con permisos correctos
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        
        # Asegurar permisos correctos en directorios críticos
        self._ensure_directory_permissions()
        
    # Configura logging
        self.setup_logging()
        
        # Cargar estadísticas persistentes
        self.stats = self._load_persistent_stats()
        
        # Variables de estado
        self.scan_success = False
    
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
    
    def _ensure_directory_permissions(self):
        """Asegura que los directorios críticos tengan permisos correctos"""
        try:
            # Cambiar permisos del directorio tmp a 0o777 (rwxrwxrwx)
            if self.tmp_dir.exists():
                os.chmod(self.tmp_dir, 0o777)
            
            # Cambiar permisos del directorio logs a 0o777 (rwxrwxrwx)
            if self.logs_dir.exists():
                os.chmod(self.logs_dir, 0o777)
                
            # Si el lockfile existe y fue creado por root, eliminarlo
            if self.lockfile.exists():
                try:
                    stat_info = self.lockfile.stat()
                    # Si el archivo pertenece a root (uid 0) y no tenemos permisos
                    if stat_info.st_uid == 0 and os.getuid() != 0:
                        # Intentar eliminarlo (solo funcionará si el directorio tiene permisos)
                        self.lockfile.unlink()
                except (PermissionError, OSError):
                    pass
        except Exception as e:
            # No fallar si no se pueden cambiar los permisos
            # pero registrar el error si el logger ya está configurado
            pass
    
    def acquire_lock(self) -> bool:
        """Adquiere un lock exclusivo para evitar ejecuciones simultáneas"""
        try:
            # Asegurar permisos antes de intentar crear el lock
            self._ensure_directory_permissions()
            
            # Intentar abrir el archivo de lock (crear si no existe) con permisos explícitos
            # Usar os.open con permisos 0o666 para que cualquier usuario pueda acceder
            try:
                fd = os.open(self.lockfile, os.O_RDWR | os.O_CREAT, 0o666)
                self.lock_fd = os.fdopen(fd, 'a+')
            except PermissionError:
                # Si hay error de permisos, intentar eliminar el archivo antiguo
                if self.lockfile.exists():
                    try:
                        self.lockfile.unlink()
                    except:
                        pass
                # Intentar nuevamente
                fd = os.open(self.lockfile, os.O_RDWR | os.O_CREAT, 0o666)
                self.lock_fd = os.fdopen(fd, 'a+')
            
            # Intentar adquirir el lock de archivo de forma no bloqueante
            try:
                fcntl.flock(self.lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (IOError, OSError):
                # No se pudo adquirir el lock, hay otra instancia ejecutándose
                self.lock_fd.seek(0)
                existing_pid = self.lock_fd.read().strip()
                self.lock_fd.close()
                self.logger.error(f"Ya hay una instancia ejecutándose (PID: {existing_pid})")
                return False
            
            # Lock adquirido exitosamente, escribir nuestro PID
            self.lock_fd.seek(0)
            self.lock_fd.truncate()
            self.lock_fd.write(str(os.getpid()))
            self.lock_fd.flush()
            return True
        except Exception as e:
            self.logger.error(f"Error al adquirir lock: {e}")
            if hasattr(self, 'lock_fd') and self.lock_fd:
                try:
                    self.lock_fd.close()
                except:
                    pass
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
                
                # Extrae estadísticas del output y acumula
                for line in result.stdout.strip().split('\n'):
                    if "Archivos encontrados:" in line:
                        self.stats['files_found'] += int(line.split(':')[1].strip())
                    elif "Nuevas rutas detectadas:" in line:
                        self.stats['files_new'] += int(line.split(':')[1].strip())
                
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
                
                # Extrae estadísticas del output y acumula
                for line in result.stdout.strip().split('\n'):
                    if "Procesados:" in line:
                        self.stats['files_processed'] += int(line.split(':')[1].strip())
                    elif "Comprimidos:" in line:
                        self.stats['files_compressed'] += int(line.split(':')[1].strip())
                    elif "Renombrados:" in line:
                        self.stats['files_renamed'] += int(line.split(':')[1].strip())
                    elif "Omitidos:" in line:
                        self.stats['files_skipped'] += int(line.split(':')[1].strip())
                
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
    
    def _reset_execution_stats(self):
        """Resetea las estadísticas de esta ejecución específica"""
        # Mantener solo las estadísticas persistentes, resetear las de ejecución
        persistent_stats = {
            'files_processed': self.stats.get('files_processed', 0),
            'files_compressed': self.stats.get('files_compressed', 0),
            'files_renamed': self.stats.get('files_renamed', 0),
            'files_skipped': self.stats.get('files_skipped', 0),
            'subtitles_translated': self.stats.get('subtitles_translated', 0),
            'subtitles_errors': self.stats.get('subtitles_errors', 0)
        }
        
        # Resetear estadísticas de esta ejecución
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
        
        # Restaurar estadísticas persistentes
        self.stats.update(persistent_stats)
        
        self.logger.info("Estadísticas de ejecución reseteadas")
    
    def _update_progress_with_stats(self):
        """Actualiza progress.json con las estadísticas acumuladas"""
        progress_file = self.tmp_dir / "progress.json"
        try:
            progress_data = self._load_progress_data(progress_file)
            if 'processing' in progress_data and 'stats' in progress_data['processing']:
                # Actualizar estadísticas en progress.json
                progress_data['processing']['stats'].update({
                    'files_processed': self.stats.get('files_processed', 0),
                    'files_compressed': self.stats.get('files_compressed', 0),
                    'files_renamed': self.stats.get('files_renamed', 0),
                    'files_skipped': self.stats.get('files_skipped', 0),
                    'files_found': self.stats.get('files_found', 0),
                    'files_new': self.stats.get('files_new', 0)
                })
                # Actualizar total_files si no está establecido
                if progress_data['processing'].get('total_files', 0) == 0:
                    progress_data['processing']['total_files'] = self.stats.get('files_found', 0)

                self._save_progress_data(progress_file, progress_data)
                self.logger.info("Progress.json actualizado con estadísticas del procesamiento")
        except Exception as e:
            self.logger.warning(f"Error actualizando progress.json con estadísticas: {e}")
    
    def get_next_pending_file(self) -> Optional[str]:
        """Obtiene el siguiente archivo pendiente de procesamiento"""
        pending_file = self.scripts_dir / self.PENDING_COMPRESSION_FILE
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
    
    def process_pending_subtitles(self) -> dict:
        """Procesa todos los archivos pendientes de subtítulos (modo nocturno)"""
        self.logger.info("=== Iniciando procesamiento nocturno de subtítulos ===")
        
        self._reset_processing_state()
        
        if self._is_subtitle_translator_running():
            return self._get_empty_stats()
        
        pending_files = self._get_pending_subtitle_files()
        if not pending_files:
            return self._get_empty_stats()
        
        self._process_pending_files_with_subprocess(pending_files)
        self._cleanup_pending_files_list()
        
        return self.get_subtitle_translation_stats()
    
    def _reset_processing_state(self):
        """Resetea el estado de processing en progress.json para modo nocturno"""
        progress_file = self.tmp_dir / "progress.json"
        try:
            progress_data = self._load_progress_data(progress_file)
            progress_data["processing"] = self._get_default_processing_state()
            self._save_progress_data(progress_file, progress_data)
        except Exception as e:
            self.logger.warning(f"Error reseteando estado de processing en progress.json: {e}")
    
    def _load_progress_data(self, progress_file: Path) -> dict:
        """Carga datos de progress.json"""
        if progress_file.exists():
            with open(progress_file, 'r') as f:
                return json.load(f)
        return {}
    
    def _get_default_processing_state(self) -> dict:
        """Retorna el estado por defecto para processing"""
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
                "no_spanish": []
            },
            "processed_files": {}
        }
    
    def _save_progress_data(self, progress_file: Path, data: dict):
        """Guarda datos en progress.json"""
        with open(progress_file, 'w') as f:
            json.dump(data, f, indent=2)
    
    def _is_subtitle_translator_running(self) -> bool:
        """Verifica si ya hay un proceso de traducción ejecutándose"""
        subtitle_lock_file = self.tmp_dir / "subtitle_translator.lock"
        if not subtitle_lock_file.exists():
            return False
        
        try:
            pid = self._read_lock_file_pid(subtitle_lock_file)
            if self._is_process_running(pid):
                self.logger.warning(f"Ya hay un proceso de traducción ejecutándose (PID: {pid}), omitiendo procesamiento nocturno")
                return True
            else:
                self._cleanup_orphaned_lock(subtitle_lock_file, pid)
        except Exception as e:
            self.logger.warning(f"Error al verificar lock de traducción: {e}, continuando...")
            subtitle_lock_file.unlink(missing_ok=True)
        
        return False
    
    def _read_lock_file_pid(self, lock_file: Path) -> int:
        """Lee el PID del archivo de lock"""
        with open(lock_file, 'r') as f:
            pid_str = f.read().strip()
            return int(pid_str) if pid_str and pid_str.isdigit() else 0
    
    def _is_process_running(self, pid: int) -> bool:
        """Verifica si un proceso con el PID dado está ejecutándose"""
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    
    def _cleanup_orphaned_lock(self, lock_file: Path, pid: int):
        """Limpia un lock huérfano"""
        self.logger.info(f"Lock de traducción huérfano detectado (PID {pid} no existe), limpiando...")
        lock_file.unlink()
    
    def _get_empty_stats(self) -> dict:
        """Retorna estadísticas vacías"""
        return {
            'files_processed': 0,
            'files_translated': 0,
            'files_skipped': 0,
            'errors': []
        }
    
    def _get_pending_subtitle_files(self) -> list:
        """Obtiene la lista de archivos pendientes de subtítulos
        
        Lógica:
        1. Si pending_subtitles.txt existe y tiene archivos, usar esos
        2. Si está vacío o no existe, intentar cargar last_input_args del proceso anterior
           (solo si el porcentaje < 100%, para reanudar procesos incompletos)
        """
        # Intentar cargar desde pending_subtitles.txt
        if self.pending_subtitles_file.exists():
            with open(self.pending_subtitles_file, 'r') as f:
                pending_files = [line.strip() for line in f if line.strip()]
            
            if pending_files:
                # Corregir rutas del contenedor al host si es necesario
                corrected_files = self._correct_pending_paths(pending_files)
                self.logger.info(f"Cargando {len(corrected_files)} archivos desde pending_subtitles.txt")
                return corrected_files
        
        # Si pending_subtitles.txt está vacío o no existe, intentar reanudar proceso anterior
        self.logger.info("pending_subtitles.txt vacío o no existe, verificando proceso anterior...")
        return self._get_files_from_last_input_args()
    
    def _get_files_from_last_input_args(self) -> list:
        """Obtiene archivos desde last_input_args si hay un proceso incompleto (< 100%)"""
        progress_file = self.tmp_dir / self.PROGRESS_FILE_NAME
        
        if not progress_file.exists():
            self.logger.info("No hay archivo de progreso, no se puede reanudar")
            return []
        
        try:
            with open(progress_file, 'r') as f:
                progress_data = json.load(f)
            
            subtitle_translation = progress_data.get('subtitle_translation', {})
            percentage = subtitle_translation.get('percentage', 0)
            last_input_args = subtitle_translation.get('last_input_args', {})
            
            # Solo reanudar si el proceso está incompleto
            if percentage >= 100.0:
                self.logger.info(f"Proceso anterior completado al {percentage:.1f}%, no se reanudará")
                return []
            
            if not last_input_args:
                self.logger.info("No hay last_input_args guardados, no se puede reanudar")
                return []
            
            # Obtener las rutas corregidas (ya normalizadas a formato Docker) y convertir al host
            corrected_paths = last_input_args.get('corrected_paths', [])
            
            if not corrected_paths:
                self.logger.info("last_input_args no contiene rutas, no se puede reanudar")
                return []
            
            # Corregir rutas del contenedor al formato del host
            corrected_paths = self._correct_pending_paths(corrected_paths)
            
            # Filtrar archivos ya procesados
            processed_files = subtitle_translation.get('processed_files', {})
            pending_files = []
            
            for file_path in corrected_paths:
                if file_path not in processed_files:
                    pending_files.append(file_path)
            
            self.logger.info(f"Reanudando proceso anterior: {len(pending_files)} archivos pendientes de {len(corrected_paths)} totales ({percentage:.1f}% completado)")
            
            return pending_files
            
        except Exception as e:
            self.logger.error(f"Error cargando last_input_args: {e}")
            return []
    
    def _process_pending_files_with_subprocess(self, pending_files: list):
        """Procesa archivos pendientes usando subprocess"""
        self.logger.info(f"Procesando {len(pending_files)} archivos pendientes de subtítulos")
        
        try:
            cmd = [
                sys.executable,
                str(self.subtitle_translator_script),
                '--max-workers', '2',
                '--no-whisper'
            ] + pending_files
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=14400  # 4 horas timeout total
            )
            
            if result.returncode == 0:
                self.logger.info(f"{EmojiGenerator.check_mark()} Procesamiento nocturno de subtítulos completado exitosamente")
            else:
                self._log_subprocess_error(result)
                
        except subprocess.TimeoutExpired:
            self.logger.error("Timeout en procesamiento nocturno de subtítulos (4 horas)")
        except Exception as e:
            self.logger.error(f"Error procesando subtítulos nocturnos: {e}")
    
    def _log_subprocess_error(self, result):
        """Registra error del subprocess"""
        self.logger.warning(f"Error en procesamiento nocturno de subtítulos (código {result.returncode})")
        if result.stderr:
            self.logger.error(f"STDERR: {result.stderr[:500]}")
    
    def _cleanup_pending_files_list(self):
        """Limpia la lista de archivos pendientes"""
        try:
            self.pending_subtitles_file.unlink(missing_ok=True)
            self.logger.info("Lista de pendientes de subtítulos limpiada")
        except Exception as e:
            self.logger.warning(f"Error limpiando lista de pendientes: {e}")
    
    def get_subtitle_translation_stats(self) -> dict:
        """Obtiene las estadísticas de traducción de subtítulos del progress.json"""
        progress_file = self.tmp_dir / self.PROGRESS_FILE_NAME
        
        if not progress_file.exists():
            return {
                'files_processed': 0,
                'files_translated': 0,
                'files_skipped': 0,
                'errors': []
            }
        
        try:
            with open(progress_file, 'r') as f:
                progress_data = json.load(f)
            
            subtitle_stats = progress_data.get('subtitle_translation', {})
            return {
                'files_processed': subtitle_stats.get('stats', {}).get('files_processed', 0),
                'files_translated': subtitle_stats.get('stats', {}).get('files_translated', 0),
                'files_skipped': subtitle_stats.get('stats', {}).get('files_skipped', 0),
                'errors': subtitle_stats.get('stats', {}).get('errors', [])
            }
        except Exception as e:
            self.logger.warning(f"Error leyendo estadísticas de traducción: {e}")
            return {
                'files_processed': 0,
                'files_translated': 0,
                'files_skipped': 0,
                'errors': []
            }
    
    def remove_from_pending_and_add_to_completed(self, file_path: str) -> None:
        """Remueve un archivo de pending-compression.txt y lo agrega a completed.txt"""
        pending_file = self.scripts_dir / self.PENDING_COMPRESSION_FILE
        completed_file = self.scripts_dir / self.COMPLETED_FILE
        
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
        """Procesa un solo archivo: traducción de subtítulos + compresión"""
        try:
            # Solo mostrar log detallado si se procesan subtítulos (modo individual)
            if process_subtitles:
                self.logger.info(f"Procesando archivo individual: {file_path}")
            
            # PRIMERO: Procesar subtítulos si está habilitado
            if process_subtitles:
                self._handle_subtitle_translation(file_path)
            
            # SEGUNDO: Ejecutar compresión
            if not self._run_processor_for_file(file_path):
                return False
            
            if not self.check_file_processed_successfully(file_path):
                self.logger.warning(f"Archivo {file_path} no fue procesado exitosamente")
                return True
            
            self.remove_from_pending_and_add_to_completed(file_path)
            self.logger.info(f"{EmojiGenerator.check_mark()} Archivo procesado completamente: {file_path}")
            
            return True
            
        except subprocess.TimeoutExpired:
            self.logger.error(f"Timeout procesando archivo individual: {file_path}")
            return False
        except Exception as e:
            self.logger.error(f"Error procesando archivo individual {file_path}: {e}")
            return False
    
    def _run_processor_for_file(self, file_path: str) -> bool:
        """Ejecuta el procesador para un archivo específico"""
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
        
        return True
    
    def _handle_subtitle_translation(self, file_path: str):
        """Maneja la traducción de subtítulos para un archivo"""
        self.logger.info(f"Traduciendo subtítulos para: {file_path}")
        
        if self._is_subtitle_translator_locked():
            self.add_to_pending_subtitles(file_path)
            return
        
        self._run_subtitle_translator_for_file(file_path)
    
    def _is_subtitle_translator_locked(self) -> bool:
        """Verifica si hay un lock de traducción activo"""
        subtitle_lock_file = self.tmp_dir / "subtitle_translator.lock"
        if not subtitle_lock_file.exists():
            return False
        
        try:
            pid = self._read_lock_file_pid(subtitle_lock_file)
            if self._is_process_running(pid):
                self.logger.warning(f"Ya hay un proceso de traducción ejecutándose (PID: {pid}), agregando a pendientes")
                return True
            else:
                self._cleanup_orphaned_lock(subtitle_lock_file, pid)
        except Exception as e:
            self.logger.warning(f"Error verificando lock de traducción: {e}, continuando...")
        
        return False
    
    def _run_subtitle_translator_for_file(self, file_path: str):
        """Ejecuta el traductor de subtítulos para un archivo específico"""
        try:
            cmd = [
                sys.executable,
                str(self.subtitle_translator_script),
                '--max-workers', '1',
                file_path
            ]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=7200  # 2 horas timeout
            )
            
            if result.returncode == 0:
                self.logger.info(f"{EmojiGenerator.check_mark()} Subtítulos procesados exitosamente para: {file_path}")
                self.remove_from_pending_subtitles(file_path)  # Remover de pendientes
            else:
                self._log_subtitle_translation_error(file_path, result)
                
        except subprocess.TimeoutExpired:
            self.logger.error(f"Timeout en traducción de subtítulos para {file_path}")
            raise
        except Exception as e:
            self.logger.error(f"Error procesando subtítulos para {file_path}: {e}")
            raise
    
    def _log_subtitle_translation_error(self, file_path: str, result):
        """Registra error en traducción de subtítulos"""
        self.logger.warning(f"Error en traducción de subtítulos para {file_path} (código {result.returncode})")
        if result.stderr:
            self.logger.error(f"STDERR: {result.stderr[:500]}")
    
    def _add_to_pending_subtitles(self, file_path: str):
        """Agrega archivo a pendientes de subtítulos"""
        self.add_to_pending_subtitles(file_path)
        self.logger.info(f"Archivo agregado a pendientes de subtítulos (procesamiento nocturno): {file_path}")
    
    def check_file_processed_successfully(self, file_path: str) -> bool:
        """Verifica si un archivo específico fue procesado exitosamente"""
        progress_file = self.tmp_dir / self.PROGRESS_FILE_NAME
        
        if not progress_file.exists():
            return False
        
        try:
            with open(progress_file, 'r') as f:
                progress_data = json.load(f)
            
            processing_section = progress_data.get('processing', {})
            file_info = processing_section.get('processed_files', {}).get(file_path, {})
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
            
            # Obtener archivos del diccionario processed_files en la sección processing
            processing_section = progress_data.get('processing', {})
            for file_path, file_info in processing_section.get('processed_files', {}).items():
                # Solo incluir archivos que fueron procesados exitosamente o renombrados
                status = file_info.get('status', '')
                if status in ['completed', 'renamed', 'skipped', 'success']:
                    processed_files.append(file_path)
            
            self.logger.info(f"Archivos procesados para traducción: {len(processed_files)}")
            
        except Exception as e:
            self.logger.warning(f"Error obteniendo archivos procesados: {e}")
        
        return processed_files
    
    def run_subtitle_translator(self, file_list: list = None, max_workers: int = 2, use_whisper: bool = False) -> bool:
        """Ejecuta el traductor de subtítulos para archivos sin audio en español
        
        Args:
            file_list: Lista de archivos a procesar. Si es None, procesa toda la carpeta media.
            max_workers: Número de archivos a procesar simultáneamente (se ajusta a 1 con Whisper)
            use_whisper: Si usar Whisper para extraer audio (default: False para modo nocturno)
        """
        try:
            max_workers = self._adjust_workers_for_whisper(max_workers, use_whisper)
            self._log_translator_start(use_whisper, max_workers)
            
            cmd = self._build_translator_command(file_list, max_workers, use_whisper)
            result = self._execute_translator_command(cmd)
            
            return self._handle_translator_result(result)
            
        except subprocess.TimeoutExpired:
            self.logger.error("Timeout en traducción de subtítulos (2 horas)")
            return False
        except Exception as e:
            self.logger.error(f"Error ejecutando traductor de subtítulos: {e}")
            return False
    
    def _adjust_workers_for_whisper(self, max_workers: int, use_whisper: bool) -> int:
        """Ajusta el número de workers basado en el uso de Whisper"""
        if use_whisper:
            return 1  # Whisper solo procesa 1 por 1
        return max_workers
    
    def _log_translator_start(self, use_whisper: bool, max_workers: int):
        """Registra el inicio del traductor"""
        if use_whisper:
            self.logger.info("Whisper habilitado: Procesamiento secuencial (1 archivo por vez)")
        else:
            self.logger.info(f"Procesamiento concurrente: {max_workers} workers")
        
        self.logger.info("Iniciando traducción de subtítulos")
    
    def _build_translator_command(self, file_list: list, max_workers: int, use_whisper: bool) -> list:
        """Construye el comando para ejecutar el traductor"""
        cmd = [sys.executable, str(self.subtitle_translator_script)]
        cmd.extend(['--max-workers', str(max_workers)])
        
        if not use_whisper:
            cmd.append('--no-whisper')
        
        if file_list and len(file_list) > 0:
            cmd.extend(file_list)
            self.logger.info(f"Procesando {len(file_list)} archivos específicos")
        else:
            cmd.append(str(self.media_dir))
            self.logger.info("Procesando toda la carpeta de medios")
        
        return cmd
    
    def _execute_translator_command(self, cmd: list):
        """Ejecuta el comando del traductor"""
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=7200  # 2 horas timeout
        )
    
    def _handle_translator_result(self, result) -> bool:
        """Maneja el resultado de la ejecución del traductor"""
        if result.returncode == 0:
            self.logger.info("Traducción de subtítulos completada")
            self._extract_stats_from_output(result.stdout)
            return True
        else:
            self.logger.error(f"Error en traducción de subtítulos (código {result.returncode})")
            if result.stderr:
                self.logger.error(f"STDERR: {result.stderr}")
            self._extract_stats_from_output(result.stdout)
            return False
    
    def _extract_stats_from_output(self, stdout: str):
        """Extrae estadísticas del output del traductor"""
        for line in stdout.strip().split('\n'):
            if "Subtítulos traducidos:" in line:
                self.stats['subtitles_translated'] += int(line.split(':')[1].strip())
            elif "Errores:" in line:
                self.stats['subtitles_errors'] += int(line.split(':')[1].strip())
    
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
                self.logger.info("Notificación duplicada de completado sin procesamiento, solo actualizando timestamp...")
                is_duplicate = True
            
            if not is_duplicate:
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
                else:
                    self.logger.error("Error al enviar notificación")
                    return False
            
            # Marca como notificado en progress.json (siempre, incluso para duplicadas)
            self._mark_as_notified()
            return True
                
        except Exception as e:
            self.logger.error(f"Error enviando notificación: {e}")
            return False
    
    def send_error_notification(self, error_message: str) -> bool:
        """Envía notificación de error vía Telegram"""
        try:
            self.logger.info(f"Enviando notificación de error: {error_message}")
            
            cmd = [
                sys.executable, str(self.notifier_script),
                "critical_error", error_message
            ]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60
            )
            
            if result.returncode == 0:
                self.logger.info("Notificación de error enviada correctamente")
                # Guardar last_notification de error
                self._save_error_notification(error_message)
                return True
            else:
                self.logger.error(f"Error al enviar notificación de error: {result.stderr}")
                return False
                
        except Exception as e:
            self.logger.error(f"Error enviando notificación de error: {e}")
            return False
    
    def send_start_processing_notification(self, files_found: int, files_new: int) -> bool:
        """Envía notificación de inicio de procesamiento vía Telegram"""
        try:
            self.logger.info(f"Enviando notificación de inicio de procesamiento: {files_found} encontrados, {files_new} nuevos")
            
            cmd = [
                sys.executable, str(self.notifier_script),
                "start_processing",
                str(files_found),
                str(files_new)
            ]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60
            )
            
            if result.returncode == 0:
                self.logger.info("Notificación de inicio de procesamiento enviada correctamente")
                # Guardar last_notification de inicio
                self._save_start_processing_notification(files_found, files_new)
                return True
            else:
                self.logger.error(f"Error al enviar notificación de inicio: {result.stderr}")
                return False
                
        except Exception as e:
            self.logger.error(f"Error enviando notificación de inicio: {e}")
            return False
    
    def _reset_notification_state(self):
        """Resetea el estado de notificación cuando hay archivos nuevos para procesar"""
        try:
            progress_file = self.tmp_dir / self.PROGRESS_FILE_NAME
            if progress_file.exists():
                with open(progress_file, 'r') as f:
                    progress_data = json.load(f)
                
                # Asegurar que existe la sección processing
                if 'processing' not in progress_data:
                    progress_data['processing'] = {
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
                            "no_spanish": []
                        },
                        "processed_files": {}
                    }
                
                progress_data['processing']['notified'] = False
                progress_data['processing']['status'] = 'processing'
                
                with open(progress_file, 'w') as f:
                    json.dump(progress_data, f, indent=2)
                
                self.logger.info("Estado de notificación reseteado (notified=False) - hay archivos nuevos para procesar")
        except Exception as e:
            self.logger.warning(f"Error reseteando estado de notificación: {e}")

    def _mark_as_notified(self):
        """Marca el estado actual como notificado en progress.json"""
        try:
            progress_file = self.tmp_dir / self.PROGRESS_FILE_NAME
            if progress_file.exists():
                with open(progress_file, 'r') as f:
                    progress_data = json.load(f)
                
                # Asegurar que existe la sección processing
                if 'processing' not in progress_data:
                    progress_data['processing'] = {
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
                            "no_spanish": []
                        },
                        "processed_files": {}
                    }
                
                progress_data['processing']['notified'] = True
                progress_data['processing']['status'] = 'completed'
                # Guarda la información de la última notificación
                progress_data['processing']['last_notification'] = {
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
    
    def _mark_as_notified_no_pending(self, completed_count: int):
        """Marca el estado como notificado para no_pending en progress.json"""
        try:
            progress_file = self.tmp_dir / self.PROGRESS_FILE_NAME
            if progress_file.exists():
                with open(progress_file, 'r') as f:
                    progress_data = json.load(f)
                
                # Asegurar que existe la sección processing
                if 'processing' not in progress_data:
                    progress_data['processing'] = {
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
                            "no_spanish": []
                        },
                        "processed_files": {}
                    }
                
                progress_data['processing']['notified'] = True
                # Guarda la información de la última notificación no_pending
                progress_data['processing']['last_notification'] = {
                    'type': 'no_pending',
                    'files_found': self.stats['files_found'],
                    'completed_count': completed_count,
                    'timestamp': datetime.now().isoformat()
                }
                
                with open(progress_file, 'w') as f:
                    json.dump(progress_data, f, indent=2)
                
                self.logger.info("Estado marcado como notificado no_pending (notified=True)")
        except Exception as e:
            self.logger.warning(f"Error marcando como notificado no_pending: {e}")
    
    def _save_error_notification(self, error_message: str):
        """Guarda la información de la notificación de error en progress.json"""
        try:
            progress_file = self.tmp_dir / self.PROGRESS_FILE_NAME
            if progress_file.exists():
                with progress_file.open('r') as f:
                    progress_data = json.load(f)
                
                if 'processing' not in progress_data:
                    progress_data['processing'] = {}
                
                progress_data['processing']['last_notification'] = {
                    'type': 'error',
                    'error_message': error_message,
                    'timestamp': datetime.now().isoformat()
                }
                
                with progress_file.open('w') as f:
                    json.dump(progress_data, f, indent=2)
                
                self.logger.info("Notificación de error guardada en progress.json")
        except Exception as e:
            self.logger.warning(f"Error guardando notificación de error: {e}")
    
    def _save_start_processing_notification(self, files_found: int, files_new: int):
        """Guarda la información de la notificación de inicio de procesamiento en progress.json"""
        try:
            progress_file = self.tmp_dir / self.PROGRESS_FILE_NAME
            if progress_file.exists():
                with progress_file.open('r') as f:
                    progress_data = json.load(f)
                
                if 'processing' not in progress_data:
                    progress_data['processing'] = {}
                
                progress_data['processing']['last_notification'] = {
                    'type': 'processing_started',
                    'files_found': files_found,
                    'files_new': files_new,
                    'timestamp': datetime.now().isoformat()
                }
                
                with progress_file.open('w') as f:
                    json.dump(progress_data, f, indent=2)
                
                self.logger.info("Notificación de inicio de procesamiento guardada en progress.json")
        except Exception as e:
            self.logger.warning(f"Error guardando notificación de inicio: {e}")
    
    def _save_cleanup_notification(self, files_checked: int, files_removed: int, files_kept: int):
        """Guarda la información de la notificación de limpieza en progress.json"""
        try:
            progress_file = self.tmp_dir / self.PROGRESS_FILE_NAME
            if progress_file.exists():
                with progress_file.open('r') as f:
                    progress_data = json.load(f)
                
                if 'processing' not in progress_data:
                    progress_data['processing'] = {}
                
                progress_data['processing']['last_cleanup_notification'] = {
                    'type': 'cleanup',
                    'files_checked': files_checked,
                    'files_removed': files_removed,
                    'files_kept': files_kept,
                    'timestamp': datetime.now().isoformat()
                }
                
                with progress_file.open('w') as f:
                    json.dump(progress_data, f, indent=2)
                
                self.logger.info("Notificación de limpieza guardada en progress.json")
        except Exception as e:
            self.logger.warning(f"Error guardando notificación de limpieza: {e}")
    
    def _mark_as_notified_night(self, stats: dict):
        """Guarda la información de la notificación nocturna de subtítulos en progress.json"""
        try:
            progress_file = self.tmp_dir / self.PROGRESS_FILE_NAME
            if progress_file.exists():
                with progress_file.open('r') as f:
                    progress_data = json.load(f)
                
                if 'subtitle_translation' not in progress_data:
                    progress_data['subtitle_translation'] = {}
                
                progress_data['subtitle_translation']['last_notification'] = {
                    'type': 'night_subtitles',
                    'files_translated': stats['files_translated'],
                    'files_skipped': stats['files_skipped'],
                    'errors': len(stats.get('errors', [])),
                    'timestamp': datetime.now().isoformat()
                }
                
                with progress_file.open('w') as f:
                    json.dump(progress_data, f, indent=2)
                
                self.logger.info("Notificación nocturna de subtítulos guardada en progress.json")
        except Exception as e:
            self.logger.warning(f"Error guardando notificación nocturna de subtítulos: {e}")
    
    def send_night_subtitle_notification(self, stats: dict) -> bool:
        """Envía notificación del procesamiento nocturno de subtítulos"""
        try:
            self.logger.info("Enviando notificación de procesamiento nocturno de subtítulos...")
            
            # Preparar argumentos para el notifier
            translated = stats['files_translated']
            skipped = stats['files_skipped']
            errors = len(stats['errors'])
            
            cmd = [
                sys.executable, str(self.notifier_script),
                "night_subtitles",
                str(translated),
                str(skipped),
                str(errors)
            ]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60
            )
            
            if result.returncode == 0:
                self.logger.info("Notificación de procesamiento nocturno enviada correctamente")
                self._mark_as_notified_night(stats)
                return True
            else:
                self.logger.error("Error al enviar notificación de procesamiento nocturno")
                return False
                
        except Exception as e:
            self.logger.error(f"Error enviando notificación de procesamiento nocturno: {e}")
            return False
    
    def check_pending_files(self) -> bool:
        """Verifica si hay archivos pendientes para procesar"""
        pending_file = self.scripts_dir / self.PENDING_COMPRESSION_FILE
        if not pending_file.exists():
            return False
        
        with open(pending_file, 'r') as f:
            pending_lines = [line.strip() for line in f if line.strip()]
        
        return len(pending_lines) > 0
    
    def cleanup_completed_files(self) -> tuple[int, int, int]:
        """Limpia archivos que ya no existen del archivo completed.txt"""
        completed_file = self.scripts_dir / self.COMPLETED_FILE
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
                # Guardar last_cleanup_notification
                self._save_cleanup_notification(files_checked, files_removed, files_kept)
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
            
            # Guardar estadísticas persistentes
            self._save_persistent_stats()
            
            return True
            
        except Exception as e:
            self.logger.error(f"Error en limpieza mensual: {e}")
            self.send_error_notification(f"Error en limpieza mensual: {str(e)}")
            return False
        finally:
            self.release_lock()
    
    def _cleanup_invalid_pending_files(self):
        """Limpia archivos inválidos del pending-compression.txt"""
        pending_file = self.scripts_dir / self.PENDING_COMPRESSION_FILE
        if not pending_file.exists():
            return
        
        with open(pending_file, 'r') as f:
            lines = [line.strip() for line in f if line.strip()]
        
        valid_extensions = {'.mkv', '.mp4', '.avi', '.mov', '.webm', '.ts'}
        valid_lines = []
        invalid_count = 0
        
        for line in lines:
            path = Path(line)
            if path.suffix.lower() in valid_extensions and path.exists():
                valid_lines.append(line)
            else:
                invalid_count += 1
                self.logger.warning(f"Eliminando archivo inválido de pendientes: {line}")
        
        if invalid_count > 0:
            # Reescribir solo los válidos
            with open(pending_file, 'w') as f:
                for line in valid_lines:
                    f.write(f"{line}\n")
            self.logger.info(f"Limpieza completada: {invalid_count} archivos inválidos eliminados, {len(valid_lines)} válidos restantes")
        
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

    def _handle_no_pending_files(self):
        """Maneja la situación cuando no hay archivos pendientes para procesar"""
        try:
            completed_count = self.stats.get('files_processed', 0)
            
            # Verificar si ya se envió esta misma notificación
            if self._is_duplicate_no_pending_notification(completed_count):
                self.logger.info("Ya se envió notificación de no_pending con este completed_count, omitiendo...")
                return
            
            self.logger.info("Enviando notificación de no archivos pendientes...")
            
            cmd = [
                sys.executable, str(self.notifier_script),
                "scan_result", "no_pending",
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
                self.logger.info("Notificación de no_pending enviada correctamente")
                self._mark_as_notified_no_pending(completed_count)
            else:
                self.logger.error("Error al enviar notificación de no_pending")
                
        except Exception as e:
            self.logger.error(f"Error manejando no_pending: {e}")

    def is_night_time(self) -> bool:
        """Verifica si es hora nocturna (0:00 - 5:59 AM) para procesamiento de subtítulos"""
        current_hour = datetime.now().hour
        return 0 <= current_hour < 6  # De 12 AM a 5:59 AM
    
    def run(self):
        """Ejecuta el flujo completo de escaneo y procesamiento UNA SOLA VEZ (llamado por cron)"""
        if not self.acquire_lock():
            return False
        
        try:
            # Resetear estadísticas para esta ejecución
            self._reset_execution_stats()
            
            if self.is_night_time():
                self._run_night_mode()
            else:
                self._run_day_mode()
            return True
        except Exception as e:
            self.logger.error(f"Error fatal en ejecución: {e}")
            self.send_error_notification(f"Error fatal: {str(e)}")
            return False
        finally:
            self.release_lock()
    
    def _run_night_mode(self):
        """Ejecuta el modo nocturno: procesamiento de subtítulos pendientes y compresión si no hay subtítulos"""
        self.logger.info("=== Iniciando modo nocturno: procesamiento de subtítulos pendientes ===")
        
        subtitles_stats = self.process_pending_subtitles()
        
        if self._should_send_night_notification(subtitles_stats):
            self.send_night_subtitle_notification(subtitles_stats)
        else:
            self.logger.info("No hay subtítulos pendientes para procesar, omitiendo notificación nocturna")
        
        # Si no se procesaron subtítulos, intentar procesar archivos de compresión
        if subtitles_stats['files_translated'] == 0 and len(subtitles_stats['errors']) == 0:
            self.logger.info("No se procesaron subtítulos, verificando si hay archivos pendientes para comprimir...")
            first_pending = self.get_next_pending_file()
            if first_pending:
                self.logger.info("Se encontraron archivos pendientes para comprimir, iniciando procesamiento nocturno...")
                self._process_pending_files_night_mode()
            else:
                self.logger.info("No hay archivos pendientes para comprimir tampoco")
        
        self.check_and_cleanup_if_needed()
        self.logger.info("=== Modo nocturno finalizado ===")
        
        # Guardar estadísticas persistentes
        self._save_persistent_stats()
    
    def _should_send_night_notification(self, subtitles_stats: dict) -> bool:
        """Determina si se debe enviar notificación nocturna"""
        return subtitles_stats['files_translated'] > 0 or len(subtitles_stats['errors']) > 0
    
    def _run_day_mode(self):
        """Ejecuta el modo diurno: escaneo y procesamiento"""
        self.logger.info("=== Iniciando ejecución diurna: escaneo y procesamiento ===")
        
        if not self.run_scanner():
            self.send_error_notification("Falló el escaneo automático")
            return
        
        # Actualizar progress.json con estadísticas del scanner
        self._update_progress_with_stats()
        
        self._process_pending_files_day_mode()
    
    def _process_pending_files_day_mode(self):
        """Procesa archivos pendientes en modo diurno"""
        # Limpiar archivos inválidos del pending antes de procesar
        self._cleanup_invalid_pending_files()
        
        first_pending = self.get_next_pending_file()
        if first_pending:
            self._reset_notification_state()
            self.logger.info("Se encontraron archivos pendientes, comenzando procesamiento...")
            # Enviar notificación de inicio de procesamiento
            self.send_start_processing_notification(
                self.stats.get('files_found', 0),
                self.stats.get('files_new', 0)
            )
        else:
            self.logger.info("No se encontraron archivos nuevos para procesar")
            self._handle_no_pending_files()
            return
        
        processing_result = self._process_all_pending_files()
        notification_type = self._determine_notification_type(processing_result)
        
        self.send_notification(notification_type)
        self.check_and_cleanup_if_needed()
        
        files_processed = processing_result['processed']
        remaining_pending = len([line for line in open(self.scripts_dir / self.PENDING_COMPRESSION_FILE) if line.strip()])
        
        if remaining_pending > 0:
            self.logger.warning(f"Aún quedan {remaining_pending} archivos pendientes que no pudieron procesarse")
            # No reiniciar, terminar normalmente para evitar loops
            return
        
        self.logger.info(f"=== Ejecución diurna finalizada: {files_processed} archivos procesados ===")
        
        # Guardar estadísticas persistentes
        self._save_persistent_stats()
    
    def _process_pending_files_night_mode(self):
        """Procesa archivos pendientes en modo nocturno (sin notificaciones)"""
        # Limpiar archivos inválidos del pending antes de procesar
        self._cleanup_invalid_pending_files()
        
        first_pending = self.get_next_pending_file()
        if not first_pending:
            self.logger.info("No se encontraron archivos pendientes para procesar en modo nocturno")
            return
        
        self.logger.info("Iniciando procesamiento nocturno de archivos pendientes...")
        
        processing_result = self._process_all_pending_files()
        
        files_processed = processing_result['processed']
        remaining_pending = len([line for line in open(self.scripts_dir / self.PENDING_COMPRESSION_FILE) if line.strip()])
        
        if remaining_pending > 0:
            self.logger.warning(f"Aún quedan {remaining_pending} archivos pendientes que no pudieron procesarse en modo nocturno")
        
        self.logger.info(f"=== Procesamiento nocturno finalizado: {files_processed} archivos procesados ===")
        
        # Guardar estadísticas persistentes
        self._save_persistent_stats()
    
    def _process_all_pending_files(self) -> dict:
        """Procesa todos los archivos pendientes y retorna estadísticas"""
        files_processed = 0
        files_with_errors = 0
        
        while True:
            next_file = self.get_next_pending_file()
            if not next_file:
                break
            
            # En modo diurno, NO procesar subtítulos automáticamente
            # Los subtítulos se procesan solo en modo nocturno
            if self.process_single_file(next_file, process_subtitles=False):
                files_processed += 1
            else:
                files_with_errors += 1
                self.logger.error(f"Error procesando: {next_file}")
        
        # Actualizar estadísticas globales para las notificaciones
        self.stats['files_processed'] += files_processed
        if files_with_errors > 0:
            self.stats['subtitles_errors'] += files_with_errors
        
        return {'processed': files_processed, 'errors': files_with_errors}
    
    def _determine_notification_type(self, processing_result: dict) -> str:
        """Determina el tipo de notificación basado en los resultados del procesamiento"""
        files_processed = processing_result['processed']
        files_with_errors = processing_result['errors']
        
        if files_processed > 0:
            if files_with_errors > 0:
                self.logger.info(f"Procesamiento completado con {files_with_errors} errores")
            return "success"
        elif files_with_errors == 0:
            self.logger.info("No se encontraron archivos nuevos para procesar")
            return "success"
        else:
            self.logger.warning("No se procesó ningún archivo")
            return "error"

    def _load_persistent_stats(self) -> dict:
        """Carga las estadísticas persistentes desde progress.json"""
        default_stats = {
            'files_found': 0,
            'files_new': 0,
            'files_processed': 0,
            'files_compressed': 0,
            'files_renamed': 0,
            'files_skipped': 0,
            'subtitles_translated': 0,
            'subtitles_errors': 0
        }
        
        try:
            progress_file = self.tmp_dir / self.PROGRESS_FILE_NAME
            if progress_file.exists():
                with open(progress_file, 'r', encoding='utf-8') as f:
                    progress_data = json.load(f)
                    # Extraer estadísticas de la sección processing
                    if 'processing' in progress_data and 'stats' in progress_data['processing']:
                        loaded_stats = progress_data['processing']['stats']
                        self.logger.info("Estadísticas persistentes cargadas exitosamente")
                        # Asegurar que todas las claves estén presentes
                        for key, default_value in default_stats.items():
                            if key not in loaded_stats:
                                loaded_stats[key] = default_value
                        return loaded_stats
                    else:
                        self.logger.info("No se encontraron estadísticas en progress.json, inicializando con valores por defecto")
            else:
                self.logger.info("No se encontró progress.json, inicializando con valores por defecto")
        except Exception as e:
            self.logger.warning(f"Error cargando estadísticas persistentes: {e}")
        
        # Valores por defecto si no hay archivo o hay error
        return default_stats.copy()

    def _save_persistent_stats(self):
        """Las estadísticas se guardan automáticamente en progress.json por el processor"""
        # No es necesario guardar por separado ya que el processor actualiza progress.json
        # con todas las estadísticas en cada ejecución
        pass

    def _correct_pending_paths(self, paths: list) -> list:
        """Corrige las rutas de archivos pendientes del contenedor al formato del host
        
        Convierte rutas del contenedor (/mediajelly/media/...) al formato del host (/home/tafurc/mediaJelly/media/...)
        """
        corrected_paths = []
        for path_str in paths:
            if isinstance(path_str, Path):
                path_str = str(path_str)
            
            # Convertir rutas del contenedor al host
            if path_str.startswith('/mediajelly/media/'):
                corrected_path = path_str.replace('/mediajelly/media/', '/home/tafurc/mediaJelly/media/', 1)
                self.logger.debug(f"Ruta corregida (contenedor->host): {path_str} -> {corrected_path}")
                corrected_paths.append(corrected_path)
            else:
                # Ruta ya está en formato correcto
                corrected_paths.append(path_str)
        
        return corrected_paths

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