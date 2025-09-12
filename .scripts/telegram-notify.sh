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
                    local file_name=$(echo "$line" | sed 's/.*SIN ESPAÑOL: //' | xargs basename)
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

# Función para notificar errores críticos
notify_critical_error() {
    local error_message="$1"
    local log_file="$2"
    
    local message="🚨 <b>MediaJelly - Error Crítico</b>\n\n"
    message="${message}❌ <b>Error:</b> $error_message\n"
    message="${message}📅 <b>Fecha:</b> $(date '+%Y-%m-%d %H:%M:%S')\n"
    
    if [ -f "$log_file" ]; then
        message="${message}\n📄 <b>Últimas líneas del log:</b>\n"
        message="${message}$(tail -3 "$log_file" | sed 's/^/• /')"
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
        "critical_error")
            shift
            notify_critical_error "$@"
            ;;
        "test")
            send_long_message "🧪 <b>MediaJelly - Prueba de notificación</b>\n\nSi recibes este mensaje, las notificaciones están funcionando correctamente.\n\n📅 $(date '+%Y-%m-%d %H:%M:%S')"
            ;;
        *)
            echo "Uso: $0 {scan_result|critical_error|test} [argumentos...]"
            exit 1
            ;;
    esac
fi
