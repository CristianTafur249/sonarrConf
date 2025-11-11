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
    "mediajelly_subtitle_translator.py"
    "mediajelly_emoji.py"
    "mediajelly_notifier.py"
    "clean_duplicates.py"
    "normalize_paths.py"
    "mediajelly_processor.py"
    "mediajelly_language_detector.py"
)

# Función para listar procesos corriendo
list_running_processes() {
    echo "Procesos actualmente en ejecución:"
    echo "-----------------------------------"
    local count=0
    declare -a running_pids
    declare -a running_scripts
    declare -a running_containers

    # Buscar procesos en el sistema host
    for script in "${SCRIPTS[@]}"; do
        # Buscar procesos que contengan el nombre del script
        pids=$(pgrep -f "$script" 2>/dev/null)
        if [ ! -z "$pids" ]; then
            for pid in $pids; do
                # Verificar que el proceso existe y es nuestro
                if ps -p $pid > /dev/null 2>&1; then
                    cmd=$(ps -p $pid -o cmd= | head -1)
                    echo "$((count+1)). $script (PID: $pid) [HOST]"
                    echo "   Comando: $cmd"
                    running_scripts[$count]="$script"
                    running_pids[$count]="$pid"
                    running_containers[$count]="host"
                    ((count++))
                fi
            done
        fi
    done

    # Buscar procesos en contenedores Docker
    if command -v docker-compose &> /dev/null; then
        # Verificar si el contenedor mediajelly-cron está corriendo
        if docker-compose ps mediajelly-cron | grep -q "Up"; then
            echo ""
            echo "Procesos en contenedor Docker (mediajelly-cron):"
            echo "-----------------------------------------------"

            # Obtener lista de procesos del contenedor
            container_processes=$(docker-compose exec -T mediajelly-cron ps aux 2>/dev/null | grep -E "python.*mediajelly.*\.py" | grep -v grep)

            if [ ! -z "$container_processes" ]; then
                echo "$container_processes" | while read -r line; do
                    pid=$(echo "$line" | awk '{print $2}')
                    cmd=$(echo "$line" | awk '{for(i=11;i<=NF;i++) printf "%s ", $i; print ""}' | sed 's/ *$//')
                    script_name=$(echo "$cmd" | grep -oE "mediajelly_[^ ]*\.py" | head -1)

                    # Si no encontró script_name con el patrón anterior, intentar con mediajelly_processor.py
                    if [ -z "$script_name" ]; then
                        if echo "$cmd" | grep -q "mediajelly_processor\.py"; then
                            script_name="mediajelly_processor.py"
                        fi
                    fi

                    if [ ! -z "$script_name" ]; then
                        echo "$((count+1)). $script_name (PID: $pid) [DOCKER]"
                        echo "   Comando: $cmd"
                        running_scripts[$count]="$script_name"
                        running_pids[$count]="$pid"
                        running_containers[$count]="docker"
                        ((count++))
                    fi
                done
            else
                echo "No hay procesos Python de MediaJelly en el contenedor."
            fi
        fi
    fi

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
    RUNNING_CONTAINERS=("${running_containers[@]}")
    return 0
}

# Función para cancelar un proceso específico
cancel_process() {
    local pid=$1
    local script=$2
    local container=$3

    echo "Cancelando $script (PID: $pid)..."

    if [ "$container" = "docker" ]; then
        # Cancelar proceso en contenedor Docker
        docker-compose exec -T mediajelly-cron kill $pid 2>/dev/null
        sleep 2

        # Verificar si aún está corriendo
        if docker-compose exec -T mediajelly-cron ps -p $pid > /dev/null 2>&1; then
            echo "Proceso no respondió, forzando terminación..."
            docker-compose exec -T mediajelly-cron kill -9 $pid 2>/dev/null
        fi

        # Verificar que se detuvo
        if ! docker-compose exec -T mediajelly-cron ps -p $pid > /dev/null 2>&1; then
            echo "✓ $script detenido exitosamente en contenedor Docker."
        else
            echo "✗ Error: No se pudo detener $script en contenedor Docker."
        fi
    else
        # Cancelar proceso en host
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
    fi
}

