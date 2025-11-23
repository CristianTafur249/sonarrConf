#!/usr/bin/env python3
"""
MediaJelly Prometheus Exporter
Exporta métricas de MediaJelly en formato Prometheus
"""

import os
import sys
import json
import time
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, Optional

from prometheus_client import start_http_server, Gauge, Counter, Histogram, Info
from prometheus_client.core import CollectorRegistry

# Configuración de logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuración de rutas
is_container = Path("/mediajelly").exists()
base_dir = Path("/mediajelly" if is_container else os.path.expanduser("~/mediaJelly"))
scripts_dir = base_dir / "scripts"
logs_dir = scripts_dir / "logs"
tmp_dir = scripts_dir / "tmp"

# Crear registro de métricas
registry = CollectorRegistry()

# Definir métricas de Prometheus
mediajelly_info = Info(
    'mediajelly',
    'MediaJelly system information',
    registry=registry
)

# Métricas de procesamiento
files_processed_total = Counter(
    'mediajelly_files_processed_total',
    'Total number of files processed',
    registry=registry
)

files_compressed_total = Counter(
    'mediajelly_files_compressed_total',
    'Total number of files compressed',
    registry=registry
)

files_errors_total = Counter(
    'mediajelly_files_errors_total',
    'Total number of processing errors',
    registry=registry
)

compression_ratio = Gauge(
    'mediajelly_compression_ratio',
    'Current compression ratio',
    registry=registry
)

processing_time_seconds = Histogram(
    'mediajelly_processing_time_seconds',
    'Time spent processing files',
    buckets=[10, 30, 60, 120, 300, 600, 1800, 3600],
    registry=registry
)

space_saved_bytes = Gauge(
    'mediajelly_space_saved_bytes',
    'Total space saved in bytes',
    registry=registry
)

queue_pending_files = Gauge(
    'mediajelly_queue_pending_files',
    'Number of files in pending queue',
    registry=registry
)

queue_completed_files = Gauge(
    'mediajelly_queue_completed_files',
    'Number of completed files',
    registry=registry
)

# Métricas de traducción de subtítulos
subtitles_translated_total = Counter(
    'mediajelly_subtitles_translated_total',
    'Total number of subtitles translated',
    registry=registry
)

subtitles_translation_errors_total = Counter(
    'mediajelly_subtitles_translation_errors_total',
    'Total number of subtitle translation errors',
    registry=registry
)

subtitles_extracted_total = Counter(
    'mediajelly_subtitles_extracted_total',
    'Total number of subtitles extracted from audio',
    registry=registry
)

translation_time_seconds = Histogram(
    'mediajelly_translation_time_seconds',
    'Time spent translating subtitles',
    buckets=[1, 5, 10, 30, 60, 120, 300],
    registry=registry
)

# Métricas de sistema
system_cpu_percent = Gauge(
    'mediajelly_system_cpu_percent',
    'System CPU usage percentage',
    registry=registry
)

system_memory_percent = Gauge(
    'mediajelly_system_memory_percent',
    'System memory usage percentage',
    registry=registry
)

system_disk_percent = Gauge(
    'mediajelly_system_disk_percent',
    'System disk usage percentage',
    registry=registry
)

# Métricas de estado
processing_status = Gauge(
    'mediajelly_processing_status',
    'Processing status (1=running, 0=idle)',
    registry=registry
)

last_update_timestamp = Gauge(
    'mediajelly_last_update_timestamp',
    'Timestamp of last metrics update',
    registry=registry
)


