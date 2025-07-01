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

    # Extraer nombre de serie
    serie_name=$(echo "$file_name" | sed -E "s/(S[0-9]{2}E[0-9]{2}|[0-9]{1,2}x[0-9]{2}).*$//" |
                 sed 's/[._]/ /g' | sed 's/ *$//' | sed 's/\s\+/ /g')

    # Capitalizar
    serie_name_capitalized=$(echo "$serie_name" |
      sed 's/-/ - /g' |
      awk '{for(i=1;i<=NF;i++) $i=toupper(substr($i,1,1)) tolower(substr($i,2)); print}' |
      sed 's/ - /-/g' |
      sed 's/ *$//')

    if [[ -z "$serie_name_capitalized" ]]; then
      echo "No se pudo detectar serie en: $input"
      echo "$input" >> "$PENDING"
      continue
    fi

    # Nombre del episodio
    episode_title=$(echo "$file_name" | sed -E "s/^.*$episode_code[ _.-]*//; s/\.[^.]+$//" |
                    sed 's/[._-]/ /g' | sed 's/\s\+/ /g' | sed 's/ *$//' | sed 's/^ *//')

    # Temporada
    season_num=$(echo "$episode_code" | grep -oE "S[0-9]{2}" | tr -d 'S' | sed 's/^0*//')
    [[ -z "$season_num" ]] && season_num="1"

    # Tags
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

    dir_name="$(dirname "$input")"
    temp_output="$dir_name/$(basename "$input" .mkv).compressing.mkv"
    final_output="$dir_name/$new_name"

    file_size=$(stat -c %s "$input")

    if (( file_size < 1073741824 )); then
      echo "Archivo < 1GB, solo renombrado y movido: $input → $final_output"
      mv "$input" "$final_output"
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] MOVIDO: $input → $final_output" >> "$LOGFILE"
      continue
    fi

    echo "Comenzando compresión: $input → $final_output"

    if /usr/bin/ffmpeg -i "$input" -vcodec libx264 -crf 24 -preset veryfast -acodec copy "$temp_output" < /dev/null; then
      mv "$input" "$input.bak"
      mv "$temp_output" "$final_output"
      rm "$input.bak"
      echo "Comprimido y renombrado: $input → $final_output"
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
