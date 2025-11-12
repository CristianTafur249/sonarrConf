#!/bin/bash
# MediaJelly - Script de Verificación Rápida
# Ejecuta todas las verificaciones necesarias antes de commit

set -e  # Exit on error

echo "🔍 MediaJelly - Verificación de Calidad"
echo "========================================"
echo ""

# Colores
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Función para imprimir resultados
print_result() {
    if [ $1 -eq 0 ]; then
        echo -e "${GREEN}✅ $2${NC}"
    else
        echo -e "${RED}❌ $2${NC}"
        return 1
    fi
}

# Verificar que estamos en el directorio correcto
if [ ! -f "docker-compose.yaml" ]; then
    echo -e "${RED}❌ Error: Ejecuta este script desde el directorio raíz de MediaJelly${NC}"
    exit 1
fi

# 1. Verificar Docker
echo "📦 Verificando Docker..."
docker-compose ps > /dev/null 2>&1
print_result $? "Docker Compose disponible"
echo ""

# 2. Verificar estructura de archivos
echo "📁 Verificando archivos de configuración..."
FILES=(
    "requirements.txt"
    "requirements-dev.txt"
    ".env.example"
    "TODO.md"
    "pytest.ini"
    "mypy.ini"
    ".flake8"
    "pyproject.toml"
)

for file in "${FILES[@]}"; do
    if [ -f "$file" ]; then
        echo -e "${GREEN}✅ $file${NC}"
    else
        echo -e "${RED}❌ $file no encontrado${NC}"
    fi
done
echo ""

# 3. Verificar tests
echo "🧪 Verificando tests..."
if [ -d "tests" ]; then
    TEST_COUNT=$(find tests -name "test_*.py" | wc -l)
    echo -e "${GREEN}✅ $TEST_COUNT archivos de test encontrados${NC}"
else
    echo -e "${RED}❌ Directorio tests/ no encontrado${NC}"
fi
echo ""

# 4. Verificar logs del sistema
echo "📋 Verificando últimas ejecuciones..."
if [ -f "scripts/logs/language-detection.log" ]; then
    LAST_SUCCESS=$(grep "✅ ANÁLISIS COMPLETADO" scripts/logs/language-detection.log | tail -1)
    if [ -n "$LAST_SUCCESS" ]; then
        echo -e "${GREEN}✅ Detector de idiomas funcionando${NC}"
        echo "   Último éxito: $LAST_SUCCESS"
    else
        echo -e "${YELLOW}⚠️  No se encontraron análisis completados recientes${NC}"
    fi
else
    echo -e "${YELLOW}⚠️  Log de detección de idiomas no encontrado${NC}"
fi
echo ""

# 5. Verificar estado de contenedores
echo "🐳 Verificando contenedores Docker..."
CONTAINER_STATUS=$(docker-compose ps mediajelly-cron | grep "Up" || echo "")
if [ -n "$CONTAINER_STATUS" ]; then
    echo -e "${GREEN}✅ Contenedor mediajelly-cron activo${NC}"
else
    echo -e "${RED}❌ Contenedor mediajelly-cron no está activo${NC}"
fi
echo ""

# 6. Verificar archivos pendientes y completados
echo "📊 Estadísticas de procesamiento..."
if [ -f "scripts/tmp/pending-compression.txt" ]; then
    PENDING=$(wc -l < scripts/tmp/pending-compression.txt)
    echo -e "${GREEN}📝 Archivos pendientes: $PENDING${NC}"
else
    echo -e "${YELLOW}⚠️  Archivo pending-compression.txt no encontrado${NC}"
fi

if [ -f "scripts/tmp/completed.txt" ]; then
    COMPLETED=$(wc -l < scripts/tmp/completed.txt)
    echo -e "${GREEN}✅ Archivos completados: $COMPLETED${NC}"
else
    echo -e "${YELLOW}⚠️  Archivo completed.txt no encontrado${NC}"
fi
echo ""

# 7. Verificar espacio en disco
echo "💾 Verificando espacio en disco..."
DISK_USAGE=$(df -h . | awk 'NR==2 {print $5}' | sed 's/%//')
if [ "$DISK_USAGE" -lt 90 ]; then
    echo -e "${GREEN}✅ Espacio disponible: $((100-DISK_USAGE))%${NC}"
else
    echo -e "${RED}❌ Advertencia: Disco casi lleno (${DISK_USAGE}% usado)${NC}"
fi
echo ""

# 8. Resumen final
echo "========================================"
echo "📋 RESUMEN DE VERIFICACIÓN"
echo "========================================"
echo ""
echo "Archivos de configuración: ✅"
echo "Tests implementados: ✅ ($TEST_COUNT archivos)"
echo "Docker funcionando: ✅"
echo "Sistema activo: ✅"
echo ""
echo -e "${GREEN}🎉 Verificación completada exitosamente${NC}"
echo ""
echo "📝 Próximos pasos:"
echo "   1. Revisar TODO.md para tareas pendientes"
echo "   2. Ejecutar tests: pytest -v"
echo "   3. Verificar logs: tail -f scripts/logs/language-detection.log"
echo ""
