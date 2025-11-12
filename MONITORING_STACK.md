# 📊 MediaJelly Monitoring Stack

## ✅ Servicios Desplegados

### 🚀 Tareas Completadas

- **Tarea #14**: Límites Dinámicos de Recursos (get_optimal_workers)
- **Tarea #16**: Dashboard REST API
- **Tarea #17**: Monitoreo con Grafana
- **Tarea #26**: Sistema de Métricas

## 🌐 Acceso a los Servicios

### **API REST** (Puerto 8000)

- **URL**: <http://localhost:8000>
- **Endpoints disponibles**:
  - `GET /` - Información del servicio
  - `GET /health` - Health check
  - `GET /api/status` - Estado del sistema y procesamiento
  - `GET /api/queue` - Archivos en cola y completados
  - `GET /api/stats` - Estadísticas de procesamiento
  - `GET /api/metrics` - Métricas históricas
  - `GET /api/logs` - Últimas líneas de logs
  - `GET /api/logs/download` - Descarga completa de logs

**Ejemplo de uso**:

```bash
# Ver estado del sistema
curl http://localhost:8000/api/status | jq

# Ver cola de procesamiento
curl http://localhost:8000/api/queue | jq

# Ver métricas
curl http://localhost:8000/api/metrics | jq
```

### **Métricas Prometheus** (Puerto 9090)

- **URL**: <http://localhost:9090/metrics>
- **Métricas exportadas**:
  - `mediajelly_info` - Información del sistema
  - `mediajelly_files_processed_total` - Total de archivos procesados
  - `mediajelly_files_compressed_total` - Total de archivos comprimidos
  - `mediajelly_files_errors_total` - Total de errores
  - `mediajelly_compression_ratio` - Ratio de compresión actual
  - `mediajelly_space_saved_bytes` - Espacio ahorrado en bytes
  - `mediajelly_queue_pending_files` - Archivos pendientes en cola
  - `mediajelly_queue_completed_files` - Archivos completados
  - `mediajelly_system_cpu_percent` - Uso de CPU
  - `mediajelly_system_memory_percent` - Uso de memoria
  - `mediajelly_system_disk_percent` - Uso de disco
  - `mediajelly_processing_time_seconds` - Histograma de tiempos de procesamiento

**Ejemplo de uso**:

```bash
# Ver todas las métricas
curl http://localhost:9090/metrics

# Filtrar métricas específicas
curl http://localhost:9090/metrics | grep mediajelly_compression
```

### **Prometheus UI** (Puerto 9092)

- **URL**: <http://localhost:9092>
- **Características**:
  - Interfaz web de Prometheus
  - Explorador de métricas
  - Editor de consultas PromQL
  - Visualización de targets

**Consultas útiles**:

```promql
# Tasa de procesamiento (archivos/min)
rate(mediajelly_files_processed_total[5m]) * 60

# Uso de recursos del sistema
mediajelly_system_cpu_percent
mediajelly_system_memory_percent
mediajelly_system_disk_percent

# Espacio ahorrado en GB
mediajelly_space_saved_bytes / 1024 / 1024 / 1024
```

### **Grafana** (Puerto 3000)

- **URL**: <http://localhost:3000>
- **Credenciales por defecto**:
  - Usuario: `admin`
  - Contraseña: `admin`
- **Dashboard**: MediaJelly Dashboard (auto-provisionado)
- **Datasource**: Prometheus (auto-configurado)

**Paneles incluidos**:

1. **Archivos Pendientes** - Gauge con cantidad de archivos en cola
2. **Tasa de Procesamiento** - Gráfico de líneas con archivos/min
3. **CPU Usage** - Gauge con porcentaje de uso
4. **Memory Usage** - Gauge con porcentaje de uso
5. **Disk Usage** - Gauge con porcentaje de uso
6. **Compression Ratio** - Stat con ratio promedio
7. **Space Saved** - Stat con GB ahorrados

## 🔧 Gestión de Servicios

### Iniciar todos los servicios

```bash
docker-compose up -d
```

### Detener todos los servicios

```bash
docker-compose down
```

### Ver logs de un servicio específico

