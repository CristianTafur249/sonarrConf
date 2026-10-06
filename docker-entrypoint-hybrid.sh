#!/bin/bash

# MediaJelly Hybrid Docker Entrypoint
# Permite alternar entre versión Bash y Python

get_rocket() { echo "🚀"; }
get_calendar() { echo "📅"; }
get_gear() { echo "🔧"; }
get_snake() { echo "🐍"; }
get_cross_mark() { echo "❌"; }
get_scroll() { echo "📜"; }
get_clapper() { echo "🎬"; }
get_check_mark() { echo "✅"; }
get_warning() { echo "⚠️"; }
get_alarm_clock() { echo "⏰"; }
get_test_tube() { echo "🧪"; }
get_clipboard() { echo "📋"; }
get_magnifying_glass() { echo "🔍"; }

echo "$(get_rocket) Iniciando MediaJelly Hybrid Service..."

# Aplicar TZ al sistema: cron lanza los jobs con entorno limpio (sin TZ), así que
# sin esto los scripts verían la zona fijada en build (/etc/localtime -> UTC).
if [ -n "${TZ:-}" ] && [ -f "/usr/share/zoneinfo/${TZ}" ]; then
    ln -snf "/usr/share/zoneinfo/${TZ}" /etc/localtime
    echo "${TZ}" > /etc/timezone
else
    echo "$(get_warning) TZ='${TZ:-}' no válida o vacía: se mantiene $(cat /etc/timezone 2>/dev/null)"
fi

echo "$(get_calendar) $(date '+%Y-%m-%d %H:%M:%S %Z')"

# Ajustar propiedad del volumen en tiempo de ejecución al UID/GID especificado
chown -R ${PUID:-1000}:${PGID:-1000} /mediajelly/scripts /mediajelly/config 2>/dev/null || true

# Dar acceso a la GPU al usuario del pipeline: cron ejecuta los jobs como mediauser,
# que sin pertenecer al grupo dueño de /dev/dri/renderD128 no puede usar VAAPI y
# toda compresión cae al fallback por CPU.
for dri_device in /dev/dri/renderD128 /dev/dri/card*; do
    [ -e "$dri_device" ] || continue
    dri_gid=$(stat -c '%g' "$dri_device")
    [ "$dri_gid" = "0" ] && continue
    dri_group=$(getent group "$dri_gid" | cut -d: -f1)
    if [ -z "$dri_group" ]; then
        dri_group="hostdri${dri_gid}"
        groupadd -g "$dri_gid" "$dri_group" 2>/dev/null || true
    fi
    usermod -aG "$dri_group" mediauser 2>/dev/null || true
done

echo "$(get_gear) Configurando directorios y permisos..."
mkdir -p /mediajelly/scripts/logs /mediajelly/scripts/tmp /mediajelly/scripts/tmp/logs
chmod 777 /mediajelly/scripts/logs /mediajelly/scripts/tmp /mediajelly/scripts/tmp/logs

touch /mediajelly/scripts/tmp/logs/cron.log 2>/dev/null || true

if [ -f /mediajelly/config/logrotate-mediajelly ]; then
    cp /mediajelly/config/logrotate-mediajelly /etc/logrotate.d/mediajelly
    chmod 644 /etc/logrotate.d/mediajelly
    if command -v logrotate >/dev/null 2>&1; then
        logrotate -d /etc/logrotate.d/mediajelly 2>/dev/null || true
    fi
fi

# Limpiar archivos de lock antiguos
rm -f /mediajelly/scripts/tmp/*.lock

# Detectar modo de operación
MODE=${MEDIAJELLY_MODE:-python}
echo "$(get_gear) Modo de operación: $MODE"

if [ "$MODE" = "python" ]; then
    echo "$(get_snake) Configurando modo Python..."
    # Instalar crontab desde el sistema
    cp /etc/cron.d/mediajelly-python /etc/cron.d/mediajelly-active
    chmod 644 /etc/cron.d/mediajelly-active

    echo "$(get_magnifying_glass) Verificando scripts Python..."
    ls -la /mediajelly/scripts/*.py
    chmod +x /mediajelly/scripts/*.py

    echo "$(get_test_tube) Probando imports Python..."
    python3 -c "import requests, psutil; print('$(get_check_mark) Dependencias Python OK')"

    # Bot interactivo de Telegram (ejecutado con gosu sin interactividad de contraseña)
    if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
        echo "$(get_rocket) Iniciando bot interactivo de Telegram en segundo plano..."
        # Control y watchdog: /mediajelly/scripts/telegram_bot_ctl.sh {start|stop|restart|status}
        /mediajelly/scripts/telegram_bot_ctl.sh start
    else
        echo "$(get_warning) TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID no configurados: bot interactivo deshabilitado"
    fi

else
    echo "$(get_scroll) Configurando modo Bash..."
    cp /etc/cron.d/mediajelly /etc/cron.d/mediajelly-active
    chmod 644 /etc/cron.d/mediajelly-active

    echo "$(get_magnifying_glass) Verificando scripts Bash..."
    ls -la /mediajelly/scripts/*.sh
    chmod +x /mediajelly/scripts/*.sh
fi

echo "$(get_clapper) Verificando aceleración por hardware..."
if [ -e /dev/dri/renderD128 ]; then
    echo "$(get_check_mark) Hardware acceleration disponible"
    ls -la /dev/dri/
else
    echo "$(get_warning) Hardware acceleration no disponible"
fi

echo "$(get_alarm_clock) Iniciando servicio cron..."
exec cron -f
