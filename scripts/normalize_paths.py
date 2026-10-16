#!/usr/bin/env python3
"""
Script para normalizar rutas en archivos completed.txt y pending-compression.txt
"""

import os
from pathlib import Path
from mediajelly_utils import MediaJellyPaths

# Detecta entorno usando MediaJellyPaths
is_container = MediaJellyPaths.is_container()
base_dir = MediaJellyPaths.get_base_path()
media_dir = base_dir / "media"
completed_file = base_dir / "scripts" / "completed.txt"
pending_file = base_dir / "scripts" / "pending-compression.txt"

print(f"Entorno: {'Docker' if is_container else 'Host'}")
print(f"Base dir: {base_dir}")


def normalize_path(path_str, target_base):
    """Normalizar ruta para que use la base correcta"""
    # Remoción de prefijos conocidos
    path_str = path_str.strip()

    # Si ya está bien, devolverla tal como está
    if path_str.startswith(str(target_base)):
        return path_str

    # Extracción de la parte relativa después de 'media/'
    if "/media/" in path_str:
        media_part = path_str.split("/media/", 1)[1]
        return str(target_base / "media" / media_part)

    return path_str


# Normaliza completed.txt
if completed_file.exists():
    print(f"Normalizando {completed_file}...")
    with open(completed_file, "r") as f:
        completed_paths = [line.strip() for line in f if line.strip()]

    normalized_completed = []
    for path in completed_paths:
        normalized = normalize_path(path, base_dir)
        normalized_completed.append(normalized)

    with open(completed_file, "w") as f:
        for path in normalized_completed:
            f.write(f"{path}\n")

    print(f"Normalizadas {len(normalized_completed)} rutas en completed.txt")

# Normaliza pending-compression.txt
if pending_file.exists():
    print(f"Normalizando {pending_file}...")
    with open(pending_file, "r") as f:
        pending_paths = [line.strip() for line in f if line.strip()]

    normalized_pending = []
    for path in pending_paths:
        normalized = normalize_path(path, base_dir)
        normalized_pending.append(normalized)

    with open(pending_file, "w") as f:
        for path in normalized_pending:
            f.write(f"{path}\n")

    print(f"Normalizadas {len(normalized_pending)} rutas en pending-compression.txt")

print("Normalización completada")
