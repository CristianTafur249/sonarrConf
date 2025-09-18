#!/bin/bash

# Detecta si estamos en contenedor o en host
if [ -d "/mediajelly" ]; then
    # Estamos en contenedor
    BASE_DIR="/mediajelly"
else
    # Estamos en host
    BASE_DIR="/home/tafurc/mediaJelly"
fi

# --- CONFIGURACIÓN ---
OUTPUT_FILE="$BASE_DIR/.scripts/pending-compression.txt"
LOG_FILE="$BASE_DIR/.scripts/logs/scan.log"
COMPLETED="$BASE_DIR/.scripts/completed.txt"

EXTENSIONS=("mkv" "mp4" "avi" "mov" "webm")

mkdir -p "$(dirname "$OUTPUT_FILE")"
mkdir -p "$(dirname "$LOG_FILE")"

TMP_FILE=$(mktemp)
EXISTING_SORTED=$(mktemp)
NEW_SORTED=$(mktemp)
FILTERED_NEW=$(mktemp)
DIFF_FILE=$(mktemp)

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

# Función para limpiar archivos compressed.mp4 parciales
cleanup_partial_files() {
  local folder="$1"
  local cleaned=0
  
  while IFS= read -r -d '' compressed_file; do
    if [ -f "$compressed_file" ]; then
      local base_name="${compressed_file%.compressed.mp4}"
      local original_file=""
      
      # Busca el archivo original correspondiente
      for ext in "${EXTENSIONS[@]}"; do
        if [ -f "${base_name}.${ext}" ]; then
          original_file="${base_name}.${ext}"
          break
        fi
      done
      
      # Si existe el original, el compressed.mp4 es parcial
      if [ -n "$original_file" ]; then
        log "LIMPIEZA: Eliminando archivo compressed.mp4 parcial: $compressed_file"
        rm -f "$compressed_file"
        cleaned=$((cleaned + 1))
      fi
    fi
  done < <(find "$folder" -name "*.compressed.mp4" -not -path "*/.*" -print0 2>/dev/null)
  
  if [ "$cleaned" -gt 0 ]; then
    log "Limpieza completada: $cleaned archivos compressed.mp4 parciales eliminados"
  fi
}

scan_folder() {
  local folder="$1"
  if [ ! -d "$folder" ]; then
    log "Carpeta no válida: $folder"
    return
  fi

  log "Escaneando: $folder"
  
  # Limpia archivos compressed.mp4 parciales antes de escanear
  cleanup_partial_files "$folder"

  # Construye expresión con múltiples -iname conectadas por -o
  find_expr=()
  for ext in "${EXTENSIONS[@]}"; do
    find_expr+=(-iname "*.${ext}" -o)
  done
  unset 'find_expr[${#find_expr[@]}-1]'  # Quitar último -o

  # Excluye carpetas que comienzan con punto (como .deleted, .trash, etc.)
  find "$folder" -type f \( "${find_expr[@]}" \) -not -path "*/.*" -exec realpath {} \; >> "$TMP_FILE"
}

# --- EJECUCIÓN ---
if [ $# -eq 0 ]; then
  echo "Uso: $0 <carpeta1> [carpeta2 ...]"
  exit 1
fi

touch "$OUTPUT_FILE" 2>/dev/null || {
  echo "No se puede escribir en $OUTPUT_FILE"
  exit 1
}

for folder in "$@"; do
  abs_folder=$(realpath "$folder" 2>/dev/null)
  scan_folder "$abs_folder"
done

# Valida que los archivos realmente existen antes de procesarlos
VALIDATED_TMP=$(mktemp)
while IFS= read -r file_path; do
  if [ -f "$file_path" ]; then
    echo "$file_path" >> "$VALIDATED_TMP"
  else
    log "ADVERTENCIA: Archivo no encontrado, omitido: $file_path"
  fi
done < "$TMP_FILE"

touch "$COMPLETED"  # Asegura que el archivo existe

# Ordena archivos por tamaño descendente (más pesados primero)
SORTED_BY_SIZE=$(mktemp)
while IFS= read -r file_path; do
  file_size=$(stat -c %s "$file_path" 2>/dev/null || echo "0")
  printf "%020d %s\n" "$file_size" "$file_path"
done < "$VALIDATED_TMP" | sort -nr | cut -d' ' -f2- > "$SORTED_BY_SIZE"

# Filtra archivos ya procesados por nombre
while IFS= read -r file_path; do
  file_name=$(basename "$file_path")
  if ! grep -q "^$file_name$" "$COMPLETED"; then
    echo "$file_path" >> "$FILTERED_NEW"
  else
    log "Saltando archivo ya procesado: $file_name"
  fi
done < "$SORTED_BY_SIZE"

sort -u "$FILTERED_NEW" > "$NEW_SORTED"
sort -u "$OUTPUT_FILE" > "$EXISTING_SORTED"

comm -23 "$NEW_SORTED" "$EXISTING_SORTED" > "$DIFF_FILE"
total_encontrados=$(wc -l < "$NEW_SORTED")
nuevas=$(wc -l < "$DIFF_FILE")

log "Archivos encontrados: $total_encontrados"
log "Nuevas rutas detectadas: $nuevas"

if [ "$nuevas" -gt 0 ]; then
  cat "$DIFF_FILE" >> "$OUTPUT_FILE"
  log "Agregadas $nuevas nuevas rutas al archivo de pendientes"
else
  log "No hay nuevas rutas para procesar"
fi

rm -f "$TMP_FILE" "$NEW_SORTED" "$EXISTING_SORTED" "$DIFF_FILE" "$VALIDATED_TMP" "$FILTERED_NEW" "$SORTED_BY_SIZE"

# Gestiona logs antes de continuar
/home/tafurc/mediaJelly/.scripts/manage-logs.sh

# Solo procesar la compresión si hay archivos pendientes
if [ -s "$OUTPUT_FILE" ]; then
  log "Iniciando procesamiento de compresión..."
  /home/tafurc/mediaJelly/.scripts/process-compression.sh
else
  log "No hay archivos pendientes para comprimir"
fi
