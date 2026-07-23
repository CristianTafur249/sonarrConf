#!/usr/bin/env python3
"""
Ejecuta una transcripción y traducción relajada de subtítulos.

Este wrapper reutiliza el traductor principal, pero reduce los umbrales de
validación para que se conserve una transcripción aunque sea corta.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


sys.path.append(str(Path(__file__).parent))

from mediajelly_subtitle_translator import SubtitleTranslator, _setup_environment


def _build_relaxed_translator() -> tuple[dict, SubtitleTranslator]:
    """Crea un traductor con validaciones mínimas para no descartar resultados cortos."""
    env_config = _setup_environment()
    translator = SubtitleTranslator(
        max_workers=1,
        use_whisper=True,
        allow_retranslate=True,
        lock_file=env_config["subtitle_lock_file"],
    )

    translator.MIN_VALID_SRT_SIZE_BYTES = 1
    translator.MIN_VALID_SRT_BLOCKS = 1
    translator.MIN_VALID_SRT_TEXT_LINES = 1
    translator.MIN_VALID_SRT_USEFUL_CHARS = 1

    return env_config, translator


def main() -> int:
    parser = argparse.ArgumentParser(
        description="MediaJelly Subtitle Translator Relaxed"
    )
    parser.add_argument("paths", nargs="+", help="Archivos de video a procesar")
    parser.add_argument(
        "--force-language",
        dest="force_language",
        type=str,
        default=None,
        help="Forzar idioma de transcripción (ej: 'ja', 'en', 'es')",
    )
    args = parser.parse_args()

    _, translator = _build_relaxed_translator()
    if args.force_language:
        translator.force_language = args.force_language

    file_paths = [Path(path) for path in args.paths]
    expected_outputs = [path.with_suffix(".es.srt") for path in file_paths]
    stats = translator.process_file_list(file_paths)

    generated_outputs = [output for output in expected_outputs if output.exists()]
    translated = len(generated_outputs)
    extracted = stats.get("extracted", 0) if isinstance(stats, dict) else 0
    errors = stats.get("errors", 0) if isinstance(stats, dict) else 1

    print(f"Subtítulos extraídos: {extracted}")
    print(f"Subtítulos traducidos: {translated}")
    print(f"Errores: {errors}")

    return 0 if translated == len(expected_outputs) else 1


if __name__ == "__main__":
    raise SystemExit(main())
