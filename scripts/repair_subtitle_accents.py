#!/usr/bin/env python3
"""
Repara las tildes que una versión anterior del traductor añadía a todo
que/como/donde/cuando en los subtítulos en español ("lo qué haces", "es cómo ser").

Quita la tilde en los cues que no son pregunta ni exclamación. Limitación: una
pregunta indirecta sin signos ("no sé qué hacer") también la pierde.

Uso:
    python3 repair_subtitle_accents.py            # solo informa
    python3 repair_subtitle_accents.py --write    # corrige los archivos
"""

import argparse
import re
from pathlib import Path

from mediajelly_utils import MediaJellyPaths

WORD_RE = re.compile(r"\b(qué|cómo|dónde|cuándo)\b", re.IGNORECASE)
UNACCENT = str.maketrans("éóáÉÓÁ", "eoaEOA")
QUESTION_MARKS = set("¿?¡!")


def repair_text(content: str) -> tuple[str, int]:
    """Devuelve el contenido corregido y el número de palabras reparadas."""
    fixed = 0
    blocks = re.split(r"(\n\s*\n)", content)
    for i, block in enumerate(blocks):
        if not block.strip() or QUESTION_MARKS & set(block):
            continue
        new_block, count = WORD_RE.subn(lambda match: match.group(0).translate(UNACCENT), block)
        if count:
            blocks[i] = new_block
            fixed += count
    return "".join(blocks), fixed


def main():
    parser = argparse.ArgumentParser(description="Repara tildes sobrantes en subtítulos .es.srt")
    parser.add_argument("directory", nargs="?", default=None, help="Directorio a revisar (por defecto, media/)")
    parser.add_argument("--write", action="store_true", help="Corregir los archivos (sin esto solo informa)")
    args = parser.parse_args()

    base_dir = Path(args.directory) if args.directory else MediaJellyPaths.get_media_dir()
    files_affected = 0
    words_fixed = 0

    for srt_file in base_dir.rglob("*.es.srt"):
        try:
            content = srt_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            print(f"No se pudo leer {srt_file}: {e}")
            continue

        repaired, count = repair_text(content)
        if not count:
            continue

        files_affected += 1
        words_fixed += count
        if args.write:
            try:
                srt_file.write_text(repaired, encoding="utf-8")
            except OSError as e:
                print(f"No se pudo escribir {srt_file}: {e}")

    action = "reparadas" if args.write else "por reparar"
    print(f"Subtítulos .es.srt con tildes sobrantes: {files_affected}")
    print(f"Palabras {action}: {words_fixed}")
    if not args.write and files_affected:
        print("Ejecuta con --write para corregirlos.")


if __name__ == "__main__":
    main()
