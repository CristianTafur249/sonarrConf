#!/usr/bin/env python3
"""
Repara los campos booleanos de los .nfo que el traductor de NFO tradujo por error
(<watched>FALSO</watched> -> <watched>false</watched>, etc.).

Los datos de fileinfo/streamdetails (codec, scantype, language) no se pueden
recuperar aquí; Jellyfin los regenera al refrescar los metadatos del elemento.

Uso:
    python3 repair_nfo_fields.py            # solo informa
    python3 repair_nfo_fields.py --write    # corrige los archivos
"""

import argparse
import re
from pathlib import Path

from mediajelly_utils import MediaJellyPaths

BOOLEAN_TAGS = ("watched", "default", "forced", "lockdata", "isuserfavorite")
TRANSLATED_BOOLEANS = {"falso": "false", "verdadero": "true"}
BOOLEAN_FIELD_RE = re.compile(
    rf"<({'|'.join(BOOLEAN_TAGS)})>\s*({'|'.join(TRANSLATED_BOOLEANS)})\s*</\1>", re.IGNORECASE
)


def repair_text(content: str) -> tuple[str, int]:
    """Devuelve el contenido corregido y el número de campos reparados."""
    return BOOLEAN_FIELD_RE.subn(
        lambda match: f"<{match.group(1)}>{TRANSLATED_BOOLEANS[match.group(2).lower()]}</{match.group(1)}>", content
    )


def main():
    parser = argparse.ArgumentParser(description="Repara campos booleanos traducidos por error en los .nfo")
    parser.add_argument("directory", nargs="?", default=None, help="Directorio a revisar (por defecto, media/)")
    parser.add_argument("--write", action="store_true", help="Corregir los archivos (sin esto solo informa)")
    args = parser.parse_args()

    base_dir = Path(args.directory) if args.directory else MediaJellyPaths.get_media_dir()
    files_affected = 0
    fields_repaired = 0

    for nfo_file in base_dir.rglob("*.nfo"):
        try:
            content = nfo_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            print(f"No se pudo leer {nfo_file}: {e}")
            continue

        repaired, count = repair_text(content)
        if not count:
            continue

        files_affected += 1
        fields_repaired += count
        if args.write:
            try:
                nfo_file.write_text(repaired, encoding="utf-8")
            except OSError as e:
                print(f"No se pudo escribir {nfo_file}: {e}")

    action = "reparados" if args.write else "por reparar"
    print(f"Archivos .nfo con campos booleanos dañados: {files_affected}")
    print(f"Campos {action}: {fields_repaired}")
    if not args.write and files_affected:
        print("Ejecuta con --write para corregirlos.")


if __name__ == "__main__":
    main()
