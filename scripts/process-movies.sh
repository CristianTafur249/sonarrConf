#!/bin/bash

LOCKFILE="./scripts/tmp/compress-movies.lock"
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
    if ! [[ "$input" =~ ^/ ]] || ! [ -f "$input" ]; then
      echo "Línea inválida ignorada: '$input'" >&2
      continue
    fi

    dir_path="$(dirname "$input")"
    file_name="$(basename "$input")"
    base_name="${file_name%.*}"  # Sin extensión

    final_output="$dir_path/$base_name.mkv"
    temp_output="$dir_path/$base_name.compressed.mkv"

    file_size=$(stat -c %s "$input")

    if (( file_size < 1073741824 )); then
      echo "Película < 1GB, solo renombrada si es necesario: $input → $final_output"
      [[ "$input" != "$final_output" ]] && mv "$input" "$final_output"
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] MOVIDA: $input → $final_output" >> "$LOGFILE"
      continue
    fi

    echo "Comenzando compresión: $input → $temp_output"

    if /usr/bin/ffmpeg -i "$input" -vcodec libx264 -crf 24 -preset veryfast -acodec copy "$temp_output" < /dev/null; then
      rm "$input"
      mv "$temp_output" "$final_output"
      echo "Comprimida y movida: $input → $final_output"
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
