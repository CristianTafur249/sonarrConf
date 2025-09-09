#!/bin/bash

# --- CONFIGURACIÓN ---
LOG_DIR="/home/tafurc/mediaJelly/.scripts/logs"
MAX_LOG_SIZE_MB=10
MAX_COMPRESSED_LOGS=5

# Función para comprimir y rotar logs
manage_log_file() {
    local log_file="$1"
    local base_name=$(basename "$log_file" .log)
    
    if [ ! -f "$log_file" ]; then
        return
    fi
    
    # Obtener tamaño del archivo en MB
    local size_bytes=$(stat -c %s "$log_file" 2>/dev/null || echo "0")
    local size_mb=$((size_bytes / 1024 / 1024))
    
    if [ "$size_mb" -ge "$MAX_LOG_SIZE_MB" ]; then
        echo "Log $log_file supera ${MAX_LOG_SIZE_MB}MB (${size_mb}MB), rotando..."
        
        # Comprimir el log actual
        local timestamp=$(date '+%Y%m%d_%H%M%S')
        local compressed_file="${LOG_DIR}/${base_name}_${timestamp}.log.gz"
        
        gzip -c "$log_file" > "$compressed_file"
        
        # Limpiar el log original
        > "$log_file"
        
        echo "Log rotado: $compressed_file"
        
        # Limpiar logs comprimidos antiguos
        cleanup_old_compressed_logs "$base_name"
    fi
}

# Función para limpiar logs comprimidos antiguos
cleanup_old_compressed_logs() {
    local base_name="$1"
    
    # Contar archivos comprimidos existentes
    local compressed_count=$(find "$LOG_DIR" -name "${base_name}_*.log.gz" | wc -l)
    
    if [ "$compressed_count" -gt "$MAX_COMPRESSED_LOGS" ]; then
        # Eliminar los más antiguos, manteniendo solo MAX_COMPRESSED_LOGS
        find "$LOG_DIR" -name "${base_name}_*.log.gz" -type f -printf '%T@ %p\n' | \
        sort -n | \
        head -n -${MAX_COMPRESSED_LOGS} | \
        cut -d' ' -f2- | \
        while read -r file; do
            echo "Eliminando log comprimido antiguo: $file"
            rm -f "$file"
        done
    fi
}

# --- EJECUCIÓN ---
mkdir -p "$LOG_DIR"

# Gestionar todos los logs en el directorio
for log_file in "$LOG_DIR"/*.log; do
    if [ -f "$log_file" ]; then
        manage_log_file "$log_file"
    fi
done

echo "Gestión de logs completada."
