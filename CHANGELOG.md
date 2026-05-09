# Changelog - MediaJelly

Registro de cambios del proyecto MediaJelly, un servidor multimedia automatizado con Docker.

## [v3.2.4] - 2025-11-10

### Nuevas Características

- **Sistema de Detección Inteligente de Idiomas**:
  - Nuevo script `mediajelly_language_detector.py` con integración de Whisper AI
  - Análisis automático de streams de audio para detectar idiomas precisos
  - Validación segura de comandos subprocess con lista blanca (solo ffprobe/ffmpeg)
  - Detección de idiomas embebidos y externos con prioridades inteligentes
  - Optimización de recursos con modelo Whisper 'tiny' para mejor rendimiento

- **Limpieza Automática de Subtítulos Duplicados**:
  - Nuevo script `clean_duplicate_subtitles.py` para gestión de subtítulos
  - Eliminación automática de archivos .hi.srt (hindú) y .es-MX.srt duplicados
  - Priorización de subtítulos .es.srt sobre variantes regionales
  - Agrupación inteligente por nombre base de archivo para detectar duplicados
  - Compatibilidad con contenedor Docker y sistema host

- **Infraestructura Docker Optimizada**:
  - Reorganización completa de `docker-compose.yaml` con mejores prácticas
  - Límites de recursos (CPU, memoria) para todos los servicios
  - Healthchecks automáticos para servicios críticos
  - Configuración de logging centralizada con rotación automática
  - Optimización de volúmenes con permisos específicos (ro/rw)
  - Red personalizada con configuración de bridge optimizada

### Mejoras del Sistema

- **📊 Refactorización Completa del Cron Runner**:
  - Nuevo sistema de ejecución cíclica con detección de idiomas integrada
  - Ejecución secuencial: scanner → detector de idiomas → processor/subtítulos
  - Lógica horaria mejorada: compresión diurna, subtítulos nocturnos (00:00-05:59)
  - Integración directa de funciones Python en lugar de subprocess
  - Mejor manejo de concurrencia y bloqueos de archivos

- **🛡️ Seguridad y Validación Mejorada**:
  - Corrección de vulnerabilidades SonarQube en `mediajelly_language_detector.py`
  - Validación de entrada en todas las funciones críticas
  - Manejo seguro de subprocess con timeouts y validación de comandos
  - Eliminación de imports incorrectos y variables globales no utilizadas
  - Constantes definidas para rutas de archivos y configuración

- **⚡ Optimizaciones de Rendimiento**:
  - Modelo Whisper optimizado de 'small' a 'tiny' para mejor velocidad
  - Procesador con configuración adaptativa de concurrencia
  - Mejora en el manejo de memoria y CPU en contenedores
  - Optimización de timeouts y reintentos en operaciones críticas

### Correcciones

- **Sintaxis y Linting**:
  - Corrección de errores de sintaxis en scripts Python
  - Eliminación de código duplicado y variables no utilizadas
  - Mejora en la legibilidad y mantenibilidad del código

- **Gestión de Errores**:
  - Manejo mejorado de excepciones en operaciones críticas
  - Logging mejorado con emojis informativos
  - Recuperación automática de estados corruptos

### Documentación

- **README.md Actualizado**:
  - Nueva sección detallada de scripts Python del sistema
  - Descripción de funcionalidades clave y arquitectura
  - Información sobre detección de idiomas y limpieza de subtítulos
  - Estructura del proyecto actualizada con nuevos scripts

## [v3.2.3] - 2025-01-27

### Características

- **Doble pasada automática de traducción**:
  - Eliminación del sistema de verificación condicional de palabras en inglés
  - Implementación de doble pasada automática: el traductor procesa todo el contenido dos veces consecutivas
  - Simplificación del código eliminando listas hardcodeadas y funciones de verificación complejas
  - Mejor calidad de traducción garantizada mediante procesamiento redundante automático

- **Correcciones de linting**:
  - Arreglados errores de sintaxis y variables no utilizadas
  - Mejor legibilidad del código y reducción de complejidad cognitiva

## [v3.2.2] - 2025-10-21

### Mejoras

