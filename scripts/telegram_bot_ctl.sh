#!/bin/bash
# Control del bot interactivo de Telegram sin reiniciar el contenedor.
#
# Uso (desde el host):
#   docker exec mediajelly-cron /mediajelly/scripts/telegram_bot_ctl.sh {start|stop|restart|status|ensure}
#
#   start    Arranca el bot si no está corriendo (y quita el flag de "detenido").
#   stop     Lo detiene y marca que NO debe relanzarse automáticamente.
#   restart  stop + start.
#   status   Muestra si está vivo, su PID y las últimas líneas del log.
#   ensure   Lo arranca solo si debería estar corriendo (watchdog de cron).

SCRIPTS_DIR="$(cd "$(dirname "$0")" && pwd)"
TMP_DIR="$SCRIPTS_DIR/tmp"
LOG_FILE="$TMP_DIR/logs/telegram_bot.log"
PID_FILE="$TMP_DIR/telegram_bot.pid"
DISABLED_FLAG="$TMP_DIR/telegram_bot.disabled"
BOT_CMD="python3 mediajelly_telegram_bot.py"
RUN_AS="mediauser"

mkdir -p "$TMP_DIR/logs"

bot_pid() {
    # PID del bot: primero el pidfile, si no, búsqueda por línea de comandos
    # (cubre un bot lanzado por una versión anterior del entrypoint).
    if [ -f "$PID_FILE" ]; then
        local pid
        pid="$(cat "$PID_FILE" 2>/dev/null)"
        # Tras reiniciar el contenedor el PID guardado puede pertenecer a otro proceso.
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null \
            && tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null | grep -q "mediajelly_telegram_bot.py"; then
            echo "$pid"
            return 0
        fi
    fi
    pgrep -f "^$BOT_CMD" | head -n 1
}

do_start() {
    rm -f "$DISABLED_FLAG"
    local pid
    pid="$(bot_pid)"
    if [ -n "$pid" ]; then
        echo "El bot ya está corriendo (PID $pid)."
        return 0
    fi
    if [ -z "${TELEGRAM_BOT_TOKEN:-}" ] || [ -z "${TELEGRAM_CHAT_ID:-}" ]; then
        # docker exec no hereda el entorno de PID 1 si se llama desde cron.
        if [ -r /proc/1/environ ]; then
            eval "$(tr '\0' '\n' < /proc/1/environ | grep -E '^(TELEGRAM_[A-Z_]+|TZ|PYTHONUNBUFFERED)=' | sed 's/^/export /; s/=\(.*\)$/="\1"/')"
        fi
    fi

    cd "$SCRIPTS_DIR" || exit 1
    echo "$(date '+%F %T') [CTL] Iniciando bot de Telegram..." >> "$LOG_FILE"
    if [ "$(id -u)" = "0" ] && id "$RUN_AS" >/dev/null 2>&1; then
        nohup gosu "$RUN_AS" $BOT_CMD >> "$LOG_FILE" 2>&1 &
    else
        nohup $BOT_CMD >> "$LOG_FILE" 2>&1 &
    fi
    echo $! > "$PID_FILE"
    sleep 2
    if kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
        echo "Bot iniciado (PID $(cat "$PID_FILE"))."
    else
        echo "El bot terminó al arrancar. Revisa: tail -50 $LOG_FILE"
        return 1
    fi
}

do_stop() {
    touch "$DISABLED_FLAG"
    local pid
    pid="$(bot_pid)"
    if [ -z "$pid" ]; then
        echo "El bot no está corriendo."
        rm -f "$PID_FILE"
        return 0
    fi
    echo "$(date '+%F %T') [CTL] Deteniendo bot (PID $pid)..." >> "$LOG_FILE"
    kill -TERM "$pid" 2>/dev/null
    for _ in $(seq 1 10); do
        kill -0 "$pid" 2>/dev/null || break
        sleep 1
    done
    if kill -0 "$pid" 2>/dev/null; then
        kill -KILL "$pid" 2>/dev/null
        echo "El bot no respondió a SIGTERM; forzado con SIGKILL."
    fi
    rm -f "$PID_FILE"
    echo "Bot detenido."
}

do_status() {
    local pid
    pid="$(bot_pid)"
    if [ -n "$pid" ]; then
        echo "Estado: CORRIENDO (PID $pid, desde hace $(ps -o etime= -p "$pid" | tr -d ' '))"
    elif [ -f "$DISABLED_FLAG" ]; then
        echo "Estado: DETENIDO manualmente (usa 'start' para prenderlo)"
    else
        echo "Estado: CAÍDO (el watchdog lo relanzará en ≤1 min, o usa 'start')"
    fi
    echo "--- Últimas líneas del log ---"
    tail -n 5 "$LOG_FILE" 2>/dev/null
}

case "${1:-}" in
    start)   do_start ;;
    stop)    do_stop ;;
    restart) do_stop; do_start ;;
    status)  do_status ;;
    ensure)
        if [ ! -f "$DISABLED_FLAG" ] && [ -z "$(bot_pid)" ]; then
            echo "$(date '+%F %T') [CTL] Watchdog: el bot no estaba corriendo, relanzando." >> "$LOG_FILE"
            do_start
        fi
        ;;
    *)
        echo "Uso: $0 {start|stop|restart|status|ensure}"
        exit 1
        ;;
esac