# Función para cancelar todos los procesos
cancel_all_processes() {
    echo "Cancelando TODOS los procesos..."

    # Cancelar procesos en host
    for script in "${SCRIPTS[@]}"; do
        echo "Deteniendo $script en host..."
        pkill -f "$script" 2>/dev/null
    done

    # Cancelar procesos en contenedor Docker
    if command -v docker-compose &> /dev/null && docker-compose ps mediajelly-cron | grep -q "Up"; then
        echo "Deteniendo procesos en contenedor Docker..."
        
        # Primero matar procesos padre (mediajelly_processor.py) para que terminen los hijos automáticamente
        echo "Deteniendo procesos padre en contenedor Docker..."
        parent_pids=$(docker-compose exec -T mediajelly-cron ps aux 2>/dev/null | grep -E "python3.*mediajelly_processor\.py" | grep -v grep | awk '{print $2}')
        if [ ! -z "$parent_pids" ]; then
            for pid in $parent_pids; do
                echo "Matando proceso padre mediajelly_processor.py (PID: $pid)"
                docker-compose exec -T mediajelly-cron kill $pid 2>/dev/null
            done
            sleep 3  # Dar tiempo a que los procesos hijos terminen
        fi
        
        # Luego matar cualquier proceso hijo restante
        docker_pids=$(docker-compose exec -T mediajelly-cron ps aux 2>/dev/null | grep -E "python.*mediajelly.*\.py" | grep -v grep | awk '{print $2}')
        if [ ! -z "$docker_pids" ]; then
            echo "Matando procesos hijos restantes..."
            for pid in $docker_pids; do
                docker-compose exec -T mediajelly-cron kill $pid 2>/dev/null
            done
        fi
    fi

    sleep 2

    # Verificar procesos restantes en host
    local remaining_host=$(pgrep -f "mediajelly_.*\.py\|clean_duplicates\.py\|normalize_paths\.py" 2>/dev/null)

    # Verificar procesos restantes en Docker
    local remaining_docker=""
    if command -v docker-compose &> /dev/null && docker-compose ps mediajelly-cron | grep -q "Up"; then
        remaining_docker=$(docker-compose exec -T mediajelly-cron ps aux 2>/dev/null | grep -E "python.*mediajelly.*\.py" | grep -v grep)
    fi

    if [ -z "$remaining_host" ] && [ -z "$remaining_docker" ]; then
        echo "✓ Todos los procesos han sido detenidos."
    else
        echo "Procesos restantes detectados, forzando terminación..."

        # Forzar terminación en host
        if [ ! -z "$remaining_host" ]; then
            pkill -9 -f "mediajelly_.*\.py\|clean_duplicates\.py\|normalize_paths\.py" 2>/dev/null
        fi

        # Forzar terminación en Docker
        if [ ! -z "$remaining_docker" ]; then
            echo "Forzando terminación de procesos padre..."
            parent_pids=$(docker-compose exec -T mediajelly-cron ps aux 2>/dev/null | grep -E "python3.*mediajelly_processor\.py" | grep -v grep | awk '{print $2}')
            if [ ! -z "$parent_pids" ]; then
                for pid in $parent_pids; do
                    docker-compose exec -T mediajelly-cron kill -9 $pid 2>/dev/null
                done
            fi
            
            echo "Forzando terminación de procesos hijos restantes..."
            docker_pids=$(docker-compose exec -T mediajelly-cron ps aux 2>/dev/null | grep -E "python.*mediajelly.*\.py" | grep -v grep | awk '{print $2}')
            if [ ! -z "$docker_pids" ]; then
                for pid in $docker_pids; do
                    docker-compose exec -T mediajelly-cron kill -9 $pid 2>/dev/null
                done
            fi
        fi

        echo "✓ Terminación forzada completada."
    fi
}

# Función principal
main() {
    declare -a RUNNING_SCRIPTS
    declare -a RUNNING_PIDS
    declare -a RUNNING_CONTAINERS

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
                    cancel_process "${RUNNING_PIDS[$index]}" "${RUNNING_SCRIPTS[$index]}" "${RUNNING_CONTAINERS[$index]}"
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