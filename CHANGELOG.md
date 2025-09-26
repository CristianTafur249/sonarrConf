# Changelog - MediaJelly

Registro de cambios del proyecto MediaJelly, un servidor multimedia automatizado con Docker.

## [Unreleased]

### En desarrollo

- Proximas mejoras y caracteristicas

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

### Mejoras

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
