#!/usr/bin/env python3
"""
MediaJelly Cron Runner - Optimizado
Orquestador principal del sistema MediaJelly
"""

import os
import sys
import logging
import subprocess
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Tuple

# Importar módulo de emojis
from mediajelly_emoji import EmojiGenerator

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

        # Verificar si ya hay otra instancia ejecutándose
        if self.lock_file.exists():
            try:
                pid = int(self.lock_file.read_text().strip())
                # Verificar si el proceso aún está vivo usando pgrep
                result = subprocess.run(["pgrep", "-f", "mediajelly_cron_runner.py"], capture_output=True, text=True)
                running_pids = result.stdout.strip().split("\n") if result.stdout.strip() else []
                running_pids = [p for p in running_pids if p.strip()]

                if str(pid) in running_pids:
                    print(f"Otra instancia del cron runner ya está ejecutándose (PID: {pid}). Saliendo...")
                    sys.exit(1)
                else:
                    print("Lock file huérfano encontrado, eliminando...")
                    self.lock_file.unlink()
            except (ValueError, subprocess.SubprocessError):
                # PID inválido o error al verificar, eliminar lock huérfano
                print("Lock file huérfano encontrado, eliminando...")
                self.lock_file.unlink()

        # Crear archivo de bloqueo
        self.lock_file.write_text(str(os.getpid()))

        # Setup logging
        log_path = self.scripts_path / "logs"
        log_path.mkdir(exist_ok=True)

        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
            handlers=[logging.FileHandler(log_path / "cron_runner.log"), logging.StreamHandler()],
        )
        self.logger = logging.getLogger(__name__)

    def _run_script(self, script_path: Path, timeout: int = 300) -> Tuple[bool, str]:
        """
        Ejecuta un script Python con timeout.
        
        Args:
            script_path: Ruta al script a ejecutar
            timeout: Timeout en segundos
            
        Returns:
            Tuple con (éxito, output)
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
        """Ejecuta el scanner de archivos usando la clase directamente"""
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
        """Verifica si es hora de procesar subtítulos (noche: 00:00-05:59)"""
        current_hour = datetime.now().hour
        # Noche: de 00:00 a 05:59 (0-5)
        return current_hour <= 5

    def run_subtitle_translator(self) -> bool:
        """Ejecuta el traductor de subtítulos usando función directa"""
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

    def run_language_detector(self) -> bool:
        """Ejecuta el detector de idiomas antes del procesador"""
        if not LANGUAGE_DETECTOR_AVAILABLE:
            self.logger.warning("Language detector no disponible, usando subprocess fallback")
            success, _ = self._run_script(self.language_detector_script, timeout=1800)  # 30 minutos
            return success

        try:
            self.logger.info(f"{EmojiGenerator.audio()} Ejecutando detector de idiomas usando función directa")

            # Llamar directamente a la función main
            language_detector_main()
            self.logger.info(f"{EmojiGenerator.success()} Detector de idiomas completado exitosamente")
            return True

        except Exception as e:
            self.logger.error(f"{EmojiGenerator.error()} Error ejecutando detector de idiomas: {e}")
            return False

    def run_processor(self) -> bool:
        """Ejecuta el procesador de archivos usando la clase directamente"""
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

    def run_cycle(self) -> bool:
        """Ejecuta un ciclo completo de scanner + detector de idiomas + processor/subtítulos según la hora"""
        try:
            self.logger.info("Iniciando ciclo de procesamiento")

            # Paso 1: Ejecutar scanner siempre
            if not self.run_scanner():
                self.logger.error("Error en scanner, saltando procesamiento")
                return False

            # Paso 2: Ejecutar detector de idiomas ANTES del procesador
            self.logger.info(f"{EmojiGenerator.audio()} Ejecutando pre-análisis de idiomas...")
            if not self.run_language_detector():
                self.logger.warning(
                    f"{EmojiGenerator.warning_msg()} Advertencia en detector de idiomas, continuando con procesamiento"
                )

            # Verificar si es hora de procesar subtítulos (noche)
            is_night = self._is_night_time()
            current_time = datetime.now().strftime("%H:%M")

            if is_night:
                self.logger.info(f"Es de noche ({current_time}) - Ejecutando subtítulos + compresión")

                # Ejecutar subtítulos primero
                if not self.run_subtitle_translator():
                    self.logger.warning("Error en traductor de subtítulos, continuando con compresión")

                # Luego ejecutar compresión con idiomas pre-detectados
                if not self.run_processor():
                    self.logger.error("Error en processor")
                    return False
            else:
                self.logger.info(f"Es de día ({current_time}) - Ejecutando solo compresión")

                # Solo ejecutar compresión con idiomas pre-detectados
                if not self.run_processor():
                    self.logger.error("Error en processor")
                    return False

            self.logger.info("Ciclo completado exitosamente")
            return True
        finally:
            # Limpiar archivo de bloqueo al finalizar
            if self.lock_file.exists():
                self.lock_file.unlink()


def main():
    """Función principal"""
    cron = MediaJellyCron()

    # Ejecutar una sola vez y terminar
    cron.run_cycle()


if __name__ == "__main__":
    main()
