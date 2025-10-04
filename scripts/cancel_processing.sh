#!/bin/bash

# Script interactivo para cancelar el procesamiento de scripts en MediaJelly
# Muestra procesos en ejecución y permite cancelar individualmente

echo "=== Cancelador de Procesos MediaJelly ==="
echo ""

# Lista de scripts a monitorear
SCRIPTS=(
    "mediajelly_python.py"
    "mediajelly_scanner.py"
    "mediajelly_cron_runner.py"
    "mediajelly_emoji.py"
    "mediajelly_notifier.py"
    "clean_duplicates.py"
    "normalize_paths.py"
)

# Función para listar procesos corriendo
list_running_processes() {
    echo "Procesos actualmente en ejecución:"
    echo "-----------------------------------"
    local count=0
    declare -a running_pids
    declare -a running_scripts

    for script in "${SCRIPTS[@]}"; do
        # Buscar procesos que contengan el nombre del script
        pids=$(pgrep -f "$script" 2>/dev/null)
        if [ ! -z "$pids" ]; then
            for pid in $pids; do
                # Verificar que el proceso existe y es nuestro
                if ps -p $pid > /dev/null 2>&1; then
                    cmd=$(ps -p $pid -o cmd= | head -1)
                    echo "$((count+1)). $script (PID: $pid)"
                    echo "   Comando: $cmd"
                    running_scripts[$count]="$script"
                    running_pids[$count]="$pid"
                    ((count++))
                fi
            done
        fi
    done

    if [ $count -eq 0 ]; then
        echo "No hay procesos de MediaJelly ejecutándose."
        return 1
    fi

    echo ""
    echo "Opciones:"
    echo "0. Cancelar TODOS los procesos"
    echo "q. Salir sin cancelar nada"
    echo ""

    # Devolver arrays
    RUNNING_SCRIPTS=("${running_scripts[@]}")
    RUNNING_PIDS=("${running_pids[@]}")
    return 0
}

# Función para cancelar un proceso específico
cancel_process() {
    local pid=$1
    local script=$2

    echo "Cancelando $script (PID: $pid)..."

    # Intentar terminación graceful
    kill $pid 2>/dev/null
    sleep 2

    # Verificar si aún está corriendo
    if ps -p $pid > /dev/null 2>&1; then
        echo "Proceso no respondió, forzando terminación..."
        kill -9 $pid 2>/dev/null
    fi

    # Verificar que se detuvo
    if ! ps -p $pid > /dev/null 2>&1; then
        echo "✓ $script detenido exitosamente."
    else
        echo "✗ Error: No se pudo detener $script."
    fi
}

# Función para cancelar todos los procesos
cancel_all_processes() {
    echo "Cancelando TODOS los procesos..."
    for script in "${SCRIPTS[@]}"; do
        echo "Deteniendo $script..."
        pkill -f "$script" 2>/dev/null
    done

    sleep 2

    # Verificar si quedan procesos
    local remaining=$(pgrep -f "mediajelly_.*\.py\|clean_duplicates\.py\|normalize_paths\.py" 2>/dev/null)
    if [ -z "$remaining" ]; then
        echo "✓ Todos los procesos han sido detenidos."
    else
        echo "Procesos restantes detectados, forzando terminación..."
        pkill -9 -f "mediajelly_.*\.py\|clean_duplicates\.py\|normalize_paths\.py" 2>/dev/null
        echo "✓ Terminación forzada completada."
    fi
}

# Función principal
main() {
    declare -a RUNNING_SCRIPTS
    declare -a RUNNING_PIDS

    if ! list_running_processes; then
        exit 0
    fi

    while true; do
        read -p "Selecciona una opción (número, 0 para todos, q para salir): " choice

        case $choice in
            0)
                cancel_all_processes
                break
                ;;
            q|Q)
                echo "Saliendo sin cancelar procesos."
                break
                ;;
            [1-9]|[1-9][0-9])
                index=$((choice-1))
                if [ $index -ge 0 ] && [ $index -lt ${#RUNNING_SCRIPTS[@]} ]; then
                    cancel_process "${RUNNING_PIDS[$index]}" "${RUNNING_SCRIPTS[$index]}"
                    echo ""
                    # Actualizar lista
                    if ! list_running_processes; then
                        break
                    fi
                else
                    echo "Opción inválida. Intenta de nuevo."
                fi
                ;;
            *)
                echo "Opción inválida. Intenta de nuevo."
                ;;
        esac
    done

    echo ""
    echo "Cancelación completada."
}

# Ejecutar función principal
main