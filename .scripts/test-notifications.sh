#!/bin/bash

# Script de prueba para las notificaciones inteligentes de Telegram

TELEGRAM_SCRIPT="/home/tafurc/mediaJelly/.scripts/telegram-notify.sh"

echo "=== Prueba del Sistema de Notificaciones Inteligentes ==="
echo ""

# Función para mostrar estado actual
show_current_state() {
    echo "📊 Estado actual del sistema:"
    
    local pending_count=0
    local completed_count=0
    
    if [ -f "/home/tafurc/mediaJelly/.scripts/pending-compression.txt" ]; then
        pending_count=$(wc -l < "/home/tafurc/mediaJelly/.scripts/pending-compression.txt" 2>/dev/null || echo "0")
    fi
    
    if [ -f "/home/tafurc/mediaJelly/.scripts/completed.txt" ]; then
        completed_count=$(wc -l < "/home/tafurc/mediaJelly/.scripts/completed.txt" 2>/dev/null || echo "0")
    fi
    
    echo "  • Archivos pendientes: $pending_count"
    echo "  • Archivos completados: $completed_count"
    echo ""
}

# Función para simular escenarios
simulate_scenario() {
    local scenario="$1"
    echo "📋 Simulando: $scenario"
    
    case "$scenario" in
        "no_changes")
            echo "  → Enviando notificación sin cambios..."
            "$TELEGRAM_SCRIPT" scan_result "success" "278" "0" "0" "0" "0" "0"
            ;;
        "no_pending")
            echo "  → Enviando notificación sin archivos pendientes..."
            "$TELEGRAM_SCRIPT" no_pending "278" "277"
            ;;
        "with_processing")
            echo "  → Reseteando estado para simular procesamiento..."
            "$TELEGRAM_SCRIPT" reset_state
            echo "  → Enviando notificación con procesamiento..."
            "$TELEGRAM_SCRIPT" scan_result "success" "278" "5" "5" "3" "2" "0"
            ;;
        "test_message")
            echo "  → Enviando mensaje de prueba..."
            "$TELEGRAM_SCRIPT" test
            ;;
    esac
    
    echo "  ✅ Enviado"
    echo ""
}

# Mostrar estado actual
show_current_state

# Menú de opciones
echo "🔧 Opciones de prueba:"
echo "1) Simular escaneo sin cambios (debería mostrar mensaje simple)"
echo "2) Simular escaneo sin archivos pendientes"
echo "3) Simular escaneo con procesamiento nuevo (forzar estado)"
echo "4) Enviar mensaje de prueba"
echo "5) Resetear estado de notificaciones"
echo "6) Mostrar ayuda del script"
echo "0) Salir"
echo ""

read -p "Selecciona una opción (0-6): " option

case "$option" in
    "1")
        simulate_scenario "no_changes"
        ;;
    "2")
        simulate_scenario "no_pending"
        ;;
    "3")
        simulate_scenario "with_processing"
        ;;
    "4")
        simulate_scenario "test_message"
        ;;
    "5")
        echo "🔄 Reseteando estado..."
        "$TELEGRAM_SCRIPT" reset_state
        echo "✅ Estado reseteado. La próxima notificación mostrará estadísticas completas."
        ;;
    "6")
        echo "📖 Ayuda del script:"
        "$TELEGRAM_SCRIPT"
        ;;
    "0")
        echo "👋 ¡Hasta luego!"
        ;;
    *)
        echo "❌ Opción inválida"
        ;;
esac