#!/bin/bash

# Script de instalación para MediaJelly
# Configura automáticamente el sistema de automatización

set -e

INSTALL_DIR="$(pwd)"
USER_HOME="$HOME"

echo "🚀 Instalando MediaJelly..."

# Verificar que estamos en el directorio correcto
if [ ! -f "docker-compose.yaml" ]; then
    echo "❌ Error: Ejecuta este script desde el directorio raíz de MediaJelly"
    exit 1
fi

# Crear directorios necesarios
echo "📁 Creando directorios..."
mkdir -p .scripts/logs .scripts/tmp media/Peliculas media/series config

# Dar permisos de ejecución a todos los scripts
echo "🔧 Configurando permisos..."
chmod +x .scripts/*.sh
chmod +x install.sh

# Crear archivo de configuración de Telegram si no existe
if [ ! -f "config/telegram.conf" ]; then
    echo "📱 Configurando Telegram..."
    cp config/telegram.conf.example config/telegram.conf
    echo "✏️  Edita config/telegram.conf con tus credenciales de Telegram"
fi

# Mostrar información sobre cron
echo ""
echo "⏰ Para configurar la ejecución automática, añade esta línea a tu crontab:"
echo "   (ejecuta: crontab -e)"
echo ""
echo "0 */6 * * * $INSTALL_DIR/.scripts/cron-runner.sh"
echo ""

# Probar configuración básica
echo "🧪 Probando configuración..."
if ./.scripts/manage-logs.sh; then
    echo "✅ Gestión de logs funcionando"
else
    echo "❌ Error en gestión de logs"
fi

echo ""
echo "🎉 Instalación completada!"
echo ""
echo "📋 Próximos pasos:"
echo "1. Configura docker-compose.yaml con tus rutas específicas"
echo "2. Edita config/telegram.conf con tus credenciales"
echo "3. Inicia los servicios: docker compose up -d"
echo "4. Configura el cron job para automatización"
echo "5. Ejecuta manualmente: ./.scripts/scan-to-pending.sh /ruta/a/medios"
echo ""
echo "📖 Ver README.md para más información"
