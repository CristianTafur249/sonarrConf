#!/bin/bash

# MediaJelly Hybrid Docker Entrypoint
# Permite alternar entre versión Bash y Python

# Funciones para generar emojis
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
echo "$(get_calendar) $(date '+%Y-%m-%d %H:%M:%S')"

# Verificar que los directorios existen con permisos correctos
echo "$(get_gear) Configurando directorios y permisos..."
mkdir -p /mediajelly/scripts/logs /mediajelly/scripts/tmp
chmod 777 /mediajelly/scripts/logs /mediajelly/scripts/tmp

# Limpiar archivos de lock antiguos que puedan causar problemas
rm -f /mediajelly/scripts/tmp/*.lock

# Detectar modo de operación
MODE=${MEDIAJELLY_MODE:-python}
echo "$(get_gear) Modo de operación: $MODE"

if [ "$MODE" = "python" ]; then
    echo "$(get_snake) Configurando modo Python..."
    # Instalar crontab Python
    crontab -u mediauser /etc/cron.d/mediajelly-python
    echo "$(get_clipboard) Configuración de cron Python:"
    crontab -l -u mediauser
    echo "$(get_magnifying_glass) Verificando scripts Python..."
    ls -la /mediajelly/scripts/*.py
    # Dar permisos de ejecución a scripts Python
    chmod +x /mediajelly/scripts/*.py
    echo "$(get_test_tube) Probando imports Python..."
    python3 -c "import requests, psutil; print('$(get_check_mark) Dependencias Python OK')"

    # Bot interactivo de Telegram (long-polling, proceso de fondo de larga duración).
    # Es independiente del cron: solo responde consultas de estado, no procesa videos.
    if [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ]; then
        echo "$(get_rocket) Iniciando bot interactivo de Telegram en segundo plano..."
        cd /mediajelly/scripts
        su -s /bin/bash -c "python3 mediajelly_telegram_bot.py >> /mediajelly/scripts/tmp/logs/telegram_bot.log 2>&1 &" mediauser
    else
        echo "$(get_warning) TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID no configurados: bot interactivo deshabilitado"
    fi

else
    echo "$(get_scroll) Configurando modo Bash (por defecto)..."
    
    # Instalar crontab Bash
    crontab -u mediauser /etc/cron.d/mediajelly
    
    echo "$(get_clipboard) Configuración de cron Bash:"
    crontab -l -u mediauser
    
    echo "$(get_magnifying_glass) Verificando scripts Bash..."
    ls -la /mediajelly/scripts/*.sh
    
    # Dar permisos de ejecución a scripts Bash
    chmod +x /mediajelly/scripts/*.sh
fi

# Verificar hardware acceleration
echo "$(get_clapper) Verificando aceleración por hardware..."
if [ -e /dev/dri/renderD128 ]; then
    echo "$(get_check_mark) Hardware acceleration disponible"
    ls -la /dev/dri/
else
    echo "$(get_warning) Hardware acceleration no disponible"
fi

echo "$(get_alarm_clock) Iniciando servicio cron..."
exec cron -f