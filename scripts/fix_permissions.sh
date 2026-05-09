#!/bin/bash
# Script para corregir permisos en directorios críticos de MediaJelly
# Ejecutar con: sudo ./fix_permissions.sh

set -e

# Detectar el directorio base
if [ -d "/mediajelly" ]; then
    BASE_DIR="/mediajelly"
else
    BASE_DIR="/home/tafurc/mediaJelly"
fi

echo "=== MediaJelly - Corrección de Permisos ==="
echo "Directorio base: $BASE_DIR"

# Directorios críticos que necesitan permisos amplios
CRITICAL_DIRS=(
    "$BASE_DIR/scripts/tmp"
    "$BASE_DIR/scripts/tmp/logs"
)

echo ""
echo "Corrigiendo permisos de directorios críticos..."
for dir in "${CRITICAL_DIRS[@]}"; do
    if [ -d "$dir" ]; then
        echo "  - $dir"
        chmod 777 "$dir"
    else
        echo "  - Creando $dir"
        mkdir -p "$dir"
        chmod 777 "$dir"
    fi
done

# Limpiar archivos de lock antiguos que puedan tener permisos incorrectos
echo ""
echo "Limpiando archivos de lock antiguos..."
LOCK_FILES=(
    "$BASE_DIR/scripts/tmp/cron_python.lock"
    "$BASE_DIR/scripts/tmp/*.lock"
)

for lock_pattern in "${LOCK_FILES[@]}"; do
    for lock_file in $lock_pattern; do
        if [ -f "$lock_file" ]; then
            echo "  - Eliminando: $lock_file"
            rm -f "$lock_file"
        fi
    done
done

# Asegurar que los scripts Python sean ejecutables
echo ""
echo "Asegurando que los scripts Python sean ejecutables..."
PYTHON_SCRIPTS=(
    "$BASE_DIR/scripts/mediajelly_cron_runner.py"
    "$BASE_DIR/scripts/mediajelly_scanner.py"
    "$BASE_DIR/scripts/mediajelly_processor.py"
    "$BASE_DIR/scripts/mediajelly_notifier.py"
    "$BASE_DIR/scripts/mediajelly_subtitle_translator.py"
)

for script in "${PYTHON_SCRIPTS[@]}"; do
    if [ -f "$script" ]; then
        echo "  - $script"
        chmod +x "$script"
    fi
done

echo ""
echo "=== Corrección completada exitosamente ==="
echo ""
echo "Los siguientes directorios ahora tienen permisos 777:"
for dir in "${CRITICAL_DIRS[@]}"; do
    if [ -d "$dir" ]; then
        ls -ld "$dir"
    fi
done
