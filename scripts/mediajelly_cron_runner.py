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

class MediaJellyCronRunner:
    """Orquestador principal de MediaJelly"""
    
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
        
        # Scripts Python
        self.scanner_script = self.scripts_dir / "mediajelly_scanner.py"
        self.processor_script = self.scripts_dir / "mediajelly_processor.py"
        self.notifier_script = self.scripts_dir / "mediajelly_notifier.py"
        
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
            'files_skipped': 0
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
    
    def send_notification(self, notification_type: str) -> bool:
        """Envía notificación vía Telegram según el tipo especificado"""
        try:
            self.logger.info("Enviando notificación...")
            
            cmd = [
                sys.executable, str(self.notifier_script),
                "scan_result", notification_type,
                str(self.stats['files_found']),
                str(self.stats['files_new']),
                str(self.stats['files_processed']),
                str(self.stats['files_compressed']),
                str(self.stats['files_renamed']),
                str(self.stats['files_skipped'])
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
                return False
                
        except Exception as e:
            self.logger.error(f"Error enviando notificación: {e}")
            return False
    
    def _mark_as_notified(self):
        """Marca el estado actual como notificado en progress.json"""
        try:
            progress_file = self.tmp_dir / "progress.json"
            if progress_file.exists():
                with open(progress_file, 'r') as f:
                    progress_data = json.load(f)
                
                progress_data['notified'] = True
                
                with open(progress_file, 'w') as f:
                    json.dump(progress_data, f, indent=2)
                
                self.logger.info("Estado marcado como notificado (notified=True)")
        except Exception as e:
            self.logger.warning(f"Error marcando como notificado: {e}")
    
    def send_error_notification(self, error_message: str) -> bool:
        """Enviar notificación de error"""
        try:
            cmd = [
                sys.executable, str(self.notifier_script),
                "critical_error", error_message, str(self.log_file)
            ]
            
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60
            )
            
            return result.returncode == 0
        except Exception:
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
    
    def run(self) -> bool:
        """Ejecuta el flujo completo de escaneo y procesamiento"""
        if not self.acquire_lock():
            return False
        
        try:
            self.logger.info("=== Iniciando ejecución automática Python ===")
            
            # 1. Ejecuta el scanner
            if not self.run_scanner():
                self.send_error_notification("Falló el escaneo automático")
                return False
            
            # 2. Verifica si hay archivos para procesar
            if not self.check_pending_files():
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
                subprocess.run(cmd, timeout=60)
                
                # Verifica y ejecuta limpieza si es necesario
                self.check_and_cleanup_if_needed()
                
                # Guarda el estado final del progreso
                self.logger.info("=== Ejecución automática Python finalizada ===")
                return True
            
            # 3. Ejecuta el procesador
            if not self.run_processor():
                self.send_error_notification("Falló el procesamiento automático")
                return False
            
            # 4. Envía notificación de éxito
            self.send_notification("success")
            
            
            # Verifica y ejecuta limpieza si es necesario
            self.check_and_cleanup_if_needed()
            
            self.logger.info("=== Ejecución automática Python finalizada ===")
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