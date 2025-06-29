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

    name=$(echo "$file_name" | sed -E 's/\.[^.]+$//' | sed 's/[._]/ /g' | sed 's/\s\+/ /g' | sed 's/ *$//')
    title=$(echo "$name" | sed -E 's/([12][0-9]{3}).*//')
    year=$(echo "$name" | grep -oE '[12][0-9]{3}' | head -n1)

    # Tags
    quality=$(echo "$file_name" | grep -oEi "(2160p|1080p|720p|480p)" | head -n1)
    source=$(echo "$file_name" | grep -oEi "(WEB[-\.]?DL|BluRay|HDTV|DVDRip)" | head -n1 | sed 's/[-.]/-/g')
    language=$(echo "$file_name" | grep -oEi "(Dual[-\.]?Lat|Sub[-\.]?Esp|Latino|Español)" | head -n1 | sed 's/[-.]/-/g')

    extra_tags=""
    [[ -n "$source" ]] && extra_tags+="$source "
    [[ -n "$quality" ]] && extra_tags+="$quality "
    [[ -n "$language" ]] && extra_tags+="$language"
    extra_tags=$(echo "$extra_tags" | sed 's/ *$//')

    title_clean=$(echo "$title" | awk '{for(i=1;i<=NF;i++) $i=toupper(substr($i,1,1)) tolower(substr($i,2)); print}' | sed 's/ *$//')

    new_name="$title_clean"
    [[ -n "$year" ]] && new_name+=" ($year)"
    [[ -n "$extra_tags" ]] && new_name+=" [$extra_tags]"
    new_name=$(echo "$new_name" | sed 's/ *$//').mkv

    output="$dir_path/$new_name"
    file_size=$(stat -c %s "$input")

    if (( file_size < 1073741824 )); then
      echo "Película < 1GB, solo movida: $input → $output"
      mv "$input" "$output"
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] MOVIDA: $input → $output" >> "$LOGFILE"
      continue
    fi

    echo "Comenzando compresión: $input → $output"

    if /usr/bin/ffmpeg -i "$input" -vcodec libx264 -crf 24 -preset veryfast -acodec copy "$output" < /dev/null; then
      rm "$input"
      echo "Comprimida y movida: $input → $output"
      echo "[$(date '+%Y-%m-%d %H:%M:%S')] COMPRIMIDA: $input → $output" >> "$LOGFILE"
    else
      echo "Falló la compresión: $input"
      echo "$input" >> "$PENDING"
      rm -f "$output"
    fi

  done < "$TEMP"

  rm -f "$TEMP"
  sort -u "$PENDING" -o "$PENDING"

) 9>"$LOCKFILE"
