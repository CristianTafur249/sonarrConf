#!/usr/bin/env python3
"""
MediaJelly Scanner Python - Escaneo optimizado de archivos multimedia
"""

import os
import sys
import time
import json
import fcntl
import subprocess
import asyncio
from pathlib import Path
from typing import Set, List
from datetime import datetime
import logging

class MediaScanner:
    """Scanner optimizado de archivos multimedia"""
    
    EXTENSIONS = {'.mkv', '.mp4', '.avi', '.mov', '.webm'}
    
    def __init__(self):
        # Detecta el entorno
        self.is_container = Path("/mediajelly").exists()
        self.base_dir = Path("/mediajelly" if self.is_container else "/home/tafurc/mediaJelly")
        self.scripts_dir = self.base_dir / "scripts"
        self.logs_dir = self.scripts_dir / "logs"
        
        # Archivos
        self.pending_file = self.scripts_dir / "pending-compression.txt"
        self.completed_file = self.scripts_dir / "completed.txt"
        self.log_file = self.logs_dir / "scan.log"
        
        # Configura logging
        self.setup_logging()
        
        # Crea directorios
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        self.completed_file.touch()
    
    def setup_logging(self):
        """Configurar logging"""
        logging.basicConfig(
            level=logging.INFO,
            format='[%(asctime)s] %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S',
            handlers=[
                logging.FileHandler(self.log_file),
                logging.StreamHandler()
            ]
        )
        self.logger = logging.getLogger(__name__)
    
    def is_processor_locked(self) -> bool:
        """Verifica si el procesador está bloqueado por otra instancia"""
        processor_lock_file = self.scripts_dir / "tmp" / "mediajelly_processor.lock"
        
        if not processor_lock_file.exists():
            return False
        
        try:
            with open(processor_lock_file, 'r') as f:
                content = f.read().strip()
                if not content:
                    return False
                
                lines = content.split('\n')
                if not lines or not lines[0].isdigit():
                    return False
                
                pid = int(lines[0])
                
                # Verificar si el proceso existe
                try:
                    os.kill(pid, 0)  # No mata, solo verifica existencia
                    self.logger.info(f"Procesador bloqueado por proceso activo (PID: {pid})")
                    return True
                except OSError:
                    # Proceso no existe, lock huérfano
                    self.logger.info(f"Lock huérfano del procesador detectado (PID {pid} no existe), ignorando")
                    return False
                    
        except Exception as e:
            self.logger.warning(f"Error verificando lock del procesador: {e}")
            return False
    
    def load_completed_files(self) -> Set[str]:
        """Cargar archivos ya completados (rutas completas)"""
        completed = set()
        if self.completed_file.exists():
            with open(self.completed_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        completed.add(line)
        return completed
    
    def find_compressed_files(self, folder: Path) -> Set[Path]:
        """Encontrar archivos .compressed.mp4 para excluir originales"""
        compressed_files = set()
        for compressed in folder.rglob("*.compressed.mp4"):
            # Obtiene el archivo original correspondiente
            original = compressed.with_suffix('').with_suffix('')
            if original.exists():
                compressed_files.add(original)
        return compressed_files
    
    def scan_folder_optimized(self, folder: Path) -> List[Path]:
        """Escaneo optimizado de carpeta usando pathlib"""
        if not folder.exists():
            self.logger.warning(f"Carpeta no encontrada: {folder}")
            return []
        
        self.logger.info(f"Escaneando: {folder}")
        found_files = []
        
        # Usa rglob para búsqueda recursiva eficiente
        for file_path in folder.rglob("*"):
            if (file_path.is_file() and 
                file_path.suffix.lower() in self.EXTENSIONS and
                not file_path.name.startswith('.') and
                not any(part.startswith('.') for part in file_path.parts) and
                not file_path.name.endswith('.compressed.mp4')):  # Excluir archivos temporales
                found_files.append(file_path.resolve())
        
        return found_files
    
    def filter_new_files(self, all_files: List[Path], completed: Set[Path], compressed: Set[Path]) -> List[Path]:
        """Filtrar archivos nuevos (no completados ni con .compressed)"""
        new_files = []
        for file_path in all_files:
            if file_path not in completed and file_path not in compressed:
                new_files.append(file_path)
        return new_files
    
    def sort_by_size_desc(self, files: List[Path]) -> List[Path]:
        """Ordenar archivos por tamaño descendente (más grandes primero)"""
        return sorted(files, key=lambda f: f.stat().st_size if f.exists() else 0, reverse=True)
    
    def clean_pending_duplicates(self) -> int:
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
                        self.logger.info(f"Duplicado encontrado: {line_clean}")
                    else:
                        unique_files.add(normalized_path)
        
        # Reescribe el archivo sin duplicados
        if duplicates_removed > 0:
            with open(self.pending_file, 'w') as f:
                for file_path in sorted(unique_files):
                    f.write(f"{file_path}\n")
            
            self.logger.info(f"Limpieza de duplicados: {duplicates_removed} entradas duplicadas eliminadas")
        
        return duplicates_removed
    
    def _load_existing_pending_files(self) -> tuple[list[str], set[str]]:
        """Carga archivos pendientes existentes y sus rutas normalizadas."""
        existing_pending_paths = []
        existing_pending_normalized = set()
        if self.pending_file.exists():
            with open(self.pending_file, 'r') as f:
                for line in f:
                    line_clean = line.strip()
                    if line_clean:
                        existing_pending_paths.append(line_clean)
                        try:
                            normalized_path = str(Path(line_clean).resolve())
                            existing_pending_normalized.add(normalized_path)
                        except (OSError, RuntimeError):
                            existing_pending_normalized.add(line_clean)
        return existing_pending_paths, existing_pending_normalized
    
    def _filter_new_files(self, all_found_files: list[Path], existing_pending_normalized: set[str]) -> list[str]:
        """Filtra archivos que ya están en pendientes y asegura que sean solo formatos de video."""
        VIDEO_EXTENSIONS = {'.mkv', '.mp4', '.avi', '.mov', '.webm'}
        new_files_to_add = []
        for file_path in all_found_files:
            try:
                normalized_path = str(file_path.resolve())
            except (OSError, RuntimeError):
                normalized_path = str(file_path)
            
            # Solo agregar si es formato de video y no está en pendientes
            if (file_path.suffix.lower() in VIDEO_EXTENSIONS and 
                normalized_path not in existing_pending_normalized):
                new_files_to_add.append(str(file_path))
                existing_pending_normalized.add(normalized_path)
        return new_files_to_add
    
    def _prepare_pending_list(self, existing_pending_paths: list[str], new_files_to_add: list[str]) -> list[Path]:
        """Prepara la lista final de archivos pendientes."""
        all_pending_paths = existing_pending_paths + new_files_to_add
        all_pending_objects = []
        for path_str in all_pending_paths:
            try:
                path_obj = Path(path_str)
                all_pending_objects.append(path_obj)
            except (OSError, RuntimeError):
                dummy_path = Path(path_str)
                all_pending_objects.append(dummy_path)
        return all_pending_objects
    
    def _sort_by_size_desc(self, file_paths: list[Path]) -> list[Path]:
        """Ordena archivos por tamaño descendente."""
        def get_size_safe(path_obj):
            try:
                return path_obj.stat().st_size
            except (OSError, RuntimeError):
                return 0
        
        return sorted(file_paths, key=get_size_safe, reverse=True)
    
    def _write_pending_file(self, all_pending: list[Path]) -> None:
        """Escribe el archivo de pendientes."""
        with open(self.pending_file, 'w') as f:
            for file_path in all_pending:
                f.write(f"{file_path}\n")
    
    def _log_scan_results(self, total_found: int, new_detected: int, new_files_to_add: list[str]) -> None:
        """Registra los resultados del escaneo."""
        if new_files_to_add:
            self.logger.info(f"Nuevos archivos agregados a pendientes: {len(new_files_to_add)}")
            for file_path in new_files_to_add:
                self.logger.info(f"  + {Path(file_path).name}")
        
        self.logger.info(f"Archivos encontrados: {total_found}")
        self.logger.info(f"Nuevas rutas detectadas: {new_detected}")

    def scan_media_directories(self, media_dirs: List[Path]) -> tuple[int, int]:
        """Escanea los directorios de medios y actualiza archivo de pendientes."""
        # Carga estado actual
        completed_files = self.load_completed_files()
        
        all_found_files = []
        
        # Escanea cada directorio
        for media_dir in media_dirs:
            if media_dir.exists():
                # Escanea archivos
                found_files = self.scan_folder_optimized(media_dir)
                
                # Filtra archivos nuevos (comparando rutas completas)
                new_files = [f for f in found_files if str(f) not in completed_files]
                all_found_files.extend(new_files)
                
                self.logger.info(f"Directorio {media_dir.name}: {len(found_files)} encontrados, {len(new_files)} nuevos")
        
        self.logger.info(f"Total encontrados: {len(all_found_files)}")
        
        # Ordena por tamaño (más grandes primero)
        all_found_files.sort(key=lambda f: f.stat().st_size, reverse=True)
        
        # Carga archivos pendientes existentes
        existing_pending_paths, existing_pending_normalized = self._load_existing_pending_files()
        
        # Filtra archivos nuevos
        new_files_to_add = self._filter_new_files(all_found_files, existing_pending_normalized)
        
        # Si no hay archivos nuevos, no modifica el archivo
        if not new_files_to_add:
            self.logger.info("No hay archivos nuevos para agregar a pendientes")
            total_found = len(existing_pending_paths)
            new_detected = 0
            return total_found, new_detected
        
        # Prepara la lista final
        all_pending_objects = self._prepare_pending_list(existing_pending_paths, new_files_to_add)
        
        # Ordena por tamaño
        all_pending = self._sort_by_size_desc(all_pending_objects)
        
        # Escribe archivo
        self._write_pending_file(all_pending)
        
        # Limpieza de duplicados finales después de agregar nuevos
        final_duplicates = self.clean_pending_duplicates()
        if final_duplicates > 0:
            self.logger.info(f"Post-limpieza: eliminados {final_duplicates} duplicados finales")
        
        # Registra resultados
        total_found = len(all_pending) - final_duplicates  # Ajusta conteo si se eliminaron duplicados
        new_detected = len(new_files_to_add)
        self._log_scan_results(total_found, new_detected, new_files_to_add)
        
        return total_found, new_detected
    
    def run(self, media_paths: List[str]) -> tuple[int, int]:
        """Ejecutar escaneo completo"""
        start_time = time.time()
        
        # Verifica si necesita enviar notificación de inicio
        self._check_and_send_start_notification()
        
        # Limpieza de duplicados del archivo pending antes de comenzar
        duplicates_removed = self.clean_pending_duplicates()
        if duplicates_removed > 0:
            self.logger.info(f"Pre-limpieza: eliminados {duplicates_removed} duplicados")
        
        # Convierte rutas a objetos Path
        media_dirs = [Path(path) for path in media_paths]
        
        # Verifica que al menos una ruta existe
        valid_dirs = [d for d in media_dirs if d.exists()]
        if not valid_dirs:
            self.logger.error("No se encontraron directorios válidos para escanear.")
            return 0, 0
        
        self.logger.info(f"=== Inicia escaneo de {len(valid_dirs)} directorios ===")
        
        # Realiza escaneo
        total_found, new_detected = self.scan_media_directories(valid_dirs)
        
        elapsed_time = time.time() - start_time
        self.logger.info(f"=== Escaneo completado en {elapsed_time:.2f}s ===")
        
        return total_found, new_detected
    
    def _check_and_send_start_notification(self):
        """Verifica si se debe enviar notificación de inicio"""
        progress_file = self.scripts_dir / "tmp" / "progress.json"
        
        try:
            if progress_file.exists():
                with open(progress_file, 'r') as f:
                    progress = json.load(f)
                    
                # Si no ha sido notificado, envía notificación
                if not progress.get('notified', False):
                    # Verifica si el procesador está bloqueado antes de enviar notificación
                    if self.is_processor_locked():
                        self.logger.info("Procesador bloqueado, omitiendo notificación de inicio")
                        return
                    
                    self.logger.info("Enviando notificación de inicio...")
                    files_found = progress.get('stats', {}).get('files_found', 0)
                    files_new = progress.get('stats', {}).get('files_new', 0)
                    
                    # Llama al notifier para enviar la notificación
                    cmd = [
                        sys.executable,
                        str(self.scripts_dir / "mediajelly_notifier.py"),
                        "start_processing",
                        str(files_found),
                        str(files_new)
                    ]
                    
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
                    if result.returncode == 0:
                        self.logger.info("Notificación de inicio enviada correctamente")
                        # Marca como notificado
                        progress['notified'] = True
                        with open(progress_file, 'w') as f:
                            json.dump(progress, f, indent=2)
                    else:
                        self.logger.warning("Error enviando notificación de inicio")
                else:
                    self.logger.info("Ya se envió notificación de inicio previamente")
        except Exception as e:
            self.logger.warning(f"Error verificando notificación: {e}")

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