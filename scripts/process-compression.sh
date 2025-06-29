#!/bin/bash

LOCKFILE="./scripts/tmp/compress.lock"
PENDING="./scripts/pending-compression.txt"
TEMP="./scripts/pending-compression.tmp"
LOGFILE="./scripts/logs/compression-success.log"

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

    file_name="$(basename "$input")"

    # Detectar código de episodio
    episode_code=$(echo "$file_name" | grep -oEi "(S[0-9]{2}E[0-9]{2}|[0-9]{1,2}x[0-9]{2})")
    if [[ -z "$episode_code" ]]; then
      echo "No se pudo detectar episodio en: $input"
      echo "$input" >> "$PENDING"
      continue
    fi

    # Normalizar código (ej. 1x07 → S01E07)
    if [[ "$episode_code" =~ ^[0-9]{1,2}x[0-9]{2}$ ]]; then
      season=$(echo "$episode_code" | cut -d'x' -f1 | awk '{printf "S%02d", $1}')
      episode=$(echo "$episode_code" | cut -d'x' -f2 | awk '{printf "E%02d", $1}')
      episode_code="${season}${episode}"
    else
      episode_code=$(echo "$episode_code" | awk '{print toupper($0)}')
    fi

    # Extraer nombre de serie (hasta antes del código del episodio) — NO reemplazar guiones
    serie_name=$(echo "$file_name" | sed -E "s/(S[0-9]{2}E[0-9]{2}|[0-9]{1,2}x[0-9]{2}).*$//" |
                 sed 's/[._]/ /g' | sed 's/ *$//' | sed 's/\s\+/ /g')

    # Capitalizar pero sin alterar los guiones
    serie_name_capitalized=$(echo "$serie_name" |
      awk 'BEGIN{OFS=FS="-"} {for(i=1;i<=NF;i++){gsub(/(^|\s)([a-z])/, "\\1\\U\\2", $i)}; print}' |
      sed 's/^\s*//;s/\s*$//')

    if [[ -z "$serie_name_capitalized" ]]; then
      echo "No se pudo detectar serie en: $input"
      echo "$input" >> "$PENDING"
      continue
    fi

    # Detectar nombre del episodio si está
    episode_title=$(echo "$file_name" | sed -E "s/^.*$episode_code[ _.-]*//; s/\.[^.]+$//" |
                    sed 's/[._-]/ /g' | sed 's/\s\+/ /g' | sed 's/ *$//' | sed 's/^ *//')

    # Detectar temporada
    season_num=$(echo "$episode_code" | grep -oE "S[0-9]{2}" | tr -d 'S' | sed 's/^0*//')
    [[ -z "$season_num" ]] && season_num="1"
    season_dir="Season $season_num"

    # Tags extra (calidad, fuente, idioma)
    quality=$(echo "$file_name" | grep -oEi "(2160p|1080p|720p|480p)" | head -n1)
    source=$(echo "$file_name" | grep -oEi "(WEB[-\.]?DL|BluRay|HDTV|DVDRip)" | head -n1 | sed 's/[-.]/-/g')
    language=$(echo "$file_name" | grep -oEi "(Dual[-\.]?Lat|Sub[-\.]?Esp|Latino|Español)" | head -n1 | sed 's/[-.]/-/g')

    extra_tags=""
    [[ -n "$source" ]] && extra_tags+="$source "
    [[ -n "$quality" ]] && extra_tags+="$quality "
    [[ -n "$language" ]] && extra_tags+="$language"
    extra_tags=$(echo "$extra_tags" | sed 's/ *$//')

    year=$(echo "$file_name" | grep -oE "[12][0-9]{3}" | head -n1)

    new_name="$serie_name_capitalized $episode_code"
    [[ -n "$episode_title" ]] && new_name+=" - $episode_title"
    [[ -n "$year" ]] && new_name+=" $year"
    [[ -n "$extra_tags" ]] && new_name+=" $extra_tags"
    new_name=$(echo "$new_name" | sed 's/ *$//').mkv

    relative_dir="media/series/$serie_name_capitalized/$season_dir"
    mkdir -p "$relative_dir"
    output="$relative_dir/$new_name"

    file_size=$(stat -c %s "$input")

    if (( file_size < 1073741824 )); then
      echo "Archivo < 1GB, solo renombrado y movido: $input → $output"
      mv "$input" "$output"
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] MOVIDO: $input → $output" >> "$LOGFILE"
      continue
    fi

    echo "Comenzando compresión: $input → $output"

    if /usr/bin/ffmpeg -i "$input" -vcodec libx264 -crf 24 -preset veryfast -acodec copy "$output" < /dev/null; then
      rm "$input"
      echo "Comprimido, renombrado y eliminado original: $input → $output"
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] COMPRIMIDO: $input → $output" >> "$LOGFILE"
    else
      echo "Falló la compresión: $input"
      echo "$input" >> "$PENDING"
      rm -f "$output"
    fi

  done < "$TEMP"

  rm -f "$TEMP"
  sort -u "$PENDING" -o "$PENDING"

) 9>"$LOCKFILE"
