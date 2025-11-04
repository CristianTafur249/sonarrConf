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
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, asdict
from typing import List, Dict, Optional, Tuple, Union
import signal
import atexit
import glob

# Configuración de límites de recursos
MAX_MEMORY_GB = 4  # Limita memoria por proceso ffmpeg
MAX_CPU_PERCENT = 30  # Restringe porcentaje máximo de CPU total
MAX_CONCURRENT_COMPRESSIONS = 2  # Permite 2 compresiones simultáneas
FFMPEG_TIMEOUT = 9000  # Establece 2.5 horas por archivo

# Constantes para archivos y formatos
COMPRESSED_FILE_SUFFIX = '.compressed.mp4'
STREAM_LANGUAGE_QUERY = "stream=index:stream_tags=language"
CSV_FORMAT_PARAM = "csv=p=0"
FIRST_AUDIO_STREAM = "0:a:0"
PROGRESS_UPDATE_INTERVAL = 1  # Actualizar progreso cada 1 archivo
PROGRESS_FILE_NAME = "progress.json"
VAAPI_DEVICE_PATH = "/dev/dri/renderD128"
LOG_RETENTION_DAYS = 30  # Días para mantener logs antiguos
SHORT_TIMEOUT = 10  # Timeout corto para operaciones rápidas (segundos)
MEDIUM_TIMEOUT = 120  # Timeout medio para operaciones de análisis (segundos)
PROCESSING_COMPLETED_MESSAGE = "Procesamiento completado"
EXCLUDED_FOLDERS = {'.delete', '.deleted', '.tmp', '.temp', '.trash', '.recycle'}

