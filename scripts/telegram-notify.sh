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

# Función para crear resumen de procesamiento
create_processing_summary() {
    local log_file="$1"
    local error_log="$2"
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
    local error_summary=$(create_processing_summary "/home/tafurc/mediaJelly/scripts/logs/compression-success.log" "/home/tafurc/mediaJelly/scripts/logs/compression-errors.log")
    if [ -n "$error_summary" ]; then
        message="$message

$error_summary"
    fi
    
    send_telegram_message "$message"
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
    
    send_telegram_message "$message"
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
            send_telegram_message "🧪 <b>MediaJelly - Prueba de notificación</b>\n\nSi recibes este mensaje, las notificaciones están funcionando correctamente.\n\n📅 $(date '+%Y-%m-%d %H:%M:%S')"
            ;;
        *)
            echo "Uso: $0 {scan_result|critical_error|test} [argumentos...]"
            exit 1
            ;;
    esac
fi
