#!/usr/bin/env bash
# Aplica los commits descritos en COMMITS_PLAN.md, en orden.
#
# USO:
#   1. (Opcional) Rellena D01..D10 abajo con tus fechas reales,
#      formato "YYYY-MM-DD HH:MM:SS" (hora local). Deja "" para usar
#      la fecha/hora real de cuando corras el script.
#   2. bash apply_commits.sh
#
# El script NO hace push. Revisa con `git log` al final y decide tú
# cuándo subir. Se detiene ante cualquier error (set -e).

set -euo pipefail
cd "$(dirname "$0")"

# --- Fechas (opcional, de tu libreta de notas) -----------------------------
D01="2026-07-26 09:23"   # fix(processor): evitar fallos de compresión por memoria insuficiente...
D02="2026-07-30 14:47"   # fix(telegram_bot): migrar a Telethon...
D03="2026-08-05 11:02"   # feat(config): soportar contraseña de Redis y credenciales de Telethon
D04="2026-08-10 18:35"   # chore(docker-compose): habilitar autenticación de Redis...
D05="2026-08-16 21:15"   # fix(logging): usar RotatingFileHandler estándar...
D06="2026-08-22 08:50"   # fix(cron_runner): registrar PID del lock existente...
D07="2026-08-27 16:10"   # chore(docker): mejorar entrypoint...
D08="2026-09-01 12:40"   # chore(deps): simplificar requirements.txt...
D09="2026-09-03 22:55"   # test: reemplazar test_pending.py por suite pytest
D10="2026-09-05 10:05"   # fix(subtitle_translator, whisper_extractor): guardas de disponibilidad...

# --- Helper: hace commit con fecha opcional --------------------------------
do_commit() {
    local date_var="$1"
    local message="$2"
    shift 2
    local files=("$@")

    git add -- "${files[@]}"

    if [ -z "$date_var" ]; then
        git commit -m "$message"
    else
        GIT_COMMITTER_DATE="$date_var" git commit --date="$date_var" -m "$message"
    fi
}

echo "== 1/10 =="
do_commit "$D01" "$(cat <<'EOF'
fix(processor): evitar fallos de compresión por memoria insuficiente y ajustar límites de recursos

- Control de memoria/disco previo antes de lanzar ffmpeg: si no hay margen
  suficiente (host y cgroup del contenedor), el archivo se pospone en vez de
  intentarlo y fallar por SIGKILL/OOM.
- La cuarentena de reintentos (3 intentos / 24h) ya no cuenta fallos por
  falta de recursos (SIGKILL, OOM, timeout) como intento fallido; solo
  archivos con error real de contenido quedan en cuarentena.
- Detección de idioma dentro del worker de compresión ya no carga Whisper
  (evita sumar presión de memoria al mismo proceso que ya corre ffmpeg); el
  paso dedicado de detección de idioma sigue poblando el caché aparte.
- get_optimal_workers() ahora lee el cupo real de CPU/memoria del cgroup del
  contenedor en vez de solo los recursos del host completo.
- -threads acotado en los comandos ffmpeg (GPU, fallback CPU, sin subtítulos)
  para no competir sin control por más cores de los que el contenedor tiene
  asignados.
- Ajustes de límites de memoria para contenido UHD/10-bit.
- Corregido un bug donde MediaJellyProcessor.setup_logging() borraba todos
  los handlers del logger raíz de Python, dejando sin efecto el logging de
  mediajelly_cron_runner.py (cron_runner.log quedaba casi vacío en cada
  corrida porque MediaJellyProcessor se instancia en el mismo proceso).
- Limpieza menor de logging en PendingFilesMixin.

EOF
)" scripts/mediajelly_processor.py scripts/mediajelly_language_detection.py scripts/mediajelly_pending_files.py

echo "== 2/10 =="
do_commit "$D02" "$(cat <<'EOF'
fix(telegram_bot): migrar a Telethon con descarga/organización de archivos y corregir extracción de título

- Reescritura del bot sobre Telethon (antes: polling HTTP simple) con cola
  de descargas persistida en SQLite, sesiones de usuario y auto-organización
  de series/anime comparando el título extraído contra carpetas existentes.
- Corregido un bug real de extracción de título: cuando una palabra de
  relleno ("Episodio", "Capitulo", "Ep", ...) queda pegada justo antes del
  marcador de temporada/episodio (ej. "Cowboy Bebop Episodio 1x16"), el
  algoritmo la dejaba pegada al título extraído ("Cowboy Bebop Episodio"),
  lo que rompía el auto-organize por similitud contra la carpeta real
  ("Cowboy Bebop") — confirmado en logs con decenas de fallos repetidos de
  auto-organización para el mismo título.
