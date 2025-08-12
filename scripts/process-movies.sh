#!/bin/bash

LOCKFILE="./scripts/tmp/compress.lock"
PENDING="./scripts/pending-movies.txt"
TEMP="./scripts/pending-movies.tmp"
LOGFILE="./scripts/logs/compression-movies.log"

mkdir -p ./scripts/tmp
mkdir -p ./scripts/logs

(
  flock 9

  cp "$PENDING" "$TEMP" 2>/dev/null || exit 0
  > "$PENDING"

  while IFS= read -r input || [ -n "$input" ]; do
    # Validar ruta válida
    if ! [[ "$input" =~ ^/ ]] || ! [ -f "$input" ]; then
      echo "Línea inválida ignorada: '$input'" >&2
      continue
    fi

    dir_path="$(dirname "$input")"
    file_name="$(basename "$input")"
    base_name="${file_name%.*}"

    final_output="$dir_path/$base_name.mp4"
    temp_output="$dir_path/$base_name.compressed.mp4"

    file_size=$(stat -c %s "$input")

    # Si la película pesa menos de 1GB, solo convertir a MP4 si es necesario
    if (( file_size < 1073741824 )); then
      echo "Película < 1GB, no comprimida: $input"
      if [[ "$input" != "$final_output" ]]; then
        mv "$input" "$final_output"
      fi
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] SIN CAMBIO: $input" >> "$LOGFILE"
      continue
    fi

    echo "Comenzando compresión: $input → $temp_output"

    # Comprimir y dejar solo español (audio y subtítulos)
    if ffmpeg -hide_banner -vaapi_device /dev/dri/renderD128 \
      -i "$input" \
      -vf 'format=nv12,hwupload' \
      -map 0:v:0 \
      -map 0:a:m:language:spa? -map 0:a:0 \
      -map 0:s:m:language:spa? \
      -c:v h264_vaapi -qp 28 -preset fast \
      -c:a aac -b:a 128k -ac 2 \
      -c:s mov_text \
      -movflags +faststart \
      "$temp_output" < /dev/null; then

      mv "$input" "$input.bak"
      mv "$temp_output" "$final_output"
      rm "$input.bak"

      echo "Comprimida (solo español, audio/subs): $input → $final_output"
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] COMPRIMIDA: $input → $final_output" >> "$LOGFILE"
    else
      echo "Falló la compresión: $input"
      echo "$input" >> "$PENDING"
      rm -f "$temp_output"
    fi

  done < "$TEMP"

  rm -f "$TEMP"
  sort -u "$PENDING" -o "$PENDING"

) 9>"$LOCKFILE"