class MediaJellyMetricsCollector:
    """Colector de métricas de MediaJelly para Prometheus"""

    def __init__(self):
        self.progress_file = tmp_dir / "progress.json"
        self.metrics_file = tmp_dir / "processing_metrics.json"
        self.pending_file = tmp_dir / "pending-compression.txt"
        self.completed_file = tmp_dir / "completed.txt"

        # Configurar información del sistema
        mediajelly_info.info({
            'version': '3.2.4',
            'base_dir': str(base_dir),
        })

    def update_metrics(self):
        """Actualiza todas las métricas desde archivos de estado"""
        try:
            logger.info("Actualizando métricas...")
            self._update_processing_metrics()
            self._update_translation_metrics()
            self._update_queue_metrics()
            self._update_system_metrics()
            
            # Actualizar timestamp de última actualización
            last_update_timestamp.set(time.time())
            logger.info("Métricas actualizadas exitosamente")

        except Exception as e:
            logger.error(f"Error actualizando métricas: {e}")

    def _update_processing_metrics(self):
        """Actualiza métricas de procesamiento"""
        try:
            # Leer métricas guardadas
            if self.metrics_file.exists():
                with open(self.metrics_file, 'r', encoding='utf-8') as f:
                    metrics_data = json.load(f)

                # Manejar tanto listas como diccionarios
                if isinstance(metrics_data, list) and metrics_data:
                    latest_metrics = metrics_data[-1]
                elif isinstance(metrics_data, dict):
                    latest_metrics = metrics_data
                else:
                    latest_metrics = {}

                # Actualizar métricas de Prometheus
                compression_ratio.set(latest_metrics.get('compression_ratio', 0.0))
                space_saved_bytes.set(latest_metrics.get('space_saved_gb', 0.0) * 1024**3)
                
                # Actualizar histograma de tiempo de procesamiento
                avg_time = latest_metrics.get('avg_time_per_file', 0.0)
                if avg_time > 0:
                    processing_time_seconds.observe(avg_time)

            # Leer progreso actual
            if self.progress_file.exists():
                with open(self.progress_file, 'r', encoding='utf-8') as f:
                    progress = json.load(f)

                processing_data = progress.get("processing", {})
                stats = processing_data.get("stats", {})

                # Actualizar contadores absolutos
                files_processed_total._value.set(stats.get("files_processed", 0))
                files_compressed_total._value.set(stats.get("files_compressed", 0))
                files_errors_total._value.set(len(stats.get("errors", [])))

                # Determinar estado de procesamiento
                status = processing_data.get("status", "idle")
                processing_status.set(1 if status == "processing" else 0)

        except Exception as e:
            logger.error(f"Error actualizando métricas de procesamiento: {e}")

    def _update_translation_metrics(self):
        """Actualiza métricas de traducción de subtítulos"""
        try:
            translation_metrics_file = tmp_dir / "translation_metrics.json"
            
            if translation_metrics_file.exists():
                with open(translation_metrics_file, 'r', encoding='utf-8') as f:
                    metrics_data = json.load(f)

                # Manejar tanto listas como diccionarios
                if isinstance(metrics_data, list) and metrics_data:
                    latest_metrics = metrics_data[-1]  # Última entrada
                elif isinstance(metrics_data, dict):
                    latest_metrics = metrics_data
                else:
                    return

                # Actualizar métricas de traducción
                subtitles_translated_total._value.set(latest_metrics.get('subtitles_translated', 0))
                subtitles_translation_errors_total._value.set(latest_metrics.get('translation_errors', 0))
                subtitles_extracted_total._value.set(latest_metrics.get('subtitles_extracted', 0))
                
                # Actualizar histograma de tiempo de traducción
                avg_translation_time = latest_metrics.get('avg_translation_time', 0.0)
                if avg_translation_time > 0:
                    translation_time_seconds.observe(avg_translation_time)

        except Exception as e:
            logger.error(f"Error actualizando métricas de traducción: {e}")

    def _update_queue_metrics(self):
        """Actualiza métricas de cola"""
        try:
            # Contar archivos pendientes
            pending_count = 0
            if self.pending_file.exists():
                with open(self.pending_file, 'r', encoding='utf-8') as f:
                    pending_count = sum(1 for line in f if line.strip())

            # Contar archivos completados
            completed_count = 0
            if self.completed_file.exists():
                with open(self.completed_file, 'r', encoding='utf-8') as f:
                    completed_count = sum(1 for line in f if line.strip())

            queue_pending_files.set(pending_count)
            queue_completed_files.set(completed_count)

        except Exception as e:
            logger.error(f"Error actualizando métricas de cola: {e}")

    def _update_system_metrics(self):
        """Actualiza métricas del sistema"""
        try:
            import psutil

            system_cpu_percent.set(psutil.cpu_percent(interval=1))
            system_memory_percent.set(psutil.virtual_memory().percent)
            system_disk_percent.set(psutil.disk_usage(str(base_dir)).percent)

        except Exception as e:
            logger.error(f"Error actualizando métricas del sistema: {e}")


def main():
    """Función principal"""
    # Configurar puerto
    port = 9090  # Puerto fijo para evitar conflictos
    # port = int(os.getenv("MEDIAJELLY_METRICS_PORT", "9090"))
    
    logger.info(f"Iniciando Prometheus exporter en puerto {port}")
    logger.info(f"Base directory: {base_dir}")
    
    # Iniciar servidor HTTP
    start_http_server(port, registry=registry)
    
    # Crear colector
    collector = MediaJellyMetricsCollector()
    
    # Intervalo de actualización en segundos
    update_interval = int(os.getenv("MEDIAJELLY_METRICS_INTERVAL", "15"))
    
    logger.info(f"Actualizando métricas cada {update_interval} segundos")
    
    # Loop principal
    try:
        while True:
            collector.update_metrics()
            time.sleep(update_interval)
    except KeyboardInterrupt:
        logger.info("Deteniendo exporter...")


if __name__ == "__main__":
    main()
