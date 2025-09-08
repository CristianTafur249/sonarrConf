# Changelog - MediaJelly

Registro de cambios del proyecto MediaJelly, un servidor multimedia automatizado con Docker.

## [Unreleased] - 2025-09-07

### ✨ Añadido
- **Sistema de notificaciones Telegram**: Notificaciones automáticas de éxitos y errores
- **Ejecución programada (Cron)**: Script para ejecutar automáticamente cada 12 horas
- **Gestión automática de logs**: Rotación automática cuando superan 10MB, máximo 5 comprimidos
- **Validación de compresión**: Detecta archivos comprimidos más grandes que el original
- **Limpieza de archivos parciales**: Elimina automáticamente archivos `.compressed.mp4` corruptos
- **Procesamiento condicional**: Solo procesa cuando hay archivos pendientes
- **Configuración de credenciales**: Archivo de configuración para Telegram (no versionado)

### 🔧 Mejorado
- **Compresión adaptativa**: Configuración de compresión basada en tamaño del archivo
  - Archivos > 2GB: compresión agresiva (qp 32, preset slow)
  - Archivos > 1GB: compresión moderada (qp 30, preset medium) 
  - Archivos < 1GB: compresión rápida (qp 28, preset fast)
- **Logs optimizados**: Menos verbosos, solo registra cuando hay trabajo real
- **Validación de archivos**: Verifica que los archivos existan antes de procesarlos
- **Estadísticas detalladas**: Información de reducción de tamaño y procesamiento

### 🐛 Corregido
- **Problema de archivos parciales**: Evita corrupción al reanudar después de apagones
- **Validación real de archivos nuevos**: El scanner ahora valida correctamente archivos nuevos
- **Verificación de integridad**: Valida archivos comprimidos antes de reemplazar originales

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
