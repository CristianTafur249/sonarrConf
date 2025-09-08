#!/bin/bash

# Script principal para ejecutar desde cron
# Ejecuta el escaneo y compresión de forma silenciosa

SCRIPT_DIR="/home/tafurc/mediaJelly/scripts"
MEDIA_DIR="/home/tafurc/mediaJelly/media"
LOCKFILE="/home/tafurc/mediaJelly/scripts/tmp/cron.lock"
LAST_RUN_FILE="/home/tafurc/mediaJelly/scripts/tmp/last_run"
LOG_FILE="/home/tafurc/mediaJelly/scripts/logs/cron.log"

# Configuración
MIN_HOURS_BETWEEN_RUNS=12

# Función de logging
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$LOG_FILE"
}

# Verificar si han pasado las horas mínimas desde la última ejecución
check_time_since_last_run() {
    if [ ! -f "$LAST_RUN_FILE" ]; then
        return 0  # Primera ejecución
    fi
    
    local last_run=$(cat "$LAST_RUN_FILE" 2>/dev/null || echo "0")
    local current_time=$(date +%s)
    local time_diff=$(( (current_time - last_run) / 3600 ))
    
    if [ "$time_diff" -lt "$MIN_HOURS_BETWEEN_RUNS" ]; then
        log "Ejecución omitida: Solo han pasado $time_diff horas desde la última ejecución (mínimo: $MIN_HOURS_BETWEEN_RUNS)"
        return 1
    fi
    
    return 0
}

# Función principal
main() {
    mkdir -p "$(dirname "$LOCKFILE")"
    mkdir -p "$(dirname "$LOG_FILE")"
    
    # Usar flock para evitar ejecuciones simultáneas
    (
        if ! flock -n 9; then
            log "Error: Ya hay una instancia ejecutándose"
            exit 1
        fi
        
        log "=== Iniciando ejecución automática ==="
        
        # Verificar tiempo desde última ejecución
        if ! check_time_since_last_run; then
            exit 0
        fi
        
        # Actualizar archivo de última ejecución
        echo "$(date +%s)" > "$LAST_RUN_FILE"
        
        # Variables para recopilar estadísticas
        local scan_success=false
        local files_found=0
        local files_new=0
        local files_processed=0
        local files_compressed=0
        local files_renamed=0
        local files_skipped=0
        
        # Ejecutar escaneo y procesamiento
        log "Iniciando escaneo de $MEDIA_DIR"
        
        # Redirigir stdout y stderr para capturar la salida
        local scan_output_file=$(mktemp)
        local scan_error_file=$(mktemp)
        
        if cd /home/tafurc/mediaJelly && timeout 3600 "$SCRIPT_DIR/scan-to-pending.sh" "$MEDIA_DIR" > "$scan_output_file" 2> "$scan_error_file"; then
            scan_success=true
            log "Escaneo completado exitosamente"
            
            # Extraer estadísticas del log
            local success_log="/home/tafurc/mediaJelly/scripts/logs/compression-success.log"
            if [ -f "$success_log" ]; then
                local last_summary=$(grep "RESUMEN:" "$success_log" | tail -1)
                if [ -n "$last_summary" ]; then
                    files_processed=$(echo "$last_summary" | grep -o "Procesados: [0-9]*" | grep -o "[0-9]*" || echo "0")
                    files_compressed=$(echo "$last_summary" | grep -o "Comprimidos: [0-9]*" | grep -o "[0-9]*" || echo "0")
                    files_renamed=$(echo "$last_summary" | grep -o "Renombrados: [0-9]*" | grep -o "[0-9]*" || echo "0")
                    files_skipped=$(echo "$last_summary" | grep -o "Omitidos: [0-9]*" | grep -o "[0-9]*" || echo "0")
                fi
            fi
            
            local scan_log="/home/tafurc/mediaJelly/scripts/logs/scan.log"
            if [ -f "$scan_log" ]; then
                files_found=$(grep "Archivos encontrados:" "$scan_log" | tail -1 | grep -o "Archivos encontrados: [0-9]*" | grep -o "[0-9]*" || echo "0")
                files_new=$(grep "Nuevas rutas detectadas:" "$scan_log" | tail -1 | grep -o "Nuevas rutas detectadas: [0-9]*" | grep -o "[0-9]*" || echo "0")
            fi
            
            # Debug - Log de las estadísticas
            log "Estadísticas extraídas: encontrados=$files_found, nuevos=$files_new, procesados=$files_processed, comprimidos=$files_compressed, renombrados=$files_renamed, omitidos=$files_skipped"
            
            # Leer archivos con errores si existen
            local error_files_file="/home/tafurc/mediaJelly/scripts/tmp/error_files.tmp"
            local error_files_args=""
            if [ -f "$error_files_file" ]; then
                while IFS= read -r error_file; do
                    error_files_args="$error_files_args \"$error_file\""
                done < "$error_files_file"
            fi
            
            # Notificar éxito
            log "Enviando notificación de éxito..."
            if [ -n "$error_files_args" ]; then
                eval "$SCRIPT_DIR/telegram-notify.sh scan_result \"success\" \"$files_found\" \"$files_new\" \"$files_processed\" \"$files_compressed\" \"$files_renamed\" \"$files_skipped\" $error_files_args"
            else
                "$SCRIPT_DIR/telegram-notify.sh" scan_result "success" "$files_found" "$files_new" "$files_processed" "$files_compressed" "$files_renamed" "$files_skipped"
            fi
            
            if [ $? -eq 0 ]; then
                log "Notificación enviada correctamente"
            else
                log "Error al enviar notificación"
            fi
            
        else
            local exit_code=$?
            scan_success=false
            log "Error en el escaneo (código de salida: $exit_code)"
            
            # Log de errores
            if [ -s "$scan_error_file" ]; then
                log "Errores capturados:"
                cat "$scan_error_file" >> "$LOG_FILE"
            fi
            
            # Notificar error
            log "Enviando notificación de error..."
            if "$SCRIPT_DIR/telegram-notify.sh" critical_error "Falló el escaneo automático (código: $exit_code)" "$LOG_FILE"; then
                log "Notificación de error enviada correctamente"
            else
                log "Error al enviar notificación de error"
            fi
        fi
        
        # Limpiar archivos temporales
        rm -f "$scan_output_file" "$scan_error_file"
        
        log "=== Ejecución automática finalizada ==="
        
    ) 9>"$LOCKFILE"
}

# Ejecutar solo si se llama directamente (no si se incluye como source)
if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
    main "$@"
fi
