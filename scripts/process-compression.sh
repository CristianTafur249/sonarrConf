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
    if [[ ! "$input" =~ ^/ ]] || [[ ! -f "$input" ]]; then
      echo "Línea inválida ignorada: '$input'" >&2
      continue
    fi

    file_name="$(basename "$input")"

    episode_code=$(echo "$file_name" | grep -oEi "(S[0-9]{2}E[0-9]{2}|[0-9]{1,2}x[0-9]{2})")
    if [[ -z "$episode_code" ]]; then
      echo "No se pudo detectar episodio en: $input"
      echo "$input" >> "$PENDING"
      continue
    fi

    if [[ "$episode_code" =~ ^[0-9]{1,2}x[0-9]{2}$ ]]; then
      season=$(echo "$episode_code" | cut -d'x' -f1 | awk '{printf "S%02d", $1}')
      episode=$(echo "$episode_code" | cut -d'x' -f2 | awk '{printf "E%02d", $1}')
      episode_code="${season}${episode}"
    else
      episode_code=$(echo "$episode_code" | awk '{print toupper($0)}')
    fi

    serie_name=$(echo "$file_name" | sed -E "s/(S[0-9]{2}E[0-9]{2}|[0-9]{1,2}x[0-9]{2}).*$//" |
                 sed 's/[._]/ /g' | sed 's/ *$//' | sed 's/\s\+/ /g')

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

    episode_title=$(echo "$file_name" | sed -E "s/^.*$episode_code[ _.-]*//; s/\.[^.]+$//" |
                    sed 's/[._-]/ /g' | sed 's/\s\+/ /g' | sed 's/ *$//' | sed 's/^ *//')

    season_num=$(echo "$episode_code" | grep -oE "S[0-9]{2}" | tr -d 'S' | sed 's/^0*//')
    [[ -z "$season_num" ]] && season_num="1"

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

    original_dir="$(dirname "$input")"

    if [[ "$original_dir" =~ /media/series/.*/Season[[:space:]]*[0-9]+/?$ ]]; then
      final_dir="$original_dir"
      final_name="$file_name"
    else
      final_dir="./media/series/$serie_name_capitalized/Season $season_num"
      mkdir -p "$final_dir"
      final_name="$new_name"
    fi

    temp_output="$final_dir/$(basename "$final_name" .mkv).compressing.mkv"
    final_output="$final_dir/$final_name"

    file_size=$(stat -c %s "$input")

    if (( file_size < 1073741824 )); then
      echo "Archivo < 1GB, solo renombrado y movido: $input → $final_output"
      mv "$input" "$final_output"
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] MOVIDO: $input → $final_output" >> "$LOGFILE"
      continue
    fi

    echo "Comenzando compresión: $input → $final_output"

    if ffmpeg -hide_banner -vaapi_device /dev/dri/renderD128 \
      -i "$input" -vf 'format=nv12,hwupload' \
      -c:v h264_vaapi -qp 24 -preset fast -c:a copy "$temp_output" < /dev/null; then

      mv "$input" "$input.bak"
      mv "$temp_output" "$final_output"
      rm "$input.bak"

      find "$final_dir" -maxdepth 1 -type f \( -iname "*.nfo" -o -iname "*.txt" -o -iname "sample.*" \) -exec rm -f {} \;

      echo "Comprimido y limpiado: $input → $final_output"
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
