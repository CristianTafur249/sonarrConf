#!/usr/bin/env python3
"""
MediaJelly Python - Sistema de procesamiento multimedia
Versión optimizada del sistema bash con control de recursos y concurrencia
"""

import os
import sys
import time
import json
import logging
import logging.handlers
import resource
import subprocess
from pathlib import Path
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, asdict
from typing import List, Dict, Optional, Tuple
import signal
import glob

# Configuración de límites de recursos
MAX_MEMORY_GB = 4  # Limita memoria por proceso ffmpeg
MAX_CPU_PERCENT = 30  # Restringe porcentaje máximo de CPU total
MAX_CONCURRENT_COMPRESSIONS = 2  # Permite 2 compresiones simultáneas
FFMPEG_TIMEOUT = 7200  # Establece 2 horas por archivo

# Constantes para archivos y formatos
COMPRESSED_FILE_SUFFIX = '.compressed.mp4'
STREAM_LANGUAGE_QUERY = "stream=index:stream_tags=language"
CSV_FORMAT_PARAM = "csv=p=0"
FIRST_AUDIO_STREAM = "0:a:0"
PROGRESS_UPDATE_INTERVAL = 10  # Actualizar progreso cada 10 archivos
PROGRESS_FILE_NAME = "progress.json"
VAAPI_DEVICE_PATH = "/dev/dri/renderD128"
LOG_RETENTION_DAYS = 30  # Días para mantener logs antiguos
SHORT_TIMEOUT = 10  # Timeout corto para operaciones rápidas (segundos)
MEDIUM_TIMEOUT = 30  # Timeout medio para operaciones de análisis (segundos)
PROCESSING_COMPLETED_MESSAGE = "Procesamiento completado"

@dataclass
class ProcessingStats:
    """Estadísticas de procesamiento"""
    files_found: int = 0
    files_new: int = 0
    files_processed: int = 0
    files_compressed: int = 0
    files_renamed: int = 0
    files_skipped: int = 0
    errors: Optional[List[str]] = None
    no_spanish: Optional[List[str]] = None
    
    def __post_init__(self):
        if self.errors is None:
            self.errors = []
        if self.no_spanish is None:
            self.no_spanish = []

