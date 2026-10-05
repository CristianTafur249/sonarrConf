#!/usr/bin/env python3
"""
MediaJelly NFO Translator
Traduce al español los textos descriptivos (sinopsis, lema, reseña) de los .nfo.
Solo revisa los archivos nuevos o modificados desde la última ejecución.
Soporta procesamiento en paralelo
"""

import os
import sys
import logging
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import hashlib

# Importaciones opcionales con manejo de errores
try:
    from langdetect import DetectorFactory, detect as langdetect_detect

    # Sin semilla langdetect no es determinista y el mismo texto cambia de idioma entre ejecuciones
    DetectorFactory.seed = 0
    LANGDETECT_AVAILABLE = True
except ImportError:
    langdetect_detect = None
    LANGDETECT_AVAILABLE = False

try:
    from translatepy import Translator
    TRANSLATE_AVAILABLE = True
except ImportError:
    Translator = None
    TRANSLATE_AVAILABLE = False

from mediajelly_emoji import EmojiGenerator

logger = logging.getLogger(__name__)

# Únicas etiquetas con texto libre que tiene sentido traducir. El resto del .nfo son
# datos técnicos (codec, watched, language, URLs...) y nombres propios que no deben tocarse.
TRANSLATABLE_TAGS = {"plot", "outline", "tagline", "review", "biography"}


