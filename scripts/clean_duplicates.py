#!/usr/bin/env python3
"""
Script para limpiar duplicados del archivo pending-compression.txt
"""

from pathlib import Path
import sys
from mediajelly_emoji import EmojiGenerator

def process_file_line(line: str, seen: set) -> tuple[str | None, bool]:
    """Procesa una línea del archivo y determina si es válida y única"""
    line_clean = line.strip()
    if not line_clean:
        return None, False
    
    if not Path(line_clean).exists():
        print(f"{EmojiGenerator.warning()} Archivo no existe, eliminando: {line_clean}")
        return None, True  # Es duplicado/error
    
    if line_clean in seen:
        print(f"{EmojiGenerator.info()} Duplicado encontrado: {Path(line_clean).name}")
        return None, True  # Es duplicado
    
    seen.add(line_clean)
    return line_clean, False

def clean_pending_duplicates():
    """Limpia duplicados del archivo pending-compression.txt"""
    # Detecta el entorno
    is_container = Path("/mediajelly").exists()
    base_dir = Path("/mediajelly" if is_container else "/home/tafurc/mediaJelly")
    scripts_dir = base_dir / "scripts"
    pending_file = scripts_dir / "pending-compression.txt"
    
    if not pending_file.exists():
        print(f"{EmojiGenerator.info()} No existe archivo pending-compression.txt")
        return 0
    
    # Lee todas las líneas
    with open(pending_file, 'r') as f:
        original_lines = f.readlines()
    
    # Procesa y elimina duplicados
    unique_files = []
    seen = set()
    duplicates_found = 0
    
    for line in original_lines:
        clean_line, is_duplicate = process_file_line(line, seen)
        if clean_line:
            unique_files.append(clean_line)
        if is_duplicate:
            duplicates_found += 1
    
    # Reescribe el archivo si hay cambios
    if duplicates_found > 0:
        with open(pending_file, 'w') as f:
            for file_path in sorted(unique_files):
                f.write(f"{file_path}\n")
        
        print(f"{EmojiGenerator.success()} Limpieza completada:")
        print(f"   {EmojiGenerator.memo()} Archivos originales: {len(original_lines)}")
        print(f"   {EmojiGenerator.wastebasket()} Duplicados eliminados: {duplicates_found}")
        print(f"   {EmojiGenerator.success()} Archivos únicos: {len(unique_files)}")
    else:
        print(f"{EmojiGenerator.success()} No se encontraron duplicados")
    
    return duplicates_found

def main():
    """Función principal"""
    print(f"{EmojiGenerator.cleanup()} Limpiador de Duplicados MediaJelly")
    print("=" * 40)
    
    duplicates_removed = clean_pending_duplicates()
    
    if duplicates_removed > 0:
        print(f"\n{EmojiGenerator.party()} Proceso completado: {duplicates_removed} duplicados eliminados")
    else:
        print(f"\n{EmojiGenerator.sparkle()} El archivo ya está limpio")

if __name__ == "__main__":
    main()