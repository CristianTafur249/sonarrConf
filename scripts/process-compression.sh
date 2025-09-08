#!/bin/bash

LOCKFILE="./scripts/tmp/compress.lock"
PENDING="./scripts/pending-compression.txt"
TEMP="./scripts/pending-compression.tmp"
LOGFILE="./scripts/logs/compression-success.log"
NO_SPANISH_LOG="./scripts/logs/no-spanish.log"

mkdir -p ./scripts/tmp
mkdir -p ./scripts/logs

(
  flock 9

  cp "$PENDING" "$TEMP" 2>/dev/null || exit 0
  > "$PENDING"

  while IFS= read -r input || [ -n "$input" ]; do
    # Validar ruta válida
    if [[ ! "$input" =~ ^/ ]] || [[ ! -f "$input" ]]; then
      echo "Línea inválida ignorada: '$input'" >&2
      continue
    fi

    # Saltar si el archivo ya es .mp4
    if [[ "$input" == *.mp4 ]]; then
      echo "Archivo ya es MP4, ignorado: $input"
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] YA ES MP4: $input" >> "$LOGFILE"
      continue
    fi

    dir_path="$(dirname "$input")"
    file_name="$(basename "$input")"
    base_name="${file_name%.*}"

    temp_output="$dir_path/$base_name.compressed.mp4"
    final_output="$dir_path/$base_name.mp4"

    file_size=$(stat -c %s "$input")

    # Si pesa menos de 500 MB, solo renombrar a .mp4
    if (( file_size < 524288000 )); then
      echo "Archivo < 500MB, renombrado a MP4: $input"
      mv "$input" "$final_output"
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] RENOMBRADO: $input → $final_output" >> "$LOGFILE"
      continue
    fi

    echo "Comenzando compresión: $input → $temp_output"

    # Obtener todos los idiomas de audio y subtítulos
    audio_languages=$(ffprobe -v error -select_streams a -show_entries stream_tags=language -of csv=p=0 "$input" 2>/dev/null | tr '\n' ',' | sed 's/,$//')
    sub_languages=$(ffprobe -v error -select_streams s -show_entries stream_tags=language -of csv=p=0 "$input" 2>/dev/null | tr '\n' ',' | sed 's/,$//')

    # Detectar todas las pistas de audio y subtítulos en español
    audio_map=()
    subs_map=()
    has_spanish=false

    # Buscar pistas de audio en español
    if ffprobe -v error -select_streams a -show_entries stream=index:stream_tags=language -of csv=p=0 "$input" | grep -i -E '(spa|esp|es|es-LA|es-ES)' > /dev/null; then
      audio_map=(-map 0:a:m:language:spa? -map 0:a:m:language:esp? -map 0:a:m:language:es? -map 0:a:m:language:es-LA? -map 0:a:m:language:es-ES?)
      has_spanish=true
    else
      # Si no hay audio en español, usar la primera pista de audio
      audio_map=(-map 0:a:0)
    fi

    # Buscar pistas de subtítulos en español
    if ffprobe -v error -select_streams s -show_entries stream=index:stream_tags=language -of csv=p=0 "$input" | grep -i -E '(spa|esp|es|es-LA|es-ES)' > /dev/null; then
      subs_map=(-map 0:s:m:language:spa? -map 0:s:m:language:esp? -map 0:s:m:language:es? -map 0:s:m:language:es-LA? -map 0:s:m:language:es-ES?)
      has_spanish=true
    fi

    # Registrar archivos sin español
    if [ "$has_spanish" = false ]; then
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] SIN ESPAÑOL: $input" >> "$NO_SPANISH_LOG"
      echo "   Idiomas de audio: $audio_languages" >> "$NO_SPANISH_LOG"
      echo "   Idiomas de subtítulos: $sub_languages" >> "$NO_SPANISH_LOG"
      echo "   ---" >> "$NO_SPANISH_LOG"
    fi

    # Comprimir y convertir a MP4
    if ffmpeg -hide_banner -vaapi_device /dev/dri/renderD128 \
      -i "$input" \
      -vf 'format=nv12,hwupload' \
      -map 0:v:0 \
      "${audio_map[@]}" \
      "${subs_map[@]}" \
      -c:v h264_vaapi -qp 28 \
      -c:a aac -b:a 128k -ac 2 \
      -c:s mov_text \
      -movflags +faststart \
      "$temp_output" < /dev/null; then

      # Reemplazar el original por la versión comprimida
      mv "$input" "$input.bak"
      mv "$temp_output" "$final_output"
      rm "$input.bak"

      echo "Comprimido: $input → $final_output"
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] COMPRIMIDO: $input → $final_output" >> "$LOGFILE"
    else
      echo "Falló la compresión: $input"
      echo "$input" >> "$PENDING"
      rm -f "$temp_output"
    fi

  done < "$TEMP"

  rm -f "$TEMP"
  sort -u "$PENDING" -o "$PENDING"

) 9>"$LOCKFILE"
