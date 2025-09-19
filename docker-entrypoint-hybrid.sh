#!/bin/bash

# MediaJelly Hybrid Docker Entrypoint
# Permite alternar entre versión Bash y Python

echo "🚀 Iniciando MediaJelly Hybrid Service..."
echo "📅 $(date '+%Y-%m-%d %H:%M:%S')"

# Verificar que los directorios existen
mkdir -p /mediajelly/.scripts/logs /mediajelly/.scripts/tmp

# Detectar modo de operación
MODE=${MEDIAJELLY_MODE:-bash}
echo "🔧 Modo de operación: $MODE"

if [ "$MODE" = "python" ]; then
    echo "🐍 Configurando modo Python..."
    
    # Instalar crontab Python
    crontab -u mediauser /etc/cron.d/mediajelly-python
    
    echo "📋 Configuración de cron Python:"
    crontab -l -u mediauser
    
    echo "🔍 Verificando scripts Python..."
    ls -la /mediajelly/.scripts/*.py
    
    # Dar permisos de ejecución a scripts Python
    chmod +x /mediajelly/.scripts/*.py
    
    echo "🧪 Probando imports Python..."
    python3 -c "import requests, psutil; print('✅ Dependencias Python OK')"
    
else
    echo "📜 Configurando modo Bash (por defecto)..."
    
    # Instalar crontab Bash
    crontab -u mediauser /etc/cron.d/mediajelly
    
    echo "📋 Configuración de cron Bash:"
    crontab -l -u mediauser
    
    echo "🔍 Verificando scripts Bash..."
    ls -la /mediajelly/.scripts/*.sh
    
    # Dar permisos de ejecución a scripts Bash
    chmod +x /mediajelly/.scripts/*.sh
fi

# Verificar hardware acceleration
echo "🎬 Verificando aceleración por hardware..."
if [ -e /dev/dri/renderD128 ]; then
    echo "✅ Hardware acceleration disponible"
    ls -la /dev/dri/
else
    echo "⚠️ Hardware acceleration no disponible"
fi

echo "⏰ Iniciando servicio cron..."
exec cron -f