- .gitignore: ignorar archivos de sesión de Telethon.

EOF
)" scripts/mediajelly_telegram_bot.py scripts/mediajelly_db.py .gitignore

echo "== 3/10 =="
do_commit "$D03" "$(cat <<'EOF'
feat(config): soportar contraseña de Redis y credenciales de Telethon

EOF
)" scripts/mediajelly_config.py scripts/mediajelly_language_detector.py

echo "== 4/10 =="
do_commit "$D04" "$(cat <<'EOF'
chore(docker-compose): habilitar autenticación de Redis y reequilibrar memoria/CPU

- REDIS_PASSWORD para mediajelly-cron y redis (--requirepass).
- Servicio jellyseerr comentado (no usado actualmente).
- mediajelly-cron: 3g/2.0 cpus -> 4g/3.0 cpus.
- transmission/radarr/sonarr/prowlarr/bazarr recortados a límites más
  realistas para su uso real, dejando margen de memoria real en el host sin
  tocar Jellyfin ni mediajelly-cron.

EOF
)" docker-compose.yaml

echo "== 5/10 =="
do_commit "$D05" "$(cat <<'EOF'
fix(logging): usar RotatingFileHandler estándar y truncar in-place para no perder logs

La rotación con gzip manual (namer/rotator personalizados) causaba rollovers
inconsistentes al mezclarse varios handlers sobre el mismo log o al
reutilizarse desde varios procesos. Se vuelve a la implementación nativa de
RotatingFileHandler.

archive_legacy_log_file() ahora trunca el archivo in-place en vez de
borrarlo y recrearlo: si algo externo al proceso Python (una redirección de
shell tipo '>> cron.log' en el entrypoint/cron del contenedor) mantiene el
archivo abierto, un unlink() no libera nada — ese escritor sigue escribiendo
para siempre en el inodo ya borrado, invisible por su ruta pero ocupando
disco. Truncar el mismo inodo sí es visible para cualquier escritor que ya
lo tenga abierto.

check_transmission_bot_commands.py y set_bot_commands.py: FileHandler simple
-> RotatingFileHandler con límite de tamaño.

EOF
)" scripts/mediajelly_utils.py scripts/check_transmission_bot_commands.py scripts/set_bot_commands.py

echo "== 6/10 =="
do_commit "$D06" "$(cat <<'EOF'
fix(cron_runner): registrar PID del lock existente y corregir ruta de cron.log legado

EOF
)" scripts/mediajelly_cron_runner.py

echo "== 7/10 =="
do_commit "$D07" "$(cat <<'EOF'
chore(docker): mejorar entrypoint (gosu, PUID/GID, logrotate) y documentar crontab legado

- Ajuste de propiedad de volúmenes al UID/GID configurado en tiempo de
  ejecución (chown en el entrypoint).
- Bot de Telegram lanzado con gosu en vez de 'su -c' (evita interactividad
  de contraseña).
- Integración con logrotate vía config/logrotate-mediajelly.
- Documentado por qué config/crontab.example no representa un riesgo de
  doble disparo de cron (está íntegramente comentado, no define jobs).

EOF
)" docker-entrypoint-hybrid.sh Dockerfile.hybrid config/crontab.example config/logrotate-mediajelly

echo "== 8/10 =="
do_commit "$D08" "$(cat <<'EOF'
chore(deps): simplificar requirements.txt y eliminar scripts obsoletos

Elimina requirements-dev.txt y scripts ya no referenciados desde el
pipeline activo (clean_duplicate_subtitles.py, fix_orphan_subtitles.py,
mediajelly_subtitle_translator_relaxed.py).

EOF
)" requirements.txt requirements-dev.txt scripts/clean_duplicate_subtitles.py scripts/fix_orphan_subtitles.py scripts/mediajelly_subtitle_translator_relaxed.py

echo "== 9/10 =="
do_commit "$D09" "$(cat <<'EOF'
test: reemplazar test_pending.py por suite pytest

EOF
)" tests/test_bot_persistence.py tests/test_log_rotation_and_language_fallback.py scripts/test_pending.py

echo "== 10/10 =="
do_commit "$D10" "$(cat <<'EOF'
fix(subtitle_translator, whisper_extractor): guardas de disponibilidad opcional y lock de Whisper

EOF
)" scripts/mediajelly_subtitle_translator.py scripts/mediajelly_whisper_extractor.py

echo
echo "Listo. Estado restante (debería ser solo lo excluido a propósito):"
git status --short
echo
echo "Revisa con: git log --oneline -10"
