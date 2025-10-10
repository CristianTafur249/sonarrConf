# Mejoras al Sistema de Progress.json

## Problemas Corregidos

### 1. **Progress.json no mostraba archivos encontrados**
**Problema:** Aunque se encontraban archivos durante el escaneo, el `progress.json` no reflejaba esta información.

**Solución:** 
- Agregado estado `status` al progress.json con valores: `scanning`, `scanned`, `processing`, `completed`, `error`
- Actualización del estado durante el escaneo para mostrar archivos encontrados
- Mensaje descriptivo en `current_file_name` durante cada fase

### 2. **Estado de funcionamiento no se guardaba**
**Problema:** No había forma de saber si el proceso estaba escaneando, procesando o completado.

**Solución:**
- Agregado campo `status` que indica el estado actual del proceso
- Estados disponibles:
  - `scanning`: Buscando archivos
  - `scanned`: Escaneo completado
  - `processing`: Procesando archivos
  - `completed`: Procesamiento completado
  - `error`: Error durante el proceso

### 3. **Estadísticas se perdían en errores**
**Problema:** Si el proceso se interrumpía antes de completar el 90%, se perdían todas las estadísticas.

**Solución:**
- Implementado sistema de recuperación de estadísticas
- Si el progreso anterior es < 90%, se conservan las estadísticas previas
- Las nuevas estadísticas se suman a las anteriores
- Los archivos ya procesados exitosamente se omiten automáticamente

### 4. **current_file_name no mostraba archivos en procesamiento**
**Problema:** Solo mostraba el último archivo procesado, no los que estaban actualmente en procesamiento.

**Solución:**
- Implementado rastreo de archivos en procesamiento simultáneo
- Muestra hasta 3 archivos en procesamiento actualmente
- Si hay más de 3, muestra "archivo1, archivo2, archivo3 y X más"
- Actualización en tiempo real conforme se completan archivos

### 5. **Mensajes de notificación no usaban progress.json**
**Problema:** Las notificaciones no usaban la información actualizada del `progress.json`.

**Solución:**
- Modificado `mediajelly_notifier.py` para leer `progress.json`
- Las notificaciones ahora muestran:
  - Estado actual del proceso
  - Porcentaje de progreso
  - Archivos en procesamiento
  - Estadísticas completas desde progress.json

## Estructura del progress.json

```json
{
  "current_file": 2,
  "total_files": 5,
  "current_file_name": "archivo1.mkv, archivo2.mkv",
  "percentage": 40.0,
  "last_updated": "2025-10-08T20:38:24.609947",
  "status": "processing",
  "stats": {
    "files_found": 5,
    "files_new": 3,
    "files_processed": 2,
    "files_compressed": 2,
    "files_renamed": 0,
    "files_skipped": 0,
    "errors": [],
    "no_spanish": []
  },
  "processed_files": {
    "/path/to/file1.mkv": {
      "status": "success",
      "timestamp": "2025-10-08T20:30:00.000000"
    },
    "/path/to/file2.mkv": {
      "status": "success",
      "timestamp": "2025-10-08T20:35:00.000000"
    }
  }
}
```

## Campos del progress.json

- **current_file**: Número de archivos procesados hasta ahora
- **total_files**: Total de archivos a procesar
- **current_file_name**: Archivos actualmente en procesamiento o mensaje de estado
- **percentage**: Porcentaje de progreso (0-100)
- **last_updated**: Timestamp de última actualización
- **status**: Estado actual del proceso (scanning, scanned, processing, completed, error)
- **stats**: Estadísticas completas del procesamiento
  - **files_found**: Archivos encontrados en el escaneo
  - **files_new**: Archivos nuevos detectados
  - **files_processed**: Archivos procesados completamente
  - **files_compressed**: Archivos comprimidos exitosamente
  - **files_renamed**: Archivos renombrados
  - **files_skipped**: Archivos omitidos
  - **errors**: Lista de nombres de archivos con errores
  - **no_spanish**: Lista de archivos sin audio/subtítulos en español
- **processed_files**: Diccionario con estado de cada archivo procesado
  - Clave: Ruta completa del archivo
  - Valor: Objeto con `status` y `timestamp`

## Flujo de Estados

```
idle → scanning → scanned → processing → completed
                                    ↓
                                  error
```

## Conservación de Estadísticas

### Reglas de Conservación:
1. Si el progreso anterior es **< 90%**:
   - Se conservan todas las estadísticas previas
   - Se suman las nuevas estadísticas a las previas
   - Se mantienen los archivos procesados exitosamente

2. Si el progreso anterior es **≥ 90%**:
   - Se considera un nuevo ciclo
   - Las estadísticas empiezan desde cero
   - Los archivos ya procesados se agregan a la cola si hay nuevos

### Ejemplo de Conservación:

#### Ejecución 1 (interrumpida al 60%):
```json
{
  "percentage": 60.0,
  "stats": {
    "files_found": 10,
    "files_processed": 6,
    "files_compressed": 5
  }
}
```

#### Ejecución 2 (continúa desde donde quedó):
```json
{
  "percentage": 100.0,
  "stats": {
    "files_found": 10,
    "files_processed": 10,  // 6 + 4 nuevos
    "files_compressed": 9   // 5 + 4 nuevos
  }
}
```

## Pruebas Recomendadas

1. **Interrupción durante procesamiento:**
   ```bash
   docker-compose exec -d mediajelly-cron python3 /mediajelly/scripts/mediajelly_cron_runner.py
   # Esperar al 50%
   docker-compose exec mediajelly-cron pkill -f mediajelly_python.py
   # Reiniciar
   docker-compose exec -d mediajelly-cron python3 /mediajelly/scripts/mediajelly_cron_runner.py
   ```

2. **Ver progress.json en tiempo real:**
   ```bash
   watch -n 2 'cat /home/tafurc/mediaJelly/scripts/tmp/progress.json | python3 -m json.tool'
   ```

3. **Verificar notificaciones:**
   - Las notificaciones deben mostrar el estado actual
   - Deben incluir archivos en procesamiento
   - Deben mostrar porcentaje de progreso
