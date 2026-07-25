#!/usr/bin/env python3
"""
mediajelly_pending_files.py

Mixin con la gestión de la cola de archivos pendientes de compresión y de
archivos ya completados, respaldada por mediajelly_db.py (SQLite).
"""

from pathlib import Path
from typing import List, Optional, Tuple, Union

from mediajelly_db import get_pending_files, get_all_completed_files, add_pending, remove_pending


class PendingFilesMixin:
    """Cola de archivos pendientes/completados (respaldada por la DB SQLite)."""

    def _load_failed_files(self, failed_files_path: Path) -> set:
        """Carga la lista de archivos que han fallado en compresión"""
        failed_files = set()
        if failed_files_path.exists():
            with open(failed_files_path, "r") as f:
                for line in f:
                    file_path = line.strip()
                    if file_path:
                        failed_files.add(file_path)
        return failed_files

    def _load_completed_files(self) -> set:
        """Carga archivos completados para evitar reprocesamiento (desde DB)."""
        try:
            files = get_all_completed_files()
            return {str(p) for p in files}
        except Exception:
            return set()

    def _get_pending_files(self) -> List[Path]:
        """
        Obtiene archivos pendientes de procesamiento desde la DB.
        NOTA: Ya no depende del archivo pending-compression.txt.
        """
        # ⚠️ CORREGIDO: Ya no verifica pending_file.exists()
        # Ahora siempre consulta la DB como fuente principal

        pending_files = []
        files_renamed: List[Tuple[str, str]] = []
        invalid_files: List[str] = []

        try:
            db_pending = get_pending_files()
            self.logger.info(f"📊 _get_pending_files: {len(db_pending)} archivos obtenidos desde DB")
        except Exception as e:
            self.logger.error(f"❌ Error obteniendo pendientes desde DB: {e}")
            db_pending = []

        for p in db_pending:
            processed_file = self._process_pending_file_line(str(p), invalid_files, files_renamed)
            if processed_file:
                pending_files.append(processed_file)

        # Limpiar archivos inválidos del pending (DB)
        if invalid_files:
            self._remove_invalid_files_from_pending(invalid_files)
            self.logger.info(f"Removidos {len(invalid_files)} archivos no-video de pending (DB)")

        # Actualizar pending después de renombramientos en DB
        if files_renamed:
            self._update_pending_after_rename(files_renamed)

        self.logger.info(f"✅ _get_pending_files: {len(pending_files)} archivos pendientes válidos")
        return pending_files

    def _process_pending_file_line(
        self, file_path: str, invalid_files: List[str], files_renamed: List[Tuple[str, str]]
    ) -> Optional[Path]:
        """Procesa una línea individual del archivo pending"""
        original_path = Path(file_path)

        # Verificar que el archivo existe
        if not original_path.exists():
            self.logger.debug(f"Archivo no existe en disco: {original_path}")
            return None

        # Filtrar archivos que no son videos
        if not self._is_video_file(original_path):
            invalid_files.append(str(original_path))
            self.logger.warning(
                f"Archivo no es video, removiendo de pending: {original_path.name} (extensión: {original_path.suffix})"
            )
            return None

        # Normalizar nombre del archivo
        new_path = self._normalize_filename(original_path)

        if new_path and new_path != original_path:
            # El archivo fue renombrado
            files_renamed.append((str(original_path), str(new_path)))
            self.logger.info(f"✏️ Archivo renombrado: {original_path.name} -> {new_path.name}")
            return new_path
        else:
            # No se renombró, usar el path original
            return original_path

    def _is_video_file(self, file_path: Path) -> bool:
        """Verifica si un archivo es un video válido para procesamiento"""
        VALID_VIDEO_EXTENSIONS = {".mkv", ".avi", ".ts", ".mp4", ".mov", ".wmv", ".flv", ".webm", ".m4v"}
        return file_path.suffix.lower() in VALID_VIDEO_EXTENSIONS

    def _remove_invalid_files_from_pending(self, invalid_files: List[str]) -> None:
        """Remueve archivos inválidos (no-video) del pending en la DB."""
        for inv in invalid_files:
            try:
                remove_pending(inv)
                self.logger.debug(f"Removido de pending: {inv}")
            except Exception as e:
                self.logger.debug(f"No se pudo remover pending inválido de la DB: {inv} - {e}")

    def _update_pending_after_rename(self, renamed_files: List[Tuple[str, str]]) -> None:
        """Actualiza pendings en DB después de renombrar archivos."""
        for old, new in renamed_files:
            try:
                remove_pending(old)
                add_pending(new)
                self.logger.debug(f"Actualizado pending: {old} -> {new}")
            except Exception as e:
                self.logger.debug(f"No se pudo actualizar pending en DB: {old} -> {new} - {e}")
        self.logger.info(f"Pendings (DB) actualizados con {len(renamed_files)} renombramientos")

    def _update_pending_file(self, old_path: str, new_path: str) -> None:
        """Actualiza un pending individual en la DB después de renombrarlo."""
        try:
            remove_pending(old_path)
            add_pending(new_path)
            self.logger.debug(f"Actualizado pending (DB): {old_path} -> {new_path}")
        except Exception as e:
            self.logger.debug(f"No se pudo actualizar pending (DB): {old_path} -> {new_path} - {e}")

    def _clean_pending_duplicates(self) -> int:
        """Limpia duplicados en la tabla pending_files de la DB."""
        try:
            paths = get_pending_files()
        except Exception as e:
            self.logger.error(f"Error obteniendo pendientes para limpieza: {e}")
            return 0

        seen = set()
        duplicates_removed = 0
        for p in paths:
            norm = str(Path(p).resolve())
            if norm in seen:
                try:
                    remove_pending(str(p))
                    duplicates_removed += 1
                    self.logger.debug(f"Duplicado removido: {p}")
                except Exception as e:
                    self.logger.debug(f"No se pudo remover duplicado {p}: {e}")
            else:
                seen.add(norm)

        if duplicates_removed > 0:
            self.logger.info(f"Limpieza DB completada: {duplicates_removed} duplicados eliminados")

        return duplicates_removed

    def _add_to_pending(self, file_path: Union[str, Path]) -> None:
        """Agrega un archivo a la cola de pendientes de compresión"""
        file_path_str = str(file_path) if isinstance(file_path, Path) else file_path
        try:
            current = {str(p) for p in get_pending_files()}
        except Exception as e:
            self.logger.error(f"Error obteniendo pendientes actuales: {e}")
            current = set()

        if file_path_str not in current:
            try:
                add_pending(file_path_str)
                self.logger.debug(f"Archivo agregado a pendientes (DB): {file_path_str}")
            except Exception as e:
                self.logger.warning(f"No se pudo agregar pending a DB: {e}")
        else:
            self.logger.debug(f"Archivo ya existe en pendientes (DB): {file_path_str}")
