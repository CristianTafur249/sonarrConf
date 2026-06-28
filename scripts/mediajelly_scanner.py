#!/usr/bin/env python3
"""
MediaJelly Scanner - Media File Discovery and Management

Este módulo implementa el escáner de archivos multimedia para MediaJelly.
Detecta archivos de video nuevos, los valida y gestiona las colas de procesamiento.

Funcionalidades principales:
    - Escaneo recursivo de directorios multimedia
    - Validación de integridad de archivos con ffmpeg
    - Gestión de archivos corruptos (movimiento a .delete)
    - Detección de archivos que necesitan subtítulos
    - Exclusión automática de carpetas temporales

Example:
    Uso básico desde línea de comandos::

        $ python3 mediajelly_scanner.py /media/anime /media/series
        Archivos encontrados: 481
        Nuevas rutas detectadas: 15

    Uso programático::

        from mediajelly_scanner import MediaScanner

        scanner = MediaScanner()
        total, new = scanner.run(['/media/anime', '/media/series'])
        print(f"Nuevos archivos: {new}")

Attributes:
    EXTENSIONS (set): Extensiones de video soportadas (.mkv, .mp4, etc.)
    EXCLUDED_FOLDERS (set): Carpetas excluidas del escaneo (.delete, .tmp, etc.)

Note:
    El escáner automáticamente detecta si está corriendo en un contenedor
    Docker y ajusta las rutas base en consecuencia.

See Also:
    - mediajelly_processor.py: Procesamiento de archivos detectados
    - mediajelly_language_detector.py: Detección de idiomas con Whisper
"""

import sys
import time
import logging
import subprocess
from pathlib import Path
from typing import List

from mediajelly_utils import MediaJellyPaths, create_compressed_rotating_file_handler
from mediajelly_db import add_pending, get_pending_files, get_all_completed_files