class MediaJellyProcessor:
    """Procesador principal de MediaJelly"""
    
    def __init__(self):
        # Detecta el entorno
        self.is_container = Path("/mediajelly").exists()
        self.base_dir = Path("/mediajelly" if self.is_container else "/home/tafurc/mediaJelly")
        self.media_dir = self.base_dir / "media"
        self.scripts_dir = self.base_dir / "scripts"
        self.logs_dir = self.scripts_dir / "logs"
        self.tmp_dir = self.scripts_dir / "tmp"
        
        # Archivos
        self.pending_file = self.scripts_dir / "pending-compression.txt"
        self.completed_file = self.scripts_dir / "completed.txt"
        self.config_file = self.base_dir / "config" / "telegram.conf"
        
        # Logs
        self.setup_logging()
        
        # Crea los directorios
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.tmp_dir.mkdir(parents=True, exist_ok=True)
        self.completed_file.touch()
        
        # Estadísticas
        self.stats = ProcessingStats()
        
        # Archivos procesados con estado
        self.processed_files = {}
        
    def setup_logging(self):
        """Configura logging estructurado con rotación automática"""
        # Limpia los handlers existentes
        for handler in logging.root.handlers[:]:
            logging.root.removeHandler(handler)
        
        # Configuración del logger principal
        self.logger = logging.getLogger('mediajelly')
        self.logger.setLevel(logging.INFO)
        self.logger.handlers.clear()
        
        # Configura rotación de logs (máximo 10MB por archivo, mantiene 5 archivos)
        max_bytes = 10 * 1024 * 1024  # Define 10MB como límite
        backup_count = 5
        
        # Configuración del handler para compression-success.log con rotación
        success_handler = logging.handlers.RotatingFileHandler(
            self.logs_dir / "compression-success.log",
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding='utf-8'
        )
        success_handler.setLevel(logging.INFO)
        success_formatter = logging.Formatter('[%(asctime)s] %(message)s', '%Y-%m-%d %H:%M:%S')
        success_handler.setFormatter(success_formatter)
        
        # Configuración del handler para compression-errors.log con rotación
        error_handler = logging.handlers.RotatingFileHandler(
            self.logs_dir / "compression-errors.log",
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding='utf-8'
        )
        error_handler.setLevel(logging.ERROR)
        error_formatter = logging.Formatter('[%(asctime)s] ERROR: %(message)s', '%Y-%m-%d %H:%M:%S')
        error_handler.setFormatter(error_formatter)
        
        # Configuración del handler para no-spanish.log con rotación
        self.no_spanish_logger = logging.getLogger('mediajelly.no_spanish')
        self.no_spanish_logger.setLevel(logging.INFO)
        self.no_spanish_logger.handlers.clear()
        no_spanish_handler = logging.handlers.RotatingFileHandler(
            self.logs_dir / "no-spanish.log",
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding='utf-8'
        )
        no_spanish_formatter = logging.Formatter('[%(asctime)s] SIN ESPAÑOL: %(message)s', '%Y-%m-%d %H:%M:%S')
        no_spanish_handler.setFormatter(no_spanish_formatter)
        
        # Adición de los handlers
        self.logger.addHandler(success_handler)
        self.logger.addHandler(error_handler)
        self.no_spanish_logger.addHandler(no_spanish_handler)
        
        # Limpieza de logs antiguos al inicio (más de 30 días)
        self.cleanup_old_logs()
        
    def cleanup_old_logs(self):
        """Limpia archivos de log antiguos (más de 30 días)"""
        try:
            import time
            
            current_time = time.time()
            cutoff_time = current_time - (LOG_RETENTION_DAYS * 24 * 60 * 60)  # Calcula LOG_RETENTION_DAYS días atrás
            
            # Busca y elimina logs antiguos
            for log_file in self.logs_dir.glob("*.log*"):
                try:
                    if log_file.stat().st_mtime < cutoff_time:
                        self.logger.info(f"Eliminando log antiguo: {log_file.name}")
                        log_file.unlink()
                except Exception as e:
                    # Evitar logging aquí para prevenir recursión
                    print(f"Error eliminando log {log_file}: {e}")
                    
        except Exception as e:
            print(f"Error en limpieza de logs: {e}")
        
    def set_resource_limits(self):
        """Establecimiento de límites de recursos del proceso"""
        try:
            # Límite de memoria (en bytes)
            memory_limit = MAX_MEMORY_GB * 1024 * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (memory_limit, memory_limit))
            
            # Límite de tiempo de CPU (en segundos)
            resource.setrlimit(resource.RLIMIT_CPU, (FFMPEG_TIMEOUT, FFMPEG_TIMEOUT))
            
            self.logger.info(f"Límites de recursos establecidos: {MAX_MEMORY_GB}GB RAM, {FFMPEG_TIMEOUT}s CPU")
        except Exception as e:
            self.logger.warning(f"No se pudieron establecer límites de recursos: {e}")
    
    def _save_progress_state(self, current_file: int, total_files: int, current_file_name: str, status: str = 'processing') -> None:
        """Guarda el estado actual del progreso"""
        progress_data = {
            'current_file': current_file,
            'total_files': total_files,
            'current_file_name': current_file_name,
            'percentage': round((current_file / total_files) * 100, 1) if total_files > 0 else 0,
            'last_updated': datetime.now().isoformat(),
            'status': status,  # scanning, processing, completed, error
            'stats': asdict(self.stats),
            'processed_files': self.processed_files
        }
        
        progress_file = self.tmp_dir / PROGRESS_FILE_NAME
        try:
            with open(progress_file, 'w') as f:
                json.dump(progress_data, f, indent=2)
        except Exception as e:
            self.logger.warning(f"Error guardando progreso: {e}")

    def get_progress_info(self) -> Dict:
        """Obtiene información del progreso actual"""
        progress_file = self.tmp_dir / PROGRESS_FILE_NAME
        try:
            if progress_file.exists():
                with open(progress_file, 'r') as f:
                    data = json.load(f)
                    # Asegura que tenga el campo status
                    if 'status' not in data:
                        data['status'] = 'unknown'
                    return data
        except Exception as e:
            self.logger.warning(f"Error leyendo progreso: {e}")
        
        return {
            'current_file': 0,
            'total_files': 0,
            'current_file_name': 'N/A',
            'percentage': 0,
            'last_updated': None,
            'status': 'idle',
            'stats': asdict(self.stats),
            'processed_files': {}
        }

    def _check_hardware_acceleration_available(self) -> bool:
        """Verifica si la aceleración por hardware está disponible"""
        try:
            # Verifica dispositivo VAAPI
            if not Path(VAAPI_DEVICE_PATH).exists():
                self.logger.warning("Dispositivo VAAPI no encontrado")
                return False
            
            # Test simplificado: verificación de que ffmpeg pueda abrir el dispositivo VAAPI
            test_cmd = [
                "ffmpeg", "-hide_banner", "-hwaccels"
            ]
            
            result = subprocess.run(test_cmd, capture_output=True, text=True, timeout=SHORT_TIMEOUT)
            
            if result.returncode == 0 and "vaapi" in result.stdout:
                self.logger.info("Aceleración VAAPI disponible en FFmpeg")
                return True
            else:
                self.logger.warning("VAAPI no disponible en FFmpeg")
                return False
            
        except Exception as e:
            self.logger.warning(f"Error verificando aceleración de hardware: {e}")
            return False

    def _clean_incomplete_compressed_files(self) -> Tuple[int, int]:
        """Limpia archivos .compressed.mp4 incompletos y retorna (files_cleaned, files_added_to_pending)"""
        compressed_files_found = list(self.media_dir.rglob(f"*{COMPRESSED_FILE_SUFFIX}"))
        self.logger.info(f"Busco archivos {COMPRESSED_FILE_SUFFIX} incompletos... Encontrados: {len(compressed_files_found)}")
        
        files_cleaned = 0
        files_added_to_pending = 0
        
        for compressed_file in compressed_files_found:
            try:
                # Busca el archivo original correspondiente
                base_name = compressed_file.name.replace(COMPRESSED_FILE_SUFFIX, '')
                parent_dir = compressed_file.parent
                
                # Busca archivos con el mismo nombre base pero diferente extensión
                pattern = f"{base_name}.*"
                original_candidates = list(parent_dir.glob(pattern))
                
                # Filtrado para encontrar el archivo original (no .compressed.mp4)
                original_files = [f for f in original_candidates 
                                if not f.name.endswith(COMPRESSED_FILE_SUFFIX)]
                
                if original_files:
                    original_file = original_files[0]  # Toma el primero encontrado
                    self.logger.info(f"Elimino archivo incompleto: {compressed_file}")
                    self.logger.info(f"Archivo original correspondiente: {original_file}")
                    
                    # Elimina el archivo temporal
                    compressed_file.unlink()
                    files_cleaned += 1
                    
                    # Agrega el archivo original a pendientes
                    with open(self.pending_file, 'a') as f:
                        f.write(f"{original_file}\n")
                    files_added_to_pending += 1
                    
                else:
                    # No encuentra archivo original, elimina el temporal huérfano
                    self.logger.warning(f"Archivo temporal huérfano eliminado: {compressed_file}")
                    compressed_file.unlink()
                    files_cleaned += 1
                    
            except Exception as e:
                self.logger.error(f"Error procesando archivo temporal {compressed_file}: {e}")
        
        if files_cleaned > 0:
            self.logger.info(f"Limpieza completada: {files_cleaned} archivos {COMPRESSED_FILE_SUFFIX} eliminados, "
                           f"{files_added_to_pending} archivos agregados a pendientes")
            
            # Limpieza de duplicados después de agregar archivos
            if files_added_to_pending > 0:
                duplicates_removed = self._clean_pending_duplicates()
                if duplicates_removed > 0:
                    self.logger.info(f"Duplicados eliminados del archivo pending: {duplicates_removed}")
        
        return files_cleaned, files_added_to_pending

    def _load_completed_files(self) -> set:
        """Carga archivos completados para evitar reprocesamiento"""
        completed_files = set()
        if self.completed_file.exists():
            with open(self.completed_file, 'r') as f:
                for line in f:
                    file_path = line.strip()
                    if file_path:
                        completed_files.add(file_path)
        return completed_files

    def _get_pending_files(self) -> List[Path]:
        """Obtiene archivos pendientes de procesamiento"""
        pending_files = []
        if self.pending_file.exists():
            with open(self.pending_file, 'r') as f:
                for line in f:
                    file_path = line.strip()
                    if file_path and Path(file_path).exists():
                        pending_files.append(Path(file_path))
        return pending_files

    def _clean_pending_duplicates(self) -> int:
        """Limpia duplicados del archivo pending-compression.txt"""
        if not self.pending_file.exists():
            return 0
        
        unique_files = set()
        duplicates_removed = 0
        
        # Lee el archivo y mantiene solo rutas únicas
        with open(self.pending_file, 'r') as f:
            for line in f:
                line_clean = line.strip()
                if line_clean:
                    # Normaliza la ruta para comparación consistente
                    normalized_path = str(Path(line_clean).resolve())
                    if normalized_path in unique_files:
                        duplicates_removed += 1
                        self.logger.info(f"Duplicado encontrado y eliminado: {line_clean}")
                    else:
                        unique_files.add(normalized_path)
        
        # Reescribe el archivo sin duplicados
        if duplicates_removed > 0:
            with open(self.pending_file, 'w') as f:
                for file_path in sorted(unique_files):
                    f.write(f"{file_path}\n")
            self.logger.info(f"Limpieza completada: {duplicates_removed} duplicados eliminados")
        
        return duplicates_removed

    def scan_media_files(self) -> List[Path]:
        """Escanea archivos multimedia con pathlib optimizado"""
        self.logger.info("Iniciando escaneo de archivos multimedia...")
        
        # Actualiza estado de progreso como escaneando
        self._save_progress_state(0, 1, "Escaneando archivos multimedia...", status='scanning')
        
        # Limpia archivos .compressed.mp4 incompletos
        self._clean_incomplete_compressed_files()
        
        # Carga de archivos completados para evitar reprocesamiento
        completed_files = self._load_completed_files()
        self.logger.info(f"Archivos completados cargados: {len(completed_files)}")
        
        # Carga archivos pendientes
        pending_files = self._get_pending_files()
        
        # Filtra archivos ya completados
        filtered_files = []
        for file_path in pending_files:
            if str(file_path) not in completed_files:
                filtered_files.append(file_path)
            else:
                self.logger.info(f"Archivo ya procesado, omito: {file_path.name}")
        
        self.stats.files_found = len(filtered_files)
        self.logger.info(f"Escaneo completado: {len(filtered_files)} archivos pendientes para procesar")
        
        # Actualiza estado como escaneo completado
        if len(filtered_files) > 0:
            self._save_progress_state(0, len(filtered_files), f"{len(filtered_files)} archivos encontrados", status='scanned')
        else:
            self._save_progress_state(0, 0, "Escaneo completado", status='scanned')
        
        return filtered_files
    
    def detect_language_streams(self, file_path: Path) -> Tuple[bool, str, str]:
        """Detecta streams de audio y subtítulos en español"""
        try:
            # Detecta audio en español
            audio_cmd = [
                "ffprobe", "-v", "error", "-select_streams", "a",
                "-show_entries", STREAM_LANGUAGE_QUERY,
                "-of", CSV_FORMAT_PARAM, str(file_path)
            ]
            audio_result = subprocess.run(audio_cmd, capture_output=True, text=True, timeout=MEDIUM_TIMEOUT)
            audio_languages = audio_result.stdout.strip()
            
            # Detecta subtítulos en español
            subs_cmd = [
                "ffprobe", "-v", "error", "-select_streams", "s",
                "-show_entries", STREAM_LANGUAGE_QUERY,
                "-of", CSV_FORMAT_PARAM, str(file_path)
            ]
            subs_result = subprocess.run(subs_cmd, capture_output=True, text=True, timeout=MEDIUM_TIMEOUT)
            sub_languages = subs_result.stdout.strip()
            
            # Verifica si hay español
            spanish_patterns = ['spa', 'esp', 'es', 'es-LA', 'es-ES', 'lat']
            has_spanish_audio = any(pattern in audio_languages.lower() for pattern in spanish_patterns)
            has_spanish_subs = any(pattern in sub_languages.lower() for pattern in spanish_patterns)
            
            return (has_spanish_audio or has_spanish_subs), audio_languages, sub_languages
            
        except subprocess.TimeoutExpired:
            self.logger.error(f"Timeout detectando idiomas: {file_path}")
            return False, "", ""
        except Exception as e:
            self.logger.error(f"Error detectando idiomas en {file_path}: {e}")
            return False, "", ""
    
    def _validate_file_for_compression(self, file_path: Path) -> Tuple[bool, str]:
        """Valida si el archivo necesita compresión"""
        if not file_path.exists():
            return False, f"Archivo no encontrado: {file_path}"
        
        # Verifica si ya está comprimido
        if file_path.suffix.lower() == '.mp4' and file_path.stat().st_size < 50 * 1024 * 1024:  # Menos de 50MB
            return False, "archivo_pequeno"
        
        # Verifica que el archivo no esté corrupto usando ffprobe
        try:
            probe_cmd = [
                "ffprobe", "-v", "error", "-show_entries", "format=duration",
                "-of", "csv=p=0", str(file_path)
            ]
            probe_result = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=MEDIUM_TIMEOUT)
            
            if probe_result.returncode != 0:
                return False, f"Archivo corrupto o no válido: {file_path}"
            
            # Verifica que tenga duración
            duration_str = probe_result.stdout.strip()
            if not duration_str or duration_str == "N/A":
                return False, f"Archivo sin duración válida: {file_path}"
            
            # Verifica que la duración sea razonable (más de 10 segundos)
            try:
                duration = float(duration_str)
                if duration < 10.0:
                    return False, f"Archivo demasiado corto ({duration:.1f}s): {file_path}"
            except ValueError:
                return False, f"Duración no válida: {file_path}"
                
        except subprocess.TimeoutExpired:
            return False, f"Timeout validando archivo: {file_path}"
        except Exception as e:
            return False, f"Error validando archivo: {file_path} - {str(e)}"
        
        return True, ""

    def _detect_streams(self, file_path: Path) -> tuple[bool, bool, list, list]:
        """Detecta streams de audio y subtítulos disponibles"""
        try:
            # Primero verifica streams de audio
            audio_cmd = [
                "ffprobe", "-v", "error", "-select_streams", "a",
                "-show_entries", STREAM_LANGUAGE_QUERY,
                "-of", CSV_FORMAT_PARAM, str(file_path)
            ]
            audio_result = subprocess.run(audio_cmd, capture_output=True, text=True, timeout=SHORT_TIMEOUT)
            audio_streams = audio_result.stdout.strip().split('\n') if audio_result.stdout.strip() else []
            
            # Verifica streams de subtítulos
            subs_cmd = [
                "ffprobe", "-v", "error", "-select_streams", "s",
                "-show_entries", STREAM_LANGUAGE_QUERY,
                "-of", CSV_FORMAT_PARAM, str(file_path)
            ]
            subs_result = subprocess.run(subs_cmd, capture_output=True, text=True, timeout=SHORT_TIMEOUT)
            sub_streams = subs_result.stdout.strip().split('\n') if subs_result.stdout.strip() else []
            
            has_audio_stream = len(audio_streams) > 0 and audio_streams[0] != ''
            has_subtitle_stream = len(sub_streams) > 0 and sub_streams[0] != ''
            
            return has_audio_stream, has_subtitle_stream, audio_streams, sub_streams
            
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError, OSError) as e:
            self.logger.warning(f"Error detectando streams, usando fallback: {e}")
            return True, False, [], []
    
    def _add_audio_mapping(self, ffmpeg_cmd: list, has_spanish: bool, audio_streams: list) -> None:
        """Agrega mapeo de audio al comando ffmpeg"""
        if has_spanish and audio_streams:
            # Busca streams de audio en español
            spanish_audio_found = any(
                any(lang in stream_info.lower() for lang in ['spa', 'esp', 'es', 'lat'])
                for stream_info in audio_streams
            )
            
            if spanish_audio_found:
                # Mapea audio en español si existe
                ffmpeg_cmd.extend([
                    "-map", "0:a:m:language:spa?",
                    "-map", "0:a:m:language:esp?", 
                    "-map", "0:a:m:language:es?",
                    "-map", "0:a:m:language:lat?"
                ])
            else:
                # Si no hay español, mapeo del primer audio
                ffmpeg_cmd.extend(["-map", FIRST_AUDIO_STREAM])
        else:
            # Mapeo del primer stream de audio (más seguro)
            ffmpeg_cmd.extend(["-map", FIRST_AUDIO_STREAM])
        
        # Configuración de audio
        ffmpeg_cmd.extend([
            "-c:a", "aac",
            "-b:a", "128k",
            "-ac", "2"
        ])
    
    def _add_subtitle_mapping(self, ffmpeg_cmd: list, has_spanish: bool, sub_streams: list) -> None:
        """Agrega mapeo de subtítulos al comando ffmpeg"""
        if not sub_streams:
            return
            
        # Busca subtítulos en español
        spanish_subs_found = any(
            any(lang in stream_info.lower() for lang in ['spa', 'esp', 'es', 'lat'])
            for stream_info in sub_streams
        )
        
        if has_spanish and spanish_subs_found:
            ffmpeg_cmd.extend([
                "-map", "0:s:m:language:spa?",
                "-map", "0:s:m:language:esp?",
                "-map", "0:s:m:language:es?",
                "-map", "0:s:m:language:lat?",
                "-c:s", "mov_text"
            ])

    def _build_ffmpeg_command(self, file_path: Path, temp_output: Path, has_spanish: bool) -> List[str]:
        """Construye comando ffmpeg optimizado con mejor manejo de errores"""
        ffmpeg_cmd = [
            "ffmpeg", "-hide_banner", "-y",
            "-hwaccel", "vaapi",  # Decodificación por hardware
            "-hwaccel_device", VAAPI_DEVICE_PATH,
            "-i", str(file_path),
            "-vf", "format=nv12,hwupload",
            "-c:v", "h264_vaapi",
            "-qp", "28",  # Calidad constante más conservadora
            "-low_power", "1",  # Usa codificación de bajo consumo (más eficiente)
            "-max_muxing_queue_size", "1024"  # Buffer más grande para evitar pérdidas
        ]
        
        # Detecta streams disponibles de forma más robusta
        has_audio_stream, has_subtitle_stream, audio_streams, sub_streams = self._detect_streams(file_path)
        
        # Mapeo de video (siempre presente)
        ffmpeg_cmd.extend(["-map", "0:v:0"])
        
        # Mapeo de audio más robusto
        if has_audio_stream:
            self._add_audio_mapping(ffmpeg_cmd, has_spanish, audio_streams)
        
        # Mapeo de subtítulos más robusto
        if has_subtitle_stream and has_spanish:
            self._add_subtitle_mapping(ffmpeg_cmd, has_spanish, sub_streams)
        
        # Configuración final con parámetros anti-corrupción
        ffmpeg_cmd.extend([
            "-movflags", "+faststart+frag_keyframe+empty_moov",  # Mejor estructura MP4
            "-fflags", "+genpts",  # Regenera timestamps si es necesario
            "-avoid_negative_ts", "make_zero",  # Evita timestamps negativos
            str(temp_output)
        ])
        
        return ffmpeg_cmd

    def _build_fallback_ffmpeg_command(self, file_path: Path, temp_output: Path) -> List[str]:
        """Construye comando ffmpeg fallback sin aceleración de hardware"""
        ffmpeg_cmd = [
            "ffmpeg", "-hide_banner", "-y",
            "-i", str(file_path),
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "28",
            "-max_muxing_queue_size", "1024"  # Buffer más grande para estabilidad
        ]
        
        # Mapeo de video
        ffmpeg_cmd.extend(["-map", "0:v:0"])
        
        # Mapeo de audio (simplificado para fallback)
        ffmpeg_cmd.extend([
            "-map", FIRST_AUDIO_STREAM,
            "-c:a", "aac",
            "-b:a", "128k",
            "-ac", "2"
        ])
        
        # No incluir subtítulos en fallback para simplicidad y estabilidad
        
        # Configuración final con parámetros anti-corrupción
        ffmpeg_cmd.extend([
            "-movflags", "+faststart+frag_keyframe+empty_moov",  # Mejor estructura MP4
            "-fflags", "+genpts",  # Regenera timestamps si es necesario
            "-avoid_negative_ts", "make_zero",  # Evita timestamps negativos
            str(temp_output)
        ])
        
        return ffmpeg_cmd

    def _validate_compressed_file(self, compressed_file: Path, original_file: Path) -> Tuple[bool, str]:
        """Valida la integridad del archivo comprimido y verifica que tenga streams de audio"""
        try:
            # Verifica que el archivo exista y tenga tamaño
            if not compressed_file.exists() or compressed_file.stat().st_size == 0:
                return False, "Archivo no existe o está vacío"
            
            # Verifica integridad básica con ffprobe
            probe_cmd = [
                "ffprobe", "-v", "quiet", "-print_format", "json", 
                "-show_streams", "-show_format", str(compressed_file)
            ]
            
            result = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=MEDIUM_TIMEOUT)
            
            if result.returncode != 0:
                return False, f"FFprobe falló: {result.stderr[:200]}"
            
            # Parsea JSON de ffprobe
            import json
            try:
                probe_data = json.loads(result.stdout)
            except json.JSONDecodeError:
                return False, "Error parseando salida de ffprobe"
            
            # Verifica que tenga streams
            streams = probe_data.get('streams', [])
            if not streams:
                return False, "No se encontraron streams en el archivo"
            
            # Verifica que tenga al menos un stream de video
            video_streams = [s for s in streams if s.get('codec_type') == 'video']
            if not video_streams:
                return False, "No se encontró stream de video"
            
            # Verifica que tenga al menos un stream de audio
            audio_streams = [s for s in streams if s.get('codec_type') == 'audio']
            if not audio_streams:
                return False, "No se encontraron streams de audio"
            
            # Verifica duración similar al original (±5 segundos de tolerancia)
            try:
                compressed_duration = float(probe_data.get('format', {}).get('duration', 0))
                
                # Obtiene duración del archivo original
                original_probe_cmd = [
                    "ffprobe", "-v", "quiet", "-print_format", "json", 
                    "-show_format", str(original_file)
                ]
                original_result = subprocess.run(original_probe_cmd, capture_output=True, text=True, timeout=MEDIUM_TIMEOUT)
                
                if original_result.returncode == 0:
                    original_data = json.loads(original_result.stdout)
                    original_duration = float(original_data.get('format', {}).get('duration', 0))
                    
                    if abs(compressed_duration - original_duration) > 5.0:
                        return False, f"Duración muy diferente: original={original_duration:.1f}s, comprimido={compressed_duration:.1f}s"
                        
            except ValueError:
                self.logger.warning(f"No se pudo verificar duración de {compressed_file.name}")
            
            return True, f"Archivo válido: {len(video_streams)} video, {len(audio_streams)} audio"
            
        except subprocess.TimeoutExpired:
            return False, "Timeout validando archivo"
        except Exception as e:
            return False, f"Error validando: {str(e)}"

    def _process_compression_result(self, process: subprocess.CompletedProcess, 
                                  file_path: Path, temp_output: Path, 
                                  elapsed_time: float, used_gpu: bool = True) -> Dict:
        """Procesa el resultado de la compresión"""
        if process.returncode == 0 and temp_output.exists() and temp_output.stat().st_size > 0:
            return self._handle_successful_compression(temp_output, file_path, elapsed_time, used_gpu)
        else:
            return self._handle_failed_compression(process, file_path, temp_output)

    def _handle_successful_compression(self, temp_output: Path, file_path: Path, elapsed_time: float, used_gpu: bool) -> Dict:
        """Maneja el caso de compresión exitosa"""
        result = {'compressed': False, 'renamed': False, 'success': True, 'error': None}
        
        # Validación
        is_valid, validation_msg = self._validate_compressed_file(temp_output, file_path)
        
        if not is_valid:
            self.logger.error(f"Archivo comprimido inválido para {file_path.name}: {validation_msg}")
            result['error'] = f"Archivo comprimido corrupto: {validation_msg}"
            if temp_output.exists():
                temp_output.unlink()
            return result
        
        self.logger.info(f"Validación exitosa para {file_path.name}: {validation_msg}")
        
        # Tamaños
        original_size = file_path.stat().st_size
        compressed_size = temp_output.stat().st_size
        
        if compressed_size >= original_size:
            method_str = "GPU" if used_gpu else "CPU"
            self.logger.info(f"Archivo comprimido es mayor, renombrando ({method_str}): {file_path.name}")
            final_name = file_path.with_suffix('.mp4')
            temp_output.rename(final_name)
            file_path.unlink()
            result['renamed'] = True
        else:
            reduction_percent = ((original_size - compressed_size) / original_size) * 100
            method_str = "GPU" if used_gpu else "CPU"
            self.logger.info(f"Compresión {method_str} exitosa: {file_path.name} ({elapsed_time:.1f}s, -{reduction_percent:.1f}%)")
            file_path.unlink()
            temp_output.rename(file_path.with_suffix('.mp4'))
            result['compressed'] = True
        
        # Marca como completado
        with open(self.completed_file, 'a') as f:
            f.write(f"{file_path.with_suffix('.mp4')}\n")
        
        return result

    def _handle_failed_compression(self, process: subprocess.CompletedProcess, file_path: Path, temp_output: Path) -> Dict:
        """Maneja el caso de compresión fallida"""
        result = {'compressed': False, 'renamed': False, 'success': False, 'error': None}
        
        error_msg = f"Falló compresión (código {process.returncode}): {file_path}"
        if process.returncode == 124:
            error_msg = f"Timeout alcanzado ({FFMPEG_TIMEOUT/3600:.1f}h): {file_path}"
        
        if process.stderr:
            self.logger.error(f"FFmpeg stderr: {process.stderr[:500]}")
        
        result['error'] = error_msg
        
        if temp_output.exists():
            temp_output.unlink()
        
        return result

    def compress_single_file(self, file_path: Path) -> Dict:
        """Comprime un solo archivo con selección inteligente de método de compresión"""
        result = {
            'file': str(file_path),
            'success': False,
            'error': None,
            'no_spanish': False,
            'compressed': False,
            'renamed': False,
            'skipped': False
        }
        
        try:
            # Establece límites para este proceso
            self.set_resource_limits()
            
            # Prepara compresión: validación, detección de idiomas, selección de método
            is_prepared, compression_info, temp_output, audio_langs, sub_langs = self._prepare_compression(file_path)
            if not is_prepared:
                return {**result, **compression_info}
            
            use_gpu = compression_info['use_gpu']
            has_spanish = compression_info['has_spanish']
            
            if not has_spanish:
                result['no_spanish'] = True
                self.no_spanish_logger.info(f"{file_path}")
                self.no_spanish_logger.info(f"   Idiomas de audio: {audio_langs}")
                self.no_spanish_logger.info(f"   Idiomas de subtítulos: {sub_langs}")
                self.no_spanish_logger.info("   ---")
            
            # Ejecuta compresión
            process, elapsed_time = self._execute_compression_attempt(file_path, temp_output, use_gpu, has_spanish)
            
            # Procesa resultado inicial
            compression_result = self._process_compression_result(process, file_path, temp_output, elapsed_time, use_gpu)
            
            # Lógica de fallback en caso de fallo con GPU
            compression_result = self._handle_fallback_logic(compression_result, process, file_path, temp_output, use_gpu, has_spanish)
            
            result.update(compression_result)
                    
        except subprocess.TimeoutExpired:
            result['error'] = f"Timeout alcanzado ({FFMPEG_TIMEOUT/3600:.1f}h): {file_path}"
            temp_output = file_path.with_suffix(COMPRESSED_FILE_SUFFIX)
            if temp_output.exists():
                temp_output.unlink()
        except Exception as e:
            result['error'] = f"Error inesperado: {file_path} - {str(e)}"
            
        return result

    def _prepare_compression(self, file_path: Path) -> Tuple[bool, Dict, Path, List, List]:
        """Prepara la compresión: validación, idiomas, método"""
        # Validación
        valid, validation_msg = self._validate_file_for_compression(file_path)
        if not valid:
            result = {'skipped': validation_msg == "archivo_pequeno", 'error': validation_msg if validation_msg != "archivo_pequeno" else None}
            return False, result, None, [], []
        
        # Idiomas
        has_spanish, audio_langs, sub_langs = self.detect_language_streams(file_path)
        
        # Temporal
        temp_output = file_path.with_suffix(COMPRESSED_FILE_SUFFIX)
        
        # Método
        use_gpu = self._should_use_gpu_compression(file_path)
        
        return True, {'use_gpu': use_gpu, 'has_spanish': has_spanish}, temp_output, audio_langs, sub_langs

    def _execute_compression_attempt(self, file_path: Path, temp_output: Path, use_gpu: bool, has_spanish: bool) -> Tuple[subprocess.CompletedProcess, float]:
        """Ejecuta un intento de compresión"""
        if use_gpu:
            ffmpeg_cmd = self._build_ffmpeg_command(file_path, temp_output, has_spanish)
            cmd_str = " ".join(ffmpeg_cmd[:10]) + " ... [GPU/audio/subs/output]"
            self.logger.info(f"Comando ffmpeg GPU: {cmd_str}")
        else:
            ffmpeg_cmd = self._build_fallback_ffmpeg_command(file_path, temp_output)
            cmd_str = " ".join(ffmpeg_cmd[:8]) + " ... [CPU/audio/output]"
            self.logger.info(f"Comando ffmpeg CPU: {cmd_str}")
        
        start_time = time.time()
        process = subprocess.run(
            ffmpeg_cmd,
            capture_output=True,
            text=True,
            timeout=FFMPEG_TIMEOUT
        )
        elapsed_time = time.time() - start_time
        
        return process, elapsed_time

    def _handle_fallback_logic(self, compression_result: Dict, process: subprocess.CompletedProcess, file_path: Path, temp_output: Path, use_gpu: bool, has_spanish: bool) -> Dict:
        """Maneja la lógica de fallback CPU"""
        if not compression_result['success'] and use_gpu and 'corrupto' in str(compression_result.get('error', '')):
            self.logger.warning(f"Archivo corrupto con GPU, intentando fallback CPU: {file_path.name}")
            if temp_output.exists():
                temp_output.unlink()
            process, new_elapsed_time = self._execute_compression_attempt(file_path, temp_output, False, has_spanish)
            compression_result = self._process_compression_result(process, file_path, temp_output, new_elapsed_time, False)
        elif process.returncode != 0 and use_gpu:
            self.logger.warning(f"Compresión GPU falló, intentando fallback CPU: {file_path.name}")
            if temp_output.exists():
                temp_output.unlink()
            process, new_elapsed_time = self._execute_compression_attempt(file_path, temp_output, False, has_spanish)
            compression_result = self._process_compression_result(process, file_path, temp_output, new_elapsed_time, False)
        
        return compression_result

    def _should_use_gpu_compression(self, file_path: Path) -> bool:
        """Decide si usar compresión GPU basado en historial y disponibilidad de hardware"""
        # Verifica si el hardware está disponible (test más completo)
        if not self._check_hardware_acceleration_available():
            return False
        
        # Archivos pequeños o con calidad ya baja van a CPU por eficiencia
        file_size_mb = file_path.stat().st_size / (1024 * 1024)
        if file_size_mb < 100:  # Archivos menores a 100MB van a CPU
            return False
        
        # Verifica si este archivo específico ha fallado antes con GPU
        # (Implementación de lógica de historial si es necesario)
        
        # Por defecto usa GPU para archivos grandes
        return True
    
    def _process_result(self, result: Dict, file_path: Path, 
                       current_errors: List[str], current_no_spanish: List[str]) -> None:
        """Procesa el resultado de compresión de un archivo individual"""
        # Incremento de files_processed solo si el archivo realmente se procesó (no omitido por estar ya procesado)
        if not result.get('already_processed', False):
            self.stats.files_processed += 1
        
        if result['success']:
            if result['compressed']:
                self.stats.files_compressed += 1
            elif result['renamed']:
                self.stats.files_renamed += 1
            elif result['skipped']:
                self.stats.files_skipped += 1
        
        if result['error']:
            self.logger.error(result['error'])
            current_errors.append(file_path.name)
            # Agrega el error a las estadísticas también
            if file_path.name not in self.stats.errors:
                self.stats.errors.append(file_path.name)
            
        if result['no_spanish']:
            current_no_spanish.append(file_path.name)
            # Agrega el archivo sin español a las estadísticas también
            if file_path.name not in self.stats.no_spanish:
                self.stats.no_spanish.append(file_path.name)

    def _save_temporary_results(self, current_errors: List[str], current_no_spanish: List[str]) -> None:
        """Guarda errores y archivos sin español en archivos temporales"""
        if current_errors:
            with open(self.tmp_dir / "error_files.tmp", 'w') as f:
                f.write('\n'.join(current_errors))
                
        if current_no_spanish:
            with open(self.tmp_dir / "no_spanish_files.tmp", 'w') as f:
                f.write('\n'.join(current_no_spanish))

    def _initialize_processing(self, files: List[Path]) -> tuple[bool, int]:
        """Inicializa el procesamiento verificando aceleración y limpiando temporales"""
        if not files:
            self.logger.info("No hay archivos pendientes para comprimir")
            self._save_progress_state(0, 0, "Sin archivos pendientes", status='completed')
            return False, 0
        
        # Verifica disponibilidad de aceleración por hardware
        hw_acceleration_available = self._check_hardware_acceleration_available()
        if not hw_acceleration_available:
            self.logger.warning("Aceleración por hardware no disponible, usando solo CPU")
        else:
            self.logger.info("Aceleración por hardware disponible")
        
        total_files = len(files)
        self.logger.info(f"Iniciando procesamiento de {total_files} archivos con {MAX_CONCURRENT_COMPRESSIONS} workers")
        
        # Limpia archivos temporales de ejecución anterior
        (self.tmp_dir / "error_files.tmp").unlink(missing_ok=True)
        (self.tmp_dir / "no_spanish_files.tmp").unlink(missing_ok=True)
        
        return True, total_files

    def _load_previous_progress(self, progress_info: dict) -> None:
        """Carga estadísticas y archivos procesados de progreso anterior"""
        # Conservación de estadísticas previas si el progreso anterior no era completo (< 90%)
        if progress_info.get('percentage', 0) < 90 and 'stats' in progress_info:
            prev_stats = progress_info['stats']
            self.logger.info(f"Cargando estadísticas previas (progreso anterior: {progress_info.get('percentage', 0):.1f}%)")
            self.stats.files_found = prev_stats.get('files_found', 0)
            self.stats.files_new = prev_stats.get('files_new', 0) 
            self.stats.files_processed = prev_stats.get('files_processed', 0)
            self.stats.files_compressed = prev_stats.get('files_compressed', 0)
            self.stats.files_renamed = prev_stats.get('files_renamed', 0)
            self.stats.files_skipped = prev_stats.get('files_skipped', 0)
            if 'errors' in prev_stats:
                self.stats.errors = prev_stats['errors']
            if 'no_spanish' in prev_stats:
                self.stats.no_spanish = prev_stats['no_spanish']
        
        # Carga archivos procesados previamente
        if 'processed_files' in progress_info:
            self.processed_files = progress_info['processed_files']

    def _filter_already_processed_files(self, files: List[Path]) -> List[Path]:
        """Filtra archivos ya procesados exitosamente"""
        files_before_filter = len(files)
        files = [f for f in files if str(f) not in self.processed_files or self.processed_files[str(f)]['status'] != 'success']
        files_filtered = files_before_filter - len(files)
        if files_filtered > 0:
            self.logger.info(f"Archivos ya procesados exitosamente omitidos: {files_filtered}")
        return files

    def _submit_concurrent_jobs(self, files: List[Path]) -> tuple[dict, list[str]]:
        """Envía trabajos concurrentes y retorna futures y lista de archivos en progreso"""
        with ProcessPoolExecutor(max_workers=MAX_CONCURRENT_COMPRESSIONS) as executor:
            future_to_file = {executor.submit(self.compress_single_file, file_path): file_path 
                            for file_path in files}
            files_in_progress = [f.name for f in files]
            return future_to_file, files_in_progress

    def _process_concurrent_results(self, future_to_file: dict, files_in_progress: list[str], total_files: int) -> tuple[list[str], list[str]]:
        """Procesa resultados de trabajos concurrentes"""
        current_errors = []
        current_no_spanish = []
        processed_count = 0
        
        for future in as_completed(future_to_file):
            file_path = future_to_file[future]
            processed_count += 1
            
            # Remueve el archivo de la lista de procesamiento
            if file_path.name in files_in_progress:
                files_in_progress.remove(file_path.name)
            
            # Construye mensaje de archivos en procesamiento
            current_file_name = self._build_progress_message(files_in_progress)
            
            # Actualiza progreso
            self._save_progress_state(processed_count, total_files, current_file_name, status='processing')
            
            # Log progreso
            if processed_count % PROGRESS_UPDATE_INTERVAL == 0 or processed_count == total_files:
                percentage = (processed_count / total_files) * 100
                self.logger.info(f"Progreso: {processed_count}/{total_files} ({percentage:.1f}%) - Archivo procesado: {file_path.name}")
            
            try:
                result = future.result()
                self._process_result(result, file_path, current_errors, current_no_spanish)
                self.processed_files[str(file_path)] = {'status': 'success', 'timestamp': datetime.now().isoformat()}
                    
            except Exception as e:
                error_msg = f"Error procesando {file_path}: {str(e)}"
                self.logger.error(error_msg)
                current_errors.append(file_path.name)
                self.processed_files[str(file_path)] = {'status': 'failed', 'timestamp': datetime.now().isoformat(), 'error': str(e)}
        
        return current_errors, current_no_spanish

    def _build_progress_message(self, files_in_progress: list[str]) -> str:
        """Construye mensaje de progreso basado en archivos en procesamiento"""
        if files_in_progress:
            display_files = files_in_progress[:3]
            if len(files_in_progress) > 3:
                return f"{', '.join(display_files)} y {len(files_in_progress) - 3} más"
            else:
                return ', '.join(display_files)
        else:
            return PROCESSING_COMPLETED_MESSAGE

    def process_files_concurrent(self, files: List[Path]) -> ProcessingStats:
        """Procesa archivos con concurrencia limitada y seguimiento de progreso"""
        # Inicializa procesamiento
        initialized, total_files = self._initialize_processing(files)
        if not initialized:
            return self.stats
        
        # Limpia archivos procesados previamente (si existen)
        self._clean_processed_files_if_necessary()
        
        # Carga progreso anterior si existe
        progress_info = self.get_progress_info()
        
        # Carga estadísticas y archivos procesados de progreso anterior
        self._load_previous_progress(progress_info)
        
        # Filtra archivos ya procesados exitosamente
        files = self._filter_already_processed_files(files)
        
        # Actualiza total de archivos después del filtrado
        total_files = len(files)
        if total_files == 0:
            self.logger.info("Todos los archivos ya han sido procesados exitosamente")
            self._save_progress_state(0, 0, PROCESSING_COMPLETED_MESSAGE, status='completed')
            return self.stats
            
        self.logger.info(f"Procesando {total_files} archivos pendientes con {MAX_CONCURRENT_COMPRESSIONS} workers")
        
        # Envía trabajos concurrentes
        future_to_file, files_in_progress = self._submit_concurrent_jobs(files)
        
        # Procesa resultados de trabajos concurrentes
        current_errors, current_no_spanish = self._process_concurrent_results(future_to_file, files_in_progress, total_files)
        
        # Guarda errores y archivos sin español de esta ejecución
        self._save_temporary_results(current_errors, current_no_spanish)
        
        # Actualiza progreso como completado
        self._save_progress_state(total_files, total_files, PROCESSING_COMPLETED_MESSAGE, status='completed')
        
        # Log de resumen
        self.logger.info(f"RESUMEN: Procesados: {self.stats.files_processed}, "
                        f"Comprimidos: {self.stats.files_compressed}, "
                        f"Renombrados: {self.stats.files_renamed}, "
                        f"Omitidos: {self.stats.files_skipped}")
        
        return self.stats
    
    def run(self) -> ProcessingStats:
        """Ejecuta procesamiento completo"""
        try:
            self.logger.info("=== Iniciando MediaJelly Python ===")
            
            # Limpia archivos pendientes procesados antes de escanear
            self.cleanup_processed_files()
            
            # Escanea archivos
            files = self.scan_media_files()
            
            if not files:
                self.logger.info("No hay archivos pendientes para procesar")
                # Verifica si todos los archivos pendientes ya están procesados
                self._check_all_pending_processed()
                return self.stats
            
            # Procesa archivos
            self.stats = self.process_files_concurrent(files)
            
            # Limpia archivos pendientes procesados exitosamente (de nuevo por si acaso)
            self.cleanup_processed_files()
            
            # Verifica si todos los archivos pendientes ya están procesados
            self._check_all_pending_processed()
            
            self.logger.info("=== MediaJelly Python completado ===")
            
        except Exception as e:
            self.logger.error(f"Error fatal en MediaJelly: {e}")
            
        return self.stats
    
    def cleanup_processed_files(self):
        """Limpia archivos procesados exitosamente del archivo pending"""
        if not self.pending_file.exists():
            return
            
        completed_files = self._load_completed_files()
        
        # Lee archivos pendientes actuales y filtra
        remaining_pending, orphaned_files, files_removed = self._filter_pending_files(completed_files)
        
        # Reescribe el archivo con solo los archivos válidos pendientes
        self._rewrite_pending_file(remaining_pending)

        orphaned_count = len(orphaned_files)
        files_kept = len(remaining_pending)
        
        self._log_cleanup_results(files_removed, orphaned_count, files_kept, orphaned_files)

    def _load_completed_base_paths(self) -> set:
        """Carga las rutas base de archivos completados"""
        completed_base_paths = set()
        if self.completed_file.exists():
            with open(self.completed_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        path_obj = Path(line)
                        base_path = str(path_obj.parent / path_obj.stem)
                        completed_base_paths.add(base_path)
        return completed_base_paths

    def _filter_pending_files(self, completed_files: set) -> Tuple[List[str], List[str], int]:
        """Filtra archivos pendientes, separando válidos y huérfanos"""
        remaining_pending = []
        orphaned_files = []
        files_removed = 0
        with open(self.pending_file, 'r') as f:
            for line in f:
                file_path = line.strip()
                if file_path:
                    pending_path_obj = Path(file_path)
                    mp4_path = pending_path_obj.with_suffix('.mp4')
                    if str(mp4_path) in completed_files:
                        files_removed += 1
                        continue  # Remover archivos procesados
                    elif not pending_path_obj.exists():
                        orphaned_files.append(file_path)
                        continue
                    remaining_pending.append(file_path)
        return remaining_pending, orphaned_files, files_removed

    def _rewrite_pending_file(self, remaining_pending: List[str]) -> None:
        """Reescribe el archivo de pendientes con archivos válidos"""
        with open(self.pending_file, 'w') as f:
            for file_path in remaining_pending:
                f.write(f"{file_path}\n")

    def _log_cleanup_results(self, files_removed: int, orphaned_count: int, files_kept: int, orphaned_files: List[str]) -> None:
        """Loggea los resultados de la limpieza"""
        self.logger.info(f"Archivos procesados removidos de pendientes: {files_removed}")
        if orphaned_count > 0:
            self.logger.warning(f"Archivos huérfanos encontrados y removidos: {orphaned_count}")
            for orphaned in orphaned_files:
                self.logger.warning(f"  Huérfano: {orphaned}")
        self.logger.info(f"Archivos mantenidos en pendientes: {files_kept}")
        
        if files_removed > 0 or orphaned_count > 0:
            self.logger.info("Limpieza de lista de pendientes completada")

    def _check_all_pending_processed(self) -> None:
        """Verifica si todos los archivos pendientes ya están procesados y termina si es así"""
        if not self.pending_file.exists():
            self.logger.info("No existe archivo de pendientes")
            return
            
        # Lee archivos pendientes actuales
        with open(self.pending_file, 'r') as f:
            pending_lines = [line.strip() for line in f if line.strip()]
        
        if not pending_lines:
            self.logger.info("No hay archivos pendientes - todos procesados")
            return
        
        # Carga archivos completados para comparación
        completed_files = self._load_completed_files()
        
        # Normaliza rutas para comparación (convierte rutas absolutas a relativas desde media_dir)
        def normalize_path(path_str: str) -> str:
            """Normaliza una ruta para comparación"""
            path_obj = Path(path_str)
            
            # Reemplaza diferentes prefijos de ruta para normalizar
            path_str_normalized = str(path_obj)
            path_str_normalized = path_str_normalized.replace('/home/tafurc/mediaJelly/', '')
            path_str_normalized = path_str_normalized.replace('/mediajelly/', '')
            
            return path_str_normalized
        
        # Verifica cada archivo pendiente
        all_processed = True
        for pending_path in pending_lines:
            # Normaliza la ruta pendiente
            normalized_pending = normalize_path(pending_path)
            
            # Verifica si está en archivos completados (comparando rutas normalizadas)
            found_in_completed = False
            for completed_path in completed_files:
                normalized_completed = normalize_path(completed_path)
                if normalized_pending == normalized_completed:
                    found_in_completed = True
                    break
            
            if not found_in_completed:
                all_processed = False
                break
        
        if all_processed:
            self.logger.info(f"Todos los {len(pending_lines)} archivos pendientes ya están procesados - terminando")
            # Limpia el archivo de pendientes ya que todos están procesados
            self.pending_file.unlink(missing_ok=True)
        else:
            remaining_count = len(pending_lines)
            self.logger.info(f"Aún quedan {remaining_count} archivos pendientes por procesar")

def main():
    """Función principal"""
    processor = MediaJellyProcessor()
    
    # Manejo de señales para terminación limpia
    def signal_handler(signum, frame):
        processor.logger.info(f"Recibida señal {signum}, terminando...")
        sys.exit(0)
    
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    # Ejecuta procesamiento
    stats = processor.run()
    
    # Imprime estadísticas
    print(f"Procesados: {stats.files_processed}")
    print(f"Comprimidos: {stats.files_compressed}")
    print(f"Renombrados: {stats.files_renamed}")
    print(f"Omitidos: {stats.files_skipped}")

if __name__ == "__main__":
    main()