- **Soporte multiidioma para subtítulos embebidos**:
  - Ahora acepta subtítulos embebidos en cualquier idioma soportado (ES, EN, FR, DE, IT, PT, RU, JA, KO, ZH)
  - Detección automática de idioma de subtítulos embebidos antes de extraerlos
  - Mejor parsing de subtítulos con formato HTML para traducciones precisas
  - Reconstrucción correcta del formato HTML después de la traducción

- **Mejoras en calidad de traducción**:
  - Extracción de texto puro de etiquetas HTML antes de traducir
  - Reconstrucción del formato visual después de la traducción
  - Eliminación de traducciones mixtas (francés + español) en subtítulos embebidos
  - Mejor manejo de subtítulos con formato complejo

- **Control horario mejorado**:
  - **Detención automática a las 6:00 AM**: El procesamiento nocturno de subtítulos se detiene automáticamente cuando llega la hora límite (6:00 AM)
  - Verificación de hora en tiempo real durante el procesamiento
  - Optimización de recursos evitando procesamiento durante horas pico

- **Re-traducción de subtítulos existentes**:
  - Nueva opción `--retranslate` para forzar la re-traducción de archivos .es.srt ya existentes
  - Corrección de traducciones parciales en subtítulos embebidos complejos
  - Traducción completa de expresiones compuestas como "[Gasps, groans]" → "[Jadeos, gemidos]"

- **Optimizaciones de rendimiento**:
  - Procesamiento más rápido para subtítulos embebidos (6 minutos vs 7 minutos)
  - Reducción de líneas procesadas innecesarias
  - Mejor detección de subtítulos útiles vs subtítulos que requieren Whisper
  - **Simplificación de salida**: Solo se guarda el archivo final en español (.es.srt), eliminando archivos intermedios de debugging

### Correcciones

- **Permisos de archivos**: Corregido error de permisos en `pending-compression.txt` que impedía escribir archivos de pendientes
- **Método faltante**: Agregado método `send_error_notification()` en `MediaJellyCronRunner` para notificaciones de error
- **Manejo de errores**: Mejorado el manejo de excepciones en el proceso de escaneo
  - Notificación dedicada `send_night_subtitle_notification()` para resultados nocturnos
  - Detección automática de horario con `is_night_time()`
  - **Corrección de notificaciones duplicadas**: detecta y evita notificaciones repetidas cuando no hay cambios
  - **Nueva función `_reset_notification_state()`**: resetea `notified=false` cuando hay archivos nuevos
  - **Gestión de estados consistente**: `status` cambia a 'processing' al inicio y 'completed' al final

- **mediajelly_subtitle_translator.py**:
  - **Limpieza automática de extensiones de idioma**: archivos `.en.srt`, `.eng.srt`, etc. se convierten a `.es.srt` al traducir
  - Eliminación de extensiones duplicadas (evita `.en.es.srt`, genera directamente `.es.srt`)
  - Soporte para múltiples idiomas: `.fre`, `.ger`, `.ita`, `.por`, `.rus`, `.jpn`, `.kor`, `.chi`

### Rendimiento

- **Mejora del modelo Whisper**:
  - Cambio del modelo de Whisper de `small` a `tiny` para mejor rendimiento en procesamiento masivo (~3-5x más rápido)
  - **Revertido a `small`** para mejor precisión en pruebas de calidad
  - ~~Nueva funcionalidad de debugging: guarda archivos `.en.srt` (Whisper) y `.es.srt` (traducidos) para comparación~~
  - Mejor calidad de subtítulos extraídos del audio sin subtítulos embebidos
- **Separación horaria**: Reduce carga diurna, optimiza recursos para procesamiento intensivo de noche

### Notas Técnicas

- Procesamiento nocturno usa Whisper de forma secuencial (lock global)
- Archivos procesados diurnamente se marcan como completados, subtítulos pendientes para noche
- Notificaciones separadas para procesamiento diurno y nocturno

## [v3.2.0] - 2025-01-26

### Nuevas Funcionalidades Mayores

- **Procesamiento concurrente de subtítulos**:
  - Uso de `ThreadPoolExecutor` para procesar múltiples archivos simultáneamente
  - Parámetro `--max-workers` para controlar nivel de concurrencia (default: 2)
  - Thread-safe para escritura de `progress.json` y logs
  - Mejor aprovechamiento de CPU multi-core

