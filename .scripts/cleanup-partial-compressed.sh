#!/bin/bash

# Script para limpiar archivos compressed.mp4 parciales o corruptos
# Uso: ./cleanup-partial-compressed.sh <directorio_media>

MEDIA_DIR="${1:-/home/tafurc/mediaJelly/media}"
LOG_FILE="/home/tafurc/mediaJelly/scripts/logs/cleanup.log"

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

cleanup_partial_compressed() {
  local media_dir="$1"
  
  if [ ! -d "$media_dir" ]; then
    log "ERROR: Directorio no válido: $media_dir"
    return 1
  fi

  log "Iniciando limpieza de archivos compressed.mp4 parciales en: $media_dir"
  
  local count=0
  while IFS= read -r -d '' compressed_file; do
    log "Encontrado archivo compressed.mp4: $compressed_file"
    
    # Verifica si el archivo existe y tiene tamaño
    if [ -f "$compressed_file" ] && [ -s "$compressed_file" ]; then
      # Verifica integridad con ffprobe
      if ! ffprobe -v error "$compressed_file" > /dev/null 2>&1; then
        log "CORRUPTO: Eliminando archivo corrupto: $compressed_file"
        rm -f "$compressed_file"
        count=$((count + 1))
      else
        # El archivo parece válido, pero podría ser parcial
        local base_name="${compressed_file%.compressed.mp4}"
        local original_file="${base_name}.mkv"
        
        # Si existe el archivo original, el compressed.mp4 es probablemente parcial
        if [ -f "$original_file" ]; then
          log "PARCIAL: Eliminando archivo parcial (original existe): $compressed_file"
          rm -f "$compressed_file"
          count=$((count + 1))
        else
          log "VÁLIDO: Manteniendo archivo (no existe original): $compressed_file"
        fi
      fi
    else
      log "VACÍO: Eliminando archivo vacío: $compressed_file"
      rm -f "$compressed_file"
      count=$((count + 1))
    fi
    
  done < <(find "$media_dir" -name "*.compressed.mp4" -not -path "*/.*" -print0)
  
  log "Limpieza completada. Archivos eliminados: $count"
}

# --- EJECUCIÓN ---
mkdir -p "$(dirname "$LOG_FILE")"

if [ $# -eq 0 ]; then
  echo "Uso: $0 <directorio_media>"
  echo "Ejemplo: $0 /home/tafurc/mediaJelly/media"
  exit 1
fi

cleanup_partial_compressed "$1"
