#!/bin/bash

# Script para enviar notificaciones a Telegram
CONFIG_FILE="/home/tafurc/mediaJelly/config/telegram.conf"

# Cargar configuración
if [ -f "$CONFIG_FILE" ]; then
    source "$CONFIG_FILE"
else
    echo "Error: Archivo de configuración no encontrado: $CONFIG_FILE"
    exit 1
fi

# Función para enviar mensaje a Telegram
send_telegram_message() {
    local message="$1"
    local parse_mode="${2:-}"
    
    if [ -z "$TELEGRAM_BOT_TOKEN" ] || [ -z "$TELEGRAM_CHAT_ID" ]; then
        echo "Error: Credenciales de Telegram no configuradas" >&2
        return 1
    fi
    
    # Usar modo sin formato para evitar problemas con caracteres especiales
    local response=$(curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
        -d chat_id="$TELEGRAM_CHAT_ID" \
        -d text="$message")
    
    # Verificar si fue exitoso
    if echo "$response" | grep -q '"ok":true'; then
        return 0
    else
        echo "Error en respuesta de Telegram: $response" >&2
        return 1
    fi
}

# Función para enviar mensajes largos dividiéndolos en partes
send_long_message() {
    local full_message="$1"
    local max_length=4096  # Límite real de Telegram (4096 caracteres UTF-8)
    local chunk_size=3000   # Tamaño de cada parte
    
    local message_length=${#full_message}
    
    if [ "$message_length" -le "$max_length" ]; then
        # Mensaje corto, enviar directamente
        send_telegram_message "$full_message"
        return $?
    fi
    
    # Mensaje largo, dividir en partes de chunk_size caracteres
    local start=0
    local part_num=1
    
    while [ "$start" -lt "$message_length" ]; do
        local end=$((start + chunk_size))
        if [ "$end" -gt "$message_length" ]; then
            end="$message_length"
        fi
        
        local chunk="${full_message:start:chunk_size}"
        
        # Agregar indicador de parte si hay múltiples partes
        if [ "$message_length" -gt "$chunk_size" ]; then
            chunk="(Parte $part_num) $chunk"
        fi
        
        if ! send_telegram_message "$chunk"; then
            return 1
        fi
        
        start="$end"
        part_num=$((part_num + 1))
        
        # Pequeña pausa para evitar rate limits
        sleep 0.5
    done
    
    return 0
}

# Función para crear resumen de procesamiento
create_processing_summary() {
    local log_file="$1"
    local error_log="$2"
    local no_spanish_log="/home/tafurc/mediaJelly/.scripts/logs/no-spanish.log"
    local summary=""
    
    if [ -f "$log_file" ]; then
        local last_summary=$(grep "RESUMEN:" "$log_file" | tail -1)
        if [ -n "$last_summary" ]; then
            summary="📊 Resumen del procesamiento:
$last_summary

"
        fi
    fi
    
    if [ -f "$error_log" ] && [ -s "$error_log" ]; then
        local error_count=$(wc -l < "$error_log")
        summary="${summary}❌ Errores encontrados: $error_count
$(tail -5 "$error_log" | sed 's/^/• /')

"
    fi
    
    # Agrega información de archivos sin español
    if [ -f "$no_spanish_log" ] && [ -s "$no_spanish_log" ]; then
        local no_spanish_count=$(grep -c "SIN ESPAÑOL:" "$no_spanish_log")
        if [ "$no_spanish_count" -gt 0 ]; then
            summary="${summary}🌍 Archivos sin español detectados: $no_spanish_count

"
            # Muestra hasta 10 archivos sin español
            local shown=0
            while IFS= read -r line && [ "$shown" -lt 10 ]; do
                if [[ "$line" == *"SIN ESPAÑOL:"* ]]; then
                    local file_path=$(echo "$line" | sed 's/.*SIN ESPAÑOL: //')
                    local file_name=$(basename "$file_path" 2>/dev/null || echo "$file_path")
                    summary="${summary}• $file_name
"
                    shown=$((shown + 1))
                fi
            done < "$no_spanish_log"
            
            if [ "$no_spanish_count" -gt 10 ]; then
                summary="${summary}... y $((no_spanish_count - 10)) más
"
            fi
        fi
    fi
    
    echo "$summary"
}

# Función para detectar si hubo procesamiento real
has_new_processing() {
    local success_log="/home/tafurc/mediaJelly/.scripts/logs/compression-success.log"
    local pending_file="/home/tafurc/mediaJelly/.scripts/pending-compression.txt"
    local completed_file="/home/tafurc/mediaJelly/.scripts/completed.txt"
    local state_file="/home/tafurc/mediaJelly/.scripts/tmp/last_notification_state"
    
    # Crear directorio tmp si no existe
    mkdir -p "/home/tafurc/mediaJelly/.scripts/tmp"
    
    # Obtener estado actual
    local current_pending_count=0
    local current_completed_count=0
    local current_last_log_line=""
    
    if [ -f "$pending_file" ]; then
        current_pending_count=$(wc -l < "$pending_file" 2>/dev/null || echo "0")
    fi
    
    if [ -f "$completed_file" ]; then
        current_completed_count=$(wc -l < "$completed_file" 2>/dev/null || echo "0")
    fi
    
    if [ -f "$success_log" ]; then
        current_last_log_line=$(tail -1 "$success_log" 2>/dev/null || echo "")
    fi
    
    # Leer estado anterior si existe
    local previous_pending_count=0
    local previous_completed_count=0
    local previous_last_log_line=""
    
    if [ -f "$state_file" ]; then
        local line_num=0
        while IFS= read -r line; do
            line_num=$((line_num + 1))
            case $line_num in
                1) previous_pending_count="$line" ;;
                2) previous_completed_count="$line" ;;
                3) previous_last_log_line="$line" ;;
            esac
        done < "$state_file"
    fi
    
    # Guardar estado actual para la próxima vez
    cat > "$state_file" << EOF