- **Extracción de subtítulos del audio con Whisper**:
  - Integración de OpenAI Whisper para speech-to-text
  - Extracción automática cuando no hay subtítulos embebidos
  - Auto-detección de idioma del audio
  - Modelo `tiny` para eficiencia en CPU Intel i5-8250U
  - Parámetro `--no-whisper` para deshabilitar si es necesario
  - Generación de archivos .srt con timestamps precisos

### Mejoras en Procesamiento Concurrente

- **mediajelly_subtitle_translator.py**:
  - Nuevo método `extract_subtitles_from_audio()` con soporte Whisper
  - Método `_format_timestamp()` para conversión a formato SRT
  - Procesamiento concurrente en `process_directory()` y `process_file_list()`
  - Lock threading para operaciones thread-safe
  - Logs mejorados con indicadores visuales (✓, ✗)
  - Tracking de uso de Whisper en progress.json

- **Dockerfile.hybrid**:
  - Agregadas dependencias `openai-whisper`, `torch`, `torchaudio`
  - Optimización de instalación con `--no-cache-dir`
  - Soporte completo para speech recognition

- **mediajelly_cron_runner.py**:
  - Parámetros adicionales `max_workers` y `use_whisper` en `run_subtitle_translator()`
  - Logs informativos sobre configuración de Whisper y concurrencia

### Rendimiento en v3.2.0

- **Velocidad**: 2-3x más rápido con 2 workers en CPU de 4 cores
- **Whisper**: ~5-10x tiempo real (video 24min → 2-5min procesamiento)
- **RAM**: ~1-2GB por worker activo
- **Modelo**: Whisper `tiny` (~80-85% precisión, perfecto para traducción masiva de series)

### Documentación

- Nuevo archivo `INSTRUCCIONES_TRADUCCION_SUBTITULOS_V2.md` con:
  - Guía completa de uso de Whisper
  - Ejemplos de procesamiento concurrente
  - Requisitos de hardware y software
  - Troubleshooting y limitaciones
  - Métricas de rendimiento

### Notas Técnicas en v3.2.0

- Whisper se carga lazy (solo cuando se necesita)
- Audio temporal se extrae a 16kHz mono para Whisper
- Limpieza automática de archivos temporales
- Compatible con CPU (no requiere GPU, pero puede aprovecharla)
- Thread-safe: Múltiples workers pueden escribir logs sin conflictos

## [v3.1.0] - 2025-01-20

### Nuevas Funcionalidades en v3.1.0

- **Sistema de traducción automática de subtítulos**:
  - `mediajelly_subtitle_translator.py`: Nuevo script para extracción y traducción automática de subtítulos
  - Detección automática de archivos sin audio en español
  - Extracción de subtítulos embebidos usando ffmpeg
  - Traducción automática al español usando translatepy y langdetect
  - Integración completa con el flujo de procesamiento de MediaJelly
  - Notificaciones de Telegram con estadísticas de subtítulos traducidos
  - Registro detallado en archivo de log dedicado
  - Actualización del archivo progress.json con estadísticas de traducción
  - Soporte para procesar directorios completos o listas específicas de archivos

### Mejoras en Traducción de Subtítulos

- **Dockerfile.hybrid**:
  - Agregadas dependencias `translatepy` y `langdetect` para traducción de subtítulos
  - Optimización de instalación de paquetes Python
- **mediajelly_cron_runner.py**:
  - Integración del traductor de subtítulos en el flujo automático
  - Nuevas estadísticas de subtítulos en el sistema de tracking
  - Traducción solo de archivos procesados (comprimidos/renombrados)
  - El procesamiento se considera completo solo después de intentar la traducción
  - Notificaciones de éxito incluso si la traducción falla (con indicador de error)
  - Nuevo método `get_processed_files()` para obtener archivos procesados
- **mediajelly_notifier.py**:
  - Soporte para notificaciones de subtítulos traducidos
  - Nuevos parámetros en mensajes de Telegram para subtítulos
  - Mejora en formato de mensajes con información de traducción
  - Indicadores de estado de traducción (exitosa, parcial, fallida)
- **mediajelly_subtitle_translator.py**:
  - Nuevo método `process_file_list()` para procesar listas específicas de archivos
  - Flexibilidad para procesar directorios completos o archivos individuales

### Notas Técnicas en v3.1.0

