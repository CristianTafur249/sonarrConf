# Contributing to MediaJelly

¡Gracias por tu interés en contribuir a MediaJelly! 🎉

## 🚀 Cómo Empezar

### 1. Fork y Clone

```bash
git clone https://github.com/TU_USUARIO/mediaJelly.git
cd mediaJelly
```

### 2. Configurar Entorno de Desarrollo

```bash
# Crear entorno virtual
python3 -m venv venv
source venv/bin/activate  # En Windows: venv\Scripts\activate

# Instalar dependencias de desarrollo
pip install -r requirements-dev.txt

# Configurar pre-commit hooks
pre-commit install
```

### 3. Configurar Variables de Entorno

```bash
cp .env.example .env
# Edita .env con tus configuraciones
```

## 📋 Estándares de Código

### Formateo

Usamos **Black** para formateo automático:

```bash
# Formatear todos los archivos
black scripts/

# Verificar sin modificar
black --check scripts/
```

### Linting

Usamos **flake8** para análisis estático:

```bash
# Ejecutar flake8
flake8 scripts/

# Ignorar errores específicos
flake8 --ignore=E501 scripts/
```

### Type Checking

Usamos **mypy** para verificación de tipos:

```bash
# Ejecutar mypy
mypy scripts/

# Ignorar archivos específicos
mypy --exclude scripts/legacy/ scripts/
```

## 🧪 Tests

### Ejecutar Tests

```bash
# Todos los tests
pytest

# Tests específicos
pytest tests/test_scanner.py

# Con cobertura
pytest --cov=scripts --cov-report=html

# Solo tests rápidos
pytest -m "not slow"
```

### Escribir Tests

- Coloca los tests en `tests/`
- Nombre: `test_*.py`
- Usa fixtures de `conftest.py`
- Marca tests lentos con `@pytest.mark.slow`

Ejemplo:

```python
import pytest

def test_my_feature(scanner):
    """Test description"""
    result = scanner.scan_folder("/path")
    assert len(result) > 0
```

## 🔀 Flujo de Trabajo Git

### Branches

- `main` - Branch principal (protegida)
- `develop` - Branch de desarrollo
- `feature/nombre-feature` - Nuevas características
- `fix/nombre-bug` - Correcciones de bugs
- `docs/nombre-doc` - Mejoras de documentación

### Commits

Usamos [Conventional Commits](https://www.conventionalcommits.org/):

```
tipo(scope): descripción corta

Descripción más detallada si es necesario

- Cambio 1
- Cambio 2

Fixes #123
```

**Tipos:**

- `feat:` Nueva característica
- `fix:` Corrección de bug
- `docs:` Documentación
- `style:` Formateo, puntos y comas, etc.
- `refactor:` Refactorización de código
- `test:` Tests
- `chore:` Mantenimiento

**Ejemplos:**

```bash
git commit -m "feat(scanner): add support for .webm files"
git commit -m "fix(processor): resolve memory leak in compression"
git commit -m "docs(readme): update installation instructions"
```

### Pull Requests

1. **Crea un branch desde `develop`:**

   ```bash
   git checkout -b feature/nueva-caracteristica develop
   ```

2. **Haz tus cambios y commits:**

   ```bash
   git add .
   git commit -m "feat: descripción"
   ```

3. **Push a tu fork:**

   ```bash
   git push origin feature/nueva-caracteristica
   ```

4. **Crea PR en GitHub:**
   - Base: `develop`
   - Título descriptivo
   - Descripción completa de cambios
   - Link a issues relacionados

### PR Checklist

- [ ] Código formateado con `black`
- [ ] Pasa `flake8` sin errores críticos
- [ ] Pasa `mypy` sin errores de tipos
- [ ] Tests agregados/actualizados
- [ ] Todos los tests pasan
- [ ] Documentación actualizada
- [ ] CHANGELOG.md actualizado

## 🐛 Reportar Bugs

### Template de Issue

```markdown
**Descripción del Bug**
Descripción clara y concisa.

**Para Reproducir**
1. Ir a '...'
2. Ejecutar '...'
3. Ver error

**Comportamiento Esperado**
Qué debería suceder.

**Screenshots/Logs**
Si aplica.

**Entorno:**
- OS: [e.g. Ubuntu 22.04]
- Docker: [e.g. 24.0.5]
- Python: [e.g. 3.10.12]

**Contexto Adicional**
Cualquier otra información relevante.
```

## 💡 Solicitar Features

### Template de Feature Request

```markdown
**¿El feature resuelve un problema? Descríbelo.**
Descripción clara del problema.

**Describe la solución que te gustaría**
Cómo debería funcionar.

**Describe alternativas consideradas**
Otras soluciones que hayas pensado.

**Contexto Adicional**
Screenshots, mockups, etc.
```

## 📚 Documentación

### Docstrings

Usamos formato Google:

```python
def compress_file(file_path: Path, quality: int = 23) -> bool:
    """Comprime un archivo de video.

    Args:
        file_path: Ruta al archivo de video
        quality: Factor de calidad CRF (18-28)

    Returns:
        True si la compresión fue exitosa

    Raises:
        FileNotFoundError: Si el archivo no existe
        CompressionError: Si la compresión falla

    Example:
        >>> compress_file(Path("/video.mkv"), quality=23)
        True
    """
```

## 🏗️ Arquitectura

### Estructura del Proyecto

```
mediaJelly/
├── scripts/          # Scripts Python principales
│   ├── mediajelly_scanner.py
│   ├── mediajelly_processor.py
│   └── ...
├── tests/           # Suite de tests
├── config/          # Configuraciones
├── media/           # Contenido multimedia
└── docker-compose.yaml
```

### Módulos Principales

- **Scanner:** Encuentra archivos nuevos
- **Processor:** Comprime videos
- **Language Detector:** Detecta idiomas con Whisper
- **Subtitle Translator:** Traduce subtítulos
- **Notifier:** Envía notificaciones a Telegram

## 🤝 Código de Conducta

- Se respetuoso con todos los contribuidores
- Acepta críticas constructivas
- Enfócate en lo mejor para el proyecto
- Ayuda a otros cuando sea posible

## 📞 Contacto

- **Issues:** [GitHub Issues](https://github.com/CristianTafur249/mediaJelly/issues)
- **Discussions:** [GitHub Discussions](https://github.com/CristianTafur249/mediaJelly/discussions)

## 📄 Licencia

Al contribuir, aceptas que tus contribuciones se licenciarán bajo la misma licencia del proyecto.

---

¡Gracias por contribuir a MediaJelly! 🎬✨
