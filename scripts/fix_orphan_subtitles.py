#!/usr/bin/env python3
"""
fix_orphan_subtitles.py - Limpieza única de subtítulos huérfanos

Antes de la corrección en mediajelly_subtitle_translator.py, los subtítulos
generados por Whisper se guardaban con un nombre volátil:
    "{nombre_del_video}_{mtime_ns}.srt"
    "{nombre_del_video}_{mtime_ns}.es.srt"

Ese sufijo numérico (mtime en nanosegundos) impedía que el sistema reconociera
el archivo como "ya procesado" en la siguiente ejecución, por lo que el video
se re-procesaba una y otra vez, dejando huérfanos nuevos en cada corrida.

Este script busca esos huérfanos y, para cada uno:
  - Si ya existe el archivo canónico ("{nombre_del_video}.srt" /
    ".es.srt") junto al video, el huérfano es un duplicado: se reporta
    para eliminar.
  - Si NO existe el canónico, el huérfano tiene contenido útil (subtítulo ya
    extraído/traducido): se renombra al nombre canónico para aprovecharlo,
    en vez de perder el trabajo y forzar una nueva transcripción con Whisper.

Por defecto corre en modo DRY RUN (solo reporta). Usar --apply para ejecutar
los cambios.

Uso:
    python3 fix_orphan_subtitles.py            # solo reporta
    python3 fix_orphan_subtitles.py --apply    # aplica los cambios
"""

import re
import sys
from pathlib import Path
from typing import List, Optional, Tuple

from mediajelly_emoji import EmojiGenerator
from mediajelly_utils import MediaJellyPaths

# Mismo patrón de carpetas de medios que usa clean_duplicate_subtitles.py
base_media = MediaJellyPaths.get_base_path() / "media"
MEDIA_PATHS = [
    base_media / "anime",
    base_media / "series",
    base_media / "Peliculas",
]

# "{stem}_{10 a 20 dígitos}[.lang].srt" - el sufijo numérico es st_mtime_ns
ORPHAN_PATTERN = re.compile(r"^(?P<stem>.+)_(?P<digits>\d{10,20})(?P<lang>\.[A-Za-z\-]{2,10})?$")

VIDEO_EXTENSIONS = MediaJellyPaths.EXTENSIONS


def _video_exists_for_stem(directory: Path, stem: str) -> bool:
    return any((directory / f"{stem}{ext}").exists() for ext in VIDEO_EXTENSIONS)


def find_orphans() -> List[Tuple[Path, str, Optional[str]]]:
    """Devuelve lista de (ruta_huerfano, stem_video, sufijo_idioma_o_None)."""
    orphans = []
    for media_path in MEDIA_PATHS:
        media_dir = Path(media_path)
        if not media_dir.exists():
            continue

        for srt_file in media_dir.rglob("*.srt"):
            # El stem puede incluir un sufijo de idioma antes del .srt final,
            # p.ej. "Nombre_1781490723537125344.es" -> stem del Path ya quita solo ".srt"
            match = ORPHAN_PATTERN.match(srt_file.stem)
            if not match:
                continue

            video_stem = match.group("stem")
            lang_suffix = match.group("lang")  # p.ej. ".es" o None

            if not _video_exists_for_stem(srt_file.parent, video_stem):
                # No hay video correspondiente; no tocar (podría ser coincidencia)
                continue

            orphans.append((srt_file, video_stem, lang_suffix))

    return orphans


def process_orphans(dry_run: bool = True) -> None:
    orphans = find_orphans()

    print("=" * 80)
    print("LIMPIEZA DE SUBTÍTULOS HUÉRFANOS (bug de nombre volátil con Whisper)")
    print("=" * 80)
    print(f"Modo: {'DRY RUN (sin cambios)' if dry_run else 'APLICAR cambios'}")
    print()

    if not orphans:
        print(f"{EmojiGenerator.success()} No se encontraron huérfanos. Nada que hacer.")
        return

    renamed = 0
    removed = 0

    for orphan_path, video_stem, lang_suffix in orphans:
        canonical_name = f"{video_stem}{lang_suffix or ''}.srt"
        canonical_path = orphan_path.parent / canonical_name

        if canonical_path.exists():
            print(f"{EmojiGenerator.error()} ELIMINAR (duplicado, ya existe {canonical_name}): {orphan_path.name}")
            if not dry_run:
                try:
                    orphan_path.unlink()
                    removed += 1
                except Exception as e:
                    print(f"   {EmojiGenerator.warning_msg()} Error eliminando: {e}")
            else:
                removed += 1
        else:
            print(f"{EmojiGenerator.check_mark()} RENOMBRAR a {canonical_name}: {orphan_path.name}")
            if not dry_run:
                try:
                    orphan_path.rename(canonical_path)
                    renamed += 1
                except Exception as e:
                    print(f"   {EmojiGenerator.warning_msg()} Error renombrando: {e}")
            else:
                renamed += 1

    print()
    print("=" * 80)
    print("RESUMEN")
    print("=" * 80)
    print(f"Huérfanos encontrados: {len(orphans)}")
    print(f"{'A renombrar' if dry_run else 'Renombrados'}: {renamed}")
    print(f"{'A eliminar' if dry_run else 'Eliminados'}: {removed}")
    print()

    if dry_run:
        print("💡 Para aplicar los cambios, ejecuta:")
        print("   python3 fix_orphan_subtitles.py --apply")


def main() -> None:
    dry_run = "--apply" not in sys.argv
    if not dry_run:
        response = input(f"{EmojiGenerator.warning_msg()} ¿Aplicar los cambios sobre los archivos reales? (s/N): ")
        if response.lower() != "s":
            print("Operación cancelada.")
            return

    process_orphans(dry_run=dry_run)


if __name__ == "__main__":
    main()
