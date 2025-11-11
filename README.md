# MediaJelly - Servidor Multimedia Automatizado 🎬

[![Docker](https://img.shields.io/badge/Docker-Compose-blue)](docker-compose.yaml)
[![Scripts](https://img.shields.io/badge/Scripts-Bash-green)](scripts/)
[![License](https://img.shields.io/badge/License-MIT-yellow)](LICENSE)

Un servidor multimedia completamente automatizado con Docker, que incluye descarga, organización, compresión inteligente y notificaciones por Telegram.

## 🚀 Características principales

- **🎯 Totalmente automatizado**: Descarga, organiza y comprime contenido automáticamente
- **�️ Detección inteligente de idiomas**: Análisis automático de audio con Whisper AI para subtítulos precisos
- **🧹 Limpieza automática de subtítulos**: Eliminación de duplicados y archivos de baja calidad
- **📱 Notificaciones Telegram**: Recibe reportes de éxito y errores con emojis informativos
- **⏰ Ejecución programada**: Cron jobs para procesamiento automático cada 12 horas
- **🗜️ Compresión inteligente**: Optimización basada en el tamaño del archivo con límites de recursos
- **🔧 Recuperación de errores**: Manejo robusto de interrupciones y archivos corruptos
- **🏗️ Infraestructura optimizada**: Docker Compose con healthchecks, límites de memoria y CPU

## 🐳 Servicios incluidos

| Servicio | Puerto | Descripción |
|----------|--------|-------------|
| **Sonarr** | 8989 | Descarga y organiza series automáticamente |
| **Radarr** | 7878 | Descarga y organiza películas automáticamente |
| **Prowlarr** | 9696 | Gestor de indexadores torrent |
| **Jellyfin** | 8096 | Servidor de streaming multimedia |
| **Jellyseerr** | 5055 | Interface de solicitudes de contenido |
| **Transmission** | 9091 | Cliente BitTorrent |
| **Bazarr** | 6767 | Gestión automática de subtítulos |
| **Portainer** | 9000 | Gestión de contenedores Docker |

## 📁 Estructura del proyecto

```text
mediaJelly/
├── docker-compose.yaml          # Configuración de servicios con límites de recursos
├── media/                       # Contenido multimedia
│   ├── Peliculas/              # Películas organizadas
│   ├── series/                 # Series organizadas
│   └── anime/
├── config/                     # Configuraciones persistentes
│   ├── telegram.conf          # Credenciales de Telegram (no versionado)
│   └── */                     # Configs de cada servicio
└── scripts/                   # Scripts de automatización Python
    ├── mediajelly_cron_runner.py    # Orquestador principal del sistema
    ├── mediajelly_scanner.py        # Escaneo inteligente de archivos nuevos
    ├── mediajelly_processor.py      # Compresión y procesamiento multimedia
    ├── mediajelly_language_detector.py # Detección de idiomas con Whisper AI
    ├── mediajelly_subtitle_translator.py # Traducción automática de subtítulos
    ├── mediajelly_notifier.py       # Sistema de notificaciones Telegram
    ├── clean_duplicate_subtitles.py # Limpieza de subtítulos duplicados
    ├── cancel_processing.sh         # Cancelación de procesos en ejecución
    ├── logs/                        # Logs del sistema
    └── tmp/                         # Archivos temporales
```

## ⚡ Instalación rápida

### 1. Clona el repositorio

```bash
git clone https://github.com/CristianTafur249/mediaJelly.git
cd mediaJelly
```

### 2. Configura las notificaciones Telegram (opcional)

```bash
cp config/telegram.conf.example config/telegram.conf
# Edita el archivo con tus credenciales de Telegram
```

### 3. Inicia los servicios

```bash
docker compose up -d
```

### 4. Configura el cron job (opcional)

```bash
# Añade esta línea a tu crontab (crontab -e)
0 */6 * * * /home/tu_usuario/mediaJelly/scripts/cron-runner.sh
```

## 🔧 Configuración de Telegram

1. Crea un bot con [@BotFather](https://t.me/BotFather) en Telegram
2. Obtén tu `chat_id` enviando `/start` a [@userinfobot](https://t.me/userinfobot)
3. Edita `config/telegram.conf`:

```bash
TELEGRAM_BOT_TOKEN="tu_bot_token_aqui"
TELEGRAM_CHAT_ID="tu_chat_id_aqui"
NOTIFY_ON_SUCCESS=true
NOTIFY_ON_ERROR=true
```

## 📋 Uso manual

### Escanear y procesar contenido

```bash
# Escanear toda la biblioteca de medios
./scripts/scan-to-pending.sh /path/to/media

# Escanear solo películas
./scripts/scan-to-pending.sh /path/to/media/Peliculas

# Escanear solo series
./scripts/scan-to-pending.sh /path/to/media/series
```

### Probar notificaciones

```bash
./scripts/telegram-notify.sh test
```

### Limpiar archivos corruptos

```bash
./scripts/cleanup-partial-compressed.sh /path/to/media
```

## 🎛️ Compresión inteligente

El sistema adapta la compresión según el tamaño del archivo:

- **Archivos > 2GB**: Compresión agresiva (`qp 32, preset slow`)
- **Archivos > 1GB**: Compresión moderada (`qp 30, preset medium`)
- **Archivos < 1GB**: Compresión rápida (`qp 28, preset fast`)
- **Archivos < 500MB**: Solo renombrado a MP4

## 📊 Gestión de logs

- Los logs se rotan automáticamente cuando superan 10MB
- Se mantienen máximo 5 archivos comprimidos por tipo
- Limpieza automática de archivos antiguos

## 🔒 Archivos no versionados

Los siguientes archivos se excluyen del control de versiones:

- `config/telegram.conf` - Credenciales de Telegram
- `media/*` - Contenido multimedia
- `scripts/logs/*` - Logs del sistema
- `scripts/tmp/*` - Archivos temporales

## � Scripts Python del Sistema

### Core Scripts

| Script | Función | Descripción |
|--------|---------|-------------|
| **mediajelly_cron_runner.py** | 🕐 Orquestador principal | Coordina escaneo, detección de idiomas, procesamiento y notificaciones |
| **mediajelly_scanner.py** | 🔍 Escáner inteligente | Detecta archivos nuevos y pendientes de procesamiento |
| **mediajelly_language_detector.py** | 🗣️ Detector de idiomas | Usa Whisper AI para analizar audio y detectar idiomas |
| **mediajelly_processor.py** | 🗜️ Procesador multimedia | Compresión inteligente con algoritmos adaptativos |
| **mediajelly_subtitle_translator.py** | 🌐 Traductor de subtítulos | Traducción automática con doble pasada para calidad |
| **mediajelly_notifier.py** | 📱 Notificador Telegram | Sistema de notificaciones con emojis y estado persistente |

### Utility Scripts

| Script | Función | Descripción |
|--------|---------|-------------|
| **clean_duplicate_subtitles.py** | 🧹 Limpiador de subtítulos | Elimina duplicados y archivos de baja calidad (.hi.srt, .es-MX.srt) |
| **cancel_processing.sh** | 🛑 Cancelador de procesos | Detiene procesos de compresión en ejecución |

### Funcionalidades Clave

- **🗣️ Detección Inteligente**: Análisis de audio con Whisper AI para subtítulos precisos
- **🧹 Limpieza Automática**: Eliminación de subtítulos duplicados y de baja calidad
- **📊 Estado Persistente**: Seguimiento de progreso y notificaciones para evitar duplicados
- **🔄 Recuperación de Errores**: Reintentos automáticos y manejo robusto de fallos
- **⚡ Optimización de Recursos**: Límites de memoria, CPU y healthchecks en Docker

## �🐛 Solución de problemas

### Error de permisos en archivos de lock

Si encuentras errores como `Permission denied: '/mediajelly/scripts/tmp/cron_python.lock'`:

```bash
# Ejecutar el script de corrección de permisos
sudo ./scripts/fix_permissions.sh
```

Este script:

- Ajusta los permisos de directorios críticos (`tmp/` y `logs/`)
- Elimina archivos de lock antiguos que puedan causar problemas
- Asegura que los scripts Python sean ejecutables

### Error de permisos general

```bash
chmod +x scripts/*.sh
chmod +x scripts/*.py
```

### Problema con archivos parcialmente comprimidos

```bash
./scripts/cleanup-partial-compressed.sh /path/to/media
```

### Verificar estado de servicios

```bash
docker compose logs -f servicio_nombre
```

## 📖 Documentación adicional

- [CHANGELOG.md](CHANGELOG.md) - Historial de cambios
- [docker-compose.yaml](docker-compose.yaml) - Configuración de servicios

## 🤝 Contribuir

1. Fork el proyecto
2. Crea una rama para tu feature (`git checkout -b feature/nueva-caracteristica`)
3. Commit tus cambios (`git commit -am 'Agregar nueva característica'`)
4. Push a la rama (`git push origin feature/nueva-caracteristica`)
5. Abre un Pull Request

## 📄 Licencia

Este proyecto está bajo la Licencia MIT. Ver [LICENSE](LICENSE) para más detalles.
4. Los scripts de procesamiento se ejecutan automáticamente al final del escaneo, pero puedes lanzarlos manualmente si lo deseas.

## Notas

- Los scripts requieren `ffmpeg` instalado en el sistema anfitrión.
- Los archivos menores a 1GB solo se renombran y mueven, no se comprimen.
- Los logs se almacenan en `scripts/logs/`.

---

> Proyecto personal para automatizar la gestión y streaming de contenido multimedia en el hogar, utilizando tecnologías de código abierto y contenedores ligeros.