class NFOTranslator:
    def __init__(self, tmp_dir: Optional[Path] = None):
        if not LANGDETECT_AVAILABLE:
            raise ImportError("langdetect no está disponible. Instala con: pip install langdetect")
        if not TRANSLATE_AVAILABLE:
            raise ImportError("translatepy no está disponible. Instala con: pip install translatepy")

        self.translator = Translator()
        # langdetect carga sus perfiles en el primer uso y esa carga no es segura entre
        # hilos ("Need to load profiles"): forzarla aquí, antes de lanzar los workers
        try:
            langdetect_detect("texto de calentamiento")
        except Exception:
            pass
        # Cache de traducciones
        tmp_dir = Path(tmp_dir) if tmp_dir else Path("/mediajelly/scripts/tmp")
        self.cache_file = tmp_dir / "nfo_translation_cache.json"
        self.cache = self.load_cache()
        # Archivos ya revisados: {ruta: [mtime_ns, size]}. Mientras no cambien, no se vuelven a parsear
        self.state_file = tmp_dir / "nfo_state.json"
        self.state = self._load_json(self.state_file)

    @staticmethod
    def _load_json(path: Path) -> dict:
        """Carga un JSON de estado; devuelve {} si no existe o está dañado"""
        if path.exists():
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    @staticmethod
    def _save_json(path: Path, data: dict, indent: Optional[int] = None) -> None:
        """Guarda un JSON de estado de forma atómica"""
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = path.with_suffix(path.suffix + ".tmp")
            with open(tmp_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=indent)
            os.replace(tmp_path, path)
        except Exception as e:
            logger.warning(f"Error guardando {path.name}: {e}")

    def load_cache(self):
        """Carga el cache de traducciones"""
        return self._load_json(self.cache_file)

    def save_cache(self):
        """Guarda el cache de traducciones y el estado de archivos revisados"""
        self._save_json(self.cache_file, self.cache, indent=2)
        self._save_json(self.state_file, self.state)

    @staticmethod
    def _file_signature(nfo_path: Path) -> List[int]:
        """Firma de un archivo para detectar cambios: [mtime_ns, size]"""
        stat = nfo_path.stat()
        return [stat.st_mtime_ns, stat.st_size]

    def is_unchanged(self, nfo_path: Path) -> bool:
        """Indica si el archivo ya fue revisado y no ha cambiado desde entonces"""
        try:
            return self.state.get(str(nfo_path)) == self._file_signature(nfo_path)
        except OSError:
            return False

    def get_cache_key(self, text: str, target_lang: str = 'es') -> str:
        """Genera clave de cache para el texto"""
        return hashlib.md5(f"{text}:{target_lang}".encode()).hexdigest()

    def detect_language(self, text: str) -> str:
        """Detecta el idioma del texto"""
        try:
            if not LANGDETECT_AVAILABLE:
                return 'unknown'
            # Limpiar texto para mejor detección
            text = text.strip()
            if len(text) < 3:  # Texto demasiado corto
                return 'unknown'
            # Remover caracteres no alfabéticos excesivos
            import re
            if re.match(r'^[^a-zA-Z]*$', text):  # Solo caracteres no letras
                return 'unknown'
            return langdetect_detect(text)
        except Exception as e:
            logger.warning(f"Error detectando idioma: {e}")
            return 'unknown'

    def translate_text(self, text: str, target_lang: str = 'es') -> Optional[str]:
        """Traduce el texto al idioma objetivo con cache. Devuelve None si la traducción falla"""
        cache_key = self.get_cache_key(text, target_lang)
        if cache_key in self.cache:
            return self.cache[cache_key]

        try:
            result = self.translator.translate(text, target_lang)
            translated = result.result
            self.cache[cache_key] = translated
            return translated
        except Exception as e:
            logger.warning(f"Error traduciendo texto: {e}")
            return None

    def should_translate_element(self, element: ET.Element) -> bool:
        """Determina si un elemento XML debe ser traducido"""
        if not isinstance(element.tag, str) or element.tag.lower() not in TRANSLATABLE_TAGS:
            return False
        return bool(element.text and element.text.strip())

    def process_nfo_file(self, nfo_path: Path) -> bool:
        """Procesa un archivo .nfo individual. Devuelve True si se modificó"""
        try:
            # Parsear XML
            tree = ET.parse(nfo_path)
            root = tree.getroot()

            modified = False
            complete = True

            # Recorrer todos los elementos
            for elem in root.iter():
                if self.should_translate_element(elem):
                    text = elem.text.strip()
                    lang = self.detect_language(text)
                    if lang != 'es' and lang != 'unknown':
                        logger.debug(f"Traduciendo {nfo_path.name}: {elem.tag} ({lang} -> es)")
                        translated = self.translate_text(text)
                        if translated is None:
                            # Fallo de red: reintentar este archivo en la próxima ejecución
                            complete = False
                        elif translated != text:
                            elem.text = translated
                            modified = True

            if modified:
                # Guardar el archivo modificado
                tree.write(nfo_path, encoding='utf-8', xml_declaration=True)
                logger.debug(f"Archivo actualizado: {nfo_path}")

            if complete:
                self.state[str(nfo_path)] = self._file_signature(nfo_path)
            return modified

        except ET.ParseError as e:
            # XML inválido: no tiene arreglo hasta que el archivo cambie, no reintentar cada ciclo
            logger.warning(f"NFO con XML inválido, se omite: {nfo_path}: {e}")
            try:
                self.state[str(nfo_path)] = self._file_signature(nfo_path)
            except OSError:
                pass
            return False
        except Exception as e:
            logger.error(f"Error procesando {nfo_path}: {e}")
            return False

    def find_nfo_files(self, base_path: Path) -> List[Path]:
        """Encuentra los archivos .nfo nuevos o modificados desde la última revisión"""
        return [nfo_file for nfo_file in base_path.rglob('*.nfo') if not self.is_unchanged(nfo_file)]

    def process_directory(self, directory: str, max_workers: Optional[int] = None) -> None:
        """Procesa todos los .nfo en un directorio usando paralelismo"""
        base_path = Path(directory)

        if not base_path.exists():
            logger.error(f"Directorio no existe: {directory}")
            return

        # Encontrar archivos .nfo nuevos o modificados
        nfo_files = self.find_nfo_files(base_path)

        if not nfo_files:
            logger.info("No hay archivos .nfo nuevos o modificados")
            return

        logger.info(f"Encontrados {len(nfo_files)} archivos .nfo nuevos o modificados")

        # Usar paralelismo limitado
        if max_workers is None:
            max_workers = min(8, len(nfo_files))  # Reducir workers para evitar sobrecargar API

        logger.info(f"Procesando con {max_workers} workers en paralelo")

        processed = 0
        translated = 0

        try:
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                # Enviar tareas
                future_to_file = {
                    executor.submit(self.process_nfo_file, Path(nfo_file)): nfo_file
                    for nfo_file in nfo_files
                }

                # Procesar resultados
                for future in as_completed(future_to_file):
                    nfo_file = future_to_file[future]
                    try:
                        result = future.result()
                        processed += 1
                        if result:
                            translated += 1
                        if processed % 50 == 0:
                            logger.info(f"Procesados: {processed}/{len(nfo_files)}, Traducidos: {translated}")
                    except Exception as e:
                        logger.error(f"Error procesando {nfo_file}: {e}")
        finally:
            # Guardar siempre: una ejecución interrumpida no debe perder lo ya traducido
            self.save_cache()

        logger.info(f"Procesamiento completado. Total: {len(nfo_files)}, Traducidos: {translated}")

def main():
    if len(sys.argv) != 2:
        print("Uso: python3 mediajelly_nfo_translator.py <directorio>")
        print("Ejemplo: python3 mediajelly_nfo_translator.py media/series")
        sys.exit(1)

    directory = sys.argv[1]

    try:
        translator = NFOTranslator()
        translator.process_directory(directory)
        logger.info("Traducción de NFO completada exitosamente")
    except Exception as e:
        logger.error(f"Error en la traducción: {e}")
        sys.exit(1)

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="[%(asctime)s] %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    main()
