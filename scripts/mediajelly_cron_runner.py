#!/usr/bin/env python3
"""
MediaJelly Cron Runner - Optimizado
Orquestador principal del sistema MediaJelly
"""

import os
import sys
import logging
import subprocess
import signal
import threading
from dataclasses import dataclass, field
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Tuple

# Importar módulo de emojis
from mediajelly_emoji import EmojiGenerator
from mediajelly_utils import MediaJellyPaths, archive_legacy_log_file, create_compressed_rotating_file_handler

# Agregar scripts al path para imports
sys.path.insert(0, str(Path(__file__).parent))

# Importar módulos directamente para evitar subprocess overhead
try:
    from mediajelly_scanner import MediaScanner

    SCANNER_AVAILABLE = True
except ImportError:
    SCANNER_AVAILABLE = False

try:
    from mediajelly_processor import MediaJellyProcessor

    PROCESSOR_AVAILABLE = True
except ImportError:
    PROCESSOR_AVAILABLE = False

try:
    from mediajelly_language_detector import main as language_detector_main

    LANGUAGE_DETECTOR_AVAILABLE = True
except ImportError:
    LANGUAGE_DETECTOR_AVAILABLE = False

# Ventana nocturna para subtítulos: de 00:00 hasta NIGHT_END_HOUR (exclusivo)
NIGHT_END_HOUR = 6
# Segundos de gracia entre SIGTERM y SIGKILL al cortar el traductor
SUBTITLE_KILL_GRACE_SECONDS = 30
# Tope para la traducción de .nfo, que corre en segundo plano durante el ciclo
NFO_TIMEOUT_SECONDS = 600


@dataclass
class BackgroundJob:
    """Subproceso que corre en paralelo al resto del ciclo, con hora límite propia."""

    name: str
    proc: subprocess.Popen
    timer: threading.Timer
    timed_out: threading.Event = field(default_factory=threading.Event)


