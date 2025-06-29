# Servidor Multimedia Personal

Este proyecto contiene la configuración y automatización de un servidor multimedia casero, utilizando contenedores Docker y scripts Bash para la gestión y compresión automática de archivos multimedia.

## Servicios incluidos

- **Sonarr**: Descarga y organiza series de TV automáticamente.
- **Radarr**: Descarga y organiza películas automáticamente.
- **Prowlarr**: Gestor de indexadores para Sonarr y Radarr.
- **Jellyfin**: Servidor de streaming multimedia.
- **Jellyseerr**: Solicitudes de contenido para Jellyfin.
- **Transmission**: Cliente BitTorrent para descargas automatizadas.
- **qbittorrent**: (comentado, alternativa a Transmission).

Todos los servicios están definidos en [`docker-compose.yaml`](docker-compose.yaml) y comparten una red interna.

## Estructura de carpetas

- `media/`  
  - `Peliculas/`  
  - `series/`  
- `config/`  
  - Configuraciones persistentes de cada servicio.
- `scripts/`  
  - Scripts Bash para escaneo, compresión y organización de archivos.
  - Logs y archivos temporales.

## Automatización con scripts

- [`scripts/scan-to-pending.sh`](scripts/scan-to-pending.sh):  
  Escanea carpetas de series y agrega archivos nuevos a la cola de compresión.
- [`scripts/process-compression.sh`](scripts/process-compression.sh):  
  Renombra, organiza y comprime episodios de series automáticamente.
- [`scripts/scan-to-pending-movies.sh`](scripts/scan-to-pending-movies.sh):  
  Escanea carpetas de películas y agrega archivos nuevos a la cola de compresión.
- [`scripts/process-movies.sh`](scripts/process-movies.sh):  
  Renombra y comprime películas automáticamente.

## Uso rápido

1. Clona el repositorio y ajusta rutas si es necesario.
2. Lanza los servicios con Docker Compose:
   ```sh
   docker compose up -d
   ```
3. Ejecuta los scripts de escaneo para poblar las colas de compresión:
   ```sh
   ./scripts/scan-to-pending.sh /ruta/a/series
   ./scripts/scan-to-pending-movies.sh /ruta/a/peliculas
   ```
4. Los scripts de procesamiento se ejecutan automáticamente al final del escaneo, pero puedes lanzarlos manualmente si lo deseas.

## Notas

- Los scripts requieren `ffmpeg` instalado en el sistema anfitrión.
- Los archivos menores a 1GB solo se renombran y mueven, no se comprimen.
- Los logs se almacenan en `scripts/logs/`.

---

> Proyecto personal para automatizar la gestión y streaming de contenido multimedia en el hogar, utilizando tecnologías de código abierto y contenedores ligeros.
