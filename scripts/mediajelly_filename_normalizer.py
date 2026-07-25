#!/usr/bin/env python3
"""
mediajelly_filename_normalizer.py

Mixin con toda la lógica de normalización de nombres de archivo
(eliminación de prefijos fansub, normalización de formato temporada/episodio,
limpieza de espacios/guiones, renombrado seguro).

Extraído de MediaJellyProcessor (mediajelly_processor.py) para reducir el
tamaño de esa clase y aislar esta responsabilidad, que es puramente de
manejo de strings/nombres y no depende del resto del pipeline de compresión.

Requiere que la clase que lo use (mixin) tenga disponible `self.logger`.
"""

import re
from pathlib import Path
from typing import Optional


class FilenameNormalizerMixin:
    """Normalización de nombres de archivos de video (series/películas)."""

    def _normalize_filename(self, file_path: Path) -> Optional[Path]:
        """Normaliza el nombre del archivo eliminando prefijos fansub y normalizando formato de temporada/episodio"""
        # DETECTAR SI ES PELÍCULA: no aplicar normalización de temporada/episodio
        is_movie = any(part.lower() in ["peliculas", "movies"] for part in file_path.parts)

        original_name = file_path.stem
        extension = file_path.suffix
        normalized_name = original_name

        # 1. Eliminar prefijos de grupos fansub
        normalized_name = self._remove_fansub_prefixes(normalized_name)

        # 2. Normalizar formato de temporada/episodio - SOLO PARA SERIES
        if not is_movie:
            normalized_name = self._normalize_season_episode_format(normalized_name)

        # 3. Limpiar espacios y guiones
        normalized_name = self._clean_filename_formatting(normalized_name)

        # Si el nombre cambió, renombrar el archivo
        if normalized_name != original_name:
            return self._rename_file_safely(file_path, normalized_name, extension)

        return None

    def _remove_fansub_prefixes(self, filename: str) -> str:
        """Elimina prefijos de grupos fansub del nombre del archivo"""
        fansub_patterns = [
            r"^\[([^\]]+)\]\s*",  # [Grupo]
            r"^\(([^\)]+)\)\s*",  # (Grupo)
            r"^\{([^\}]+)\}\s*",  # {Grupo}
        ]

        for pattern in fansub_patterns:
            match = re.match(pattern, filename)
            if match:
                group_name = match.group(1)
                rest_of_name = filename[match.end() :]
                if rest_of_name and not rest_of_name.lower().startswith(group_name.lower()):
                    return rest_of_name.strip()

        return filename

    def _normalize_season_episode_format(self, filename: str) -> str:
        """Normaliza el formato de temporada/episodio en nombres de series"""
        # Intentar diferentes patrones de normalización
        normalized = self._try_normalize_sxe_pattern(filename)
        if normalized != filename:
            return normalized

        normalized = self._try_normalize_season_dash_pattern(filename)
        if normalized != filename:
            return normalized

        normalized = self._try_normalize_season_episode_pattern(filename)
        if normalized != filename:
            return normalized

        normalized = self._try_normalize_xxy_pattern(filename)
        if normalized != filename:
            return normalized

        normalized = self._try_normalize_episode_only_pattern(filename)
        if normalized != filename:
            return normalized

        return filename

    def _try_normalize_sxe_pattern(self, filename: str) -> str:
        """Intenta normalizar patrón S02E10 (ya normalizado)"""
        pattern = r"\bS(\d+)E(\d+)\b"
        match = re.search(pattern, filename, re.IGNORECASE)
        if match:
            return self._build_normalized_filename(filename, match, match.groups())
        return filename

    def _try_normalize_season_dash_pattern(self, filename: str) -> str:
        """Intenta normalizar patrón Season 2 - 10"""
        pattern = r"\bSeason\s+(\d+)\s*-\s*(\d+)\b"
        match = re.search(pattern, filename, re.IGNORECASE)
        if match:
            return self._build_normalized_filename(filename, match, match.groups())
        return filename

    def _try_normalize_season_episode_pattern(self, filename: str) -> str:
        """Intenta normalizar patrón Season 2 Episode 10"""
        pattern = r"\bSeason\s+(\d+)\s+Episode\s+(\d+)\b"
        match = re.search(pattern, filename, re.IGNORECASE)
        if match:
            return self._build_normalized_filename(filename, match, match.groups())
        return filename

    def _try_normalize_xxy_pattern(self, filename: str) -> str:
        """Intenta normalizar patrón 2x10"""
        pattern = r"\b(\d+)x(\d+)\b"
        match = re.search(pattern, filename, re.IGNORECASE)
        if match:
            return self._build_normalized_filename(filename, match, match.groups())
        return filename

    def _try_normalize_episode_only_pattern(self, filename: str) -> str:
        """Intenta normalizar patrón E10 (sin Season)"""
        pattern = r"\bE(\d+)\b(?!.*season)(?!.*S\d+)"
        match = re.search(pattern, filename, re.IGNORECASE)
        if match:
            season = "01"
            episode = match.group(1).zfill(2)
            return self._build_normalized_filename(filename, match, (season, episode))
        return filename

    def _build_normalized_filename(self, filename: str, match, groups) -> str:
        """Construye el nombre de archivo normalizado"""
        if len(groups) == 2:
            season = groups[0].zfill(2)
            episode = groups[1].zfill(2)
        elif len(groups) == 1:
            season = "01"
            episode = groups[0].zfill(2)
        else:
            return filename

        # Construir nombre normalizado
        before_match = filename[: match.start()].strip()
        after_match = filename[match.end() :].strip()

        if before_match and after_match:
            return f"{before_match} S{season}E{episode} {after_match}"
        elif before_match:
            return f"{before_match} S{season}E{episode}"
        else:
            return f"S{season}E{episode} {after_match}" if after_match else f"S{season}E{episode}"

    def _clean_filename_formatting(self, filename: str) -> str:
        """Limpia espacios múltiples, guiones redundantes y formato del nombre"""
        # Limpiar espacios y guiones
        filename = re.sub(r"\s*-\s*(S\d+E\d+)\s*-\s*", r" \1 ", filename)
        filename = re.sub(r"\s+", " ", filename)
        filename = re.sub(r"\s*-\s*-\s*", " - ", filename)
        filename = re.sub(r"\s*-\s*$", "", filename)
        filename = re.sub(r"^\s*-\s*", "", filename)
        return filename.strip()

    def _rename_file_safely(self, file_path: Path, new_name: str, extension: str) -> Optional[Path]:
        """Renombra un archivo de forma segura verificando conflictos"""
        new_path = file_path.parent / f"{new_name}{extension}"

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
