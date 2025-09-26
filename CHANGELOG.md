# Changelog - MediaJelly

Registro de cambios del proyecto MediaJelly, un servidor multimedia automatizado con Docker.

## [Unreleased]

### En desarrollo

- Próximas mejoras y características

## [v2.0.1] - 2025-01-12

### 🔧 Mejorado

- **Calidad de código**: Corrección de errores identificados por SonarQube
  - Eliminación de import duplicado de 'json' en mediajelly_python.py
  - Reemplazo de números mágicos con constantes descriptivas
  - Agregado de REQUEST_TIMEOUT constante en mediajelly_notifier.py
- **Funcionalidad de verificación**: Implementación de `_check_all_pending_processed()` para verificar archivos ya procesados
- **Normalización de rutas**: Mejora en comparación de rutas para diferentes formatos (/home/tafurc/mediaJelly/ vs /mediajelly/)
- **Validación de integridad**: Validación mejorada de archivos comprimidos con verificación de streams de audio

### 🐛 Corregido

- **Errores de SonarQube**: Resueltos problemas de calidad de código identificados por análisis estático
- **Verificación de pendientes**: Archivos ya procesados ahora se verifican correctamente antes de reprocesamiento

## [v2.0.0] - 2025-09-08

### ✨ Añadido

- **Sistema de notificaciones Telegram**: Notificaciones automáticas de éxitos y errores con información detallada
- **Ejecución programada (Cron)**: Script cron-runner.sh para ejecutar automáticamente cada 12 horas
- **Gestión automática de logs**: Rotación automática cuando superan 10MB, máximo 5 comprimidos
- **Validación de compresión**: Detecta archivos comprimidos más grandes que el original y corruptos
- **Limpieza de archivos parciales**: Elimina automáticamente archivos .compressed.mp4 corruptos o parciales
- **Procesamiento condicional**: Solo procesa cuando hay archivos pendientes
- **Configuración de credenciales**: Archivo telegram.conf.example y crontab.example
- **Script de instalación**: install.sh para configuración automática del sistema
- **Exclusión de directorios ocultos**: Filtrado de carpetas que empiezan con punto (.deleted, .trash, etc.)
- **Script de limpieza**: cleanup-partial-compressed.sh para mantenimiento manual

### 🔧 Mejorado

- **Compresión adaptativa**: Configuración de compresión basada en tamaño del archivo
  - Archivos > 2GB: compresión agresiva (qp 32, preset slow)
  - Archivos > 1GB: compresión moderada (qp 30, preset medium)
  - Archivos < 1GB: compresión rápida (qp 28, preset fast)
  - Archivos < 500MB: solo renombrado a MP4
- **Logs optimizados**: Menos verbosos, solo registra cuando hay trabajo real
- **Validación de archivos**: Verifica que los archivos existan antes de procesarlos
- **Estadísticas detalladas**: Información de reducción de tamaño y procesamiento
- **Manejo de errores robusto**: Mejor tracking de errores específicos por archivo
- **Gitignore actualizado**: Más patrones de exclusión para limpieza del repositorio

### 🐛 Corregido

- **Problema de archivos parciales**: Evita corrupción al reanudar después de apagones
- **Validación real de archivos nuevos**: El scanner ahora valida correctamente archivos nuevos
- **Verificación de integridad**: Valida archivos comprimidos antes de reemplazar originales
- **Discrepancias en estadísticas**: Extracción correcta de números en reportes de cron
- **Exclusión de carpetas temporales**: Evita procesar directorios .deleted y similares

### 🗑️ Eliminado

- **pending-movies.txt**: Archivo no utilizado removido del sistema

## [v1.2.0] - 2025-09-02

### ✨ Añadido

- **Configuración de dispositivos para Jellyfin**: Soporte para hardware de GPU
- **Mejora en procesamiento de películas**: Mejor organización y renombrado
- **Scripts automatizados**: Compresión y procesamiento de medios

### 🔧 Mejorado

- **Permisos de scripts**: Configuración correcta de permisos de ejecución
- **Documentación**: README.md actualizado con estructura y uso

## [v1.1.0] - 2025-08-31

### ✨ Añadido

- **Servicio Samba**: Compartir archivos en red
- **Bazarr**: Gestión automática de subtítulos
- **Script de compresión mejorado**: Renombrado y movimiento automático de archivos

### 🔧 Mejorado

- **Docker Compose**: Configuración optimizada de servicios
- **Gestión de archivos**: Mejor organización de medios

## [v1.0.0] - 2025-08-28

### ✨ Añadido

- **Compresión VAAPI**: Utilización de ffmpeg con aceleración por hardware
- **Detección inteligente de episodios**: Mejor organización de series
- **Scripts de automatización**: Procesamiento batch de archivos multimedia

### 🔧 Mejorado

- **Eficiencia de compresión**: Uso de GPU para acelerar el procesamiento
- **Organización de archivos**: Estructura mejorada para medios

## [v0.3.0] - 2025-08-25

### ✨ Añadido

- **Gestión mejorada de archivos**: Scripts para compresión y organización
- **Detección de idiomas**: Filtrado por contenido en español
- **Logs detallados**: Seguimiento de procesamiento de archivos

### 🔧 Mejorado

- **Compresión de medios**: Conversión automática a MP4
- **Estructura de directorios**: Organización clara de scripts y logs

## [v0.2.0] - 2025-08-20

### ✨ Añadido

- **Scripts de compresión**: Automatización de conversión de medios
- **Gitignore**: Exclusión de archivos temporales y logs
- **Documentación inicial**: README con instrucciones básicas

### 🔧 Mejorado

- **Gestión de archivos**: Mejor organización de scripts y configuraciones

## [v0.1.0] - 2025-08-15

### ✨ Añadido

- **Configuración inicial de Transmission**: Settings.json básico
- **Gitignore**: Exclusión de configuraciones sensibles
- **Estructura base del proyecto**: Directorios y configuración inicial

---

## Leyenda

- ✨ **Añadido**: Nuevas características
- 🔧 **Mejorado**: Mejoras en características existentes
- 🐛 **Corregido**: Corrección de errores
- 🗑️ **Eliminado**: Características removidas
- 🔒 **Seguridad**: Correcciones de seguridad
