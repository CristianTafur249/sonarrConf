#!/usr/bin/env python3
"""
Script para limpiar subtítulos duplicados y de baja calidad
Prioriza .es.srt sobre .es-MX.srt y elimina .hi.srt duplicados
"""

import os
import sys
from pathlib import Path
from collections import defaultdict

from mediajelly_emoji import EmojiGenerator

# Detectar si estamos en contenedor o host
if Path("/mediajelly").exists():
    MEDIA_PATHS = [
        "/mediajelly/media/anime",
        "/mediajelly/media/series",
        "/mediajelly/media/Peliculas"
    ]
else:
    MEDIA_PATHS = [
        "/home/tafurc/mediaJelly/media/anime",
        "/home/tafurc/mediaJelly/media/series",
        "/home/tafurc/mediaJelly/media/Peliculas"
    ]

def find_subtitle_groups():
    """Encuentra grupos de subtítulos que pertenecen al mismo video"""
    subtitle_groups = defaultdict(list)
    
    for media_path in MEDIA_PATHS:
        media_dir = Path(media_path)
        if not media_dir.exists():
            continue
        
        # Buscar todos los archivos SRT
        for srt_file in media_dir.rglob("*.srt"):
            # Obtener el nombre base (sin extensión y sin idioma)
            base_name = srt_file.stem
            
            # Remover sufijos de idioma comunes para agrupar
            for suffix in ['.es', '.spa', '.es-MX', '.es-ES', '.es-AR', '.en', '.eng', '.hi']:
                if base_name.endswith(suffix):
                    base_name = base_name[:-len(suffix)]
                    break
            
            # Agrupar por directorio + nombre base
            key = (srt_file.parent, base_name)
            subtitle_groups[key].append(srt_file)
    
    return subtitle_groups

def prioritize_subtitles(subs):
    """Prioriza subtítulos según calidad y tipo."""
    # Convertir Path objects a diccionarios con información
    sub_info = [{'path': str(sub), 'size': sub.stat().st_size, 'obj': sub} for sub in subs]
    
    def score(sub):
        path = sub['path']
        size = sub['size']
        
        points = 0
        
        # Prioridad por extensión (en orden de preferencia)
        if '.es.srt' in path:
            points += 100
        elif '.spa.srt' in path:
            points += 80
        elif '.es-MX.srt' in path:
            points += 60
        
        # Penalización para hearing impaired
        if '.hi.' in path.lower():
            points -= 50
        
        # Bonus por tamaño (archivos más grandes suelen ser más completos)
        # Normalizado a un máximo de 20 puntos
        points += min(size / 10000, 20)
        
        return points
    
    # Ordenar por puntuación descendente
    scored = sorted(sub_info, key=score, reverse=True)
    
    # VALIDACIÓN ESPECIAL: Si el mejor tiene <5KB y hay otro >20KB, invertir
    if len(scored) >= 2:
        best = scored[0]
        second = scored[1]
        
        # Si el "mejor" es sospechosamente pequeño y el segundo es mucho más grande
        if best['size'] < 5000 and second['size'] > 20000:
            # Verificar que el segundo no sea .hi.
            if '.hi.' not in second['path'].lower():
                print(f"  {EmojiGenerator.warning_msg()}  CORRECCIÓN: {Path(best['path']).name} ({best['size']:,} bytes) parece corrupto")
                print(f"     → Priorizando {Path(second['path']).name} ({second['size']:,} bytes)")
                scored[0], scored[1] = scored[1], scored[0]
    
    # Devolver solo los objetos Path en orden prioritario
    return [sub['obj'] for sub in scored]

def clean_duplicates(dry_run=True):
    """Limpia subtítulos duplicados, manteniendo solo el de mayor calidad"""
    subtitle_groups = find_subtitle_groups()
    
    removed_count = 0
    kept_count = 0
    total_groups = 0
    
    print("=" * 80)
    print("LIMPIEZA DE SUBTÍTULOS DUPLICADOS")
    print("=" * 80)
    print(f"Modo: {'DRY RUN (sin cambios)' if dry_run else 'ELIMINAR archivos'}")
    print()
    
    for (directory, base_name), subtitle_list in subtitle_groups.items():
        # Solo procesar grupos con múltiples subtítulos
        if len(subtitle_list) <= 1:
            continue
        
        total_groups += 1
        
        # Filtrar solo subtítulos en español
        spanish_subtitles = [
            srt for srt in subtitle_list
            if any(marker in srt.name.lower() for marker in ['.es.', '.spa.', '.es-mx.', '.es-es.', '.es-ar.'])
        ]
        
        if len(spanish_subtitles) <= 1:
            continue
        
        # Priorizar subtítulos
        prioritized = prioritize_subtitles(spanish_subtitles)
        
        # Mantener el de mayor prioridad, eliminar los demás
        to_keep = prioritized[0]
        to_remove = prioritized[1:]
        
        print(f"\n{EmojiGenerator.folder()} {directory.name}/{base_name}")
        print(f"  {EmojiGenerator.success()} MANTENER: {to_keep.name} ({to_keep.stat().st_size:,} bytes)")
        
        for srt_file in to_remove:
            print(f"  {EmojiGenerator.error()} ELIMINAR: {srt_file.name} ({srt_file.stat().st_size:,} bytes)")
            
            if not dry_run:
                try:
                    srt_file.unlink()
                    removed_count += 1
                except Exception as e:
                    print(f"     {EmojiGenerator.warning_msg()}  Error: {e}")
            else:
                removed_count += 1
        
        kept_count += 1
    
    print("\n" + "=" * 80)
    print("RESUMEN")
    print("=" * 80)
    print(f"Grupos de subtítulos analizados: {total_groups}")
    print(f"Archivos a mantener: {kept_count}")
    print(f"Archivos {'eliminados' if not dry_run else 'a eliminar'}: {removed_count}")
    print()
    
    if dry_run:
        print("💡 Para aplicar los cambios, ejecuta:")
        print("   python3 clean_duplicate_subtitles.py --apply")

def main():
    """Función principal"""
    dry_run = True
    
    if len(sys.argv) > 1 and sys.argv[1] == '--apply':
        dry_run = False
        response = input(f"{EmojiGenerator.warning_msg()}  ¿Estás seguro de que deseas ELIMINAR los subtítulos duplicados? (s/N): ")
        if response.lower() != 's':
            print("Operación cancelada.")
            return
    
    clean_duplicates(dry_run=dry_run)

if __name__ == "__main__":
    main()
