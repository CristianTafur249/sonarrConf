#!/usr/bin/env python3
"""
MediaJelly NFO Translator
Traduce archivos .nfo que no están en español al español
Soporta procesamiento en paralelo
"""

import os
import sys
import logging
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import List, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
import multiprocessing
import subprocess
import json
import hashlib

# Importaciones opcionales con manejo de errores
try:
    from langdetect import detect as langdetect_detect
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

# Configuración de logging
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger(__name__)

class NFOTranslator:
    def __init__(self):
        if not LANGDETECT_AVAILABLE:
            raise ImportError("langdetect no está disponible. Instala con: pip install langdetect")
        if not TRANSLATE_AVAILABLE:
            raise ImportError("translatepy no está disponible. Instala con: pip install translatepy")

        self.translator = Translator()
        # Cache de traducciones
        self.cache_file = Path("/mediajelly/scripts/tmp/nfo_translation_cache.json")
        self.cache = self.load_cache()

    def load_cache(self):
        """Carga el cache de traducciones"""
        if self.cache_file.exists():
            try:
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def save_cache(self):
        """Guarda el cache de traducciones"""
        try:
            self.cache_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.cache_file, 'w', encoding='utf-8') as f:
                json.dump(self.cache, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"Error guardando cache: {e}")

    def get_cache_key(self, text: str, target_lang: str = 'es') -> str:
        """Genera clave de cache para el texto"""
        return hashlib.md5(f"{text}:{target_lang}".encode()).hexdigest()

    def detect_language(self, text: str) -> str:
        """Detecta el idioma del texto"""
        try:
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

    def translate_text(self, text: str, target_lang: str = 'es') -> str:
        """Traduce el texto al idioma objetivo con cache"""
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
            return text

    def should_translate_element(self, element: ET.Element) -> bool:
        """Determina si un elemento XML debe ser traducido"""
        # No traducir títulos, nombres, IDs, etc.
        non_translate_tags = {
            'title', 'name', 'filename', 'path', 'id', 'uniqueid',
            'season', 'episode', 'aired', 'premiered', 'rating',
            'mpaa', 'genre', 'status', 'studio', 'episodeguide'
        }

        return element.tag.lower() not in non_translate_tags and element.text and element.text.strip()

    def process_nfo_file(self, nfo_path: Path) -> bool:
        """Procesa un archivo .nfo individual"""
        try:
            # Parsear XML
            tree = ET.parse(nfo_path)
            root = tree.getroot()

            modified = False

            # Recorrer todos los elementos
            for elem in root.iter():
                if self.should_translate_element(elem):
                    text = elem.text.strip()
                    if text:
                        lang = self.detect_language(text)
                        if lang != 'es' and lang != 'unknown':
                            logger.info(f"Traduciendo {nfo_path.name}: {elem.tag} ({lang} -> es)")
                            translated = self.translate_text(text)
                            elem.text = translated
                            modified = True

            if modified:
                # Guardar el archivo modificado
                tree.write(nfo_path, encoding='utf-8', xml_declaration=True)
                logger.info(f"Archivo actualizado: {nfo_path}")
                return True
            else:
                logger.info(f"No se modificó: {nfo_path}")
                return False

        except Exception as e:
            logger.error(f"Error procesando {nfo_path}: {e}")
            return False

    def is_file_spanish(self, nfo_path: Path) -> bool:
        """Verifica si el archivo ya está mayoritariamente en español"""
        try:
            tree = ET.parse(nfo_path)
            root = tree.getroot()
            spanish_count = 0
            total_count = 0

            for elem in root.iter():
                if self.should_translate_element(elem):
                    text = elem.text.strip()
                    if text and len(text) > 10:  # Solo textos significativos
                        lang = self.detect_language(text)
                        total_count += 1
                        if lang == 'es':
                            spanish_count += 1

            # Si más del 80% está en español, considerarlo español
            if total_count > 0 and spanish_count / total_count > 0.8:
                return True
            return False
        except Exception:
            return False

    def find_nfo_files(self, base_path: Path) -> List[Path]:
        """Encuentra todos los archivos .nfo en el directorio, filtrando los ya en español"""
        nfo_files = []
        for nfo_file in base_path.rglob('*.nfo'):
            if self.is_file_spanish(nfo_file):
                logger.info(f"Saltando archivo ya en español: {nfo_file}")
            else:
                nfo_files.append(nfo_file)
        return nfo_files

    def process_directory(self, directory: str, max_workers: Optional[int] = None) -> None:
        """Procesa todos los .nfo en un directorio usando paralelismo"""
        base_path = Path(directory)

        if not base_path.exists():
            logger.error(f"Directorio no existe: {directory}")
            return

        # Cambiar permisos para asegurar escritura
        try:
            subprocess.run(['chmod', '-R', '777', str(base_path)], check=True)
            logger.info(f"Permisos cambiados en {base_path}")
        except subprocess.CalledProcessError as e:
            logger.warning(f"No se pudieron cambiar permisos en {base_path}: {e}")

        # Encontrar archivos .nfo
        nfo_files = self.find_nfo_files(base_path)
        logger.info(f"Encontrados {len(nfo_files)} archivos .nfo")

        if not nfo_files:
            logger.info("No hay archivos .nfo para procesar")
            return

        # Usar paralelismo limitado
        if max_workers is None:
            max_workers = min(8, len(nfo_files))  # Reducir workers para evitar sobrecargar API

        logger.info(f"Procesando con {max_workers} workers en paralelo")

        processed = 0
        translated = 0

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
                    if processed % 10 == 0:
                        logger.info(f"Procesados: {processed}/{len(nfo_files)}, Traducidos: {translated}")
                except Exception as e:
                    logger.error(f"Error procesando {nfo_file}: {e}")

        logger.info(f"Procesamiento completado. Total: {len(nfo_files)}, Traducidos: {translated}")
        self.save_cache()

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
    main()