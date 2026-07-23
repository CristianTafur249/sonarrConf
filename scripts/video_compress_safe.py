#!/usr/bin/env python3
"""
video_compress_safe.py - Compresión segura de video con respaldo de metadata y verificación de integridad.

Uso:
    python video_compress_safe.py /ruta/a/carpeta/con/videos
    python video_compress_safe.py "mtp://Dispositivo/DCIM/Camera"
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import unquote, urlparse

try:
    import xattr  # type: ignore

    XATTR_AVAILABLE = True
except ImportError:
    xattr = None
    XATTR_AVAILABLE = False


EXTENSIONS = {".mkv", ".mp4", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v", ".ts"}
EXCLUDED_FOLDERS = {
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

SCRIPT_DIR = Path(__file__).resolve().parent
TMP_DIR = SCRIPT_DIR / "tmp"
LOG_DIR = TMP_DIR / "logs"
TEMP_OUTPUT = TMP_DIR / ".compressed.mp4"
FAILED_VERIFICATION_FILE = TMP_DIR / "failed_verification.txt"
COMPLETED_REPLACEMENTS_FILE = TMP_DIR / "completed_replacements.txt"
FFPROBE_TIMEOUT_SECONDS = 120
FFMPEG_TIMEOUT_SECONDS = 6 * 60 * 60

logger = logging.getLogger("video_compress_safe")
STOP_REQUESTED = False


def setup_logging() -> None:
    """Configura logging en consola y archivo rotativo."""
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    logger.propagate = False

    formatter = logging.Formatter(
        fmt="[%(asctime)s] [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)

    file_handler = RotatingFileHandler(
        LOG_DIR / "video_compress_safe.log",
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat()


def iso_from_timestamp(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).astimezone().isoformat()


def parse_iso_timestamp(value: str) -> float:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def format_bytes(size_bytes: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(size_bytes)
    for unit in units:
        if size < 1024.0 or unit == units[-1]:
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size_bytes} B"


def sanitize_xattr_value(raw_value: bytes) -> Any:
    """Serializa el valor de un xattr preservando binarios cuando sea necesario."""
    try:
        return raw_value.decode("utf-8")
    except UnicodeDecodeError:
        return {"__base64__": base64.b64encode(raw_value).decode("ascii")}


def restore_xattr_value(stored_value: Any) -> bytes:
    if isinstance(stored_value, dict) and "__base64__" in stored_value:
        return base64.b64decode(stored_value["__base64__"])
    if isinstance(stored_value, bytes):
        return stored_value
    return str(stored_value).encode("utf-8")


def is_video_file(path: Path) -> bool:
    return path.suffix.lower() in EXTENSIONS


def is_excluded_path(path: Path) -> bool:
    if any(part.lower() in EXCLUDED_FOLDERS for part in path.parts):
        return True
    # Excluir archivos con prefijo .trashed- (papelera de Android)
    if path.name.startswith(".trashed-"):
        return True
    return False


def ensure_accessible_path(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"La ruta no existe o no es accesible: {path}")
    if path.is_dir():
        if not os.access(path, os.R_OK | os.X_OK):
            raise PermissionError(f"La ruta no tiene permisos de lectura/recorrido: {path}")
    else:
        if not os.access(path, os.R_OK):
            raise PermissionError(f"El archivo no tiene permisos de lectura: {path}")


def find_gvfs_mount(device_name: str) -> Optional[Path]:
    uid = os.getuid() if hasattr(os, "getuid") else None
    if uid is not None:
        base_mount = Path(f"/run/user/{uid}/gvfs")
        if base_mount.exists():
            direct = base_mount / f"mtp:host={device_name}"
            if direct.exists():
                return direct
            for child in base_mount.iterdir():
                if child.is_dir() and child.name.startswith("mtp:host=") and device_name in child.name:
                    return child

    fallback_bases = [Path("/run/user"), Path("/run/media")]
    for base in fallback_bases:
        if not base.exists():
            continue
        for candidate_root in base.glob("*/gvfs"):
            if not candidate_root.is_dir():
                continue
            for child in candidate_root.iterdir():
                if child.is_dir() and child.name.startswith("mtp:host=") and device_name in child.name:
                    return child
    return None


def resolve_source_path(raw_argument: str) -> Tuple[str, Path]:
    decoded_argument = unquote(raw_argument.strip())
    parsed = urlparse(decoded_argument)

    if parsed.scheme in {"", "file"}:
        candidate = Path(parsed.path if parsed.scheme == "file" else decoded_argument).expanduser()
        resolved = candidate.resolve(strict=False)
        ensure_accessible_path(resolved)
        return decoded_argument, resolved

    if parsed.scheme == "mtp":
        mount = find_gvfs_mount(parsed.netloc)
        if mount is None:
            raise FileNotFoundError(
                "No se encontró un montaje GVFS accesible para la ruta MTP proporcionada. "
                "El dispositivo debe estar montado y visible como mtp:host=..."
            )
        relative_path = parsed.path.lstrip("/")
        candidate = mount / relative_path if relative_path else mount
        resolved = candidate.resolve(strict=False)
        ensure_accessible_path(resolved)
        return decoded_argument, resolved

    raise ValueError(f"Esquema de ruta no soportado: {parsed.scheme}")


def run_ffprobe(file_path: Path, show_chapters: bool = True) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    command = ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", "-show_streams"]
    if show_chapters:
        command.append("-show_chapters")
    command.append(str(file_path))

    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=FFPROBE_TIMEOUT_SECONDS,
            check=True,
        )
    except subprocess.TimeoutExpired as exc:
        return None, f"ffprobe excedió el tiempo máximo: {exc}"
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or exc.stdout or str(exc)).strip()
        return None, stderr or "ffprobe falló sin salida de error"
    except FileNotFoundError:
        raise RuntimeError("No se encontró ffprobe en el sistema")

    try:
        return json.loads(completed.stdout or "{}"), None
    except json.JSONDecodeError as exc:
        return None, f"Salida JSON inválida de ffprobe: {exc}"


def read_file_xattrs(file_path: Path) -> Dict[str, Any]:
    if not XATTR_AVAILABLE:
        return {}

    xattrs: Dict[str, Any] = {}
    try:
        keys = xattr.listxattr(str(file_path))
        for key in keys:
            key_name = key.decode("utf-8") if isinstance(key, bytes) else str(key)
            value = xattr.getxattr(str(file_path), key)
            xattrs[key_name] = sanitize_xattr_value(value)
    except Exception as exc:
        logger.warning("No se pudieron leer xattrs de %s: %s", file_path, exc)
        return {}
    return xattrs


def write_failed_verification(file_path: Path, reason: str) -> None:
    FAILED_VERIFICATION_FILE.parent.mkdir(parents=True, exist_ok=True)
    timestamp = utc_now_iso()
    line = f"{timestamp}\t{file_path}\t{reason}\n"
    with FAILED_VERIFICATION_FILE.open("a", encoding="utf-8") as handle:
        handle.write(line)


def write_completed_replacement(
    file_path: Path,
    original_size: int,
    final_size: int,
    restoration_status: str,
) -> None:
    COMPLETED_REPLACEMENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    reduction = 0.0 if original_size <= 0 else ((original_size - final_size) / original_size) * 100.0
    timestamp = utc_now_iso()
    line = (
        f"{timestamp}\t{file_path}\t{original_size}\t{final_size}\t"
        f"{reduction:.2f}%\t{restoration_status}\n"
    )
    with COMPLETED_REPLACEMENTS_FILE.open("a", encoding="utf-8") as handle:
        handle.write(line)


@dataclass
class FileSystemMetadata:
    file_size_bytes: int
    file_atime: str
    file_mtime: str
    file_ctime: str
    file_mode: int
    file_uid: int
    file_gid: int
    file_xattrs: Dict[str, Any]


class VideoMetadataExtractor:
    def __init__(self, source_path: Path, output_file: Path) -> None:
        self.source_path = source_path
        self.output_file = output_file

    def collect_video_files(self) -> List[Path]:
        if self.source_path.is_file():
            if is_video_file(self.source_path) and not is_excluded_path(self.source_path):
                return [self.source_path.resolve(strict=False)]
            return []

        files: List[Path] = []
        for root, dirnames, filenames in os.walk(self.source_path):
            root_path = Path(root)
            dirnames[:] = [name for name in dirnames if name.lower() not in EXCLUDED_FOLDERS]
            if is_excluded_path(root_path):
                dirnames[:] = []
                continue
            for filename in filenames:
                candidate = root_path / filename
                if is_excluded_path(candidate):
                    continue
                if is_video_file(candidate):
                    files.append(candidate.resolve(strict=False))

        files.sort(key=lambda item: str(item))
        return files

    def _filesystem_metadata(self, file_path: Path) -> FileSystemMetadata:
        stats = os.stat(file_path, follow_symlinks=False)
        return FileSystemMetadata(
            file_size_bytes=os.path.getsize(file_path),
            file_atime=iso_from_timestamp(stats.st_atime),
            file_mtime=iso_from_timestamp(stats.st_mtime),
            file_ctime=iso_from_timestamp(stats.st_ctime),
            file_mode=stat.S_IMODE(stats.st_mode),
            file_uid=stats.st_uid,
            file_gid=stats.st_gid,
            file_xattrs=read_file_xattrs(file_path),
        )

    def extract(self) -> Dict[str, Any]:
        video_files = self.collect_video_files()
        session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        payload: Dict[str, Any] = {
            "session_id": session_id,
            "source_path": str(self.source_path),
            "generated_at": utc_now_iso(),
            "total_files": len(video_files),
            "files": {},
        }

        logger.info("Guardando metadata en: %s", self.output_file)

        for file_path in video_files:
            metadata = self._filesystem_metadata(file_path)
            ffprobe_data, ffprobe_error = run_ffprobe(file_path)

            file_entry: Dict[str, Any] = {
                "absolute_path": str(file_path.resolve(strict=False)),
                "filename": file_path.name,
                "extension": file_path.suffix.lower(),
                "file_size_bytes": metadata.file_size_bytes,
                "file_atime": metadata.file_atime,
                "file_mtime": metadata.file_mtime,
                "file_ctime": metadata.file_ctime,
                "file_mode": metadata.file_mode,
                "file_uid": metadata.file_uid,
                "file_gid": metadata.file_gid,
                "file_xattrs": metadata.file_xattrs,
                "ffprobe": ffprobe_data or {},
            }
            if ffprobe_error:
                file_entry["ffprobe_error"] = ffprobe_error

            payload["files"][str(file_path.resolve(strict=False))] = file_entry

        self.output_file.parent.mkdir(parents=True, exist_ok=True)
        with self.output_file.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)

        return payload


class VideoCompressor:
    def __init__(self, temp_output: Path) -> None:
        self.temp_output = temp_output

    def cleanup_temp_output(self) -> None:
        try:
            if self.temp_output.exists():
                self.temp_output.unlink()
        except Exception as exc:
            logger.warning("No se pudo limpiar el temporal %s: %s", self.temp_output, exc)

    def compress(self, original_path: Path) -> Tuple[bool, Optional[str]]:
        self.cleanup_temp_output()

        command = [
            "ffmpeg",
            "-y",
            "-i",
            str(original_path),
            "-c:v",
            "libx264",
            "-crf",
            "23",
            "-preset",
            "medium",
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            "-map_metadata",
            "0",
            str(self.temp_output),
        ]

        try:
            subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=FFMPEG_TIMEOUT_SECONDS if FFMPEG_TIMEOUT_SECONDS > 0 else None,
                check=True,
            )
        except subprocess.TimeoutExpired:
            self.cleanup_temp_output()
            return False, "ffmpeg excedió el tiempo máximo de ejecución"
        except subprocess.CalledProcessError as exc:
            self.cleanup_temp_output()
            error_message = (exc.stderr or exc.stdout or str(exc)).strip()
            return False, error_message or "ffmpeg falló sin salida de error"
        except FileNotFoundError:
            self.cleanup_temp_output()
            raise RuntimeError("No se encontró ffmpeg en el sistema")

        return True, None


class IntegrityVerifier:
    @staticmethod
    def _first_video_stream(probe_data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        for stream in probe_data.get("streams", []):
            if stream.get("codec_type") == "video":
                return stream
        return None

    @staticmethod
    def _rotation_degrees(stream: Dict[str, Any]) -> int:
        rotation_value = stream.get("tags", {}).get("rotate")
        if rotation_value is not None:
            try:
                return int(float(rotation_value)) % 360
            except (TypeError, ValueError):
                pass

        for side_data in stream.get("side_data_list", []):
            rotation_value = side_data.get("rotation")
            if rotation_value is not None:
                try:
                    return int(float(rotation_value)) % 360
                except (TypeError, ValueError):
                    continue

        return 0

    @classmethod
    def _display_dimensions(cls, stream: Dict[str, Any]) -> Tuple[Optional[int], Optional[int]]:
        width = stream.get("width")
        height = stream.get("height")
        if not isinstance(width, int) or not isinstance(height, int):
            return None, None

        rotation = cls._rotation_degrees(stream)
        if rotation in {90, 270}:
            return height, width
        return width, height

    @staticmethod
    def _format_duration(probe_data: Dict[str, Any]) -> Optional[float]:
        try:
            value = probe_data.get("format", {}).get("duration")
            if value is None:
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _stream_count(probe_data: Dict[str, Any]) -> int:
        streams = probe_data.get("streams", [])
        return len(streams) if isinstance(streams, list) else 0

    def verify(self, original_metadata: Dict[str, Any], compressed_path: Path) -> Tuple[bool, str]:
        if original_metadata.get("ffprobe_error"):
            return False, "No hay metadata ffprobe válida para el archivo original"

        original_probe = original_metadata.get("ffprobe", {})
        compressed_probe, compressed_error = run_ffprobe(compressed_path)
        if compressed_error or not compressed_probe:
            return False, compressed_error or "No fue posible leer metadata del archivo comprimido"

        compressed_duration = self._format_duration(compressed_probe)
        if compressed_duration is None or compressed_duration <= 0:
            return False, "La duración del archivo comprimido no es válida"

        original_duration = self._format_duration(original_probe)
        if original_duration is None:
            return False, "La duración del archivo original no está disponible"

        if abs(original_duration - compressed_duration) > 2:
            return False, (
                f"La duración difiere más de 2 segundos: original={original_duration:.3f}, "
                f"comprimido={compressed_duration:.3f}"
            )

        if self._stream_count(compressed_probe) < 1:
            return False, "El archivo comprimido no contiene streams válidos"

        original_video = self._first_video_stream(original_probe)
        compressed_video = self._first_video_stream(compressed_probe)
        if original_video is None:
            return False, "El archivo original no contiene un stream de video"
        if compressed_video is None:
            return False, "El archivo comprimido no contiene un stream de video"

        original_width, original_height = self._display_dimensions(original_video)
        compressed_width, compressed_height = self._display_dimensions(compressed_video)

        if original_width is None or original_height is None:
            return False, "No se pudieron calcular las dimensiones orientadas del video original"
        if compressed_width is None or compressed_height is None:
            return False, "No se pudieron calcular las dimensiones orientadas del video comprimido"

        if original_width != compressed_width or original_height != compressed_height:
            return False, (
                f"La resolución orientada cambió: original={original_width}x{original_height}, "
                f"comprimido={compressed_width}x{compressed_height}"
            )

        return True, "Verificación completada correctamente"


class FilesystemMetaRestorer:
    def __init__(self, metadata_entry: Dict[str, Any]) -> None:
        self.metadata_entry = metadata_entry

    def restore(self, file_path: Path) -> Tuple[bool, List[str]]:
        warnings: List[str] = []
        restoration_ok = True

        try:
            atime = parse_iso_timestamp(self.metadata_entry["file_atime"])
            mtime = parse_iso_timestamp(self.metadata_entry["file_mtime"])
            os.utime(file_path, (atime, mtime))
        except Exception as exc:
            restoration_ok = False
            warnings.append(f"utime falló: {exc}")

        try:
            os.chmod(file_path, int(self.metadata_entry["file_mode"]))
        except Exception as exc:
            restoration_ok = False
            warnings.append(f"chmod falló: {exc}")

        try:
            if hasattr(os, "chown"):
                os.chown(file_path, int(self.metadata_entry["file_uid"]), int(self.metadata_entry["file_gid"]))
        except PermissionError as exc:
            restoration_ok = False
            warnings.append(f"chown sin permisos: {exc}")
        except Exception as exc:
            restoration_ok = False
            warnings.append(f"chown falló: {exc}")

        file_xattrs = self.metadata_entry.get("file_xattrs", {})
        if file_xattrs and XATTR_AVAILABLE:
            try:
                for key, stored_value in file_xattrs.items():
                    xattr.setxattr(str(file_path), key, restore_xattr_value(stored_value))
            except Exception as exc:
                restoration_ok = False
                warnings.append(f"restauración de xattrs falló: {exc}")
        elif file_xattrs and not XATTR_AVAILABLE:
            restoration_ok = False
            warnings.append("xattr no está disponible en este entorno")

        # En Linux el ctime es gestionado por el kernel y no es restaurable directamente.
        return restoration_ok, warnings


def print_summary(
    found: int,
    succeeded: int,
    skipped: int,
    failed: int,
    original_size: int,
    final_size: int,
    backup_file: Path,
) -> None:
    reduction = 0.0 if original_size <= 0 else ((original_size - final_size) / original_size) * 100.0
    print("=" * 60)
    print(" RESUMEN DE COMPRESIÓN")
    print("=" * 60)
    print(f"  Archivos encontrados:    {found}")
    print(f"  Comprimidos con éxito:    {succeeded}")
    print(f"  Skipped (ya pequeños):    {skipped}")
    print(f"  Fallidos:                 {failed}")
    print(f"  Tamaño original total:   {format_bytes(original_size)}")
    print(f"  Tamaño final total:      {format_bytes(final_size)}")
    print(f"  Reducción total:         {reduction:.1f}%")
    print(f"  Metadata respaldada en:  {backup_file}")
    print("=" * 60)


def request_stop(signum: int, frame: Any) -> None:
    del signum, frame
    global STOP_REQUESTED
    STOP_REQUESTED = True
    raise KeyboardInterrupt()


def build_backup_file_path() -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return TMP_DIR / f"metadata_backup_{timestamp}.json"


def copy_file_contents(source_path: Path, target_path: Path) -> None:
    """Copia solo el contenido binario, sin intentar preservar metadatos."""
    buffer_size = 1024 * 1024
    with source_path.open("rb") as source_handle, target_path.open("wb") as target_handle:
        while True:
            chunk = source_handle.read(buffer_size)
            if not chunk:
                break
            target_handle.write(chunk)
        target_handle.flush()
        os.fsync(target_handle.fileno())


def replace_file_with_fallback(source_path: Path, target_path: Path) -> str:
    """Reemplaza un archivo usando os.replace cuando sea posible y copia cuando no."""
    try:
        os.replace(source_path, target_path)
        return "replace"
    except OSError as exc:
        if exc.errno != 18:
            raise
        copy_file_contents(source_path, target_path)
        source_path.unlink(missing_ok=True)
        return "copy"


def process_videos(source_path: Path, backup_file: Path) -> int:
    extractor = VideoMetadataExtractor(source_path, backup_file)
    compressor = VideoCompressor(TEMP_OUTPUT)
    verifier = IntegrityVerifier()

    metadata_payload = extractor.extract()
    file_entries = metadata_payload.get("files", {})
    total_files = len(file_entries)
    if total_files == 0:
        logger.info("No se encontraron archivos de video en la ruta indicada")
        return 0

    logger.info("Encontrados %s archivos de video", total_files)

    found = total_files
    succeeded = 0
    skipped = 0
    failed = 0
    original_total = 0
    final_total = 0

    ordered_files = sorted(file_entries.items(), key=lambda item: item[0])

    for index, (file_key, metadata_entry) in enumerate(ordered_files, start=1):
        if STOP_REQUESTED:
            logger.warning("Interrupción solicitada. Se detiene el procesamiento restante.")
            break

        original_path = Path(file_key)
        original_size = int(metadata_entry.get("file_size_bytes", 0))
        original_total += original_size
        final_total += original_size

        logger.info("[%s/%s] Comprimiendo: %s (%s)", index, total_files, original_path.name, format_bytes(original_size))

        try:
            compressed_ok, compression_error = compressor.compress(original_path)
            if not compressed_ok:
                failed += 1
                logger.error("  Error de compresión en %s: %s", original_path, compression_error)
                continue

            if not TEMP_OUTPUT.exists():
                failed += 1
                logger.error("  No se generó el archivo temporal comprimido para %s", original_path)
                continue

            compressed_size = TEMP_OUTPUT.stat().st_size
            if compressed_size >= original_size:
                skipped += 1
                logger.warning(
                    "  Omitido: el archivo comprimido es mayor o igual al original (%s >= %s)",
                    format_bytes(compressed_size),
                    format_bytes(original_size),
                )
                compressor.cleanup_temp_output()
                continue

            logger.info("  → Verificando integridad...")
            verification_ok, verification_reason = verifier.verify(metadata_entry, TEMP_OUTPUT)
            if not verification_ok:
                failed += 1
                logger.error("  Verificación fallida en %s: %s", original_path, verification_reason)
                write_failed_verification(original_path, verification_reason)
                compressor.cleanup_temp_output()
                continue

            replace_mode = replace_file_with_fallback(TEMP_OUTPUT, original_path)

            restorer = FilesystemMetaRestorer(metadata_entry)
            restoration_ok, restoration_warnings = restorer.restore(original_path)
            for warning in restoration_warnings:
                logger.warning("  %s", warning)

            final_size = original_path.stat().st_size
            final_total -= original_size
            final_total += final_size
            reduction = 0.0 if original_size <= 0 else ((original_size - final_size) / original_size) * 100.0
            restoration_status = "complete" if restoration_ok else "partial"

            if replace_mode == "copy":
                logger.info("  ✓ Verificación OK. Copiando al destino MTP (reducción: %.1f%%)", reduction)
            else:
                logger.info("  ✓ Verificación OK. Reemplazando original (reducción: %.1f%%)", reduction)
            write_completed_replacement(original_path, original_size, final_size, restoration_status)
            succeeded += 1

        except KeyboardInterrupt:
            compressor.cleanup_temp_output()
            raise
        except Exception as exc:
            failed += 1
            compressor.cleanup_temp_output()
            logger.exception("  Fallo inesperado procesando %s: %s", original_path, exc)

    print_summary(found, succeeded, skipped, failed, original_total, final_total, backup_file)
    return 0


def parse_args(argv: List[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compresión segura de videos con respaldo de metadata y verificación de integridad",
    )
    parser.add_argument("path", help="Ruta local o URI MTP a escanear")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    setup_logging()
    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    arguments = parse_args(argv if argv is not None else sys.argv[1:])

    try:
        source_argument, source_path = resolve_source_path(arguments.path)
    except Exception as exc:
        logger.error("No se pudo validar la ruta de entrada: %s", exc)
        return 1

    logger.info("Escaneando: %s", source_argument)

    backup_file = build_backup_file_path()

    try:
        return process_videos(source_path, backup_file)
    except KeyboardInterrupt:
        logger.warning("Procesamiento interrumpido por el usuario")
        VideoCompressor(TEMP_OUTPUT).cleanup_temp_output()
        return 130
    except RuntimeError as exc:
        logger.error("Error de entorno: %s", exc)
        VideoCompressor(TEMP_OUTPUT).cleanup_temp_output()
        return 1
    finally:
        if STOP_REQUESTED:
            VideoCompressor(TEMP_OUTPUT).cleanup_temp_output()


if __name__ == "__main__":
    raise SystemExit(main())