```bash
# API REST
docker-compose logs -f mediajelly-cron

# Prometheus
docker-compose logs -f prometheus

# Grafana
docker-compose logs -f grafana
```

### Reiniciar un servicio

```bash
docker-compose restart mediajelly-cron
docker-compose restart prometheus
docker-compose restart grafana
```

### Ver estado de los servicios

```bash
docker-compose ps
```

## 📁 Estructura de Archivos

### Configuración

- `config/prometheus/prometheus.yml` - Configuración de Prometheus
- `config/grafana/provisioning/datasources/` - Datasources de Grafana
- `config/grafana/provisioning/dashboards/` - Dashboards de Grafana

### Scripts

- `scripts/mediajelly_api.py` - API REST con FastAPI
- `scripts/mediajelly_metrics_exporter.py` - Exportador de métricas Prometheus
- `scripts/mediajelly_processor.py` - Procesador con sistema de métricas

### Logs

- `scripts/logs/api.log` - Logs de la API REST
- `scripts/logs/metrics.log` - Logs del exportador de métricas
- `scripts/logs/cron.log` - Logs del procesamiento principal

### Datos

- Prometheus: Volumen Docker `prometheus-data`
- Grafana: Volumen Docker `grafana-data`

## 🎯 Características Implementadas

### Tarea #14 - Límites Dinámicos de Recursos

- ✅ Función `get_optimal_workers()` que calcula workers óptimos basándose en:
  - RAM disponible (4GB por worker)
  - Número de CPUs físicas
- ✅ Integrado en `process_files_concurrent()`
- ✅ Usa `psutil` para detección automática

### Tarea #16 - REST API Dashboard

- ✅ API FastAPI con 8 endpoints
- ✅ CORS habilitado para acceso desde navegadores
- ✅ Retorna datos en formato JSON
- ✅ Integración con archivos de estado del sistema

### Tarea #17 - Grafana Monitoring

- ✅ Stack completo Prometheus + Grafana
- ✅ Auto-provisioning de datasource y dashboard
- ✅ Scraping automático de métricas cada 15 segundos
- ✅ Dashboard pre-configurado con 7 paneles

### Tarea #26 - Sistema de Métricas

- ✅ Dataclass `ProcessingMetrics` con 11 campos
- ✅ Clase `MetricsCollector` para cálculo y persistencia
- ✅ Métricas guardadas en JSON
- ✅ Integración automática en procesamiento

## 🔍 Verificación del Sistema

### Comprobar que todos los servicios están corriendo

```bash
docker ps | grep -E 'mediajelly|prometheus|grafana'
```

### Probar la API

```bash
curl http://localhost:8000 | jq
```

### Verificar métricas

```bash
curl http://localhost:9090/metrics | grep mediajelly
```

### Verificar Prometheus targets

```bash
curl http://localhost:9092/api/v1/targets | jq
```

### Verificar Grafana

```bash
curl http://localhost:3000/api/health | jq
```

## 📊 Próximos Pasos

1. **Acceder a Grafana**: Abre <http://localhost:3000> y explora el dashboard
2. **Personalizar Dashboard**: Agrega más paneles según tus necesidades
3. **Configurar Alertas**: Define alertas en Grafana para métricas críticas
4. **Explorar Métricas**: Usa Prometheus UI para crear queries personalizadas

## 🛠️ Troubleshooting

### La API no responde

```bash
# Ver logs
docker exec mediajelly-cron cat /mediajelly/scripts/logs/api.log

# Verificar procesos
docker exec mediajelly-cron ps aux | grep mediajelly_api
```

### Prometheus no recolecta métricas

```bash
# Verificar targets
curl http://localhost:9092/api/v1/targets | jq '.data.activeTargets[] | {job, health}'

# Ver logs
docker-compose logs prometheus
```

### Grafana no muestra datos

1. Verifica que el datasource esté configurado: Settings → Data Sources
2. Verifica que Prometheus esté recolectando: Explore → Prometheus
3. Refresca el dashboard o ajusta el rango de tiempo

---

**Fecha de Implementación**: 12 de noviembre de 2025  
**Versión**: 1.0.0  
**Estado**: ✅ Operacional
