#!/usr/bin/env python3
"""
MediaJelly API REST - Dashboard Web
Proporciona endpoints para monitorear y controlar MediaJelly
"""

import os
import sys
import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware

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

# Crear app FastAPI
app = FastAPI(
    title="MediaJelly API",
    description="API REST para monitorear y controlar MediaJelly",
    version="1.0.0"
)

# Configurar CORS para permitir acceso desde navegador
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
async def root():
    """Endpoint raíz"""
    return {
        "service": "MediaJelly API",
        "version": "1.0.0",
        "status": "running",
        "endpoints": {
            "status": "/api/status",
            "queue": "/api/queue",
            "stats": "/api/stats",
            "metrics": "/api/metrics",
            "logs": "/api/logs",
            "health": "/health"
        }
    }


@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "timestamp": datetime.now().isoformat()}


@app.get("/api/status")
async def get_status():
    """Obtiene el estado general del sistema"""
    try:
        # Leer archivo de progreso
        progress_file = tmp_dir / "progress.json"
        if progress_file.exists():
            with open(progress_file, 'r', encoding='utf-8') as f:
                progress = json.load(f)
        else:
            progress = {}

        # Obtener información del sistema
        import psutil
        
        status = {
            "system": {
                "cpu_percent": psutil.cpu_percent(interval=1),
                "memory_percent": psutil.virtual_memory().percent,
                "disk_usage": psutil.disk_usage(str(base_dir)).percent,
            },
            "processing": progress.get("processing", {}),
            "subtitle_translation": progress.get("subtitle_translation", {}),
            "language_detection": progress.get("language_detection", {}),
            "timestamp": datetime.now().isoformat()
        }

        return JSONResponse(content=status)

    except Exception as e:
        logger.error(f"Error obteniendo estado: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/queue")
async def get_queue():
    """Obtiene la cola de archivos pendientes"""
    try:
        pending_file = tmp_dir / "pending-compression.txt"
        completed_file = tmp_dir / "completed.txt"

        pending_files = []
        if pending_file.exists():
            with open(pending_file, 'r', encoding='utf-8') as f:
                pending_files = [line.strip() for line in f if line.strip()]

        completed_files = []
        if completed_file.exists():
            with open(completed_file, 'r', encoding='utf-8') as f:
                completed_files = [line.strip() for line in f if line.strip()]

        return {
            "pending_count": len(pending_files),
            "completed_count": len(completed_files),
            "pending_files": pending_files[:50],  # Limitar a 50 para no sobrecargar
            "timestamp": datetime.now().isoformat()
        }

    except Exception as e:
        logger.error(f"Error obteniendo cola: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/stats")
async def get_stats():
    """Obtiene estadísticas de procesamiento"""
    try:
        # Leer archivo de progreso
        progress_file = tmp_dir / "progress.json"
        if progress_file.exists():
            with open(progress_file, 'r', encoding='utf-8') as f:
                progress = json.load(f)
                stats = progress.get("processing", {}).get("stats", {})
        else:
            stats = {}

        return {
            "stats": stats,
            "timestamp": datetime.now().isoformat()
        }

    except Exception as e:
        logger.error(f"Error obteniendo estadísticas: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/metrics")
async def get_metrics():
    """Obtiene métricas de procesamiento"""
    try:
        metrics_file = logs_dir / "processing_metrics.json"
        if metrics_file.exists():
            with open(metrics_file, 'r', encoding='utf-8') as f:
                metrics = json.load(f)
                
                # Si es una lista, tomar las últimas 10 entradas
                if isinstance(metrics, list):
                    metrics = metrics[-10:]
                    
        else:
            metrics = []

        return {
            "metrics": metrics,
            "count": len(metrics) if isinstance(metrics, list) else 1,
            "timestamp": datetime.now().isoformat()
        }

    except Exception as e:
        logger.error(f"Error obteniendo métricas: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/logs")
async def get_logs(lines: int = 100):
    """Obtiene las últimas líneas de logs"""
    try:
        # Leer log del procesador
        log_file = logs_dir / "mediajelly_processor.log"
        if not log_file.exists():
            return {
                "logs": [],
                "count": 0,
                "timestamp": datetime.now().isoformat()
            }

        # Leer últimas N líneas
        with open(log_file, 'r', encoding='utf-8', errors='ignore') as f:
            all_lines = f.readlines()
            last_lines = all_lines[-lines:] if len(all_lines) > lines else all_lines

        return {
            "logs": [line.strip() for line in last_lines],
            "count": len(last_lines),
            "timestamp": datetime.now().isoformat()
        }

    except Exception as e:
        logger.error(f"Error obteniendo logs: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/logs/download")
async def download_logs():
    """Descarga el archivo de log completo"""
    try:
        log_file = logs_dir / "mediajelly_processor.log"
        if not log_file.exists():
            raise HTTPException(status_code=404, detail="Log file not found")

        return FileResponse(
            path=str(log_file),
            filename="mediajelly_processor.log",
            media_type="text/plain"
        )

    except Exception as e:
        logger.error(f"Error descargando logs: {e}")
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    
    # Configurar puerto
    port = int(os.getenv("MEDIAJELLY_API_PORT", "8000"))
    
    logger.info(f"Iniciando MediaJelly API en puerto {port}")
    logger.info(f"Base directory: {base_dir}")
    logger.info(f"Logs directory: {logs_dir}")
    
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=port,
        log_level="info"
    )
