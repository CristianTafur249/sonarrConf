#!/usr/bin/env python3
"""
MediaJelly Utilities - Funciones compartidas y utilidades comunes

Este módulo proporciona funciones de utilidad compartidas por todos los
componentes de MediaJelly, incluyendo gestión de rutas, configuración,
validación de archivos y sanitización de entradas.

Example:
    >>> from mediajelly_utils import MediaJellyPaths, sanitize_path
    >>>
    >>> # Obtener directorio base
    >>> base = MediaJellyPaths.get_base_path()
    >>> print(base)
    /mediajelly
    >>>
    >>> # Validar path seguro
    >>> safe_path = sanitize_path("/media/anime/../../etc/passwd")
    ValueError: Path outside allowed directory
"""

import gzip
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime
from pathlib import Path
from typing import Set, Optional
import os
import shutil
try:
    from mediajelly_config import get_config
except Exception:
    get_config = None

_module_logger = logging.getLogger("mediajelly.utils")


class MediaJellyPaths:
    """
    Gestión centralizada de rutas del sistema MediaJelly.

    Esta clase proporciona métodos estáticos para obtener rutas consistentes
    en todo el sistema, con detección automática de entorno (contenedor vs host).

    Attributes:
        EXCLUDED_FOLDERS (Set[str]): Carpetas que deben ser excluidas del procesamiento
        EXTENSIONS (Set[str]): Extensiones de video soportadas
    """

    EXCLUDED_FOLDERS: Set[str] = {
        ".delete",
        ".deleted",
        ".tmp",
        ".temp",
        ".trash",
        ".recycle",
        ".git",
        ".cache",
        "__pycache__",
    }

    EXTENSIONS: Set[str] = {".mkv", ".mp4", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v", ".ts"}

    # Archivo vacío que excluye de subtítulos a su carpeta y subcarpetas (p. ej. videos
    # con subtítulos ya incrustados en la imagen)
    NOSUBS_MARKER: str = ".nosubs"

    @staticmethod
    def has_nosubs_marker(file_path: Path) -> bool:
        """
        Indica si un video está bajo una carpeta marcada para no generar subtítulos.

        Args:
            file_path: Ruta del video.

        Returns:
            bool: True si su carpeta o alguna superior (hasta `media/`) contiene `.nosubs`.
        """
        for parent in file_path.parents:
            if (parent / MediaJellyPaths.NOSUBS_MARKER).exists():
                return True
            if parent.name == "media":
                break
        return False

    @staticmethod
    def is_container() -> bool:
        """
        Detecta si el código está corriendo dentro de un contenedor Docker.

        Returns:
            bool: True si está en contenedor, False en caso contrario

        Example:
            >>> MediaJellyPaths.is_container()
            True
        """
        return Path("/mediajelly").exists()

    @staticmethod
    def get_base_path() -> Path:
        """
        Obtiene el directorio base de MediaJelly según el entorno.
        Returns:
            Path: Prioridad: env `MEDIAJELLY_BASE` -> /mediajelly (contenedor) -> proyecto (pyproject.toml/README.md/.git) -> CWD

        Example:
            >>> base = MediaJellyPaths.get_base_path()
            >>> print(base)
            /mediajelly
        """
        # 1) Variable de entorno tiene prioridad (permite ejecutar local o en CI)
        env_base = os.environ.get("MEDIAJELLY_BASE")
        if env_base:
            return Path(env_base)

        # 2) Si estamos en contenedor (ruta estándar montada), usarla
        if MediaJellyPaths.is_container():
            return Path("/mediajelly")

        # 3) Intentar detectar la raíz del proyecto buscando indicadores
        try:
            this_file = Path(__file__).resolve()
            for ancestor in [this_file.parent] + list(this_file.parents):
                for marker in ("pyproject.toml", "README.md", ".git"):
                    if (ancestor / marker).exists():
                        return ancestor
        except Exception:
            pass

        # 4) Fallback: directorio de trabajo actual
        return Path.cwd()

    @staticmethod
    def get_scripts_dir() -> Path:
        """Obtiene el directorio de scripts."""
        return MediaJellyPaths.get_base_path() / "scripts"

    @staticmethod
    def get_logs_dir() -> Path:
        """Obtiene el directorio de logs."""
        return MediaJellyPaths.get_scripts_dir() / "logs"

    @staticmethod
    def get_tmp_dir() -> Path:
        """Obtiene el directorio temporal."""
        return MediaJellyPaths.get_scripts_dir() / "tmp"

    @staticmethod
    def get_config_dir() -> Path:
        """Obtiene el directorio de configuración."""
        return MediaJellyPaths.get_base_path() / "config"

    @staticmethod
    def get_media_dir() -> Path:
        """Obtiene el directorio de medios."""
        return MediaJellyPaths.get_base_path() / "media"

    @staticmethod
    def is_excluded_folder(file_path: Path) -> bool:
        """
        Verifica si una ruta está dentro de una carpeta excluida.

        Args:
            file_path: Ruta del archivo a verificar

        Returns:
            bool: True si está en carpeta excluida, False en caso contrario

        Example:
            >>> path = Path("/media/anime/.delete/corrupted.mkv")
            >>> MediaJellyPaths.is_excluded_folder(path)
            True
            >>>
            >>> path = Path("/media/anime/show.mkv")
            >>> MediaJellyPaths.is_excluded_folder(path)
            False
        """
        return any(part.lower() in MediaJellyPaths.EXCLUDED_FOLDERS for part in file_path.parts)

    @staticmethod
    def is_video_file(file_path: Path) -> bool:
        """
        Verifica si un archivo es un video soportado.

        Args:
            file_path: Ruta del archivo a verificar

        Returns:
            bool: True si es un video soportado, False en caso contrario

        Example:
            >>> MediaJellyPaths.is_video_file(Path("/media/video.mkv"))
            True
            >>> MediaJellyPaths.is_video_file(Path("/media/image.jpg"))
            False
        """
        return file_path.suffix.lower() in MediaJellyPaths.EXTENSIONS


def sanitize_path(path: str, base_path: Optional[Path] = None) -> Path:
    """
    Sanitiza y valida una ruta para prevenir path traversal attacks.

    Esta función resuelve la ruta completa y verifica que esté dentro del
    directorio base permitido, previniendo ataques de path traversal.

    Args:
        path: Ruta a sanitizar (puede contener .., ~, symlinks, etc.)
        base_path: Directorio base permitido. Si es None, usa get_base_path()

    Returns:
        Path: Ruta resuelta y validada

    Raises:
        ValueError: Si la ruta está fuera del directorio permitido
        FileNotFoundError: Si la ruta no existe

    Example:
        >>> # Intento de path traversal
        >>> sanitize_path("/media/../../etc/passwd")
        ValueError: Path outside allowed directory

        >>> # Ruta válida
        >>> safe = sanitize_path("/mediajelly/media/anime")
        >>> print(safe)
        /mediajelly/media/anime

    Security:
        Esta función es crítica para la seguridad. Siempre úsala cuando
        proceses rutas de archivos que vengan de entrada externa.
    """
    if base_path is None:
        base_path = MediaJellyPaths.get_base_path()

    # Resolver la ruta completa (expande ~, .., symlinks, etc.)
    try:
        resolved_path = Path(path).resolve(strict=False)
    except Exception as e:
        raise ValueError(f"Invalid path format: {path}") from e

    # Verificar que esté dentro del directorio permitido
    try:
        # is_relative_to() verifica que resolved_path esté bajo base_path
        if not str(resolved_path).startswith(str(base_path.resolve())):
            raise ValueError(f"Path outside allowed directory: {path} (resolved to {resolved_path})")
    except Exception as e:
        raise ValueError(f"Path validation failed: {path}") from e

    return resolved_path


def ensure_directory(directory: Path, create: bool = True) -> bool:
    """
    Asegura que un directorio existe, opcionalmente creándolo.

    Args:
        directory: Directorio a verificar/crear
        create: Si True, crea el directorio si no existe

    Returns:
        bool: True si el directorio existe o fue creado, False si no existe y create=False

    Example:
        >>> logs_dir = Path("/mediajelly/scripts/logs")
        >>> ensure_directory(logs_dir)
        True
        >>> logs_dir.exists()
        True
    """
    if directory.exists():
        return True

    if create:
        try:
            directory.mkdir(parents=True, exist_ok=True)
            return True
        except Exception:
            return False

    return False


def get_file_size_mb(file_path: Path) -> float:
    """
    Obtiene el tamaño de un archivo en MB.

    Args:
        file_path: Ruta del archivo

    Returns:
        float: Tamaño en megabytes

    Example:
        >>> size = get_file_size_mb(Path("/media/video.mkv"))
        >>> print(f"{size:.2f} MB")
        1024.50 MB
    """
    try:
        return file_path.stat().st_size / (1024 * 1024)
    except Exception:
        return 0.0


def create_compressed_rotating_file_handler(
    log_file: Path,
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
    encoding: str = "utf-8",
) -> RotatingFileHandler:
    """Crea un handler de logs con rotación estándar y limpieza de backups.

    La estrategia personalizada con `namer`/`rotator` (gzip manual) provocaba rollovers
    inconsistentes y limpieza agresiva de archivos antiguos al mezclarse múltiples
    handlers sobre el mismo nombre de log o al reutilizar los mismos archivos desde
    varios procesos. Para evitarlo, usamos la implementación nativa de
    `RotatingFileHandler` y dejamos la compresión para un tratamiento posterior
    explícito (logrotate o gzip manual fuera del handler).
    """

    log_file.parent.mkdir(parents=True, exist_ok=True)

    try:
        handler = RotatingFileHandler(log_file, maxBytes=max_bytes, backupCount=backup_count, encoding=encoding)
    except Exception as exc:
        _module_logger.warning(f"Fallo al crear RotatingFileHandler para '{log_file}': {exc}. Usando fallback simple.")
        from logging import FileHandler

        handler = FileHandler(log_file, encoding=encoding)
        handler._mediajelly_fallback = True

    # Deshabilitar `namer`/`rotator` para evitar rollover con nombres comprimidos
    # no estándar ni limpieza de archivos en conflicto.
    if hasattr(handler, "namer"):
        handler.namer = None
    if hasattr(handler, "rotator"):
        handler.rotator = None

    try:
        if log_file.exists() and log_file.stat().st_size >= max_bytes:
            handler.doRollover()
            _module_logger.info(f"Rollover forzado en '{log_file.name}' al iniciar por tamaño excedido")
    except Exception as exc:
        _module_logger.warning(f"No se pudo ejecutar rollover inicial para '{log_file}': {exc}")

    def cleanup_old_archives() -> None:
        # Limpiar backups legacy generados por la implementación anterior (archivo.log.1.gz, etc.)
        archive_files = sorted(
            [path for path in log_file.parent.glob(f"{log_file.name}.*") if path.is_file() and path.suffix in {".1", ".2", ".3", ".4", ".5", ".gz", ".log"}],
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for old_archive in archive_files[backup_count:]:
            try:
                old_archive.unlink()
                _module_logger.info(f"Backup antiguo eliminado: {old_archive.name}")
            except Exception as exc:
                _module_logger.warning(f"No se pudo eliminar backup antiguo '{old_archive}': {exc}")

    try:
        cleanup_old_archives()
    except Exception as exc:
        _module_logger.warning(f"No se pudo limpiar backups antiguos de '{log_file}': {exc}")

    return handler


def archive_legacy_log_file(
    legacy_log_file: Path,
    archive_dir: Path,
    archive_name: Optional[str] = None,
    backup_count: int = 5,
) -> Optional[Path]:
    """Comprime un log legado en una carpeta de archivo y limpia excedentes.

    Antes, cualquier excepción durante el archivado (permisos, archivo en uso
    por otro proceso escribiendo por redirección de shell, etc.) se tragaba
    en silencio con `except Exception: return None`, por lo que si esta
    función fallaba, el log legado (p. ej. `cron.log`) quedaba creciendo sin
    límite para siempre sin que quedara ningún rastro del porqué.
    """

    if not legacy_log_file.exists() or not legacy_log_file.is_file():
        return None

    archive_dir.mkdir(parents=True, exist_ok=True)
    base_name = archive_name or legacy_log_file.name
    stamp = datetime.now().strftime("%Y%m%d%H%M%S")
    archive_file = archive_dir / f"{base_name}.{stamp}.gz"

    try:
        with open(legacy_log_file, "rb") as source_file, gzip.open(archive_file, "wb") as destination_file:
            shutil.copyfileobj(source_file, destination_file)
    except Exception as e:
        _module_logger.error(
            f"No se pudo comprimir el log legado '{legacy_log_file}': {e}"
        )
        return None

    # Trunca in-place (equivalente a 'copytruncate' de logrotate) en vez de borrar+recrear.
    # Esto es importante: si algo externo al proceso Python (p. ej. una redirección de
    # shell '>> cron.log' en el entrypoint/cron del contenedor) mantiene el archivo abierto
    # de forma persistente, un unlink() no libera nada — ese escritor seguiría escribiendo
    # para siempre en el inodo ya borrado (invisible por su ruta, pero ocupando disco).
    # Truncar el mismo inodo sí es visible para cualquier escritor que ya lo tenga abierto.
    try:
        with open(legacy_log_file, "r+b") as f:
            f.truncate(0)
    except Exception as e:
        _module_logger.error(
            f"Se comprimió pero no se pudo truncar '{legacy_log_file}' in-place: {e}"
        )
        return None

    archive_files = sorted(
        [path for path in archive_dir.glob(f"{base_name}.*.gz") if path.is_file()],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for old_archive in archive_files[backup_count:]:
        try:
            old_archive.unlink()
        except Exception as e:
            _module_logger.warning(f"No se pudo eliminar archivo antiguo '{old_archive}': {e}")

    return archive_file


def get_file_size_gb(file_path: Path) -> float:
    """
    Obtiene el tamaño de un archivo en GB.

    Args:
        file_path: Ruta del archivo

    Returns:
        float: Tamaño en gigabytes

    Example:
        >>> size = get_file_size_gb(Path("/media/video.mkv"))
        >>> print(f"{size:.2f} GB")
        1.50 GB
    """
    return get_file_size_mb(file_path) / 1024


class MediaJellyConfig:
    """
    Gestión de configuración de MediaJelly.

    Esta clase proporciona métodos para cargar y validar la configuración
    del sistema desde archivos y variables de entorno.
    """

    @staticmethod
    def get_env_var(key: str, default: Optional[str] = None, required: bool = False) -> Optional[str]:
        """
        Obtiene una variable de entorno con manejo de valores por defecto.

        Args:
            key: Nombre de la variable de entorno
            default: Valor por defecto si no existe
            required: Si True, lanza excepción si no existe

        Returns:
            str: Valor de la variable o default

        Raises:
            ValueError: Si required=True y la variable no existe

        Example:
            >>> tz = MediaJellyConfig.get_env_var("TZ", "UTC")
            >>> print(tz)
            America/Bogota
        """
        value = os.getenv(key, default)

        if required and value is None:
            raise ValueError(f"Required environment variable '{key}' is not set")

        return value

    @staticmethod
    def get_env_int(key: str, default: int = 0) -> int:
        """
        Obtiene una variable de entorno como entero.

        Args:
            key: Nombre de la variable
            default: Valor por defecto

        Returns:
            int: Valor entero

        Example:
            >>> max_mem = MediaJellyConfig.get_env_int("MAX_MEMORY_GB", 4)
            >>> print(max_mem)
            4
        """
        try:
            return int(os.getenv(key, str(default)))
        except ValueError:
            return default

    @staticmethod
    def get_env_bool(key: str, default: bool = False) -> bool:
        """
        Obtiene una variable de entorno como booleano.

        Args:
            key: Nombre de la variable
            default: Valor por defecto

        Returns:
            bool: Valor booleano

        Example:
            >>> debug = MediaJellyConfig.get_env_bool("DEBUG", False)
            >>> print(debug)
            False
        """
        value = os.getenv(key, "").lower()
        if not value:
            return default
        return value in ("true", "1", "yes", "on")


if __name__ == "__main__":
    # Tests básicos
    print("MediaJelly Utilities - Tests")
    print("-" * 50)

    print(f"Is container: {MediaJellyPaths.is_container()}")
    print(f"Base path: {MediaJellyPaths.get_base_path()}")
    print(f"Scripts dir: {MediaJellyPaths.get_scripts_dir()}")
    print(f"Logs dir: {MediaJellyPaths.get_logs_dir()}")

    # Test path validation
    test_path = Path("/mediajelly/media/anime")
    print(f"\nIs excluded: {MediaJellyPaths.is_excluded_folder(test_path)}")
    print(f"Is video: {MediaJellyPaths.is_video_file(Path('video.mkv'))}")