- La traducción de subtítulos se ejecuta automáticamente después del procesamiento de archivos
- Solo se procesan archivos que fueron comprimidos, renombrados o marcados para procesamiento
- Solo se traducen archivos que no tienen audio en español
- Se omiten archivos que ya tienen subtítulos en español (_ES.srt)
- El sistema usa herramientas gratuitas y open-source (translatepy, langdetect, ffmpeg)
- No se requieren APIs de pago para la traducción
- Si la traducción falla, el procesamiento se marca como exitoso con indicador de traducción fallida

## [v3.0.0] - 2025-01-12

### Cambios Mayores

- **Migracion completa a Python**: Todos los scripts principales ahora estan escritos en Python para mejor mantenibilidad y robustez
- **Cambio de cliente torrent**: Reemplazo de qBittorrent con Transmission como cliente torrent principal
- **Reestructuracion del proyecto**: Movimiento de scripts desde `.scripts/` a `scripts/` para mejor organizacion

### Nuevas Funcionalidades

- **Scripts Python especializados**:
  - `mediajelly_cron_runner.py`: Orquestador principal optimizado
  - `mediajelly_scanner.py`: Escaner inteligente de archivos multimedia
  - `mediajelly_emoji.py`: Utilitario para manejo de emojis en notificaciones
  - `clean_duplicates.py`: Herramienta para limpieza de duplicados
  - `normalize_paths.py`: Utilitario para normalizacion de rutas
- **Dockerfile.hybrid**: Configuracion Docker para entorno hibrido
- **.dockerignore**: Optimizacion de construccion de imagenes Docker
- **Archivo .env**: Variables de entorno para configuracion

### Mejoras en Migración a Python

- **docker-compose.yaml**: Actualizado con configuracion de Transmission
- **.gitignore**: Patrones de exclusion mas especificos y completos
- **Scripts de instalacion**: Mejorada configuracion automatica del sistema
- **Configuracion de crontab**: Actualizada para nuevos scripts Python

### Eliminaciones

- **Scripts Bash obsoletos**:
  - `cleanup-partial-compressed.sh`
  - `cron-runner.sh`
  - `manage-logs.sh`
  - `process-compression.sh`
  - `scan-to-pending.sh`
  - `telegram-notify.sh`
  - `test-notifications.sh`
- **Dockerfile.cron**: Reemplazado por Dockerfile.hybrid
- **switch-mediajelly.sh**: Script de cambio no utilizado

## [v2.0.1] - 2025-01-12

### Mejoras v2.0.1

- **Calidad de codigo**: Correccion de errores identificados por SonarQube
  - Eliminacion de import duplicado de 'json' en mediajelly_python.py
  - Reemplazo de numeros magicos con constantes descriptivas
  - Agregado de REQUEST_TIMEOUT constante en mediajelly_notifier.py
- **Funcionalidad de verificacion**: Implementacion de `_check_all_pending_processed()` para verificar archivos ya procesados
- **Normalizacion de rutas**: Mejora en comparacion de rutas para diferentes formatos (/home/tafurc/mediaJelly/ vs /mediajelly/)
- **Validacion de integridad**: Validacion mejorada de archivos comprimidos con verificacion de streams de audio

### Correcciones v2.0.1

- **Errores de SonarQube**: Resueltos problemas de calidad de codigo identificados por analisis estatico
- **Verificacion de pendientes**: Archivos ya procesados ahora se verifican correctamente antes de reprocesamiento

## [v2.0.0] - 2025-09-08

### Nuevas Funcionalidades v2.0.0

- **Sistema de notificaciones Telegram**: Notificaciones automaticas de exitos y errores con informacion detallada
- **Ejecucion programada (Cron)**: Script cron-runner.sh para ejecutar automaticamente cada 12 horas
- **Gestion automatica de logs**: Rotacion automatica cuando superan 10MB, maximo 5 comprimidos
- **Validacion de compresion**: Detecta archivos comprimidos mas grandes que el original y corruptos
- **Limpieza de archivos parciales**: Elimina automaticamente archivos .compressed.mp4 corruptos o parciales
- **Procesamiento condicional**: Solo procesa cuando hay archivos pendientes
- **Configuracion de credenciales**: Archivo telegram.conf.example y crontab.example
- **Script de instalacion**: install.sh para configuracion automatica del sistema
- **Exclusion de directorios ocultos**: Filtrado de carpetas que empiezan con punto (.deleted, .trash, etc.)
- **Script de limpieza**: cleanup-partial-compressed.sh para mantenimiento manual

