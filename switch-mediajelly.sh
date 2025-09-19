#!/bin/bash

# Script de control para alternar entre versiones Bash y Python de MediaJelly
# Uso: ./switch-mediajelly.sh [bash|python]

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Colores para output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Función para mostrar ayuda
show_help() {
    echo -e "${BLUE}MediaJelly Mode Switcher${NC}"
    echo -e "Uso: $0 [modo] [opciones]"
    echo ""
    echo "Modos disponibles:"
    echo -e "  ${GREEN}bash${NC}     - Usar scripts Bash (por defecto)"
    echo -e "  ${GREEN}python${NC}   - Usar scripts Python"
    echo -e "  ${GREEN}status${NC}   - Mostrar estado actual"
    echo ""
    echo "Opciones:"
    echo -e "  ${YELLOW}--rebuild${NC} - Reconstruir contenedor antes de iniciar"
    echo -e "  ${YELLOW}--logs${NC}    - Mostrar logs después del cambio"
    echo -e "  ${YELLOW}--help${NC}    - Mostrar esta ayuda"
    echo ""
    echo "Ejemplos:"
    echo "  $0 python --rebuild"
    echo "  $0 bash --logs"
    echo "  $0 status"
}

# Función para obtener el modo actual
get_current_mode() {
    if docker-compose ps -q mediajelly-cron >/dev/null 2>&1; then
        docker-compose exec mediajelly-cron printenv MEDIAJELLY_MODE 2>/dev/null || echo "bash"
    else
        grep "MEDIAJELLY_MODE=" docker-compose.yaml | sed 's/.*MEDIAJELLY_MODE=//' | sed 's/#.*//' | tr -d ' '
    fi
}

# Función para mostrar estado
show_status() {
    echo -e "${BLUE}=== Estado de MediaJelly ===${NC}"
    
    current_mode=$(get_current_mode)
    echo -e "Modo actual: ${GREEN}$current_mode${NC}"
    
    # Verificar si el contenedor está corriendo
    if docker-compose ps -q mediajelly-cron >/dev/null 2>&1; then
        container_status=$(docker-compose ps mediajelly-cron | grep -v "Name" | awk '{print $3}' | head -1)
        if [[ -z "$container_status" ]]; then
            container_status="unknown"
        fi
        echo -e "Estado del contenedor: ${GREEN}$container_status${NC}"
        
        # Mostrar cron jobs activos
        echo -e "\n${YELLOW}Cron jobs activos:${NC}"
        docker-compose exec mediajelly-cron crontab -l -u mediauser 2>/dev/null || echo "No se pudo obtener crontab"
        
        # Mostrar últimos logs
        echo -e "\n${YELLOW}Últimos logs (últimas 5 líneas):${NC}"
        docker-compose logs --tail=5 mediajelly-cron
    else
        echo -e "Estado del contenedor: ${RED}detenido${NC}"
    fi
}

# Función para cambiar modo
switch_mode() {
    local new_mode="$1"
    local rebuild="$2"
    local show_logs="$3"
    
    echo -e "${BLUE}Cambiando MediaJelly a modo: ${GREEN}$new_mode${NC}"
    
    # Verificar modo válido
    if [[ "$new_mode" != "bash" && "$new_mode" != "python" ]]; then
        echo -e "${RED}Error: Modo '$new_mode' no válido. Use 'bash' o 'python'.${NC}"
        exit 1
    fi
    
    # Actualizar docker-compose.yaml
    echo -e "${YELLOW}Actualizando configuración...${NC}"
    sed -i "s/MEDIAJELLY_MODE=.*/MEDIAJELLY_MODE=$new_mode  # Cambiar a \"python\" para usar la versión Python/" docker-compose.yaml
    
    # Detener contenedor actual
    echo -e "${YELLOW}Deteniendo contenedor actual...${NC}"
    docker-compose stop mediajelly-cron 2>/dev/null || true
    
    # Reconstruir si se solicita
    if [[ "$rebuild" == "true" ]]; then
        echo -e "${YELLOW}Reconstruyendo contenedor...${NC}"
        docker-compose build --no-cache mediajelly-cron
    fi
    
    # Iniciar con nuevo modo
    echo -e "${YELLOW}Iniciando contenedor en modo $new_mode...${NC}"
    docker-compose up -d mediajelly-cron
    
    # Esperar a que el contenedor esté listo
    echo -e "${YELLOW}Esperando a que el servicio esté listo...${NC}"
    sleep 5
    
    # Verificar que el cambio fue exitoso
    echo -e "${YELLOW}Verificando cambio...${NC}"
    actual_mode=$(get_current_mode)
    if [[ "$actual_mode" == "$new_mode" ]]; then
        echo -e "${GREEN}✅ Cambio exitoso a modo $new_mode${NC}"
    else
        echo -e "${RED}❌ Error: El modo actual es '$actual_mode', esperado '$new_mode'${NC}"
        exit 1
    fi
    
    # Mostrar logs si se solicita
    if [[ "$show_logs" == "true" ]]; then
        echo -e "\n${YELLOW}Logs del contenedor:${NC}"
        docker-compose logs --tail=20 mediajelly-cron
    fi
    
    echo -e "\n${GREEN}Cambio completado. Use '$0 status' para ver el estado.${NC}"
}

# Procesar argumentos
MODE=""
REBUILD=false
SHOW_LOGS=false

while [[ $# -gt 0 ]]; do
    case $1 in
        bash|python|status)
            MODE="$1"
            shift
            ;;
        --rebuild)
            REBUILD=true
            shift
            ;;
        --logs)
            SHOW_LOGS=true
            shift
            ;;
        --help|-h)
            show_help
            exit 0
            ;;
        *)
            echo -e "${RED}Opción desconocida: $1${NC}"
            show_help
            exit 1
            ;;
    esac
done

# Ejecutar acción
case "$MODE" in
    status)
        show_status
        ;;
    bash|python)
        switch_mode "$MODE" "$REBUILD" "$SHOW_LOGS"
        ;;
    "")
        echo -e "${YELLOW}No se especificó modo. Mostrando estado actual:${NC}\n"
        show_status
        echo ""
        show_help
        ;;
    *)
        echo -e "${RED}Modo no reconocido: $MODE${NC}"
        show_help
        exit 1
        ;;
esac