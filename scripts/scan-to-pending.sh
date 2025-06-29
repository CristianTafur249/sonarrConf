#!/bin/bash

# --- CONFIGURACIÓN ---
OUTPUT_FILE="/home/tafurc/mediaJelly/scripts/pending-compression.txt"
LOG_FILE="/home/tafurc/mediaJelly/scripts/logs/scan.log"

EXTENSIONS=("mkv" "mp4" "avi" "mov" "webm")

mkdir -p "$(dirname "$OUTPUT_FILE")"
mkdir -p "$(dirname "$LOG_FILE")"

TMP_FILE=$(mktemp)
EXISTING_SORTED=$(mktemp)
NEW_SORTED=$(mktemp)
DIFF_FILE=$(mktemp)

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

scan_folder() {
  local folder="$1"
  if [ ! -d "$folder" ]; then
    log "Carpeta no válida: $folder"
    return
  fi

  log "Escaneando: $folder"

  # Construir expresión con múltiples -iname conectadas por -o
  find_expr=()
  for ext in "${EXTENSIONS[@]}"; do
    find_expr+=(-iname "*.${ext}" -o)
  done
  unset 'find_expr[${#find_expr[@]}-1]'  # Quitar último -o

  find "$folder" -type f \( "${find_expr[@]}" \) -exec realpath {} \; >> "$TMP_FILE"
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

sort -u "$TMP_FILE" > "$NEW_SORTED"
sort -u "$OUTPUT_FILE" > "$EXISTING_SORTED"

comm -23 "$NEW_SORTED" "$EXISTING_SORTED" > "$DIFF_FILE"
total_encontrados=$(wc -l < "$NEW_SORTED")
nuevas=$(wc -l < "$DIFF_FILE")

log "Archivos encontrados: $total_encontrados"
log "Nuevas rutas detectadas: $nuevas"

if [ "$nuevas" -gt 0 ]; then
  cat "$DIFF_FILE" >> "$OUTPUT_FILE"
fi

rm -f "$TMP_FILE" "$NEW_SORTED" "$EXISTING_SORTED" "$DIFF_FILE"
sleep 10

/home/tafurc/mediaJelly/scripts/process-compression.sh