$current_pending_count
$current_completed_count
$current_last_log_line
EOF
    
    # Determinar si hubo cambios
    if [ "$current_completed_count" != "$previous_completed_count" ] || 
       [ "$current_last_log_line" != "$previous_last_log_line" ]; then
        return 0  # Hubo procesamiento nuevo
    else
        return 1  # No hubo procesamiento nuevo
    fi
}

# Función principal para notificar resultado del escaneo
notify_scan_result() {
    local status="$1"
    local files_found="$2"
    local files_new="$3"
    local files_processed="$4"
    local files_compressed="$5"
    local files_renamed="$6"
    local files_skipped="$7"
    shift 7  # Remover los 7 primeros argumentos
    local error_files=("$@")  # Los argumentos restantes son los archivos con errores
    
    # Verificar si realmente hubo procesamiento nuevo
    if ! has_new_processing; then
        # No hubo procesamiento nuevo, enviar mensaje simple
        local message="🔍 MediaJelly - Escaneo Completado

📁 Archivos encontrados: $files_found
🆕 Archivos nuevos: $files_new

ℹ️ No se encontraron archivos nuevos para procesar
✅ El sistema está al día

📅 Fecha: $(date '+%Y-%m-%d %H:%M:%S')"

        send_long_message "$message"
        return 0
    fi
    
    # Hubo procesamiento nuevo, mostrar estadísticas completas
    local emoji="✅"
    local title="MediaJelly - Procesamiento Completado"
    
    if [ "$status" = "error" ]; then
        emoji="❌"
        title="MediaJelly - Error en Procesamiento"
    fi
    
    local message="$emoji $title

📁 Archivos encontrados: $files_found
🆕 Archivos nuevos: $files_new"
    
    if [ "$files_processed" -gt 0 ]; then
        message="$message
⚙️ Archivos procesados: $files_processed
🗜️ Comprimidos: $files_compressed
📝 Renombrados: $files_renamed
⏭️ Omitidos: $files_skipped"
    fi
    
    message="$message

📅 Fecha: $(date '+%Y-%m-%d %H:%M:%S')"
    
    # Agregar lista específica de archivos con errores si existen
    if [ ${#error_files[@]} -gt 0 ]; then
        message="$message

⚠️ Archivos con errores específicos:"
        for error_file in "${error_files[@]}"; do
            message="$message
• $error_file"
        done
    fi
    
    # Agregar resumen de errores si existen
    local error_summary=$(create_processing_summary "/home/tafurc/mediaJelly/.scripts/logs/compression-success.log" "/home/tafurc/mediaJelly/.scripts/logs/compression-errors.log")
    if [ -n "$error_summary" ]; then
        message="$message

$error_summary"
    fi
    
    send_long_message "$message"
}

# Función para notificar cuando no hay archivos pendientes
notify_no_pending_files() {
    local total_files="$1"
    local completed_files="$2"
    
    local message="🔍 MediaJelly - Escaneo Completado

📁 Total de archivos: $total_files
✅ Archivos completados: $completed_files
📋 Archivos pendientes: 0

ℹ️ No hay archivos pendientes para procesar
🎉 ¡Todo está actualizado!

📅 Fecha: $(date '+%Y-%m-%d %H:%M:%S')"

    send_long_message "$message"
}

# Función para notificar errores críticos
# Función para notificar errores críticos
notify_critical_error() {
    local error_message="$1"
    local log_file="$2"
    
    local message="🚨 MediaJelly - Error Crítico

❌ Error: $error_message
📅 Fecha: $(date '+%Y-%m-%d %H:%M:%S')"
    
    if [ -f "$log_file" ]; then
        message="$message

📄 Últimas líneas del log:"
        local last_lines=$(tail -3 "$log_file" | sed 's/^/• /')
        if [ -n "$last_lines" ]; then
            message="$message
$last_lines"
        fi
    fi
    
    send_long_message "$message"
}
# Si se llama directamente, usar los argumentos
if [ "${BASH_SOURCE[0]}" = "${0}" ]; then
    case "$1" in
        "scan_result")
            shift
            notify_scan_result "$@"
            ;;
        "no_pending")
            shift
            notify_no_pending_files "$@"
            ;;
        "critical_error")
            shift
            notify_critical_error "$@"
            ;;
        "test")
            send_long_message "🧪 MediaJelly - Prueba de notificación

Si recibes este mensaje, las notificaciones están funcionando correctamente.

📅 $(date '+%Y-%m-%d %H:%M:%S')"
            ;;
        "reset_state")
            # Función para resetear el estado de notificaciones
            rm -f "/home/tafurc/mediaJelly/.scripts/tmp/last_notification_state"
            echo "Estado de notificaciones reseteado"
            ;;
        *)
            echo "Uso: $0 {scan_result|no_pending|critical_error|test|reset_state} [argumentos...]"
            echo ""
            echo "Comandos disponibles:"
            echo "  scan_result <status> <found> <new> <processed> <compressed> <renamed> <skipped> [error_files...]"
            echo "  no_pending <total_files> <completed_files>"
            echo "  critical_error <error_message> [log_file]"
            echo "  test - Enviar mensaje de prueba"
            echo "  reset_state - Resetear estado para forzar próxima notificación"
            exit 1
            ;;
    esac
fi
