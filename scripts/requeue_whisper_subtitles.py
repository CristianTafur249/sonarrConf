#!/usr/bin/env python3
"""
Encola para regeneración los subtítulos que fueron generados con Whisper.

Lee los logs del traductor (subtitle_translator.log*), localiza los videos cuyo
subtítulo salió de una transcripción de audio y que aún conservan su .es.srt, y
los escribe en tmp/whisper_redo_queue.txt. El cron runner procesa esa cola de
noche con `mediajelly_subtitle_translator.py --redo-queue`.

Uso:
    python3 requeue_whisper_subtitles.py            # solo informa
    python3 requeue_whisper_subtitles.py --write    # escribe la cola
"""

import argparse
import gzip
import re
from pathlib import Path
from typing import Dict, List, Set

from mediajelly_utils import MediaJellyPaths

# "... Subtítulos extraídos del audio (idioma: ja): <stem del video>_<mtime_ns>.srt"
WHISPER_LOG_RE = re.compile(r"Subtítulos extraídos del audio \(idioma: [^)]*\): (.+)_\d+\.srt\s*$")
SPANISH_SUBTITLE_SUFFIX = ".es.srt"
QUEUE_FILE_NAME = "whisper_redo_queue.txt"


def read_whisper_stems(logs_dir: Path) -> Set[str]:
    """Devuelve los stems de video que aparecen como transcritos con Whisper en los logs."""
    stems: Set[str] = set()
    for log_file in sorted(logs_dir.glob("subtitle_translator.log*")):
        opener = gzip.open if log_file.suffix == ".gz" else open
        try:
            with opener(log_file, "rt", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    match = WHISPER_LOG_RE.search(line)
                    if match:
                        stems.add(match.group(1))
        except OSError as e:
            print(f"No se pudo leer {log_file.name}: {e}")
    return stems


def find_videos_by_stem(media_dir: Path) -> Dict[str, List[Path]]:
    """Indexa por stem los videos de la biblioteca que tienen .es.srt."""
    videos: Dict[str, List[Path]] = {}
    for video_file in media_dir.rglob("*"):
        if not MediaJellyPaths.is_video_file(video_file) or MediaJellyPaths.is_excluded_folder(video_file):
            continue
        if not video_file.with_suffix(SPANISH_SUBTITLE_SUFFIX).exists():
            continue
        videos.setdefault(video_file.stem, []).append(video_file)
    return videos


def main():
    parser = argparse.ArgumentParser(description="Encola subtítulos generados con Whisper para regenerarlos")
    parser.add_argument("--write", action="store_true", help=f"Escribir tmp/{QUEUE_FILE_NAME} (sin esto solo informa)")
    args = parser.parse_args()

    tmp_dir = MediaJellyPaths.get_tmp_dir()
    stems = read_whisper_stems(tmp_dir / "logs")
    videos = find_videos_by_stem(MediaJellyPaths.get_media_dir())

    to_redo = sorted(str(video) for stem in stems for video in videos.get(stem, []))
    not_found = len([stem for stem in stems if stem not in videos])

    print(f"Transcritos con Whisper según los logs: {len(stems)}")
    print(f"Localizados con .es.srt: {len(to_redo)}")
    print(f"Sin localizar (renombrados, eliminados o sin .es.srt): {not_found}")

    if not args.write:
        print("Ejecuta con --write para crear la cola.")
        return

    queue_file = tmp_dir / QUEUE_FILE_NAME
    existing = set()
    if queue_file.exists():
        existing = {line.strip() for line in queue_file.read_text(encoding="utf-8").splitlines() if line.strip()}
    queue = sorted(existing | set(to_redo))
    queue_file.write_text("".join(f"{path}\n" for path in queue), encoding="utf-8")
    print(f"Cola escrita: {queue_file} ({len(queue)} archivos)")


if __name__ == "__main__":
    main()