class MediaJellyCron:
    """
    Orquestador principal del sistema MediaJelly.

    Coordina la ejecución de todos los componentes: scanner, detector de idiomas,
    processor y traductor de subtítulos.
    """

    def __init__(self, base_path: Optional[str] = None) -> None:
        """
        Inicializa el cron runner.

        Args:
            base_path: Ruta base de MediaJelly. Si es None, se detecta automáticamente.
        """
        if base_path is None:
            # Detectar automáticamente el entorno usando rutas centralizadas
            base_path = str(MediaJellyPaths.get_base_path())
        self.base_path: Path = Path(base_path)
        self.scripts_path: Path = self.base_path / "scripts"
        self.tmp_path: Path = self.scripts_path / "tmp"
        self.tmp_path.mkdir(parents=True, exist_ok=True)
        self.scanner_script = self.scripts_path / "mediajelly_scanner.py"
        self.language_detector_script = self.scripts_path / "mediajelly_language_detector.py"
        self.processor_script = self.scripts_path / "mediajelly_processor.py"
        self.subtitle_script = self.scripts_path / "mediajelly_subtitle_translator.py"
        self.lock_file = self.tmp_path / "cron_runner.lock"
        # Descriptor para mantener lock (si usamos flock)
        self._lock_fd = None

        # Intentar obtener un bloqueo atómico con flock para evitar condiciones de carrera.
        try:
            import fcntl

            fd = os.open(str(self.lock_file), os.O_RDWR | os.O_CREAT, 0o644)
            try:
                # Intentar bloqueo exclusivo no bloqueante
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (BlockingIOError, OSError):
                # No se pudo adquirir el lock: leer PID existente e informar
                try:
                    existing = Path(str(self.lock_file)).read_text().strip()
                    if existing:
                        try:
                            pid = int(existing.splitlines()[0])
                            with open(f"/proc/{pid}/cmdline", "r") as f:
                                cmdline = f.read().replace('\0', ' ').strip()
                            if "mediajelly_cron_runner.py" in cmdline:
                                print(f"Otra instancia del cron runner ya está ejecutándose (PID: {pid}). Saliendo...")
                                sys.exit(1)
                        except Exception:
                            # No se pudo verificar el PID, salir de forma segura
                            pass
                except Exception:
                    pass

                os.close(fd)
                print("No se pudo obtener lock (flock), saliendo...")
                sys.exit(1)

            # Si tenemos el lock, escribir PID y timestamp en el archivo y conservar fd abierto
            import time
            os.ftruncate(fd, 0)
            os.write(fd, f"{os.getpid()}\n{int(time.time())}\n".encode())
            os.fsync(fd)
            self._lock_fd = fd
            print(f"Lock adquirido (flock) por PID {os.getpid()}")

        except Exception as e:
            # Si fcntl no está disponible o hubo un error inesperado, usar fallback simple
            print(f"Fallo al adquirir flock: {e}. Usando método de archivo (no atómico) como fallback.")
            try:
                if self.lock_file.exists():
                    lock_content = self.lock_file.read_text().strip()
                    if not lock_content:
                        print("Lock file vacío encontrado, eliminando...")
                        self.lock_file.unlink()
                    else:
                        pid = int(lock_content)
                        try:
                            with open(f"/proc/{pid}/cmdline", "r") as f:
                                cmdline = f.read().replace('\0', ' ').strip()
                            if "mediajelly_cron_runner.py" in cmdline:
                                print(f"Otra instancia del cron runner ya está ejecutándose (PID: {pid}). Saliendo...")
                                self.logger.info(f"Lock file existente con PID {pid} y cmdline: {cmdline}")
                                print(f"Comando: {cmdline}")
                                sys.exit(1)
                            else:
                                print(f"Proceso {pid} existe pero no es nuestro (cmdline: {cmdline[:100]}...)")
                                print("Continuando con la ejecución...")
                        except (FileNotFoundError, ProcessLookupError):
                            print(f"Lock file huérfano encontrado (PID {pid} no existe), eliminando...")
                            self.lock_file.unlink()
                        except Exception as e2:
                            print(f"Error al verificar proceso {pid}: {e2}")
                            print("Eliminando lock por seguridad...")
                            self.lock_file.unlink()
                import time
                lock_content = f"{os.getpid()}\n{int(time.time())}"
                self.lock_file.write_text(lock_content)
                print(f"Lock creado para PID {os.getpid()} (fallback)")
            except Exception as e3:
                print(f"No se pudo crear lock: {e3}")
                sys.exit(1)

        # Setup logging
        log_path = self.tmp_path / "logs"
        log_path.mkdir(parents=True, exist_ok=True)

        legacy_log = self.tmp_path / "logs" / "cron.log"
        archive_legacy_log_file(legacy_log, log_path, archive_name="cron.log", backup_count=5)

        try:
            file_handler = create_compressed_rotating_file_handler(
                log_path / "cron_runner.log", max_bytes=10 * 1024 * 1024, backup_count=5
            )
            logging.basicConfig(
                level=logging.INFO,
                format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                handlers=[file_handler, logging.StreamHandler()],
            )
            self.logger = logging.getLogger(__name__)
            self.logger.info("Logging configurado correctamente")
        except Exception as e:
            print(f"Error configurando logging: {e}")
            # Fallback a solo stdout
            logging.basicConfig(
                level=logging.INFO,
                format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
                handlers=[logging.StreamHandler()],
            )
            self.logger = logging.getLogger(__name__)

    def _run_script(self, script_path: Path, timeout: int = 300) -> Tuple[bool, str]:
        """
        Ejecuta un script Python con timeout.

        Args:
            script_path: Ruta al script a ejecutar.
            timeout: Tiempo máximo de ejecución en segundos.

        Returns:
            Tuple conteniendo:
            - bool: True si la ejecución fue exitosa (código 0), False en caso contrario.
            - str: Salida estándar (stdout) si fue exitoso, o error (stderr) si falló.
        """
        try:
            self.logger.info(f"Ejecutando: {script_path}")
            result = subprocess.run([sys.executable, str(script_path)], timeout=timeout, capture_output=True, text=True)

            if result.returncode == 0:
                self.logger.info(f"Script {script_path.name} completado exitosamente")
                return True, result.stdout
            else:
                self.logger.error(f"Error en {script_path.name}: {result.stderr}")
                return False, result.stderr

        except subprocess.TimeoutExpired:
            self.logger.error(f"Timeout en {script_path.name}")
            return False, "Timeout"
        except Exception as e:
            self.logger.error(f"Error ejecutando {script_path.name}: {e}")
            return False, str(e)

    def run_scanner(self) -> bool:
        """
        Ejecuta el scanner de archivos.

        Intenta usar la clase MediaScanner directamente si está disponible,
        de lo contrario hace fallback a ejecución por subprocess.

        Returns:
            bool: True si la ejecución fue exitosa.
        """
        if not SCANNER_AVAILABLE:
            self.logger.error("MediaScanner no disponible, usando subprocess fallback")
            success, _ = self._run_script(self.scanner_script, timeout=600)
            return success

        try:
            self.logger.info("Ejecutando scanner usando clase MediaScanner")
            media_folders = ["/mediajelly/media/anime", "/mediajelly/media/Peliculas", "/mediajelly/media/series"]

            scanner = MediaScanner()
            total_found, new_detected = scanner.run(media_folders)

            self.logger.info(f"Scanner completado: {total_found} archivos encontrados, {new_detected} nuevos")
            return True

        except Exception as e:
            self.logger.error(f"Error ejecutando scanner: {e}")
            return False

    def _is_night_time(self) -> bool:
        """
        Verifica si es hora de procesar subtítulos (horario nocturno).

        Returns:
            bool: True si la hora actual está entre 00:00 y 05:59.
        """
        return datetime.now().hour < NIGHT_END_HOUR

    def _seconds_until_night_end(self) -> float:
        """
        Calcula cuánto falta para que termine la ventana nocturna.

        Returns:
            float: Segundos hasta las NIGHT_END_HOUR:00 de hoy (0 si ya pasó).
        """
        now = datetime.now()
        night_end = now.replace(hour=NIGHT_END_HOUR, minute=0, second=0, microsecond=0)
        return max(0.0, (night_end - now).total_seconds())

    def _start_background(self, name: str, cmd: List[str], timeout: float) -> Optional[BackgroundJob]:
        """
        Lanza un subproceso sin esperar a que termine. Un temporizador termina su
        grupo de procesos completo al cumplirse `timeout`, aunque el ciclo esté
        ocupado en otra etapa.

        Args:
            name: Nombre para los logs.
            cmd: Comando a ejecutar.
            timeout: Segundos máximos de ejecución.

        Returns:
            Optional[BackgroundJob]: El trabajo lanzado o None si no se pudo iniciar.
        """
        try:
            self.logger.info(f"Lanzando en segundo plano: {name} (límite: {timeout / 60:.0f} min)")
            # Sin pipes: puede durar horas y cada script escribe su propio log
            proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, start_new_session=True)
        except Exception as e:
            self.logger.error(f"Error lanzando {name}: {e}")
            return None

        timed_out = threading.Event()

        def on_deadline():
            # Si ya terminó por su cuenta no hay nada que cortar (ni que reportar como corte)
            if proc.poll() is not None:
                return
            timed_out.set()
            self._terminate_process_group(proc)

        timer = threading.Timer(timeout, on_deadline)
        timer.daemon = True
        timer.start()
        return BackgroundJob(name=name, proc=proc, timer=timer, timed_out=timed_out)

    def _finish_background(self, job: Optional[BackgroundJob]) -> Optional[int]:
        """
        Espera a que termine un trabajo en segundo plano (o a que lo corte su hora límite).

        Args:
            job: Trabajo devuelto por `_start_background`.

        Returns:
            Optional[int]: Código de salida, o None si no se lanzó o fue cortado por hora límite.
        """
        if job is None:
            return None
        returncode = job.proc.wait()
        job.timer.cancel()
        return None if job.timed_out.is_set() else returncode

    def _start_subtitle_translator(self, extra_args: Optional[List[str]] = None) -> Optional[BackgroundJob]:
        """
        Lanza el traductor de subtítulos en segundo plano, limitado a la ventana nocturna.

        Al llegar la hora límite se termina el grupo de procesos completo (traductor,
        ffmpeg y Whisper). El traductor guarda progreso por archivo, así que la
        siguiente noche continúa donde quedó.

        Args:
            extra_args: Argumentos adicionales para el traductor (ej: ["--redo-queue"]).

        Returns:
            Optional[BackgroundJob]: El trabajo lanzado, o None si la ventana ya terminó.
        """
        remaining = self._seconds_until_night_end()
        if remaining <= 0:
            self.logger.info("Ventana nocturna terminada, no se inicia el traductor de subtítulos")
            return None

        cmd = [sys.executable, str(self.subtitle_script)] + list(extra_args or [])
        return self._start_background("traductor de subtítulos", cmd, remaining)

    def _finish_subtitle_translator(self, job: Optional[BackgroundJob]) -> bool:
        """
        Espera al traductor de subtítulos lanzado con `_start_subtitle_translator`.

        Returns:
            bool: True si terminó bien, se pausó por hora límite o no llegó a lanzarse.
        """
        if job is None:
            return True

        returncode = self._finish_background(job)
        if job.timed_out.is_set():
            self.logger.warning(
                f"Hora límite ({NIGHT_END_HOUR:02d}:00) alcanzada - subtítulos pausados, se reanudan la próxima noche"
            )
            return True
        if returncode == 0:
            self.logger.info("Subtitle translator completado exitosamente")
            return True
        self.logger.warning(f"Subtitle translator terminó con código: {returncode}")
        return False

    def run_subtitle_translator(self, extra_args: Optional[List[str]] = None) -> bool:
        """
        Ejecuta el traductor de subtítulos y espera a que termine o a la hora límite.

        Args:
            extra_args: Argumentos adicionales para el traductor (ej: ["--redo-queue"]).

        Returns:
            bool: True si terminó bien o se pausó por hora límite.
        """
        return self._finish_subtitle_translator(self._start_subtitle_translator(extra_args))

    def _terminate_process_group(self, proc: subprocess.Popen) -> None:
        """
        Termina un subproceso y todos sus hijos (SIGTERM y, si no basta, SIGKILL).

        Args:
            proc: Proceso lanzado con start_new_session=True.
        """
        for sig, wait_seconds in ((signal.SIGTERM, SUBTITLE_KILL_GRACE_SECONDS), (signal.SIGKILL, 10)):
            try:
                os.killpg(proc.pid, sig)
            except ProcessLookupError:
                return
            except Exception as e:
                self.logger.error(f"Error enviando señal {sig} al subproceso {proc.pid}: {e}")
            try:
                proc.wait(timeout=wait_seconds)
                return
            except subprocess.TimeoutExpired:
                continue

    def _start_nfo_translator(self) -> Optional[BackgroundJob]:
        """
        Lanza la traducción de archivos .nfo en segundo plano. Es trabajo de red, no
        de CPU, así que no hay motivo para que la compresión espere por ella.

        Returns:
            Optional[BackgroundJob]: El trabajo lanzado o None si no se pudo iniciar.
        """
        cmd = [sys.executable, str(self.scripts_path / "mediajelly_nfo_translator.py"), str(self.base_path / "media")]
        return self._start_background("traductor de NFO", cmd, NFO_TIMEOUT_SECONDS)

    def _finish_nfo_translator(self, job: Optional[BackgroundJob]) -> None:
        """Recoge el traductor de NFO lanzado con `_start_nfo_translator` y registra el resultado."""
        if job is None:
            return
        returncode = self._finish_background(job)
        if job.timed_out.is_set():
            self.logger.warning("Traductor de NFO cortado por tiempo; continúa en el próximo ciclo")
        elif returncode == 0:
            self.logger.info("Traducción de NFO completada")
        else:
            self.logger.warning(f"Traductor de NFO terminó con código: {returncode}")

    def run_language_detector(self) -> bool:
        """
        Ejecuta el detector de idiomas.

        Realiza un análisis de idiomas después del procesamiento principal.

        Returns:
            bool: True si la ejecución fue exitosa.
        """
        # Para mayor robustez siempre ejecutar detector de idiomas como proceso independiente
        # Evita problemas de ImportError o dependencias en el proceso principal y mantiene aislamiento
        try:
            self.logger.info(f"{EmojiGenerator.audio()} Ejecutando detector de idiomas como proceso independiente")
            success, output = self._run_script(self.language_detector_script, timeout=1800)
            if success:
                self.logger.info(f"{EmojiGenerator.success()} Detector de idiomas completado exitosamente (subprocess)")
            else:
                self.logger.warning(f"{EmojiGenerator.error()} Detector de idiomas falló (subprocess): {output}")
            return success
        except Exception as e:
            self.logger.error(f"{EmojiGenerator.error()} Error ejecutando detector de idiomas: {e}")
            return False

    def run_processor(self) -> bool:
        """
        Ejecuta el procesador de archivos principal.

        Intenta usar la clase MediaJellyProcessor directamente si está disponible,
        de lo contrario hace fallback a ejecución por subprocess.

        Returns:
            bool: True si la ejecución fue exitosa.
        """
        if not PROCESSOR_AVAILABLE:
            self.logger.error("MediaJellyProcessor no disponible, usando subprocess fallback")
            success, _ = self._run_script(self.processor_script, timeout=7200)
            return success

        try:
            self.logger.info("Ejecutando processor usando clase MediaJellyProcessor")
            processor = MediaJellyProcessor()
            stats = processor.run()

            self.logger.info(f"Processor completado: {stats.files_processed} archivos procesados")
            return True

        except Exception as e:
            self.logger.error(f"Error ejecutando processor: {e}")
            return False

    def _release_lock(self) -> None:
        """Libera el archivo de bloqueo"""
        try:
            # Cerrar descriptor de lock si fue usado (flock)
            if getattr(self, "_lock_fd", None):
                try:
                    os.close(self._lock_fd)
                except Exception:
                    pass
            if self.lock_file.exists():
                self.lock_file.unlink()
            print("Lock liberado después del procesamiento")
        except Exception as e:
            print(f"Error liberando lock: {e}")

    def run_cycle(self) -> bool:
        """
        Ejecuta un ciclo completo de procesamiento.

        Flujo de ejecución:
        1. Scanner: Detecta nuevos archivos.
        2. Traductor de NFO: se lanza en segundo plano y se recoge al final.
        3. Language Detector: Analiza idiomas antes del procesamiento.
        4. Processor: Comprime y optimiza archivos. De noche, el traductor de
           subtítulos corre en paralelo hasta la hora límite.

        Returns:
            bool: True si el ciclo completo fue exitoso.
        """
        # Función para limpiar el lock en caso de señales
        def cleanup_lock(signum=None, frame=None):
            if self.lock_file.exists():
                try:
                    self.lock_file.unlink()
                    print(f"Lock limpiado por señal {signum}" if signum else "Lock limpiado")
                except Exception as e:
                    print(f"Error limpiando lock: {e}")

        # Registrar manejadores de señales para limpieza del lock
        signal.signal(signal.SIGTERM, cleanup_lock)
        signal.signal(signal.SIGINT, cleanup_lock)

        nfo_job: Optional[BackgroundJob] = None
        subtitle_job: Optional[BackgroundJob] = None
        try:
            self.logger.info("Iniciando ciclo de procesamiento")

            # Paso 1: Ejecutar scanner siempre
            if not self.run_scanner():
                self.logger.error("Error en scanner, saltando procesamiento")
                return False

            # Traducción de .nfo en segundo plano: no retrasa la compresión
            nfo_job = self._start_nfo_translator()

            # Verificar si es hora de procesar subtítulos (noche)
            is_night = self._is_night_time()
            current_time = datetime.now().strftime("%H:%M")

            # Ejecutar detector de idiomas ANTES del procesador para que el processor pueda usar la cache
            self.logger.info(f"{EmojiGenerator.audio()} Ejecutando análisis de idiomas antes del procesamiento...")
            detector_ok = self.run_language_detector()

            # Si el detector falló, registramos warning pero continuamos con el processor
            if not detector_ok:
                self.logger.warning("Detector de idiomas no estuvo disponible o falló; continuando con processor sin cache")

            if is_night:
                self.logger.info(f"Es de noche ({current_time}) - Ejecutando subtítulos y compresión en paralelo")
                # Whisper usa CPU y la compresión GPU: corren a la vez. El traductor omite
                # los videos que aún están pendientes de compresión, así que no chocan.
                subtitle_job = self._start_subtitle_translator()
            else:
                self.logger.info(f"Es de día ({current_time}) - Ejecutando solo compresión")

            processor_ok = self.run_processor()
            if not processor_ok:
                self.logger.error("Error en processor")

            if is_night:
                if not self._finish_subtitle_translator(subtitle_job):
                    self.logger.warning("Error en traductor de subtítulos")
                subtitle_job = None

                # Con el tiempo que quede de la ventana, rehacer subtítulos antiguos de Whisper
                redo_queue = self.tmp_path / "whisper_redo_queue.txt"
                if redo_queue.exists() and redo_queue.stat().st_size > 0:
                    if not self.run_subtitle_translator(["--redo-queue"]):
                        self.logger.warning("Error rehaciendo subtítulos de Whisper")

            if not processor_ok:
                return False

            self.logger.info("Ciclo completado exitosamente")
            return True
        finally:
            # No dejar trabajos en segundo plano sin recoger, pase lo que pase en el ciclo
            self._finish_subtitle_translator(subtitle_job)
            self._finish_nfo_translator(nfo_job)
            # Forzar flush de logs antes de salir
            for handler in self.logger.handlers:
                handler.flush()
            # El lock se libera solo cuando todas las etapas terminaron
            self._release_lock()


def main():
    """Función principal"""
    try:
        cron = MediaJellyCron()

        # Ejecutar una sola vez y terminar
        cron.run_cycle()
    except Exception as e:
        print(f"Error en main: {e}")
    finally:
        # Forzar flush de todos los handlers de logging
        for logger_name in ["__main__", "mediajelly_cron_runner"]:
            logger = logging.getLogger(logger_name)
            for handler in logger.handlers:
                try:
                    handler.flush()
                except:
                    pass


if __name__ == "__main__":
    main()