### Mejoras v2.0.0

- **Compresion adaptativa**: Configuracion de compresion basada en tamano del archivo
  - Archivos > 2GB: compresion agresiva (qp 32, preset slow)
  - Archivos > 1GB: compresion moderada (qp 30, preset medium)
  - Archivos < 1GB: compresion rapida (qp 28, preset fast)
  - Archivos < 500MB: solo renombrado a MP4
- **Logs optimizados**: Menos verbosos, solo registra cuando hay trabajo real
- **Validacion de archivos**: Verifica que los archivos existan antes de procesarlos
- **Estadisticas detalladas**: Informacion de reduccion de tamano y procesamiento
- **Manejo de errores robusto**: Mejor tracking de errores especificos por archivo
- **Gitignore actualizado**: Mas patrones de exclusion para limpieza del repositorio

### Correcciones v2.0.0

- **Problema de archivos parciales**: Evita corrupcion al reanudar despues de apagones
- **Validacion real de archivos nuevos**: El scanner ahora valida correctamente archivos nuevos
- **Verificacion de integridad**: Valida archivos comprimidos antes de reemplazar originales
- **Discrepancias en estadisticas**: Extraccion correcta de numeros en reportes de cron
- **Exclusion de carpetas temporales**: Evita procesar directorios .deleted y similares

### Eliminaciones v2.0.0

- **pending-movies.txt**: Archivo no utilizado removido del sistema

## [v1.2.0] - 2025-09-02

### Nuevas Funcionalidades v1.2.0

- **Configuracion de dispositivos para Jellyfin**: Soporte para hardware de GPU
- **Mejora en procesamiento de peliculas**: Mejor organizacion y renombrado
- **Scripts automatizados**: Compresion y procesamiento de medios

### Mejoras v1.2.0

- **Permisos de scripts**: Configuracion correcta de permisos de ejecucion
- **Documentacion**: README.md actualizado con estructura y uso

## [v1.1.0] - 2025-08-31

### Nuevas Funcionalidades v1.1.0

- **Servicio Samba**: Compartir archivos en red
- **Bazarr**: Gestion automatica de subtitulos
- **Script de compresion mejorado**: Renombrado y movimiento automatico de archivos

### Mejoras v1.1.0

- **Docker Compose**: Configuracion optimizada de servicios
- **Gestion de archivos**: Mejor organizacion de medios

## [v1.0.0] - 2025-08-28

### Nuevas Funcionalidades v1.0.0

- **Compresion VAAPI**: Utilizacion de ffmpeg con aceleracion por hardware
- **Deteccion inteligente de episodios**: Mejor organizacion de series
- **Scripts de automatizacion**: Procesamiento batch de archivos multimedia

### Mejoras v1.0.0

- **Eficiencia de compresion**: Uso de GPU para acelerar el procesamiento
- **Organizacion de archivos**: Estructura mejorada para medios

## [v0.3.0] - 2025-08-25

### Nuevas Funcionalidades v0.3.0

- **Gestion mejorada de archivos**: Scripts para compresion y organizacion
- **Deteccion de idiomas**: Filtrado por contenido en espanol
- **Logs detallados**: Seguimiento de procesamiento de archivos

### Mejoras v0.3.0

- **Compresion de medios**: Conversion automatica a MP4
- **Estructura de directorios**: Organizacion clara de scripts y logs

## [v0.2.0] - 2025-08-20

### Nuevas Funcionalidades v0.2.0

- **Scripts de compresion**: Automatizacion de conversion de medios
- **Gitignore**: Exclusion de archivos temporales y logs
- **Documentacion inicial**: README con instrucciones basicas

### Mejoras v0.2.0

- **Gestion de archivos**: Mejor organizacion de scripts y configuraciones

## [v0.1.0] - 2025-08-15

### Nuevas Funcionalidades v0.1.0

- **Configuracion inicial de Transmission**: Settings.json basico
- **Gitignore**: Exclusion de configuraciones sensibles
- **Estructura base del proyecto**: Directorios y configuracion inicial