class MediaScanner:
    """
    Scanner optimizado de archivos multimedia con validación de integridad.

    Esta clase implementa el sistema de detección y validación de archivos
    multimedia para MediaJelly. Escanea directorios en busca de archivos de
    video, valida su integridad y gestiona las colas de procesamiento.

    Attributes:
        EXTENSIONS (set): Extensiones de archivos de video soportadas
        EXCLUDED_FOLDERS (set): Carpetas que deben ser excluidas del escaneo
        is_container (bool): True si está corriendo en contenedor Docker
        base_dir (Path): Directorio base de MediaJelly
        scripts_dir (Path): Directorio de scripts
        tmp_dir (Path): Directorio temporal para archivos de estado
        logs_dir (Path): Directorio de logs
        pending_file (Path): Archivo con lista de archivos pendientes de comprimir
        pending_subtitles_file (Path): Archivo con archivos que necesitan subtítulos
        completed_file (Path): Archivo con lista de archivos completados
        log_file (Path): Archivo de log del scanner
        logger (logging.Logger): Logger configurado
        start_time (float): Timestamp de inicio del escaneo

    Example:
        >>> scanner = MediaScanner()
        >>> total, new = scanner.run(['/media/anime'])
        >>> print(f"Total: {total}, Nuevos: {new}")
        Total: 212, Nuevos: 5
    """

    EXTENSIONS = MediaJellyPaths.EXTENSIONS
    EXCLUDED_FOLDERS = MediaJellyPaths.EXCLUDED_FOLDERS

    def __init__(self):
        # Detecta el entorno
        self.is_container = MediaJellyPaths.is_container()
        self.base_dir = MediaJellyPaths.get_base_path()
        self.scripts_dir = self.base_dir / "scripts"
        self.tmp_dir = self.scripts_dir / "tmp"
        self.logs_dir = self.scripts_dir / "tmp" / "logs"

        # Archivos
        self.pending_file = self.tmp_dir / "pending-compression.txt"
        self.pending_subtitles_file = self.tmp_dir / "pending_subtitles.txt"
        self.completed_file = self.tmp_dir / "completed.txt"
        self.log_file = self.logs_dir / "scan.log"

        # Configura logging
        self.setup_logging()

        # Crea directorios
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.completed_file.touch()
        self.pending_subtitles_file.touch()

        self.start_time = time.time()

    def setup_logging(self):
        """Configurar logging con rotación de archivos"""
        # Crear handler con rotación (máx 10MB, mantener 5 backups)
        file_handler = create_compressed_rotating_file_handler(self.log_file, max_bytes=10 * 1024 * 1024, backup_count=5)
        file_handler.setLevel(logging.INFO)

        # Handler para consola
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.INFO)

        # Formato
        formatter = logging.Formatter("[%(asctime)s] [%(levelname)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
        file_handler.setFormatter(formatter)
        console_handler.setFormatter(formatter)

        # Configurar logger
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.INFO)
        self.logger.handlers.clear()
        self.logger.addHandler(file_handler)
        self.logger.addHandler(console_handler)

        # Evitar propagación a loggers padres
        self.logger.propagate = False

    def _is_file_in_excluded_folder(self, file_path: Path) -> bool:
        """Verifica si un archivo está en una carpeta excluida"""
        return any(part.lower() in self.EXCLUDED_FOLDERS for part in file_path.parts)

    def scan_folder_optimized(self, folder: Path) -> List[Path]:
        """Escaneo optimizado de carpeta usando pathlib"""
        if not folder.exists():
            self.logger.warning(f"Carpeta no encontrada: {folder}")
            return []

        self.logger.info(f"Escaneando: {folder}")
        found_files = []

        # Usa rglob para búsqueda recursiva eficiente
        for file_path in folder.rglob("*"):
            # Excluir archivos en carpetas temporales o de eliminación
            if self._is_file_in_excluded_folder(file_path):
                continue

            if (
                file_path.is_file()
                and file_path.suffix.lower() in self.EXTENSIONS
                and not file_path.name.startswith(".")
                and not file_path.name.endswith(".compressed.mp4")
            ):
                found_files.append(file_path)

        return found_files

    def _load_completed_files(self) -> set:
        """Carga archivos completados desde la DB (compatibilidad con completed.txt)."""
        try:
            files = get_all_completed_files()
            return {str(p) for p in files}
        except Exception:
            return set()

    def _load_existing_pending(self) -> tuple[List[str], set]:
        """Carga archivos pendientes existentes"""
        existing_pending_paths = []
        existing_pending_normalized = set()
        try:
            db_pending = get_pending_files()
            for p in db_pending:
                s = str(p)
                existing_pending_paths.append(s)
                existing_pending_normalized.add(s)
        except Exception:
            pass
        return existing_pending_paths, existing_pending_normalized

    def scan_media_directories(self, media_dirs: List[Path]) -> tuple[int, int]:
        """Escanea los directorios de medios y actualiza archivo de pendientes"""
        # Validar directorios
        valid_dirs = [d for d in media_dirs if d.exists()]
        if not valid_dirs:
            self.logger.warning("No hay directorios válidos para escanear")
            return 0, 0

        # Cargar datos existentes
        completed_files = self._load_completed_files()
        _, existing_pending_normalized = self._load_existing_pending()

        # Escanear todos los directorios
        all_found_files = self._scan_all_directories(valid_dirs, existing_pending_normalized, completed_files)

        # Procesar archivos nuevos
        return self._process_new_files(all_found_files, existing_pending_normalized, completed_files)

    def _scan_all_directories(
        self, media_dirs: List[Path], existing_pending_normalized: set, completed_files: set
    ) -> List[Path]:
        """Escanea todos los directorios y retorna archivos encontrados"""
        all_found_files = []
        for media_dir in media_dirs:
            found_files = self.scan_folder_optimized(media_dir)
            all_found_files.extend(found_files)

            # Contar archivos nuevos
            new_count = len(
                [
                    f
                    for f in found_files
                    if str(f.resolve()) not in existing_pending_normalized and str(f.resolve()) not in completed_files
                ]
            )
            self.logger.info(f"Directorio {media_dir.name}: {len(found_files)} encontrados, {new_count} nuevos")

        return all_found_files

    def _process_new_files(
        self, all_found_files: List[Path], existing_pending_normalized: set, completed_files: set
    ) -> tuple[int, int]:
        """Procesa archivos nuevos y los agrega a pendientes"""
        # Filtrar archivos nuevos
        new_files_candidates = []
        for file_path in all_found_files:
            path_str = str(file_path)
            if path_str in existing_pending_normalized:
                self.logger.debug(f"Archivo ya en pendientes: {file_path.name}")
                continue
            elif path_str in completed_files:
                # Si es MKV y no hay MP4 correspondiente, reprocesar
                if file_path.suffix.lower() == '.mkv':
                    mp4_path = file_path.with_suffix('.mp4')
                    if not mp4_path.exists():
                        self.logger.info(f"MKV completado sin MP4 correspondiente, reprocesando: {file_path.name}")
                        new_files_candidates.append(file_path)
                    else:
                        self.logger.debug(f"Archivo ya completado con MP4: {file_path.name}")
                else:
                    self.logger.debug(f"Archivo ya completado: {file_path.name}")
            else:
                self.logger.debug(f"Archivo candidato nuevo: {file_path.name}")
                new_files_candidates.append(file_path)

        if not new_files_candidates:
            self.logger.info("No hay archivos nuevos para agregar a pendientes")
            return len(all_found_files), 0

        # No validar integridad aquí, dejarlo al processor
        new_files_to_add = [str(file_path) for file_path in new_files_candidates]

        if not new_files_to_add:
            self.logger.info("No hay archivos válidos para agregar a pendientes")
            return len(all_found_files), 0

        # Ordenar por tamaño (más grandes primero)
        new_files_to_add.sort(key=lambda f: Path(f).stat().st_size, reverse=True)

        # Agregar al archivo de pendientes
        self._append_to_pending_file(new_files_to_add)

        # Log de archivos agregados
        self._log_new_files_added(new_files_to_add)

        return len(all_found_files), len(new_files_to_add)

    def _append_to_pending_file(self, new_files: List[str]):
        """Agrega archivos nuevos al archivo de pendientes"""
        for fp in new_files:
            try:
                add_pending(fp)
            except Exception as e:
                self.logger.error(f"Error agregando archivo a pendientes (DB): {e}")

    def _log_new_files_added(self, new_files: List[str]):
        """Registra archivos nuevos agregados"""
        self.logger.info(f"Nuevos archivos agregados a pendientes: {len(new_files)}")
        for i, file_path in enumerate(new_files[:5]):
            self.logger.info(f"  + {Path(file_path).name}")
        if len(new_files) > 5:
            self.logger.info(f"  ... y {len(new_files) - 5} más")

    def scan_subtitle_directories(self, media_dirs: List[Path]) -> int:
        """Escanea directorios en busca de archivos que necesiten subtítulos"""
        self.logger.info("=== Inicia escaneo de subtítulos ===")

        # Buscar todos los archivos de video
        all_video_files = self._find_all_video_files(media_dirs)

        # Determinar archivos que necesitan subtítulos (sin cargar existentes)
        files_needing_subtitles = self._find_files_needing_subtitles_fresh(all_video_files)

        # REESCRIBIR el archivo completo con la lista actualizada
        self._write_subtitle_files(files_needing_subtitles)

        if not files_needing_subtitles:
            self.logger.info("No hay archivos que necesiten subtítulos")
        else:
            # Log de archivos encontrados
            self._log_subtitle_files_added(files_needing_subtitles)

        self.logger.info("=== Escaneo de subtítulos completado ===")
        return len(files_needing_subtitles)

    def _load_existing_pending_subtitles(self) -> set:
        """Carga archivos pendientes de subtítulos existentes"""
        existing_pending_subtitles = set()
        if self.pending_subtitles_file.exists():
            try:
                with open(self.pending_subtitles_file, "r") as f:
                    existing_pending_subtitles = {line.strip() for line in f if line.strip()}
            except Exception as e:
                self.logger.error(f"Error cargando archivos pendientes de subtítulos: {e}")
        return existing_pending_subtitles

    def _find_all_video_files(self, media_dirs: List[Path]) -> List[Path]:
        """Encuentra todos los archivos de video en los directorios"""
        all_video_files = []
        for media_dir in media_dirs:
            if media_dir.exists():
                self.logger.info(f"Escaneando: {media_dir}")
                for file_path in media_dir.rglob("*"):
                    if (
                        file_path.is_file()
                        and file_path.suffix.lower() in self.EXTENSIONS
                        and not self._is_file_in_excluded_folder(file_path)
                    ):
                        all_video_files.append(file_path)
        return all_video_files

    def _find_files_needing_subtitles_fresh(self, video_files: List[Path]) -> List[str]:
        """Determina qué archivos necesitan subtítulos (sin filtrar por pending existente)"""
        files_needing_subtitles = []
        for video_file in video_files:
            video_path_str = str(video_file)

            # Validación: excluir archivos con ".compressed" en el nombre
            if ".compressed" in video_file.name:
                self.logger.debug(f"Excluyendo archivo comprimido de subtítulos: {video_file.name}")
                continue

            # Solo agregar si NO tiene subtítulos en español
            if not self._has_spanish_subtitle(video_file):
                files_needing_subtitles.append(video_path_str)
        return files_needing_subtitles

    def _has_spanish_subtitle(self, video_file: Path) -> bool:
        """Verifica si el archivo ya tiene subtítulos en español (externos o embebidos)"""

        # 1. Verificar archivos de subtítulos externos en español
        video_stem = video_file.stem

        # Patrones de subtítulos en español (priorizados por calidad)
        spanish_subtitle_patterns = [
            f"{video_stem}.es.srt",  # Español genérico (prioritario)
            f"{video_stem}.spa.srt",  # Español (código ISO)
            f"{video_stem}.es-ES.srt",  # Español de España
            f"{video_stem}.es-MX.srt",  # Español de México
            f"{video_stem}.es-AR.srt",  # Español de Argentina
            f"{video_stem}.spanish.srt",  # Español (nombre completo)
            f"{video_stem}.español.srt",  # Español (acentuado)
        ]

        # Buscar subtítulos en español (excluyendo .hi - hearing impaired)
        for pattern in spanish_subtitle_patterns:
            subtitle_path = video_file.parent / pattern
            # Verificar que exista y no sea hearing impaired
            if subtitle_path.exists() and ".hi." not in subtitle_path.name:
                # Verificar que el archivo no esté vacío
                if subtitle_path.stat().st_size > 100:  # Al menos 100 bytes
                    return True

        # También buscar cualquier .srt existente que no necesite traducción
        # (por ejemplo, si ya existe un .srt sin idioma específico)
        generic_srt = video_file.with_suffix(".srt")
        if generic_srt.exists() and generic_srt.stat().st_size > 100:
            # Verificar si es español mediante detección simple
            try:
                with open(generic_srt, "r", encoding="utf-8", errors="ignore") as f:
                    sample = f.read(500)  # Leer muestra
                    # Palabras comunes en español
                    spanish_words = [
                        "el",
                        "la",
                        "los",
                        "las",
                        "de",
                        "que",
                        "en",
                        "y",
                        "a",
                        "un",
                        "una",
                        "por",
                        "con",
                        "para",
                    ]
                    sample_lower = sample.lower()
                    spanish_count = sum(1 for word in spanish_words if f" {word} " in sample_lower)
                    if spanish_count >= 3:  # Si encuentra al menos 3 palabras españolas
                        return True
            except Exception:
                pass

        # Si encuentra subtítulos externos, retorna True
        if any(sub_file.exists() for sub_file in [generic_srt]):
            return True

        # 2. Verificar subtítulos embebidos en español con ffprobe
        try:
            probe_cmd = [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "s",
                "-show_entries",
                "stream_tags=language",
                "-of",
                "csv=p=0",
                str(video_file),
            ]
            result = subprocess.run(
                probe_cmd,
                capture_output=True,
                timeout=5,  # Reducido de 10 a 5 segundos
                encoding="utf-8",
                errors="replace",
            )

            if result.returncode == 0:
                # Buscar idiomas español en los subtítulos embebidos
                subtitle_languages = result.stdout.strip().split("\n")
                spanish_codes = {"spa", "es", "esp", "spanish"}

                for lang in subtitle_languages:
                    lang_clean = lang.strip().lower()
                    if lang_clean in spanish_codes:
                        return True

        except (subprocess.TimeoutExpired, Exception) as e:
            # Si hay error verificando embebidos, asumir que no tiene para ser conservador
            self.logger.debug(f"Error verificando subtítulos embebidos en {video_file.name}: {e}")

        return False

    def _clean_compressed_files_from_pending(self):
        """Limpia archivos comprimidos del archivo pending_subtitles.txt"""
        if not self.pending_subtitles_file.exists():
            return

        try:
            with open(self.pending_subtitles_file, "r") as f:
                lines = f.readlines()

            # Filtrar líneas que no contengan ".compressed"
            cleaned_lines = [line for line in lines if ".compressed" not in line]

            # Si se removieron líneas, reescribir el archivo
            if len(cleaned_lines) != len(lines):
                removed_count = len(lines) - len(cleaned_lines)
                with open(self.pending_subtitles_file, "w") as f:
                    f.writelines(cleaned_lines)
                self.logger.info(f"Limpiados {removed_count} archivos comprimidos de pending_subtitles.txt")

        except Exception as e:
            self.logger.error(f"Error limpiando archivos comprimidos de pending_subtitles.txt: {e}")

    def _write_subtitle_files(self, files_needing_subtitles: List[str]):
        """Reescribe el archivo pending_subtitles.txt con la lista actual"""
        try:
            with open(self.pending_subtitles_file, "w") as f:
                for file_path in files_needing_subtitles:
                    f.write(f"{file_path}\n")
        except Exception as e:
            self.logger.error(f"Error escribiendo pending_subtitles.txt: {e}")

    def _log_subtitle_files_added(self, files_needing_subtitles: List[str]):
        """Registra archivos de subtítulos encontrados"""
        self.logger.info(f"Encontrados {len(files_needing_subtitles)} archivos que necesitan subtítulos")
        for i, file_path in enumerate(files_needing_subtitles[:5]):
            self.logger.info(f"  + {Path(file_path).name}")
        if len(files_needing_subtitles) > 5:
            self.logger.info(f"  ... y {len(files_needing_subtitles) - 5} más")

    def validate_file_integrity(self, file_path: Path) -> bool:
        """Valida la integridad de un archivo multimedia usando ffprobe

        Args:
            file_path: Ruta al archivo a validar

        Returns:
            True si el archivo es válido, False si está corrupto
        """
        try:
            # Comando ffprobe para validar archivo (más rápido que ffmpeg)
            cmd = ["ffprobe", "-v", "error", "-show_format", "-show_streams", str(file_path)]

            # Ejecutar comando con timeout de 300 segundos
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300, text=True)

            # Si hay errores en stderr, el archivo puede estar corrupto
            if result.stderr:
                self.logger.warning(f"Archivo potencialmente corrupto: {file_path.name}")
                self.logger.warning(f"Errores detectados: {result.stderr[:200]}")
                return False

            return True

        except subprocess.TimeoutExpired:
            self.logger.error(f"Timeout validando archivo: {file_path.name}")
            return False
        except Exception as e:
            self.logger.error(f"Error validando {file_path.name}: {e}")
            return False

    def move_corrupted_file(self, file_path: Path) -> bool:
        """Mueve un archivo corrupto a la carpeta .delete

        Args:
            file_path: Ruta al archivo corrupto

        Returns:
            True si se movió exitosamente, False en caso contrario
        """
        try:
            # Crear carpeta .delete en el mismo directorio del archivo
            delete_folder = file_path.parent / ".delete"
            delete_folder.mkdir(exist_ok=True)

            # Mover archivo
            destination = delete_folder / file_path.name
            file_path.rename(destination)

            self.logger.info(f"Archivo corrupto movido a: {destination}")
            return True

        except Exception as e:
            self.logger.error(f"Error moviendo archivo corrupto {file_path.name}: {e}")
            return False

    def run(self, media_paths: List[str]) -> tuple[int, int]:
        """Función principal de ejecución optimizada - SIN notificaciones"""
        self.logger.info("=== Inicia escaneo ===")

        # Convierte las rutas a objetos Path y valida
        valid_dirs = [Path(path) for path in media_paths if Path(path).exists()]

        if not valid_dirs:
            self.logger.error("No se encontraron directorios válidos")
            return 0, 0

        self.logger.info(f"=== Inicia escaneo de {len(valid_dirs)} directorios ===")

        # Escanea directorios de medios
        total_found, new_detected = self.scan_media_directories(valid_dirs)

        # Escanea subtítulos
        self.scan_subtitle_directories(valid_dirs)

        self.logger.info(f"=== Escaneo completado en {time.time() - self.start_time:.2f}s ===")
        return total_found, new_detected


def main():
    """Función principal"""
    if len(sys.argv) < 2:
        print("Uso: mediajelly_scanner.py <carpeta1> [carpeta2 ...]")
        sys.exit(1)

    scanner = MediaScanner()

    # Ejecución de escaneo
    media_paths = sys.argv[1:]
    total_found, new_detected = scanner.run(media_paths)

    # Salida compatible con script bash
    print(f"Archivos encontrados: {total_found}")
    print(f"Nuevas rutas detectadas: {new_detected}")


if __name__ == "__main__":
    main()
