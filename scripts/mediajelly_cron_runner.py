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
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Tuple

# Importar módulo de emojis
from mediajelly_emoji import EmojiGenerator

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

try:
    from mediajelly_subtitle_translator import main as subtitle_main

    SUBTITLE_AVAILABLE = True
except ImportError:
    SUBTITLE_AVAILABLE = False

# Importar traductor de NFO (.nfo) si está disponible
try:
    from mediajelly_nfo_translator import NFOTranslator

    NFO_AVAILABLE = True
except ImportError:
    NFO_AVAILABLE = False


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
            # Detectar automáticamente el entorno
            if Path("/mediajelly").exists():
                base_path = "/mediajelly"
            else:
                base_path = "/home/tafurc/mediaJelly"
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
        log_path = self.scripts_path / "logs"
        log_path.mkdir(exist_ok=True)

        try:
            from logging.handlers import RotatingFileHandler
            file_handler = RotatingFileHandler(
                log_path / "cron_runner.log", maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
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
        current_hour = datetime.now().hour
        # Noche: de 00:00 a 05:59 (0-5)
        return current_hour <= 5

    def run_subtitle_translator(self) -> bool:
        """
        Ejecuta el traductor de subtítulos.

        Intenta usar la función main del módulo directamente si está disponible,
        de lo contrario hace fallback a ejecución por subprocess.

        Returns:
            bool: True si la ejecución fue exitosa.
        """
        if not SUBTITLE_AVAILABLE:
            self.logger.error("Subtitle translator no disponible, usando subprocess fallback")
            success, _ = self._run_script(self.subtitle_script, timeout=3600)
            return success

        try:
            self.logger.info("Ejecutando subtitle translator usando función directa")

            # Crear argumentos simulados para el subtitle translator
            import sys
            from io import StringIO

            # Guardar sys.argv original
            original_argv = sys.argv[:]

            # Simular argumentos para procesamiento automático
            sys.argv = ["mediajelly_subtitle_translator.py"]

            # Capturar salida
            old_stdout = sys.stdout
            sys.stdout = StringIO()

            try:
                # Llamar directamente a la función main
                subtitle_main()
                self.logger.info("Subtitle translator completado exitosamente")
                return True
            finally:
                # Restaurar stdout y argv
                sys.stdout = old_stdout
                sys.argv = original_argv

        except SystemExit as e:
            # El subtitle translator hace sys.exit(1) si no es hora de noche
            if e.code == 1:
                self.logger.warning("Subtitle translator cancelado (fuera de horario de noche)")
                return False
            else:
                self.logger.error(f"Subtitle translator terminó con código: {e.code}")
                raise
        except Exception as e:
            self.logger.error(f"Error ejecutando subtitle translator: {e}")
            return False

    def run_nfo_translator(self) -> bool:
        """
        Ejecuta la traducción de archivos .nfo y .info si existen.

        Intenta usar la clase NFOTranslator directamente si está disponible, de lo
        contrario hace fallback a ejecución por subprocess.

        Returns:
            bool: True si completó la verificación (aunque no haya archivos para procesar)
        """
        try:
            media_dir = self.base_path / "media"

            # Buscar archivos .nfo y .info
            nfo_files = list(media_dir.rglob("*.nfo")) + list(media_dir.rglob("*.info"))
            if not nfo_files:
                self.logger.info("No hay archivos .nfo/.info para procesar")
                return True

            self.logger.info(f"Detectados {len(nfo_files)} archivos .nfo/.info para revisar")

            if NFO_AVAILABLE:
                try:
                    translator = NFOTranslator()
                    # Procesar por subcarpetas principales para limitar el alcance
                    for sub in ["anime", "Peliculas", "series"]:
                        path = media_dir / sub
                        if path.exists():
                            translator.process_directory(str(path))

                    self.logger.info("Traducción de NFO completada (módulo)")
                    return True
                except Exception as e:
                    self.logger.error(f"Error en NFOTranslator: {e}")
                    return False
            else:
                # Fallback a subprocess
                try:
                    self.logger.info("Ejecutando mediajelly_nfo_translator.py vía subprocess")
                    result = subprocess.run([sys.executable, str(self.scripts_path / "mediajelly_nfo_translator.py"), str(media_dir)], timeout=3600, capture_output=True, text=True)
                    if result.returncode == 0:
                        self.logger.info("Traducción de NFO completada (subprocess)")
                        return True
                    else:
                        self.logger.error(f"NFO translator falló (subprocess): {result.stderr}")
                        return False
                except Exception as e:
                    self.logger.error(f"Error ejecutando NFO translator: {e}")
                    return False

        except Exception as e:
            self.logger.error(f"Error detectando archivos .nfo/.info: {e}")
            return False

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
        2. Processor: Comprime y optimiza archivos (incluye traducción de subtítulos de noche).
        3. Language Detector: Analiza idiomas después del procesamiento.

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

        processor_executed = False
        try:
            self.logger.info("Iniciando ciclo de procesamiento")

            # Paso 1: Ejecutar scanner siempre
            if not self.run_scanner():
                self.logger.error("Error en scanner, saltando procesamiento")
                return False

            # Paso adicional: revisar y traducir archivos .nfo / .info si existen
            try:
                if not self.run_nfo_translator():
                    self.logger.warning("Error en NFO/.info translator, continuando con el ciclo")
            except Exception as e:
                self.logger.error(f"Error ejecutando run_nfo_translator: {e}")

            # Verificar si es hora de procesar subtítulos (noche)
            is_night = self._is_night_time()
            current_time = datetime.now().strftime("%H:%M")

            # Ejecutar detector de idiomas ANTES del procesador para que el processor pueda usar la cache
            self.logger.info(f"{EmojiGenerator.audio()} Ejecutando análisis de idiomas antes del procesamiento...")
            detector_ok = self.run_language_detector()

            if is_night:
                self.logger.info(f"Es de noche ({current_time}) - Ejecutando subtítulos + compresión")

                # Ejecutar subtítulos y compresión
                if not self.run_subtitle_translator():
                    self.logger.warning("Error en traductor de subtítulos, continuando con compresión")

                # Si el detector falló, registramos warning pero continuamos con el processor
                if not detector_ok:
                    self.logger.warning("Detector de idiomas no estuvo disponible o falló; continuando con processor sin cache")

                if not self.run_processor():
                    self.logger.error("Error en processor")
                    return False
                processor_executed = True
                # Liberar lock después de que el processor termine
                self._release_lock()
            else:
                self.logger.info(f"Es de día ({current_time}) - Ejecutando solo compresión")

                # Ejecutar compresión
                if not self.run_processor():
                    self.logger.error("Error en processor")
                    return False
                processor_executed = True
                # Liberar lock después de que el processor termine
                self._release_lock()

            # Paso 2: Ejecutar detector de idiomas ANTES del procesador para que el processor pueda usar la cache
            self.logger.info(f"{EmojiGenerator.audio()} Ejecutando análisis de idiomas antes del procesamiento...")
            if not self.run_language_detector():
                self.logger.warning(
                    f"{EmojiGenerator.warning_msg()} Advertencia en detector de idiomas, continuando con procesamiento"
                )

            self.logger.info("Ciclo completado exitosamente")
            return True
        finally:
            # Forzar flush de logs antes de salir
            for handler in self.logger.handlers:
                handler.flush()
            # Limpiar archivo de bloqueo al finalizar, pero solo si el processor no se ejecutó
            # (para evitar liberar el lock mientras el processor sigue trabajando)
            if not processor_executed:
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
