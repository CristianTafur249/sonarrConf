#!/bin/bash

LOCKFILE="./.scripts/tmp/compress.lock"
PENDING="./.scripts/pending-compression.txt"
TEMP="./.scripts/pending-compression.tmp"
LOGFILE="./.scripts/logs/compression-success.log"
NO_SPANISH_LOG="./.scripts/logs/no-spanish.log"
ERROR_LOG="./.scripts/logs/compression-errors.log"
COMPLETED="./.scripts/completed.txt"

mkdir -p ./.scripts/tmp
mkdir -p ./.scripts/logs
touch "$COMPLETED"

(
  flock 9

  # Limpiar archivos .compressed.mp4 incompletos de ejecuciones anteriores
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Buscando archivos .compressed.mp4 incompletos..." >> "$LOGFILE"
  find /home/tafurc/mediaJelly/media -name "*.compressed.mp4" -type f | while read -r compressed_file; do
    original_file="${compressed_file%.compressed.mp4}"
    if [ -f "$original_file" ]; then
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] Eliminando archivo incompleto y reprocesando: $compressed_file" >> "$LOGFILE"
      rm -f "$compressed_file"
      echo "$original_file" >> "$PENDING"
    else
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] Archivo incompleto sin original, eliminando: $compressed_file" >> "$LOGFILE"
      rm -f "$compressed_file"
    fi
  done

  # Verificar si hay archivos pendientes
  if [ ! -s "$PENDING" ]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] No hay archivos pendientes para comprimir" >> "$LOGFILE"
    exit 0
  fi

  cp "$PENDING" "$TEMP" 2>/dev/null || exit 0
  > "$PENDING"

  files_processed=0
  files_compressed=0
  files_renamed=0
  files_skipped=0
  error_files=()
  
  while IFS= read -r input || [ -n "$input" ]; do
    # Validar ruta válida
    if [[ ! "$input" =~ ^/ ]] || [[ ! -f "$input" ]]; then
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: Línea inválida ignorada: '$input'" >> "$ERROR_LOG"
      continue
    fi

    files_processed=$((files_processed + 1))

    dir_path="$(dirname "$input")"
    file_name="$(basename "$input")"
    base_name="${file_name%.*}"

    temp_output="$dir_path/$base_name.compressed.mp4"
    final_output="$dir_path/$base_name.mp4"

    # Verificar si existe un archivo compressed.mp4 parcial y eliminarlo
    if [ -f "$temp_output" ]; then
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] ADVERTENCIA: Archivo temporal encontrado, eliminando: $temp_output" >> "$ERROR_LOG"
      rm -f "$temp_output"
    fi

    file_size=$(stat -c %s "$input")

    # Si pesa menos de 500 MB, solo renombrar a .mp4 (si no es ya .mp4)
    if (( file_size < 524288000 )); then
      if [[ "$input" == *.mp4 ]]; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] YA ES MP4 (<500MB): $input" >> "$LOGFILE"
        files_skipped=$((files_skipped + 1))
        echo "$(basename "$input")" >> "$COMPLETED"
        continue
      else
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] RENOMBRADO (<500MB): $input → $final_output" >> "$LOGFILE"
        mv "$input" "$final_output"
        files_renamed=$((files_renamed + 1))
        echo "$(basename "$final_output")" >> "$COMPLETED"
        continue
      fi
    fi

    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Comenzando compresión: $input → $temp_output" >> "$LOGFILE"

    # Verificar integridad del archivo antes de procesarlo
    if ! ffprobe -v error "$input" > /dev/null 2>&1; then
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: Archivo corrupto o inválido: $input" >> "$ERROR_LOG"
      echo "$input" >> "$PENDING"
      error_files+=("$input (archivo corrupto/inválido)")
      continue
    fi

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

    # Configuración de compresión más agresiva para películas grandes
    compression_settings=""
    if (( file_size > 2147483648 )); then  # > 2GB
      # Compresión más agresiva para archivos grandes
      compression_settings="-qp 32 -preset slow"
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] Archivo grande (>2GB), usando compresión agresiva" >> "$LOGFILE"
    elif (( file_size > 1073741824 )); then  # > 1GB
      compression_settings="-qp 30 -preset medium"
    else
      compression_settings="-qp 28 -preset fast"
    fi

    # Comprimir y convertir a MP4 con timeout de 2 horas por archivo
    if timeout 7200 ffmpeg -hide_banner -vaapi_device /dev/dri/renderD128 \
      -i "$input" \
      -vf 'format=nv12,hwupload' \
      -map 0:v:0 \
      "${audio_map[@]}" \
      "${subs_map[@]}" \
      -c:v h264_vaapi $compression_settings \
      -c:a aac -b:a 128k -ac 2 \
      -c:s mov_text \
      -movflags +faststart \
      "$temp_output" < /dev/null > /dev/null 2>&1; then

      # Verificar que el archivo comprimido sea válido
      if [ -f "$temp_output" ] && [ -s "$temp_output" ]; then
        # Verificar integridad del archivo comprimido
        if ffprobe -v error "$temp_output" > /dev/null 2>&1; then
          # Reemplazar el original por la versión comprimida
          mv "$input" "$input.bak"
          mv "$temp_output" "$final_output"
          rm "$input.bak"
          
          original_size_mb=$((file_size / 1024 / 1024))
          compressed_size=$(stat -c %s "$final_output")
          compressed_size_mb=$((compressed_size / 1024 / 1024))
          reduction_percent=$(( (file_size - compressed_size) * 100 / file_size ))
          
          echo "[$(date '+%Y-%m-%d %H:%M:%S')] COMPRIMIDO: $input → $final_output (${original_size_mb}MB → ${compressed_size_mb}MB, reducción: ${reduction_percent}%)" >> "$LOGFILE"
          files_compressed=$((files_compressed + 1))
          
          # Agregar a lista de completados
          echo "$(basename "$final_output")" >> "$COMPLETED"
        else
          echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: Archivo comprimido corrupto: $temp_output" >> "$ERROR_LOG"
          echo "$input" >> "$PENDING"
          error_files+=("$input (corrupto)")
          rm -f "$temp_output"
        fi
      else
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: Archivo comprimido vacío o inexistente: $temp_output" >> "$ERROR_LOG"
        echo "$input" >> "$PENDING"
        error_files+=("$input (vacío)")
        rm -f "$temp_output"
      fi
    else
      local ffmpeg_exit_code=$?
      if [ $ffmpeg_exit_code -eq 124 ]; then
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: Timeout alcanzado (2 horas) en compresión: $input" >> "$ERROR_LOG"
        echo "$input" >> "$PENDING"
        error_files+=("$input (timeout 2h)")
      else
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: Falló la compresión (código $ffmpeg_exit_code): $input" >> "$ERROR_LOG"
        echo "$input" >> "$PENDING"
        error_files+=("$input (falló compresión)")
      fi
      rm -f "$temp_output"
    fi

  done < "$TEMP"

  # Log de resumen
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] RESUMEN: Procesados: $files_processed, Comprimidos: $files_compressed, Renombrados: $files_renamed, Omitidos: $files_skipped" >> "$LOGFILE"

  # Exportar archivos con errores para notificaciones
  if [ ${#error_files[@]} -gt 0 ]; then
    printf '%s\n' "${error_files[@]}" > "/home/tafurc/mediaJelly/.scripts/tmp/error_files.tmp"
  else
    rm -f "/home/tafurc/mediaJelly/.scripts/tmp/error_files.tmp"
  fi

  rm -f "$TEMP"
  sort -u "$PENDING" -o "$PENDING"

  # Gestionar logs al final
  /home/tafurc/mediaJelly/.scripts/manage-logs.sh

) 9>"$LOCKFILE"