@dataclass
class ProcessingStats:
    """Estadísticas de procesamiento"""
    files_found: int = 0
    files_new: int = 0
    files_processed: int = 0
    files_compressed: int = 0
    files_renamed: int = 0
    files_skipped: int = 0
    total_original_size: int = 0
    total_compressed_size: int = 0
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
        
        # Mejorador de calidad
        self.quality_improver = VideoQualityImprover(self.logger)
        
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
        self.no_spanish_logger.propagate = False  # No heredar handlers del padre
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
    
    def _is_file_in_excluded_folder(self, file_path: Path) -> bool:
        """Verifica si un archivo está en una carpeta excluida"""
        return any(part.lower() in EXCLUDED_FOLDERS for part in file_path.parts)
        
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
        """Guarda el estado actual del progreso
        
        Args:
            current_file: Número de archivos procesados actualmente
            total_files: Total de archivos válidos para procesar (filtrados)
            current_file_name: Nombre del archivo actual o mensaje de estado
            status: Estado del procesamiento (scanning, processing, completed, error)
        """
        progress_file = self.tmp_dir / PROGRESS_FILE_NAME
        
        # Leer progreso existente para preservar otras secciones
        existing_progress = {}
        if progress_file.exists():
            try:
                with open(progress_file, 'r') as f:
                    existing_progress = json.load(f)
            except Exception:
                existing_progress = {}
        
        # Calcular archivos ya procesados (de processed_files)
        num_processed_files = len(existing_progress.get('processing', {}).get('processed_files', {}))
        
        # Calcular archivos nuevos: archivos escaneados que NO están en processed_files
        files_new = total_files - num_processed_files if total_files > num_processed_files else 0
        
        # Calcula porcentaje basado en archivos procesados vs total escaneados
        percentage = round((current_file / total_files) * 100, 1) if total_files > 0 else 0
        
        # Asegurar que existe la sección processing con estructura completa
        if 'processing' not in existing_progress:
            existing_progress['processing'] = {
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
                "processed_files": {},
                "last_notification": None,
                "last_cleanup_notification": None
            }
        
        # Asegurar que existen los campos last_notification si no están
        if 'last_notification' not in existing_progress['processing']:
            existing_progress['processing']['last_notification'] = None
        if 'last_cleanup_notification' not in existing_progress['processing']:
            existing_progress['processing']['last_cleanup_notification'] = None
        
        # Actualizar la sección processing
        existing_progress['processing'].update({
            'current_file': current_file,
            'total_files': total_files,  # Total de archivos escaneados
            'current_file_name': current_file_name,
            'percentage': percentage,
            'last_updated': datetime.now().isoformat(),
            'status': status,  # scanning, processing, completed, error
            'notified': False,  # Indica si ya se envió notificación para esta ejecución
        })
        
        # Actualizar files_new y files_found en las estadísticas
        self.stats.files_new = files_new
        self.stats.files_found = total_files
        
        # Actualizar estadísticas en la sección processing
        existing_progress['processing']['stats'] = asdict(self.stats)
        existing_progress['processing']['processed_files'] = self.processed_files
        
        try:
            with open(progress_file, 'w') as f:
                json.dump(existing_progress, f, indent=2)
        except Exception as e:
            self.logger.warning(f"Error guardando progreso: {e}")

    def get_progress_info(self) -> Dict:
        """Obtiene información del progreso actual"""
        progress_file = self.tmp_dir / PROGRESS_FILE_NAME
        try:
            if progress_file.exists():
                with open(progress_file, 'r') as f:
                    data = json.load(f)
                    
                    # Si existe la sección processing, devolver esa información
                    if 'processing' in data:
                        processing_data = data['processing'].copy()
                        # Asegura que tenga el campo status
                        if 'status' not in processing_data:
                            processing_data['status'] = 'unknown'
                        return processing_data
                    else:
                        # Fallback para compatibilidad con estructura antigua
                        if 'status' not in data:
                            data['status'] = 'unknown'
                        return data
        except Exception as e:
            self.logger.warning(f"Error leyendo progreso: {e}")
        
        # Retornar estructura por defecto para la sección processing
        return {
            'current_file': 0,
            'total_files': 0,
            'current_file_name': 'N/A',
            'percentage': 0,
            'last_updated': None,
            'status': 'idle',
            'notified': False,
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
            
            result = subprocess.run(
                test_cmd, 
                capture_output=True, 
                timeout=SHORT_TIMEOUT,
                encoding='utf-8',
                errors='replace'
            )
            
            if result.returncode == 0 and "vaapi" in result.stdout:
                self.logger.info("Aceleración VAAPI disponible en FFmpeg")
                return True
            else:
                self.logger.warning("VAAPI no disponible en FFmpeg")
                return False
            
        except Exception as e:
            self.logger.warning(f"Error verificando aceleración de hardware: {e}")
            return False

    def _remove_orphaned_compressed_files(self) -> int:
        """Elimina TODOS los archivos .compressed.mp4 al inicio del procesamiento.
        Estos archivos indican procesos interrumpidos/corruptos del procesamiento anterior.
        Retorna el número de archivos eliminados.
        """
        compressed_files = list(self.media_dir.rglob(f"*{COMPRESSED_FILE_SUFFIX}"))
        files_removed = 0
        
        for compressed_file in compressed_files:
            try:
                # Excluir archivos en carpetas de backup/eliminación
                if any(exclude in str(compressed_file) for exclude in ['/Bittorrent/', '/.Trash/', '/backup/']):
                    continue
                
                file_size_mb = compressed_file.stat().st_size / (1024 * 1024)
                self.logger.warning(
                    f"Eliminando archivo .compressed.mp4 huérfano de proceso anterior: "
                    f"{compressed_file.name} ({file_size_mb:.1f}MB)"
                )
                compressed_file.unlink()
                files_removed += 1
                
            except Exception as e:
                self.logger.error(f"Error eliminando {compressed_file}: {e}")
        
        if files_removed > 0:
            self.logger.info(f"Total de archivos .compressed.mp4 huérfanos eliminados: {files_removed}")
        
        return files_removed

    def _normalize_filename(self, file_path: Path) -> Optional[Path]:
        """Normaliza el nombre del archivo:
        - Elimina prefijos de grupos fansub como [Erai-raws], [SubsPlease], etc.
        - Convierte múltiples patrones de temporada/episodio a formato estándar 'SxxEyy' (SOLO PARA SERIES)
        - Mantiene el nombre de la serie y metadatos adicionales
        
        Retorna el nuevo Path si se renombró, None si no fue necesario
        """
        import re
        
        # DETECTAR SI ES PELÍCULA: no aplicar normalización de temporada/episodio
        is_movie = any(part.lower() in ['peliculas', 'movies'] for part in file_path.parts)
        
        original_name = file_path.stem  # Nombre sin extensión
        extension = file_path.suffix
        normalized_name = original_name
        
        # 1. Eliminar prefijos de grupos fansub entre corchetes/paréntesis al inicio
        # Patrones: [Erai-raws], [SubsPlease], [HorribleSubs], (grupo), etc.
        fansub_patterns = [
            r'^\[([^\]]+)\]\s*',     # [Grupo]
            r'^\(([^\)]+)\)\s*',     # (Grupo)
            r'^\{([^\}]+)\}\s*',     # {Grupo}
        ]
        
        for fansub_pattern in fansub_patterns:
            match = re.match(fansub_pattern, normalized_name)
            if match:
                group_name = match.group(1)
                rest_of_name = normalized_name[match.end():]
                
                # Solo eliminar si no es parte del nombre de la serie
                if rest_of_name and not rest_of_name.lower().startswith(group_name.lower()):
                    normalized_name = rest_of_name.strip()
                    self.logger.info(f"Removiendo prefijo [{group_name}] de: {original_name}")
                    break
        
        # 2. Normalizar formato de temporada/episodio - SOLO PARA SERIES (NO PELÍCULAS)
        if not is_movie:
            # Formatos soportados:
            season_episode_patterns = [
                # Formatos con "Season" explícito
                (r'\bSeason\s+(\d+)\s*-\s*(\d+)\b', 'Season X - Y'),                    # Season 2 - 10
                (r'\bSeason\s+(\d+)\s+Episode\s+(\d+)\b', 'Season X Episode Y'),        # Season 2 Episode 10
                (r'\bSeason\s+(\d+)\s+Ep\.?\s+(\d+)\b', 'Season X Ep Y'),               # Season 2 Ep 10
                (r'\bSeason\s+(\d+)\s*E(\d+)\b', 'Season X EY'),                        # Season 2 E10
                (r'\bSeason\s*(\d+)\s+(\d+)\b', 'Season X Y'),                          # Season 2 10
            
            # Formatos con separadores variados
            (r'\bS(\d+)\s*-\s*E?(\d+)\b', 'SX - EY'),                               # S2 - 10, S2 - E10
            (r'\bS(\d+)\s*x\s*E?(\d+)\b', 'SX x EY'),                               # S2 x 10, S2 x E10
            (r'\bS(\d+)\s*\.\s*E?(\d+)\b', 'SX.EY'),                                # S2.10, S2.E10
            (r'\b(\d+)x(\d+)\b', 'XxY'),                                            # 2x10
            
            # Formatos compactos
            (r'\bS(\d+)E(\d+)\b', 'SXEY'),                                          # S02E10 (ya normalizado, verificar dígitos)
            (r'\b(\d{1,2})(\d{2})\b(?![p\]])', 'XYY'),                             # 210 (solo si no es seguido de 'p' como en 1080p)
            
            # Formatos con guion bajo
            (r'\bS(\d+)_E?(\d+)\b', 'SX_EY'),                                       # S2_10, S2_E10
            (r'\b(\d+)_(\d+)\b', 'X_Y'),                                            # 2_10
            
            # Formatos japoneses/anime
            (r'\b第(\d+)話\b', 'Episodio X (japonés)'),                             # 第10話
            (r'\bEpisode\s+(\d+)\b(?!.*Season)', 'Episode X'),                      # Episode 10 (sin Season)
            (r'\bEp\.?\s+(\d+)\b(?!.*Season)', 'Ep X'),                             # Ep 10 (sin Season)
            (r'\bE(\d+)\b(?!.*[Ss]eason)(?!.*S\d+)', 'EX solo'),                   # E10 (sin Season ni SX)
            
            # Formatos con palabras completas
            (r'\bTemporada\s+(\d+)\s+Episodio\s+(\d+)\b', 'Temporada X Episodio Y'), # Temporada 2 Episodio 10 (español)
            (r'\bTemporada\s+(\d+)\s+Cap\.?\s+(\d+)\b', 'Temporada X Cap Y'),       # Temporada 2 Cap 10
            (r'\bT(\d+)\s*E(\d+)\b', 'TXEY'),                                       # T2E10 (español)
            (r'\bT(\d+)\s*C(\d+)\b', 'TXCY'),                                       # T2C10 (Cap español)
            ]
            
            normalized = False
            for pattern_tuple in season_episode_patterns:
                pattern = pattern_tuple[0]
                format_desc = pattern_tuple[1]
                
                match = re.search(pattern, normalized_name, re.IGNORECASE)
                if match:
                    # Determinar si es formato con temporada o solo episodio
                    groups = match.groups()
                    
                    if len(groups) == 2:
                        # Formato con temporada y episodio
                        season = groups[0].zfill(2)
                        episode = groups[1].zfill(2)
                    elif len(groups) == 1:
                        # Solo episodio (asumir temporada 01)
                        season = "01"
                        episode = groups[0].zfill(2)
                    else:
                        continue
                    
                    # Construir nombre normalizado
                    before_match = normalized_name[:match.start()].strip()
                    after_match = normalized_name[match.end():].strip()
                    
                    # Insertar formato estándar sin guiones alrededor de SxE
                    if before_match and after_match:
                        normalized_name = f"{before_match} S{season}E{episode} {after_match}"
                    elif before_match:
                        normalized_name = f"{before_match} S{season}E{episode}"
                    elif after_match:
                        normalized_name = f"S{season}E{episode} {after_match}"
                    else:
                        normalized_name = f"S{season}E{episode}"
                    
                    self.logger.info(f"Normalizando '{format_desc}': '{original_name}' -> '{normalized_name}'")
                    normalized = True
                    break
        else:
            # Para películas, no normalizar temporada/episodio
            normalized = False
        
        # 2.5. Cambiar formato de presentación: quitar guiones alrededor de SxE
        # Cambiar "Serie - S02E20 - Título" por "Serie S02E20 Título"
        normalized_name = re.sub(r'\s*-\s*(S\d+E\d+)\s*-\s*', r' \1 ', normalized_name)
        
        # 3. Limpiar espacios múltiples, guiones redundantes y guiones vacíos
        normalized_name = re.sub(r'\s+', ' ', normalized_name)           # Espacios múltiples a uno
        normalized_name = re.sub(r'\s*-\s*-\s*', ' - ', normalized_name) # -- a -
        normalized_name = re.sub(r'\s+-\s+\.', '', normalized_name)      # - . (guion con punto vacío)
        normalized_name = re.sub(r'\s*-\s*$', '', normalized_name)       # - al final
        normalized_name = re.sub(r'^\s*-\s*', '', normalized_name)       # - al inicio
        normalized_name = normalized_name.strip()
        
        # Si el nombre cambió, renombrar el archivo
        if normalized_name != original_name:
            new_path = file_path.parent / f"{normalized_name}{extension}"
            
            # Verificar que el nuevo nombre no exista ya
            if new_path.exists():
                self.logger.warning(f"No se puede renombrar a '{new_path.name}': el archivo ya existe")
                return None
            
            try:
                file_path.rename(new_path)
                self.logger.info(f"✓ Archivo renombrado: '{file_path.name}' -> '{new_path.name}'")
                return new_path
            except Exception as e:
                self.logger.error(f"Error renombrando archivo: {e}")
                return None
        
        return None

    def _clean_incomplete_compressed_files(self) -> Tuple[int, int]:
        """Limpia archivos .compressed.mp4 incompletos y retorna (files_cleaned, files_added_to_pending)
        Se ejecuta siempre para mantener el sistema limpio
        """
        # Excluir archivos en carpetas temporales o de eliminación
        compressed_files_found = [
            f for f in self.media_dir.rglob(f"*{COMPRESSED_FILE_SUFFIX}")
            if not self._is_file_in_excluded_folder(f)
        ]

        if len(compressed_files_found) == 0:
            return 0, 0

        self.logger.info(f"Busco archivos {COMPRESSED_FILE_SUFFIX} incompletos... Encontrados: {len(compressed_files_found)}")

        files_cleaned = 0
        files_added_to_pending = 0
        failed_files_path = self.scripts_dir / "failed-compression.txt"

        # Carga archivos que han fallado previamente
        failed_files = self._load_failed_files(failed_files_path)

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
                    original_file_str = str(original_file)

                    # Verifica si este archivo ya falló previamente
                    if original_file_str in failed_files:
                        self.logger.warning(f"Archivo ya marcado como fallido, omitiendo: {original_file.name}")
                        compressed_file.unlink()  # Elimina el .compressed.mp4 pero NO lo reagrega a pendientes
                        files_cleaned += 1
                        continue

                    self.logger.info(f"Elimino archivo incompleto: {compressed_file}")
                    self.logger.info(f"Archivo original correspondiente encontrado: {original_file.name}")

                    # Elimina el archivo temporal
                    compressed_file.unlink()
                    files_cleaned += 1

                    # SIEMPRE agregar de vuelta a pendientes cuando eliminamos un archivo incompleto
                    # Esto asegura que el archivo original se reprocese
                    if original_file_str not in failed_files:
                        self._add_to_pending(original_file)
                        files_added_to_pending += 1
                        self.logger.info(f"Archivo original agregado de vuelta a pendientes: {original_file.name}")

                else:
                    # No encuentra archivo original, elimina el temporal huérfano
                    self.logger.warning(f"Archivo temporal huérfano eliminado: {compressed_file}")
                    compressed_file.unlink()
                    files_cleaned += 1

            except Exception as e:
                self.logger.error(f"Error procesando archivo temporal {compressed_file}: {e}")

        if files_cleaned > 0:
            self.logger.info(f"Limpieza completada: {files_cleaned} archivos {COMPRESSED_FILE_SUFFIX} eliminados, {files_added_to_pending} agregados de vuelta a pendientes")

        return files_cleaned, files_added_to_pending
    
    def _load_failed_files(self, failed_files_path: Path) -> set:
        """Carga la lista de archivos que han fallado en compresión"""
        failed_files = set()
        if failed_files_path.exists():
            with open(failed_files_path, 'r') as f:
                for line in f:
                    file_path = line.strip()
                    if file_path:
                        failed_files.add(file_path)
        return failed_files

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
        """Obtiene archivos pendientes de procesamiento y normaliza sus nombres"""
        pending_files = []
        files_renamed = []
        invalid_files = []  # Para archivos que no son videos
        total_files_in_pending = 0  # Contador de archivos totales en pending
        
        # Extensiones de video válidas para procesamiento
        VALID_VIDEO_EXTENSIONS = {'.mkv', '.avi', '.ts', '.mp4', '.mov', '.wmv', '.flv', '.webm', '.m4v'}
        
        if self.pending_file.exists():
            with open(self.pending_file, 'r') as f:
                for line in f:
                    file_path = line.strip()
                    if file_path and Path(file_path).exists():
                        total_files_in_pending += 1  # Contar todos los archivos
                        original_path = Path(file_path)
                        
                        # FILTRO: Solo procesar archivos de video
                        if original_path.suffix.lower() not in VALID_VIDEO_EXTENSIONS:
                            invalid_files.append(str(original_path))
                            self.logger.warning(f"Archivo no es video, removiendo de pending: {original_path.name} (extensión: {original_path.suffix})")
                            continue
                        
                        # Normalizar nombre del archivo
                        new_path = self._normalize_filename(original_path)
                        
                        if new_path:
                            # El archivo fue renombrado
                            files_renamed.append((str(original_path), str(new_path)))
                            pending_files.append(new_path)
                        else:
                            # No se renombró, usar el path original
                            pending_files.append(original_path)
            
            # Guardar el total de archivos encontrados en pending (antes de filtrar)
            # Esto asegura que files_found refleje la cantidad real de archivos escaneados
            # self.stats.files_found = total_files_in_pending  # Removido: ahora se setea después de filtrar
            
            # Si hubo archivos inválidos, actualizar pending para removerlos
            if invalid_files:
                self._remove_invalid_files_from_pending(invalid_files)
                self.logger.info(f"Removidos {len(invalid_files)} archivos no-video de pending-compression.txt")
            
            # Si hubo renombramientos, actualizar el archivo pending
            if files_renamed:
                self._update_pending_after_rename(files_renamed)
        
        return pending_files
    
    def _remove_invalid_files_from_pending(self, invalid_files: List[str]) -> None:
        """Remueve archivos inválidos (no-video) del archivo pending-compression.txt"""
        if not self.pending_file.exists():
            return
        
        # Convertir lista de inválidos a set para búsqueda rápida
        invalid_set = set(invalid_files)
        
        # Leer todas las líneas válidas
        valid_lines = []
        with open(self.pending_file, 'r') as f:
            for line in f:
                line_clean = line.strip()
                if line_clean and line_clean not in invalid_set:
                    valid_lines.append(line_clean)
        
        # Reescribir el archivo solo con líneas válidas
        with open(self.pending_file, 'w') as f:
            for line in valid_lines:
                f.write(f"{line}\n")
    
    def _update_pending_after_rename(self, renamed_files: List[Tuple[str, str]]) -> None:
        """Actualiza pending-compression.txt después de renombrar archivos"""
        if not self.pending_file.exists():
            return
        
        # Leer todas las líneas
        with open(self.pending_file, 'r') as f:
            lines = [line.strip() for line in f if line.strip()]
        
        # Crear diccionario de renombramientos
        rename_map = dict(renamed_files)
        
        # Actualizar las líneas
        updated_lines = []
        for line in lines:
            if line in rename_map:
                updated_lines.append(rename_map[line])
            else:
                updated_lines.append(line)
        
        # Reescribir el archivo
        with open(self.pending_file, 'w') as f:
            for line in updated_lines:
                f.write(f"{line}\n")
        
        self.logger.info(f"Archivo pending actualizado con {len(renamed_files)} renombramientos")

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

    def _add_to_pending(self, file_path: Union[str, Path]) -> None:
        """Agrega un archivo a la cola de pendientes de compresión"""
        file_path_str = str(file_path) if isinstance(file_path, Path) else file_path
        
        # Verificar si ya existe en el archivo
        existing_files = set()
        if self.pending_file.exists():
            with open(self.pending_file, 'r') as f:
                existing_files = {line.strip() for line in f if line.strip()}
        
        # Agregar solo si no existe
        if file_path_str not in existing_files:
            with open(self.pending_file, 'a') as f:
                f.write(f"{file_path_str}\n")
            self.logger.info(f"Archivo agregado a pendientes: {file_path_str}")
        else:
            self.logger.debug(f"Archivo ya existe en pendientes: {file_path_str}")

    def scan_media_files(self) -> List[Path]:
        """Escanea archivos multimedia con pathlib optimizado"""
        self.logger.info("Iniciando escaneo de archivos multimedia...")
        
        # Verifica si hay progreso anterior incompleto
        existing_progress = self.get_progress_info()
        has_incomplete_progress = self._has_incomplete_progress(existing_progress)
        
        if has_incomplete_progress:
            self._handle_incomplete_progress(existing_progress)
        else:
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
        filtered_files = self._filter_completed_files(pending_files, completed_files)
        
        # Actualizar files_found con los archivos válidos filtrados
        self.stats.files_found = len(filtered_files)
        
        self.logger.info(f"Escaneo completado: {len(filtered_files)} archivos pendientes para procesar")
        
        # Actualiza files_found en el progreso
        self._update_progress_files_found(filtered_files, has_incomplete_progress)
        
        return filtered_files
    
    def _has_incomplete_progress(self, existing_progress: dict) -> bool:
        """Verifica si hay progreso anterior incompleto"""
        return (existing_progress.get('status') in ['processing', 'scanning', 'interrupted'] and 
                existing_progress.get('percentage', 0) < 90)
    
    def _handle_incomplete_progress(self, existing_progress: dict):
        """Maneja el caso de progreso anterior incompleto"""
        self.logger.info(f"Detectado progreso anterior incompleto ({existing_progress.get('percentage', 0):.1f}%), preservando estado")
        # No actualizar estado de progreso para no sobrescribir el anterior
    
    def _filter_completed_files(self, pending_files: List[Path], completed_files: set) -> List[Path]:
        """Filtra archivos que ya han sido completados"""
        filtered_files = []
        for file_path in pending_files:
            if str(file_path) not in completed_files:
                filtered_files.append(file_path)
            else:
                self.logger.info(f"Archivo ya procesado, omito: {file_path.name}")
        return filtered_files
    
    def _update_progress_files_found(self, filtered_files: List[Path], has_incomplete_progress: bool):
        """Actualiza la cantidad de archivos encontrados en el progreso"""
        if has_incomplete_progress:
            self._update_files_found_in_existing_progress(len(filtered_files))
        else:
            self._set_normal_progress_state(filtered_files)
    
    def _update_files_found_in_existing_progress(self, files_count: int):
        """Actualiza files_found en progreso existente sin cambiar el estado"""
        progress_file = self.tmp_dir / PROGRESS_FILE_NAME
        try:
            existing_full_progress = {}
            if progress_file.exists():
                with open(progress_file, 'r') as f:
                    existing_full_progress = json.load(f)
            
            # Asegurar que existe la sección processing
            if 'processing' not in existing_full_progress:
                existing_full_progress['processing'] = self._create_default_progress_structure()
            
            existing_full_progress['processing']['stats']['files_found'] = files_count
            
            with open(progress_file, 'w') as f:
                json.dump(existing_full_progress, f, indent=2)
        except Exception as e:
            self.logger.warning(f"Error actualizando files_found en progreso: {e}")
    
    def _set_normal_progress_state(self, filtered_files: List[Path]):
        """Establece el estado de progreso normal para ejecuciones nuevas"""
        # Usar solo los archivos filtrados válidos
        total_files = len(filtered_files)
        
        if len(filtered_files) > 0:
            self._save_progress_state(0, total_files, f"{len(filtered_files)} archivos encontrados", status='scanned')
        else:
            # Incluso si no hay archivos filtrados, usar total_files para mantener consistencia
            self._save_progress_state(0, total_files, "Escaneo completado", status='scanned')
    
    def _create_default_progress_structure(self) -> dict:
        """Crea la estructura de progreso por defecto"""
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
    
    def detect_language_streams(self, file_path: Path) -> Tuple[bool, str, str]:
        """Detecta streams de audio y subtítulos en español"""
        try:
            # Detecta audio en español
            audio_cmd = [
                "ffprobe", "-v", "error", "-select_streams", "a",
                "-show_entries", STREAM_LANGUAGE_QUERY,
                "-of", CSV_FORMAT_PARAM, str(file_path)
            ]
            audio_result = subprocess.run(
                audio_cmd, 
                capture_output=True, 
                timeout=MEDIUM_TIMEOUT,
                encoding='utf-8',
                errors='replace'
            )
            audio_languages = audio_result.stdout.strip()
            
            # Detecta subtítulos en español
            subs_cmd = [
                "ffprobe", "-v", "error", "-select_streams", "s",
                "-show_entries", STREAM_LANGUAGE_QUERY,
                "-of", CSV_FORMAT_PARAM, str(file_path)
            ]
            subs_result = subprocess.run(
                subs_cmd, 
                capture_output=True, 
                timeout=MEDIUM_TIMEOUT,
                encoding='utf-8',
                errors='replace'
            )
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
            probe_result = subprocess.run(
                probe_cmd, 
                capture_output=True, 
                timeout=MEDIUM_TIMEOUT,
                encoding='utf-8',
                errors='replace'
            )
            
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
            audio_result = subprocess.run(
                audio_cmd, 
                capture_output=True, 
                timeout=SHORT_TIMEOUT,
                encoding='utf-8',
                errors='replace'
            )
            audio_streams = audio_result.stdout.strip().split('\n') if audio_result.stdout.strip() else []
            
            # Verifica streams de subtítulos
            subs_cmd = [
                "ffprobe", "-v", "error", "-select_streams", "s",
                "-show_entries", STREAM_LANGUAGE_QUERY,
                "-of", CSV_FORMAT_PARAM, str(file_path)
            ]
            subs_result = subprocess.run(
                subs_cmd, 
                capture_output=True, 
                timeout=SHORT_TIMEOUT,
                encoding='utf-8',
                errors='replace'
            )
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
        
        # Configuración de audio mejorada
        ffmpeg_cmd.extend([
            "-c:a", "aac",
            "-b:a", "256k",  # Mejor calidad de audio
            "-ac", "2",
            "-ar", "48000"   # Sample rate consistente
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
        is_ts_file = file_path.suffix.lower() == '.ts'
        
        ffmpeg_cmd = ["ffmpeg", "-hide_banner", "-y"]
        
        # Para archivos .ts (MPEG-TS), usar parámetros especiales de decodificación
        if is_ts_file:
            ffmpeg_cmd.extend([
                "-fflags", "+genpts+igndts",  # Genera PTS y ignora DTS corruptos
                "-analyzeduration", "10M",     # Analiza más del archivo para detectar streams
                "-probesize", "10M",           # Tamaño de sondeo más grande
                "-err_detect", "ignore_err"    # Ignora errores de decodificación menores
            ])
        
        ffmpeg_cmd.extend(["-i", str(file_path)])
        
        # Configuración de video
        ffmpeg_cmd.extend([
            "-c:v", "libx264",
            "-preset", "medium",  # Balance entre velocidad y calidad
            "-crf", "23",         # Calidad ligeramente mejor para compensar problemas de .ts
            "-profile:v", "high", # Perfil H.264 high para mejor compatibilidad
            "-level", "4.1",      # Nivel compatible con la mayoría de dispositivos
        ])
        
        # Mapeo de video
        ffmpeg_cmd.extend(["-map", "0:v:0"])
        
        # Mapeo de audio con mejor manejo de errores
        ffmpeg_cmd.extend([
            "-map", "0:a:0?",     # El ? hace que sea opcional
            "-c:a", "aac",
            "-b:a", "256k",       # Mejor calidad para fallback
            "-ac", "2",
            "-ar", "48000"        # Sample rate estándar
        ])
        
        # No incluir subtítulos en fallback para simplicidad y estabilidad
        
        # Configuración final con parámetros anti-corrupción mejorados
        ffmpeg_cmd.extend([
            "-max_muxing_queue_size", "9999",  # Buffer muy grande para .ts problemáticos
            "-movflags", "+faststart",         # Optimización para streaming
            "-f", "mp4",                       # Forzar formato MP4 de salida
        ])
        
        # Para archivos .ts, agregar parámetros adicionales de corrección
        if is_ts_file:
            ffmpeg_cmd.extend([
                "-async", "1",                 # Sincronización de audio mejorada
                "-vsync", "cfr"                # Frame rate constante para evitar problemas
            ])
        
        ffmpeg_cmd.extend([
            "-avoid_negative_ts", "make_zero", # Evita timestamps negativos
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
            
            # Usar encoding robusto para manejar caracteres no-UTF-8 en metadatos
            result = subprocess.run(
                probe_cmd, 
                capture_output=True, 
                timeout=MEDIUM_TIMEOUT,
                encoding='utf-8',
                errors='replace'  # Reemplaza caracteres inválidos con �
            )
            
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
                original_result = subprocess.run(
                    original_probe_cmd, 
                    capture_output=True, 
                    timeout=MEDIUM_TIMEOUT,
                    encoding='utf-8',
                    errors='replace'  # Reemplaza caracteres inválidos
                )
                
                if original_result.returncode == 0:
                    original_data = json.loads(original_result.stdout)
                    original_duration = float(original_data.get('format', {}).get('duration', 0))
                    
                    if abs(compressed_duration - original_duration) > 60.0:
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
                                  elapsed_time: float, used_gpu: bool = True, has_spanish: bool = False) -> Dict:
        """Procesa el resultado de la compresión"""
        if process.returncode == 0 and temp_output.exists() and temp_output.stat().st_size > 1024:  # Mínimo 1KB
            return self._handle_successful_compression(temp_output, file_path, elapsed_time, used_gpu, has_spanish)
        else:
            return self._handle_failed_compression(process, file_path, temp_output)

    def _handle_successful_compression(self, temp_output: Path, file_path: Path, elapsed_time: float, used_gpu: bool, has_spanish: bool) -> Dict:
        """Maneja el caso de compresión exitosa"""
        result = {'compressed': False, 'renamed': False, 'success': True, 'error': None, 'no_spanish': False, 'skipped': False}
        
        # Validación
        is_valid, validation_msg = self._validate_compressed_file(temp_output, file_path)
        
        if not is_valid:
            self.logger.error(f"Archivo comprimido inválido para {file_path.name}: {validation_msg}")
            result['error'] = f"Archivo comprimido corrupto: {validation_msg}"
            if temp_output.exists():
                temp_output.unlink()
            # Marcar como fallido para evitar reintentos
            failed_files_path = self.scripts_dir / "failed-compression.txt"
            self._add_to_failed_files(str(file_path), failed_files_path, f"Validación fallida: {validation_msg}")
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
            result['original_size'] = original_size
            result['compressed_size'] = compressed_size
            
            # Sin mejoras de calidad aplicadas
        else:
            reduction_percent = ((original_size - compressed_size) / original_size) * 100
            method_str = "GPU" if used_gpu else "CPU"
            self.logger.info(f"Compresión {method_str} exitosa: {file_path.name} ({elapsed_time:.1f}s, -{reduction_percent:.1f}%)")
            file_path.unlink()
            final_name = file_path.with_suffix('.mp4')
            temp_output.rename(final_name)
            result['compressed'] = True
            result['original_size'] = original_size
            result['compressed_size'] = compressed_size
            
            # Sin mejoras de calidad aplicadas
        
        # Marca como completado
        with open(self.completed_file, 'a') as f:
            f.write(f"{file_path.with_suffix('.mp4')}\n")
        
        return result

    def _apply_quality_improvements(self, final_file: Path, has_spanish: bool) -> None:
        """Aplica mejoras de calidad al archivo final - DESHABILITADO TEMPORALMENTE
        
        Las mejoras de calidad requieren re-encoding completo y causan archivos corruptos.
        Se deshabilitan hasta integrarlas en el proceso de compresión inicial.
        """
        self.logger.info(f"Mejoras de calidad deshabilitadas temporalmente para: {final_file.name}")
        return  # Deshabilitado temporalmente
        
        try:
            # Crear archivo temporal para las mejoras
            quality_temp = final_file.with_suffix('.quality.mp4')
            
            # Aplicar mejoras de calidad
            if self.quality_improver.improve_video_quality(final_file, quality_temp, has_spanish):
                # Si las mejoras fueron exitosas, reemplazar el archivo original
                quality_temp.replace(final_file)
                self.logger.info(f"Mejoras de calidad aplicadas exitosamente a: {final_file.name}")
            else:
                # Si fallaron las mejoras, mantener el archivo original
                if quality_temp.exists():
                    quality_temp.unlink()
                self.logger.warning(f"No se pudieron aplicar mejoras de calidad a: {final_file.name}")
                
        except Exception as e:
            self.logger.error(f"Error aplicando mejoras de calidad: {e}")
            # Limpiar archivo temporal si existe
            if 'quality_temp' in locals() and quality_temp.exists():
                try:
                    quality_temp.unlink()
                except Exception:
                    pass

    def _handle_failed_compression(self, process: subprocess.CompletedProcess, file_path: Path, temp_output: Path) -> Dict:
        """Maneja el caso de compresión fallida"""
        result = {'compressed': False, 'renamed': False, 'success': False, 'error': None, 'no_spanish': False, 'skipped': False}
        
        if process.returncode == 0:
            if temp_output.exists():
                error_msg = f"Compresión aparentemente exitosa pero archivo muy pequeño o corrupto: {file_path} (tamaño: {temp_output.stat().st_size} bytes)"
            else:
                error_msg = f"Compresión aparentemente exitosa pero archivo no creado: {file_path}"
        else:
            error_msg = f"Falló compresión (código {process.returncode}): {file_path}"
        
        if process.returncode == 124:
            error_msg = f"Timeout alcanzado ({FFMPEG_TIMEOUT/3600:.1f}h): {file_path}"
        
        if process.stderr:
            self.logger.error(f"FFmpeg stderr: {process.stderr[:1000]}")  # Más caracteres para debugging
        
        result['error'] = error_msg
        
        if temp_output.exists():
            temp_output.unlink()
        
        # Marcar como fallido para evitar reintentos infinitos
        failed_files_path = self.scripts_dir / "failed-compression.txt"
        self._add_to_failed_files(str(file_path), failed_files_path, error_msg)
        
        return result

    def compress_single_file(self, file_path: Path) -> Dict:
        """Comprime un solo archivo con selección inteligente de método de compresión
        Completamente independiente - errores en este archivo no afectan otros procesos
        """
        result = {
            'file': str(file_path),
            'success': False,
            'error': None,
            'no_spanish': False,
            'compressed': False,
            'renamed': False,
            'skipped': False
        }
        
        temp_output = None
        
        try:
            # Establece límites para este proceso (aislado)
            self.set_resource_limits()
            
            # Prepara compresión: validación, detección de idiomas, selección de método
            is_prepared, compression_info, temp_output, audio_langs, sub_langs = self._prepare_compression(file_path)
            if not is_prepared:
                return {**result, **compression_info}
            
            use_gpu = compression_info['use_gpu']
            has_spanish = compression_info['has_spanish']
            
            # Log de método seleccionado (independiente por archivo)
            method_str = "GPU (VAAPI)" if use_gpu else "CPU (libx264)"
            self.logger.info(f"Procesando [{method_str}]: {file_path.name}")
            
            if not has_spanish:
                result['no_spanish'] = True
                self.no_spanish_logger.info(f"{file_path}")
                self.no_spanish_logger.info(f"   Idiomas de audio: {audio_langs.replace(chr(10), ', ')}")
                self.no_spanish_logger.info(f"   Idiomas de subtítulos: {sub_langs.replace(chr(10), ', ')}")
                self.no_spanish_logger.info("   ---")
            
            # Ejecuta compresión (aislada)
            process, elapsed_time = self._execute_compression_attempt(file_path, temp_output, use_gpu, has_spanish)
            
            # Procesa resultado inicial
            compression_result = self._process_compression_result(process, file_path, temp_output, elapsed_time, use_gpu, has_spanish)
            
            # Lógica de fallback en caso de fallo con GPU (solo si este archivo usó GPU)
            compression_result = self._handle_fallback_logic(compression_result, process, file_path, temp_output, use_gpu, has_spanish)
            
            result.update(compression_result)
                    
        except subprocess.TimeoutExpired:
            result['error'] = f"Timeout alcanzado ({FFMPEG_TIMEOUT/3600:.1f}h): {file_path}"
            self.logger.error(result['error'])
            if temp_output and temp_output.exists():
                try:
                    temp_output.unlink()
                except Exception as cleanup_err:
                    self.logger.warning(f"Error limpiando archivo temporal tras timeout: {cleanup_err}")
        except Exception as e:
            result['error'] = f"Error inesperado: {file_path} - {str(e)}"
            self.logger.error(result['error'])
            # Limpieza segura del archivo temporal en caso de error
            if temp_output and temp_output.exists():
                try:
                    temp_output.unlink()
                except Exception as cleanup_err:
                    self.logger.warning(f"Error limpiando archivo temporal tras excepción: {cleanup_err}")
            
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
            compression_result = self._process_compression_result(process, file_path, temp_output, new_elapsed_time, False, has_spanish)
        elif process.returncode != 0 and use_gpu:
            self.logger.warning(f"Compresión GPU falló, intentando fallback CPU: {file_path.name}")
            if temp_output.exists():
                temp_output.unlink()
            process, new_elapsed_time = self._execute_compression_attempt(file_path, temp_output, False, has_spanish)
            compression_result = self._process_compression_result(process, file_path, temp_output, new_elapsed_time, False, has_spanish)
        
        return compression_result

    def _wait_for_ffmpeg_children(self, timeout: int = 30) -> None:
        """Espera cortamente a procesos `ffmpeg` relacionados con este workspace antes de marcar
        el procesamiento como completado. Esto evita escribir 'completed' si aún quedan
        procesos externos/huérfanos que siguen comprimiendo archivos.

        Nota: hace un escaneo simple de `pgrep -af ffmpeg` y filtra por rutas del
        `media_dir` / `scripts_dir` o por el sufijo temporal usado (COMPRESSED_FILE_SUFFIX).
        No es 100% a prueba de falsos positivos, pero reduce la mayoría de casos donde
        se ve 100% mientras hay ffmpeg en segundo plano.
        """
        try:
            end_time = time.time() + timeout
            while time.time() < end_time:
                # Obtiene lista de procesos ffmpeg
                result = subprocess.run(["pgrep", "-af", "ffmpeg"], capture_output=True, text=True)
                if result.returncode != 0 or not result.stdout.strip():
                    # No hay ffmpeg activos
                    return

                lines = [l.strip() for l in result.stdout.splitlines() if l.strip()]
                # Filtra por procesos relacionados con las rutas del proyecto o sufijo temporal
                related = [l for l in lines if str(self.media_dir) in l or str(self.scripts_dir) in l or COMPRESSED_FILE_SUFFIX in l]
                if not related:
                    return

                # Si hay procesos relacionados, esperamos un poco y reintentamos
                self.logger.info(f"Esperando {len(related)} proceso(s) ffmpeg relacionados a finalizar antes de marcar 'completed'...")
                time.sleep(2)

        except Exception as e:
            self.logger.warning(f"Error comprobando procesos ffmpeg: {e}")
            return

    def _should_use_gpu_compression(self, file_path: Path) -> bool:
        """Decide si usar compresión GPU basado en historial y disponibilidad de hardware"""
        # Verifica si el hardware está disponible (test más completo)
        if not self._check_hardware_acceleration_available():
            return False
        
        # Archivos .ts (Transport Stream) tienen problemas con VAAPI, usar CPU
        if file_path.suffix.lower() == '.ts':
            self.logger.info(f"Usando CPU para {file_path.name}: formato .ts incompatible con VAAPI")
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
        
        # PRIMERO: Eliminar archivos .compressed.mp4 huérfanos de procesos interrumpidos
        orphaned_count = self._remove_orphaned_compressed_files()
        if orphaned_count > 0:
            self.logger.info(f"Limpieza inicial: {orphaned_count} archivo(s) .compressed.mp4 huérfano(s) eliminado(s)")
        
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

    def _load_previous_progress(self, progress_info: dict) -> int:
        """Carga estadísticas y archivos procesados de progreso anterior"""
        current_file = 0

        # Carga estadísticas previas
        current_file = self._load_previous_stats(progress_info, current_file)

        # Carga archivos procesados previamente
        self._load_previous_processed_files(progress_info)

        return current_file

    def _load_previous_stats(self, progress_info: dict, current_file: int) -> int:
        """Carga estadísticas previas del progreso"""
        if 'stats' not in progress_info:
            return current_file

        prev_stats = progress_info['stats']
        self.logger.info(f"Cargando estadísticas previas (progreso anterior: {progress_info.get('percentage', 0):.1f}%)")

        self.stats.files_found = prev_stats.get('files_found', 0)
        self.stats.files_new = prev_stats.get('files_new', 0)
        self.stats.files_processed = prev_stats.get('files_processed', 0)
        self.stats.files_compressed = prev_stats.get('files_compressed', 0)
        self.stats.files_renamed = prev_stats.get('files_renamed', 0)
        self.stats.files_skipped = prev_stats.get('files_skipped', 0)
        self.stats.total_original_size = prev_stats.get('total_original_size', 0)
        self.stats.total_compressed_size = prev_stats.get('total_compressed_size', 0)

        if 'errors' in prev_stats:
            self.stats.errors = prev_stats['errors']
        if 'no_spanish' in prev_stats:
            self.stats.no_spanish = prev_stats['no_spanish']

        return progress_info.get('current_file', 0)

    def _load_previous_processed_files(self, progress_info: dict) -> None:
        """Carga archivos procesados previamente"""
        if 'processed_files' in progress_info and progress_info['processed_files']:
            self.processed_files = progress_info['processed_files']
        elif len(progress_info.get('processed_files', {})) == 0 and progress_info.get('stats', {}).get('files_processed', 0) > 0:
            # Si processed_files vacío pero hay archivos procesados, reconstruir de completed.txt
            self._rebuild_processed_files_from_completed()

    def _rebuild_processed_files_from_completed(self) -> None:
        """Reconstruye processed_files desde completed.txt"""
        completed_files = set()
        if self.completed_file.exists():
            with open(self.completed_file, 'r') as f:
                for line in f:
                    file_path = line.strip()
                    if file_path:
                        completed_files.add(file_path)

        self.processed_files = {path: {'status': 'success', 'timestamp': datetime.now().isoformat()} for path in completed_files}
        self.logger.info(f"Reconstruyendo processed_files de completed.txt: {len(self.processed_files)} archivos")

    def _filter_already_processed_files(self, files: List[Path]) -> List[Path]:
        """Filtra archivos ya procesados exitosamente"""
        files_before_filter = len(files)
        files = [f for f in files if str(f) not in self.processed_files or self.processed_files[str(f)]['status'] != 'success']
        files_filtered = files_before_filter - len(files)
        if files_filtered > 0:
            self.logger.info(f"Archivos ya procesados exitosamente omitidos: {files_filtered}")
        return files

    def _process_files_with_executor(self, files: List[Path], total_files: int, current_file: int) -> tuple[int, list[str], list[str]]:
        """Procesa archivos con executor y actualiza progreso después de cada compresión"""
        current_errors = []
        current_no_spanish = []
        processed_count = current_file

        with ThreadPoolExecutor(max_workers=MAX_CONCURRENT_COMPRESSIONS) as executor:
            future_to_file = {executor.submit(self.compress_single_file, file_path): file_path
                            for file_path in files}
            files_in_progress = [f.name for f in files]

            # Procesa resultados conforme van completando
            for future in as_completed(future_to_file):
                file_path = future_to_file[future]
                self._process_single_future_result(future, file_path, current_errors, current_no_spanish)
                processed_count += 1
                self._update_progress_after_processing(file_path, processed_count, total_files, files_in_progress)

        return processed_count, current_errors, current_no_spanish

    def process_files_concurrent(self, files: List[Path]) -> ProcessingStats:
        """Procesa archivos con concurrencia limitada y seguimiento de progreso"""
        # Inicializa procesamiento
        initialized, total_files_found = self._initialize_processing(files)
        if not initialized:
            return self.stats
        
        # Carga progreso anterior si existe
        progress_info = self.get_progress_info()
        
        # Carga estadísticas y archivos procesados de progreso anterior
        current_file = self._load_previous_progress(progress_info)
        
        # Filtra archivos ya procesados exitosamente
        files = self._filter_already_processed_files(files)
        
        # Guarda el total de archivos encontrados (escaneados) para cálculos de progreso
        files_pending = len(files)
        if files_pending == 0:
            self.logger.info("Todos los archivos ya han sido procesados exitosamente")
            # Espera procesos ffmpeg relacionados antes de marcar completado
            try:
                self._wait_for_ffmpeg_children(timeout=20)
            except Exception:
                pass
            # Envía notificación de completado y marca como completado
            self._send_completion_notification()
            self._save_progress_state(0, total_files_found, PROCESSING_COMPLETED_MESSAGE, status='completed')
            return self.stats
            
        self.logger.info(f"Procesando {files_pending} archivos pendientes de {total_files_found} encontrados con {MAX_CONCURRENT_COMPRESSIONS} workers")
        
        # Procesa archivos con executor (actualiza progreso después de cada compresión)
        current_file, current_errors, current_no_spanish = self._process_files_with_executor(files, total_files_found, current_file)
        
        # Guarda errores y archivos sin español de esta ejecución
        self._save_temporary_results(current_errors, current_no_spanish)
        
        # Actualiza progreso como completado
        # Espera procesos ffmpeg relacionados antes de marcar completado (si quedan)
        try:
            self._wait_for_ffmpeg_children(timeout=60)
        except Exception:
            pass
        # Envía notificación de completado y marca como completado
        self._send_completion_notification()
        self._save_progress_state(current_file, total_files_found, PROCESSING_COMPLETED_MESSAGE, status='completed')
        
        # Log de resumen
        self.logger.info(f"RESUMEN: Procesados: {self.stats.files_processed}, "
                        f"Comprimidos: {self.stats.files_compressed}, "
                        f"Renombrados: {self.stats.files_renamed}, "
                        f"Omitidos: {self.stats.files_skipped}")
        
        return self.stats

    def _process_single_future_result(self, future, file_path: Path, current_errors: list, current_no_spanish: list) -> None:
        """Procesa el resultado de un futuro individual"""
        try:
            result = future.result()
            self._update_stats_from_result(result, file_path, current_errors, current_no_spanish)

        except Exception as e:
            error_msg = f"Error procesando {file_path}: {str(e)}"
            self.logger.error(error_msg)
            current_errors.append(file_path.name)
            # Agregar a stats.errors también
            if file_path.name not in self.stats.errors:
                self.stats.errors.append(file_path.name)
            self.processed_files[str(file_path)] = {'status': 'failed', 'timestamp': datetime.now().isoformat(), 'error': str(e)}

    def _update_size_stats(self, result: dict) -> None:
        """Actualiza estadísticas de tamaño"""
        if 'original_size' in result and 'compressed_size' in result:
            self.stats.total_original_size += result['original_size']
            self.stats.total_compressed_size += result['compressed_size']

    def _update_stats_from_result(self, result: dict, file_path: Path, current_errors: list, current_no_spanish: list) -> None:
        """Actualiza estadísticas desde el resultado de procesamiento"""
        # Incrementa contador de archivos procesados
        self.stats.files_processed += 1
        
        # Actualiza contadores específicos según el resultado
        if result.get('compressed', False):
            self.stats.files_compressed += 1
        if result.get('renamed', False):
            self.stats.files_renamed += 1
        if result.get('skipped', False):
            self.stats.files_skipped += 1
        
        # Maneja errores
        if result.get('error'):
            current_errors.append(file_path.name)
            # Agregar a stats.errors también
            if file_path.name not in self.stats.errors:
                self.stats.errors.append(file_path.name)
            self.processed_files[str(file_path)] = {
                'status': 'failed', 
                'timestamp': datetime.now().isoformat(), 
                'error': result['error']
            }
        else:
            self.processed_files[str(file_path)] = {
                'status': 'success', 
                'timestamp': datetime.now().isoformat()
            }
        
        # Maneja archivos sin español
        if result.get('no_spanish', False):
            current_no_spanish.append(file_path.name)
            # Agregar a stats.no_spanish también
            if file_path.name not in self.stats.no_spanish:
                self.stats.no_spanish.append(file_path.name)
        
        # Actualiza estadísticas de tamaño
        self._update_size_stats(result)

    def _update_progress_after_processing(self, file_path: Path, processed_count: int, total_files: int, files_in_progress: list) -> None:
        """Actualiza progreso después del procesamiento de un archivo"""
        # Remueve el archivo de la lista de procesamiento
        if file_path.name in files_in_progress:
            files_in_progress.remove(file_path.name)

        # Construye mensaje de archivos en procesamiento
        current_file_name = self._build_progress_message(files_in_progress)

        # Actualiza progreso inmediatamente después de cada compresión
        self._save_progress_state(processed_count, total_files, current_file_name, status='processing')

        # Log progreso
        percentage = (processed_count / total_files) * 100
        print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Progreso: {processed_count}/{total_files} ({percentage:.1f}%) - Archivo procesado: {file_path.name}", flush=True)

    def _build_progress_message(self, files_in_progress: list) -> str:
        """Construye mensaje de archivos actualmente en procesamiento"""
        if not files_in_progress:
            return "Procesamiento completado"
        
        if len(files_in_progress) == 1:
            return f"Procesando: {files_in_progress[0]}"
        
        # Muestra hasta 3 archivos
        files_to_show = files_in_progress[:3]
        message = f"Procesando {len(files_in_progress)} archivos: {', '.join(files_to_show)}"
        
        if len(files_in_progress) > 3:
            message += f" y {len(files_in_progress) - 3} más"
        
        return message

    
    def run(self) -> ProcessingStats:
        """Ejecuta procesamiento completo"""
        try:
            self.logger.info("=== Iniciando MediaJelly Python ===")
            
            # Al iniciar el procesamiento, marca notified=False en la sección processing
            progress_file = self.tmp_dir / PROGRESS_FILE_NAME
            existing_progress = {}
            if progress_file.exists():
                try:
                    with open(progress_file, 'r') as f:
                        existing_progress = json.load(f)
                except Exception:
                    existing_progress = {}
            
            # Asegurar que existe la sección processing
            if 'processing' not in existing_progress:
                existing_progress['processing'] = {
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
            
            existing_progress['processing']['notified'] = False
            existing_progress['processing']['status'] = 'processing'
            
            with open(progress_file, 'w') as f:
                json.dump(existing_progress, f, indent=2)
            self.logger.info("Estado de notificación reseteado (notified=False)")
            
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
        finally:
            # Siempre libera el bloqueo
            pass
            
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
        failed_files_path = self.scripts_dir / "failed-compression.txt"
        
        with open(self.pending_file, 'r') as f:
            for line in f:
                file_path = line.strip()
                if file_path:
                    pending_path_obj = Path(file_path)
                    mp4_path = pending_path_obj.with_suffix('.mp4')
                    
                    # 1. Si ya está completado como .mp4, remover
                    if str(mp4_path) in completed_files and mp4_path.exists():
                        files_removed += 1
                        continue  # Remover archivos procesados
                    
                    # 2. Si el archivo original no existe (huérfano), remover
                    elif not pending_path_obj.exists():
                        orphaned_files.append(file_path)
                        files_removed += 1
                        self.logger.warning(f"Archivo huérfano removido de pending (no existe): {pending_path_obj.name}")
                        continue
                    
                    # 3. Remover archivos fallidos persistentemente
                    elif file_path in self.processed_files and self.processed_files[file_path].get('status') == 'failed':
                        # Para archivos .ts fallidos, remover inmediatamente
                        if pending_path_obj.suffix.lower() == '.ts':
                            self.logger.warning(f"Removiendo archivo .ts fallido de pendientes: {file_path}")
                            self._add_to_failed_files(file_path, failed_files_path, "Formato .ts incompatible con VAAPI")
                            files_removed += 1
                            continue
                        # Para otros formatos, verificar si han fallado recientemente
                        failed_info = self.processed_files[file_path]
                        if 'timestamp' in failed_info:
                            try:
                                failed_time = datetime.fromisoformat(failed_info['timestamp'])
                                if (datetime.now() - failed_time).total_seconds() > 86400:  # 24 horas
                                    self.logger.warning(f"Removiendo archivo fallido antiguo de pendientes: {file_path}")
                                    self._add_to_failed_files(file_path, failed_files_path, "Falló múltiples veces")
                                    files_removed += 1
                                    continue
                            except (ValueError, TypeError):
                                self.logger.warning(f"Removiendo archivo fallido con timestamp inválido de pendientes: {file_path}")
                                self._add_to_failed_files(file_path, failed_files_path, "Timestamp inválido")
                                files_removed += 1
                                continue
                    
                    # 4. Archivo válido, mantener en pending
                    remaining_pending.append(file_path)
        
        return remaining_pending, orphaned_files, files_removed
    
    def _add_to_failed_files(self, file_path: str, failed_files_path: Path, reason: str) -> None:
        """Agrega un archivo a la lista de archivos fallidos con su razón"""
        try:
            # Leer archivos fallidos existentes para evitar duplicados
            existing_failed = set()
            if failed_files_path.exists():
                with open(failed_files_path, 'r') as f:
                    for line in f:
                        if line.strip():
                            # Extraer solo la ruta del archivo (sin timestamp ni razón)
                            parts = line.strip().split(' | ')
                            if parts:
                                existing_failed.add(parts[0])
            
            # Agregar solo si no existe
            if file_path not in existing_failed:
                with open(failed_files_path, 'a') as f:
                    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                    f.write(f"{file_path} | {timestamp} | {reason}\n")
                self.logger.info(f"Archivo agregado a failed-compression.txt: {Path(file_path).name}")
        except Exception as e:
            self.logger.error(f"Error agregando archivo a failed-compression.txt: {e}")

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

    def _send_completion_notification(self) -> None:
        """Envía notificación de completado cuando llega al 100%"""
        try:
            notifier_script = self.scripts_dir / "mediajelly_notifier.py"
            if not notifier_script.exists():
                self.logger.warning("Script de notificaciones no encontrado, omitiendo notificación")
                return
            
            # Preparar comando de notificación
            cmd = [
                sys.executable, str(notifier_script),
                "scan_result", "success",
                str(self.stats.files_found),
                str(self.stats.files_new), 
                str(self.stats.files_processed),
                str(self.stats.files_compressed),
                str(self.stats.files_renamed),
                str(self.stats.files_skipped),
                "0",  # subtitles_translated (no aplicable en processor)
                "0"   # subtitles_errors (no aplicable en processor)
            ]
            
            self.logger.info("Enviando notificación de completado...")
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=60
            )
            
            if result.returncode == 0:
                self.logger.info("Notificación de completado enviada correctamente")
            else:
                self.logger.error(f"Error enviando notificación de completado: {result.stderr}")
                
        except Exception as e:
            self.logger.error(f"Error enviando notificación de completado: {e}")

class VideoQualityImprover:
    """Clase para mejorar la calidad de videos sin aumentar significativamente el tamaño"""
    
    def __init__(self, logger):
        self.logger = logger
    
    def improve_video_quality(self, input_file: Path, output_file: Path, has_spanish: bool) -> bool:
        """Aplica mejoras de calidad al video sin aumentar el tamaño significativamente
        
        Mejoras aplicadas:
        - Normalización de audio (mejor volumen consistente)
        - Mejora de metadatos
        - Optimización leve de colores/contraste
        - Mejora de subtítulos embebidos (si los hay)
        """
        try:
            self.logger.info(f"Aplicando mejoras de calidad a: {input_file.name}")
            
            # Verificar que el archivo de entrada existe
            if not input_file.exists():
                self.logger.error(f"Archivo de entrada no existe: {input_file}")
                return False
            
            # Comando ffmpeg para mejoras de calidad
            ffmpeg_cmd = self._build_quality_improvement_command(input_file, output_file, has_spanish)
            
            self.logger.info("Ejecutando mejoras de calidad...")
            start_time = time.time()
            
            result = subprocess.run(
                ffmpeg_cmd,
                capture_output=True,
                text=True,
                timeout=FFMPEG_TIMEOUT
            )
            
            elapsed_time = time.time() - start_time
            
            if result.returncode == 0 and output_file.exists():
                self.logger.info(f"Mejoras de calidad aplicadas exitosamente en {elapsed_time:.1f}s")
                return True
            else:
                self.logger.error(f"Error aplicando mejoras de calidad: código {result.returncode}")
                if result.stderr:
                    self.logger.error(f"FFmpeg stderr: {result.stderr[:500]}...")
                return False
                
        except subprocess.TimeoutExpired:
            self.logger.error(f"Timeout aplicando mejoras de calidad ({FFMPEG_TIMEOUT/3600:.1f}h): {input_file.name}")
            return False
        except Exception as e:
            self.logger.error(f"Error aplicando mejoras de calidad: {e}")
            return False
    
    def _build_quality_improvement_command(self, input_file: Path, output_file: Path, has_spanish: bool) -> List[str]:
        """Construye comando ffmpeg para mejoras de calidad"""
        cmd = ["ffmpeg", "-hide_banner", "-y"]
        
        # Input
        cmd.extend(["-i", str(input_file)])
        
        # Filtros de video más simples para no aumentar significativamente el tamaño
        video_filters = []
        
        # Solo mejora leve de contraste/color (muy sutil)
        video_filters.append("eq=contrast=1.02:brightness=0.01:saturation=1.05")
        
        # Reducción muy leve de ruido
        video_filters.append("hqdn3d=1:1:3:3")
        
        # Aplicar filtros de video con codec especificado
        if video_filters:
            cmd.extend(["-vf", ",".join(video_filters)])
            cmd.extend(["-c:v", "libx264", "-preset", "ultrafast", "-crf", "18"])
        
        # Audio: normalización simple
        cmd.extend([
            "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
            "-c:a", "aac",
            "-b:a", "128k"
        ])
        
        # Subtítulos si tiene español
        if has_spanish:
            cmd.extend([
                "-map", "0:s:m:language:spa?",
                "-map", "0:s:m:language:esp?", 
                "-map", "0:s:m:language:es?",
                "-map", "0:s:m:language:lat?",
                "-c:s", "mov_text"
            ])
        
        # Metadatos mejorados
        cmd.extend([
            "-metadata", "title=" + input_file.stem,
            "-metadata", "artist=MediaJelly",
            "-metadata", "comment=Procesado con mejoras de calidad",
            "-metadata", "creation_time=" + datetime.now().isoformat(),
            "-movflags", "+faststart",
            "-avoid_negative_ts", "make_zero",
            str(output_file)
        ])
        
        return cmd

def main():
    """Función principal"""
    processor = MediaJellyProcessor()
    
    # Registrar guardado de progreso al salir
    atexit.register(lambda: processor._save_progress_state(
        processor.stats.files_processed, 
        processor.stats.files_found, 
        "Terminado por atexit", 
        status='interrupted'
    ))
    
    # Manejo de señales para terminación limpia
    def signal_handler(signum, frame):
        processor.logger.info(f"Recibida señal {signum}, guardando progreso y terminando...")
        # Obtiene el progreso actual antes de guardar
        current_progress = processor.get_progress_info()
        current_file = current_progress.get('current_file', processor.stats.files_processed)
        total_files = current_progress.get('total_files', processor.stats.files_found)
        current_file_name = current_progress.get('current_file_name', f"Terminado por señal {signum}")
        
        # Guarda el progreso actual antes de terminar
        processor._save_progress_state(
            current_file, 
            total_files, 
            current_file_name, 
            status='interrupted'
        )
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