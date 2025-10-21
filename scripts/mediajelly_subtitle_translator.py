#!/usr/bin/env python3
"""
MediaJelly Subtitle Translator
Extrae y traduce subtítulos al español automáticamente
Soporta extracción desde audio usando Whisper
"""

import os
import sys
import subprocess
import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

# Lock global para Whisper (solo un proceso a la vez)
whisper_lock = threading.Lock()

class SubtitleTranslator:
    """Gestor de extracción y traducción de subtítulos con soporte de Whisper"""
    
    def __init__(self, max_workers: int = 2, use_whisper: bool = True):
        self.is_container = Path("/mediajelly").exists()
        self.base_dir = Path("/mediajelly" if self.is_container else "/home/tafurc/mediaJelly")
        self.scripts_dir = self.base_dir / "scripts"
        self.logs_dir = self.scripts_dir / "logs"
        self.tmp_dir = self.scripts_dir / "tmp"
        self.max_workers = max_workers  # Procesamiento concurrente
        self.use_whisper = use_whisper
        self.progress_lock = threading.Lock()  # Lock para sincronizar progreso y estadísticas
        
        # Configurar logging
        self.setup_logging()
        
        # Verificar disponibilidad de Whisper
        if self.use_whisper:
            try:
                import whisper
                self.whisper_model = None  # Se cargará cuando se necesite
                self.whisper_available = True
                self.logger.info("Whisper disponible para extracción de audio")
            except ImportError:
                self.whisper_available = False
                self.use_whisper = False
                self.logger.warning("Whisper no está instalado. Solo se usarán subtítulos embebidos.")
        
    def setup_logging(self):
        """Configura el logging"""
        log_file = self.logs_dir / "subtitle_translator.log"
        
        class LocalTimeFormatter(logging.Formatter):
            def formatTime(self, record, datefmt=None):
                dt = datetime.fromtimestamp(record.created)
                if datefmt:
                    return dt.strftime(datefmt)
                return dt.strftime('%Y-%m-%d %H:%M:%S')
        
        formatter = LocalTimeFormatter('[%(asctime)s] %(levelname)s: %(message)s', '%Y-%m-%d %H:%M:%S')
        
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(formatter)
        
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        
        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.INFO)
        self.logger.handlers.clear()
        self.logger.addHandler(file_handler)
        self.logger.addHandler(console_handler)
    
    def check_audio_tracks(self, video_file: Path) -> Tuple[bool, List[str]]:
        """Verifica si el archivo tiene audio en español"""
        try:
            cmd = [
                'ffprobe', '-v', 'error',
                '-select_streams', 'a',
                '-show_entries', 'stream=index:stream_tags=language',
                '-of', 'json',
                str(video_file)
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            
            if result.returncode != 0:
                self.logger.warning(f"Error al analizar pistas de audio: {video_file}")
                return False, []
            
            data = json.loads(result.stdout)
            streams = data.get('streams', [])
            
            languages = []
            has_spanish = False
            
            for stream in streams:
                lang = stream.get('tags', {}).get('language', 'und')
                languages.append(lang)
                if lang in ['spa', 'es', 'esp']:
                    has_spanish = True
            
            return has_spanish, languages
            
        except Exception as e:
            self.logger.error(f"Error verificando audio: {e}")
            return False, []
    
    def check_existing_spanish_subtitles(self, video_file: Path) -> bool:
        """Verifica si ya existen subtítulos en español"""
        base_name = video_file.stem
        parent_dir = video_file.parent
        
        # Buscar archivos .srt con _ES o que contengan 'spanish' o 'español'
        patterns = [
            f"{base_name}*.es.srt",
            f"{base_name}*.es.srt",
            f"{base_name}*.spa.srt",
            f"{base_name}*spanish*.srt",
            f"{base_name}*español*.srt"
        ]
        
        for pattern in patterns:
            if list(parent_dir.glob(pattern)):
                return True
        
        return False
    
    def find_existing_srt_file(self, video_file: Path) -> Optional[Path]:
        """Encuentra un archivo SRT existente (no español) para traducir"""
        base_name = video_file.stem
        parent_dir = video_file.parent
        
        # Buscar archivos .srt que no sean .es.srt
        patterns = [
            f"{base_name}*.srt",
            f"{base_name}*.en.srt",
            f"{base_name}*.eng.srt",
            f"{base_name}*.sub.srt"
        ]
        
        for pattern in patterns:
            matches = list(parent_dir.glob(pattern))
            for match in matches:
                # Excluir archivos .es.srt ya que esos ya están en español
                if not match.name.endswith('.es.srt') and not match.name.endswith('.spa.srt'):
                    return match
        
        return None
    
    def detect_srt_language(self, srt_file: Path) -> str:
        """Detecta el idioma principal de un archivo SRT"""
        try:
            from langdetect import detect
            
            with open(srt_file, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
            
            # Extraer solo líneas de texto (no números ni timestamps)
            lines = content.split('\n')
            text_lines = []
            
            for line in lines:
                line_stripped = line.strip()
                if (line_stripped and 
                    not line_stripped.isdigit() and 
                    '-->' not in line_stripped and
                    not line_stripped.lower().startswith('http') and
                    'www' not in line_stripped.lower() and
                    '@' not in line_stripped):
                    text_lines.append(line_stripped)
            
            if not text_lines:
                return 'unknown'
            
            # Tomar una muestra de las primeras líneas de texto para detectar idioma
            sample_text = ' '.join(text_lines[:10])  # Primeras 10 líneas
            
            try:
                detected_lang = detect(sample_text)
                return detected_lang
            except Exception:
                return 'unknown'
                
        except Exception as e:
            self.logger.error(f"Error detectando idioma de {srt_file.name}: {e}")
            return 'unknown'
    
    def process_existing_subtitles(self, directory: Path) -> dict:
        """Procesa subtítulos existentes que no estén en español"""
        stats = {
            'total_srt_files': 0,
            'spanish_subtitles': 0,
            'translated_subtitles': 0,
            'errors': 0,
            'results': []
        }
        
        # Buscar todos los archivos SRT en el directorio
        srt_files = list(directory.glob('*.srt'))
        stats['total_srt_files'] = len(srt_files)
        
        self.logger.info(f"Revisando {stats['total_srt_files']} archivos SRT existentes en {directory}")
        
        for srt_file in srt_files:
            result = {
                'file': str(srt_file),
                'was_spanish': False,
                'translated': False,
                'error': None
            }
            
            try:
                # Detectar idioma
                detected_lang = self.detect_srt_language(srt_file)
                
                if detected_lang == 'es':
                    # Ya está en español
                    result['was_spanish'] = True
                    stats['spanish_subtitles'] += 1
                    self.logger.debug(f"Subtítulos ya en español: {srt_file.name}")
                else:
                    # No está en español, intentar traducir
                    self.logger.info(f"Traduciendo subtítulos no españoles ({detected_lang}): {srt_file.name}")
                    
                    translated_file = self.translate_srt(srt_file)
                    
                    if translated_file:
                        result['translated'] = True
                        stats['translated_subtitles'] += 1
                        self.logger.info(f"✓ Subtítulos traducidos: {srt_file.name}")
                    else:
                        result['error'] = "No se pudo traducir"
                        stats['errors'] += 1
                        
            except Exception as e:
                result['error'] = str(e)
                stats['errors'] += 1
                self.logger.error(f"Error procesando subtítulos {srt_file.name}: {e}")
            
            stats['results'].append(result)
        
        return stats
    
    def extract_subtitles(self, video_file: Path) -> Optional[Path]:
        """Extrae subtítulos del archivo de video (embebidos o desde audio)"""
        try:
            # Primero intentar extraer subtítulos embebidos
            cmd = [
                'ffprobe', '-v', 'error',
                '-select_streams', 's',
                '-show_entries', 'stream=index:stream_tags=language',
                '-of', 'json',
                str(video_file)
            ]
            
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            
            if result.returncode == 0:
                data = json.loads(result.stdout)
                streams = data.get('streams', [])
                
                if streams:
                    # Extraer el primer stream de subtítulos embebidos
                    output_srt = video_file.parent / f"{video_file.stem}.srt"
                    
                    cmd = [
                        'ffmpeg', '-v', 'error', '-y',
                        '-i', str(video_file),
                        '-map', '0:s:0',
                        '-c:s', 'srt',
                        str(output_srt)
                    ]
                    
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
                    
                    if result.returncode == 0 and output_srt.exists():
                        self.logger.info(f"✓ Subtítulos embebidos extraídos: {output_srt.name}")
                        return output_srt
            
            # Si no hay subtítulos embebidos, intentar extraer del audio con Whisper
            if self.use_whisper and self.whisper_available:
                self.logger.info(f"No hay subtítulos embebidos, usando Whisper para extraer del audio...")
                return self.extract_subtitles_from_audio(video_file)
            else:
                self.logger.debug(f"No hay subtítulos embebidos y Whisper no disponible: {video_file.name}")
                return None
            
        except Exception as e:
            self.logger.error(f"Error extrayendo subtítulos: {e}")
            return None
    
    def extract_subtitles_from_audio(self, video_file: Path) -> Optional[Path]:
        """Extrae subtítulos del audio usando Whisper (procesamiento secuencial con lock global)"""
        audio_file = None
        output_srt = None

        try:
            import whisper

            # LOCK GLOBAL: Solo un proceso puede usar Whisper a la vez
            with whisper_lock:
                self.logger.info("🔒 Adquiriendo lock global de Whisper...")

                # Cargar modelo si no está cargado (usar tiny para mejor rendimiento)
                if self.whisper_model is None:
                    self.logger.info("Cargando modelo Whisper (tiny)...")
                    self.whisper_model = whisper.load_model("tiny")

                # Crear nombres de archivos temporales únicos
                temp_prefix = f"{video_file.stem}_{video_file.stat().st_mtime_ns}"
                audio_file = self.tmp_dir / f"{temp_prefix}_temp.wav"
                output_srt = video_file.parent / f"{video_file.stem}.srt"

                self.logger.info(f"Extrayendo audio temporal: {audio_file.name}")

                # Extraer audio con ffmpeg
                cmd = [
                    'ffmpeg', '-v', 'error', '-y',
                    '-i', str(video_file),
                    '-ar', '16000',  # Whisper requiere 16kHz
                    '-ac', '1',       # Mono
                    '-c:a', 'pcm_s16le',
                    '-t', '3600',     # Limitar a 1 hora máximo para evitar archivos enormes
                    str(audio_file)
                ]

                result = subprocess.run(cmd, capture_output=True, text=True, timeout=900)  # 15 min timeout

                if result.returncode != 0:
                    self.logger.error(f"Error extrayendo audio: ffmpeg código {result.returncode}")
                    if result.stderr:
                        self.logger.error(f"FFmpeg stderr: {result.stderr[:500]}...")
                    return None

                if not audio_file.exists() or audio_file.stat().st_size == 0:
                    self.logger.error("Archivo de audio temporal no se creó o está vacío")
                    return None

                # VALIDACIÓN: Verificar que el archivo de audio no esté corrupto
                if not self._validate_audio_file(audio_file):
                    self.logger.error("Archivo de audio temporal está corrupto o vacío")
                    return None

                self.logger.info(f"Audio extraído ({audio_file.stat().st_size} bytes), transcribiendo con Whisper...")

                # Transcribir con Whisper (dentro del lock) - con manejo específico de errores
                try:
                    result = self.whisper_model.transcribe(
                        str(audio_file),
                        language=None,  # Auto-detectar idioma
                        task='transcribe',
                        fp16=False,  # CPU mode
                        verbose=False
                    )
                except Exception as whisper_error:
                    error_msg = str(whisper_error).lower()
                    if 'cannot reshape tensor' in error_msg or 'tensor' in error_msg:
                        self.logger.error(f"Error de tensor en Whisper (archivo posiblemente corrupto): {whisper_error}")
                        return None
                    elif 'nan' in error_msg:
                        self.logger.error(f"Error NaN en Whisper (datos de audio inválidos): {whisper_error}")
                        return None
                    else:
                        # Re-lanzar otros errores
                        raise whisper_error

                self.logger.info("🔓 Liberando lock global de Whisper...")

            # Verificar que tenemos segmentos
            if not result.get('segments'):
                self.logger.warning("Whisper no generó segmentos de transcripción")
                return None

            # Generar archivo SRT
            self.logger.info(f"Generando archivo SRT con {len(result['segments'])} segmentos")

            with open(output_srt, 'w', encoding='utf-8') as f:
                for i, segment in enumerate(result['segments'], start=1):
                    start_time = self._format_timestamp(segment['start'])
                    end_time = self._format_timestamp(segment['end'])
                    text = segment['text'].strip()

                    # Solo escribir si hay texto
                    if text:
                        f.write(f"{i}\n")
                        f.write(f"{start_time} --> {end_time}\n")
                        f.write(f"{text}\n\n")

            with open(output_srt, 'w', encoding='utf-8') as f:
                for i, segment in enumerate(result['segments'], start=1):
                    start_time = self._format_timestamp(segment['start'])
                    end_time = self._format_timestamp(segment['end'])
                    text = segment['text'].strip()

                    # Solo escribir si hay texto
                    if text:
                        f.write(f"{i}\n")
                        f.write(f"{start_time} --> {end_time}\n")
                        f.write(f"{text}\n\n")

            # Verificar que el archivo SRT se creó correctamente
            if not output_srt.exists() or output_srt.stat().st_size == 0:
                self.logger.error("Archivo SRT no se creó o está vacío")
                return None

            detected_lang = result.get('language', 'unknown')
            self.logger.info(f"✓ Subtítulos extraídos del audio (idioma: {detected_lang}): {output_srt.name}")
            return output_srt

        except subprocess.TimeoutExpired:
            self.logger.error("Timeout extrayendo audio (15 minutos)")
            return None
        except Exception as e:
            self.logger.error(f"Error extrayendo subtítulos del audio: {e}")
            return None
        finally:
            # LIMPIEZA: Eliminar archivos temporales SIEMPRE, incluso en errores
            self._cleanup_temp_files(audio_file)
            # No eliminar output_srt aquí porque es el resultado exitoso

    def _cleanup_temp_files(self, *temp_files):
        """Limpia archivos temporales de forma segura"""
        for temp_file in temp_files:
            if temp_file and temp_file.exists():
                try:
                    temp_file.unlink()
                    self.logger.debug(f"Archivo temporal eliminado: {temp_file.name}")
                except Exception as e:
                    self.logger.warning(f"No se pudo eliminar archivo temporal {temp_file}: {e}")

    def _validate_audio_file(self, audio_file: Path) -> bool:
        """Valida que el archivo de audio sea válido antes de procesarlo con Whisper"""
        try:
            # Verificar tamaño mínimo (al menos header WAV + algo de audio)
            if audio_file.stat().st_size < 1024:  # Al menos 1KB
                self.logger.warning(f"Archivo de audio muy pequeño: {audio_file.stat().st_size} bytes")
                return False

            # Verificar que sea un archivo WAV válido leyendo el header
            with open(audio_file, 'rb') as f:
                header = f.read(44)  # Leer header WAV (44 bytes)

                # Verificar firma RIFF
                if header[:4] != b'RIFF':
                    self.logger.error("Archivo de audio no tiene firma RIFF válida")
                    return False

                # Verificar firma WAVE
                if header[8:12] != b'WAVE':
                    self.logger.error("Archivo de audio no tiene firma WAVE válida")
                    return False

                # Buscar la sección de datos (puede estar en diferentes posiciones)
                data_found = False
                data_size = 0

                # Buscar "data" en el archivo
                f.seek(0)
                file_content = f.read()
                data_pos = file_content.find(b'data')
                if data_pos >= 0 and data_pos < 100:  # Encontrado en una posición razonable
                    data_size = int.from_bytes(file_content[data_pos+4:data_pos+8], byteorder='little')
                    data_found = True

                if not data_found or data_size == 0:
                    self.logger.warning(f"No se encontró sección de datos válida en el WAV (data_size: {data_size})")
                    # En lugar de rechazar, ser más permisivo - verificar que haya contenido
                    if len(file_content) > 1024:  # Si el archivo es lo suficientemente grande
                        self.logger.warning("Archivo WAV tiene formato no estándar pero tamaño aceptable, continuando...")
                    else:
                        return False

                # Verificar formato de audio (si está disponible)
                try:
                    format_tag = int.from_bytes(header[20:22], byteorder='little')
                    channels = int.from_bytes(header[22:24], byteorder='little')
                    sample_rate = int.from_bytes(header[24:28], byteorder='little')

                    # Ser más flexible con los formatos
                    if sample_rate not in [8000, 16000, 22050, 44100, 48000]:
                        self.logger.warning(f"Sample rate inusual: {sample_rate}Hz")
                    if channels not in [1, 2]:
                        self.logger.warning(f"Número de canales inusual: {channels}")
                except:
                    self.logger.warning("No se pudo leer información de formato del WAV")

                # Verificar que no sea solo silencio (bytes cero)
                f.seek(44)  # Ir después del header
                sample_data = f.read(min(2048, len(file_content) - 44))  # Leer hasta 2KB de datos

                # Contar bytes no cero
                non_zero_bytes = sum(1 for b in sample_data if b != 0)
                if non_zero_bytes < len(sample_data) * 0.01:  # Menos del 1% de bytes no cero
                    self.logger.warning("Archivo de audio parece contener solo silencio")
                    return False

            self.logger.debug(f"Archivo de audio validado: {audio_file.stat().st_size} bytes")
            return True

        except Exception as e:
            self.logger.error(f"Error validando archivo de audio {audio_file}: {e}")
            return False

    def _format_timestamp(self, seconds: float) -> str:
        """Convierte segundos a formato SRT (HH:MM:SS,mmm)"""
        try:
            hours = int(seconds // 3600)
            minutes = int((seconds % 3600) // 60)
            secs = int(seconds % 60)
            millis = int((seconds % 1) * 1000)
            return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"
        except Exception as e:
            self.logger.error(f"Error formateando timestamp {seconds}: {e}")
            return "00:00:00,000"
    
    def translate_srt(self, srt_file: Path) -> Optional[Path]:
        """Traduce archivo SRT al español usando Yandex Translate"""
        try:
            from translatepy.translators.yandex import YandexTranslate
            from langdetect import detect
            import time
            
            translator = YandexTranslate()
            
            with open(srt_file, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()
            
            translated_lines = []
            translations_made = 0
            
            for line in lines:
                line_stripped = line.strip()
                
                # No traducir líneas vacías, números, timestamps, URLs, etc.
                if (not line_stripped or 
                    line_stripped.isdigit() or 
                    '-->' in line_stripped or
                    line_stripped.lower().startswith('http') or
                    'www' in line_stripped.lower() or
                    '@' in line_stripped):
                    translated_lines.append(line)
                    continue
                
                # Detectar idioma y traducir solo si no es español
                try:
                    lang = detect(line_stripped)
                    if lang != 'es' and lang in ['ja', 'en', 'fr', 'de', 'it', 'pt', 'ru', 'ko', 'zh']:
                        # Usar Yandex para traducción
                        translated = translator.translate(line_stripped, 'es')
                        translated_lines.append(translated.result + '\n')
                        translations_made += 1
                        # Pequeño delay para evitar límites de rate
                        time.sleep(0.1)
                    else:
                        translated_lines.append(line)
                except Exception as detect_error:
                    # Si falla detección de idioma, intentar traducir asumiendo japonés (para anime)
                    try:
                        translated = translator.translate(line_stripped, 'es')
                        translated_lines.append(translated.result + '\n')
                        translations_made += 1
                        time.sleep(0.1)
                    except Exception:
                        # Si todo falla, mantener original
                        translated_lines.append(line)
            
            # Solo guardar si se hicieron traducciones
            if translations_made > 0:
                output_file = srt_file.parent / f"{srt_file.stem}.es.srt"
                
                with open(output_file, 'w', encoding='utf-8') as f:
                    f.writelines(translated_lines)
                
                self.logger.info(f"Archivo traducido ({translations_made} líneas): {output_file.name}")
                
                # Eliminar archivo original si fue extraído (no tiene _ES)
                if not srt_file.name.endswith('.es.srt'):
                    try:
                        srt_file.unlink()
                        self.logger.debug(f"Archivo original eliminado: {srt_file.name}")
                    except Exception:
                        pass
                
                return output_file
            else:
                self.logger.debug(f"No se encontraron líneas para traducir en: {srt_file.name}")
                return None
                
        except ImportError as e:
            self.logger.error(f"Faltan dependencias para traducción: {e}")
            return None
        except Exception as e:
            self.logger.error(f"Error traduciendo subtítulos: {e}")
            return None
    
    def process_video(self, video_file: Path) -> dict:
        """Procesa un archivo de video para subtítulos en español"""
        result = {
            'file': str(video_file),
            'has_spanish_audio': False,
            'had_spanish_subtitles': False,
            'extracted': False,
            'translated': False,
            'subtitle_file': None,
            'error': None
        }
        
        try:
            # 0. Verificación rápida inicial
            if not self.quick_check_needs_processing(video_file):
                result['had_spanish_subtitles'] = True  # Consideramos que ya está "procesado"
                self.logger.debug(f"Verificación rápida: omitiendo {video_file.name}")
                return result
            
            # 1. Verificar audio en español
            has_spanish, languages = self.check_audio_tracks(video_file)
            result['has_spanish_audio'] = has_spanish
            
            if has_spanish:
                self.logger.debug(f"Audio en español detectado, pero se procesarán subtítulos: {video_file.name}")
                # No retornamos aquí, continuamos para crear subtítulos incluso con audio en español
            
            # 2. Verificar si ya tiene subtítulos en español (verificación adicional)
            if self.check_existing_spanish_subtitles(video_file):
                result['had_spanish_subtitles'] = True
                self.logger.debug(f"Ya tiene subtítulos en español: {video_file.name}")
                return result
            
            # 3. Buscar subtítulos existentes para traducir
            existing_srt = self.find_existing_srt_file(video_file)
            if existing_srt:
                self.logger.debug(f"Encontrados subtítulos existentes para traducir: {existing_srt.name}")
                # Traducir subtítulos existentes
                translated_file = self.translate_srt(existing_srt)
                if translated_file:
                    result['translated'] = True
                    result['subtitle_file'] = str(translated_file)
                    self.logger.info(f"✓ Subtítulos traducidos desde existentes: {video_file.name}")
                else:
                    result['error'] = "No se pudieron traducir los subtítulos existentes"
                return result
            
            # 4. Extraer subtítulos si no hay ninguno
            srt_file = self.extract_subtitles(video_file)
            
            if srt_file:
                result['extracted'] = True
                
                # 5. Traducir subtítulos extraídos
                translated_file = self.translate_srt(srt_file)
                
                if translated_file:
                    result['translated'] = True
                    result['subtitle_file'] = str(translated_file)
                    self.logger.info(f"✓ Subtítulos extraídos y traducidos: {video_file.name}")
                else:
                    result['error'] = "No se pudo traducir"
            else:
                result['error'] = "No se pudieron extraer subtítulos"
                
        except Exception as e:
            result['error'] = str(e)
            self.logger.error(f"Error procesando {video_file.name}: {e}")
        
        return result
    
    def cleanup_incomplete_files(self):
        """Elimina archivos temporales e incompletos de ejecuciones anteriores"""
        try:
            self.logger.info("🧹 Limpiando archivos temporales e incompletos...")
            
            # Limpiar archivos .wav temporales en tmp/
            if self.tmp_dir.exists():
                temp_wav_files = list(self.tmp_dir.glob("*_temp.wav"))
                for temp_file in temp_wav_files:
                    try:
                        temp_file.unlink()
                        self.logger.debug(f"Eliminado archivo temporal: {temp_file.name}")
                    except Exception as e:
                        self.logger.warning(f"No se pudo eliminar {temp_file.name}: {e}")
            
            # Limpiar archivos .srt temporales que podrían estar incompletos
            # Buscar archivos .srt que no tengan su correspondiente .es.srt (traducido)
            for video_ext in ['.mp4', '.mkv', '.avi', '.mov', '.flv', '.wmv']:
                for video_file in self.base_dir.rglob(f"*{video_ext}"):
                    try:
                        # Verificar si existe un archivo .srt sin su versión traducida
                        srt_file = video_file.with_suffix('.srt')
                        es_srt_file = video_file.with_suffix('.es.srt')
                        
                        if srt_file.exists() and not es_srt_file.exists():
                            # Verificar si el archivo .srt parece incompleto (muy pequeño o modificado recientemente)
                            file_size = srt_file.stat().st_size
                            file_age_hours = (datetime.now() - datetime.fromtimestamp(srt_file.stat().st_mtime)).total_seconds() / 3600
                            
                            # Si es muy pequeño (< 100 bytes) o modificado en las últimas 2 horas (posiblemente en proceso)
                            if file_size < 100 or file_age_hours < 2:
                                srt_file.unlink()
                                self.logger.debug(f"Eliminado archivo SRT incompleto: {srt_file.name}")
                    
                    except Exception as e:
                        self.logger.warning(f"Error verificando {video_file.name}: {e}")
            
            self.logger.info("✅ Limpieza completada")
            
        except Exception as e:
            self.logger.error(f"Error durante limpieza: {e}")
    
    def process_directory(self, directory: Path) -> dict:
        """Procesa todos los videos en un directorio (concurrente sin Whisper, secuencial con Whisper)"""
        
        # Limpiar archivos temporales e incompletos antes de iniciar
        self.cleanup_incomplete_files()
        
        stats = {
            'total_files': 0,
            'processed': 0,
            'with_spanish_audio': 0,
            'with_spanish_subs': 0,
            'extracted': 0,
            'translated': 0,
            'errors': 0,
            'results': []
        }

        # Buscar archivos de video
        video_extensions = ['.mp4', '.mkv', '.avi', '.mov', '.flv', '.wmv']
        video_files = []

        for ext in video_extensions:
            video_files.extend(directory.rglob(f"*{ext}"))

        stats['total_files'] = len(video_files)
        self.logger.info(f"Archivos de video encontrados: {stats['total_files']}")

        # Determinar si usar procesamiento concurrente o secuencial
        use_concurrent = not self.use_whisper  # Concurrente solo si NO usa Whisper
        actual_workers = 1 if self.use_whisper else self.max_workers

        self.logger.info(f"Modo de procesamiento: {'secuencial (1 por 1)' if self.use_whisper else f'concurrent ({actual_workers} workers)'}")

        if use_concurrent and actual_workers > 1:
            # Procesamiento concurrente (solo sin Whisper)
            with ThreadPoolExecutor(max_workers=actual_workers) as executor:
                futures = {executor.submit(self.process_video, vf): vf for vf in video_files}

                for future in as_completed(futures):
                    result = future.result()

                    with self.progress_lock:
                        stats['results'].append(result)
                        stats['processed'] += 1

                        if result['has_spanish_audio']:
                            stats['with_spanish_audio'] += 1
                        elif result['had_spanish_subtitles']:
                            stats['with_spanish_subs'] += 1
                        elif result['extracted']:
                            stats['extracted'] += 1
                            if result['translated']:
                                stats['translated'] += 1

                        if result['error']:
                            stats['errors'] += 1

                    self.logger.info(f"Progreso: {stats['processed']}/{stats['total_files']}")
        else:
            # Procesamiento secuencial (con Whisper o cuando max_workers=1)
            for video_file in video_files:
                result = self.process_video(video_file)

                with self.progress_lock:
                    stats['results'].append(result)
                    stats['processed'] += 1

                    if result['has_spanish_audio']:
                        stats['with_spanish_audio'] += 1
                    elif result['had_spanish_subtitles']:
                        stats['with_spanish_subs'] += 1
                    elif result['extracted']:
                        stats['extracted'] += 1
                        if result['translated']:
                            stats['translated'] += 1

                    if result['error']:
                        stats['errors'] += 1

                self.logger.info(f"Progreso: {stats['processed']}/{stats['total_files']}")

        # Procesar subtítulos existentes que no estén en español
        self.logger.info("Revisando subtítulos existentes...")
        subtitle_stats = self.process_existing_subtitles(directory)
        
        # Agregar estadísticas de subtítulos a las estadísticas principales
        stats['subtitle_check'] = subtitle_stats
        
        self.logger.info(f"Subtítulos revisados: {subtitle_stats['total_srt_files']} totales, "
                        f"{subtitle_stats['spanish_subtitles']} ya en español, "
                        f"{subtitle_stats['translated_subtitles']} traducidos")

        return stats
    
    def process_file_list(self, file_paths: List[Path]) -> dict:
        """Procesa una lista específica de archivos de video (concurrente sin Whisper, secuencial con Whisper)"""
        
        # Limpiar archivos temporales e incompletos antes de iniciar
        self.cleanup_incomplete_files()
        
        stats = {
            'total_files': len(file_paths),
            'processed': 0,
            'with_spanish_audio': 0,
            'with_spanish_subs': 0,
            'extracted': 0,
            'translated': 0,
            'errors': 0,
            'results': []
        }

        self.logger.info(f"Archivos a procesar: {stats['total_files']}")

        # Filtrar archivos que existen
        valid_files = []
        for video_file in file_paths:
            if not video_file.exists():
                self.logger.warning(f"Archivo no encontrado: {video_file}")
                stats['errors'] += 1
            else:
                valid_files.append(video_file)

        # Determinar si usar procesamiento concurrente o secuencial
        use_concurrent = not self.use_whisper  # Concurrente solo si NO usa Whisper
        actual_workers = 1 if self.use_whisper else self.max_workers

        self.logger.info(f"Modo de procesamiento: {'secuencial (1 por 1)' if self.use_whisper else f'concurrent ({actual_workers} workers)'}")

        if use_concurrent and actual_workers > 1:
            # Procesamiento concurrente (solo sin Whisper)
            with ThreadPoolExecutor(max_workers=actual_workers) as executor:
                futures = {executor.submit(self.process_video, vf): vf for vf in valid_files}

                for future in as_completed(futures):
                    result = future.result()

                    with self.progress_lock:
                        stats['results'].append(result)
                        stats['processed'] += 1

                        if result['has_spanish_audio']:
                            stats['with_spanish_audio'] += 1
                        elif result['had_spanish_subs']:
                            stats['with_spanish_subs'] += 1
                        elif result['extracted']:
                            stats['extracted'] += 1
                            if result['translated']:
                                stats['translated'] += 1

                        if result['error']:
                            stats['errors'] += 1

                    self.logger.info(f"Progreso: {stats['processed']}/{stats['total_files']}")
        else:
            # Procesamiento secuencial (con Whisper o cuando max_workers=1)
            for video_file in valid_files:
                result = self.process_video(video_file)

                with self.progress_lock:
                    stats['results'].append(result)
                    stats['processed'] += 1

                    if result['has_spanish_audio']:
                        stats['with_spanish_audio'] += 1
                    elif result['had_spanish_subtitles']:
                        stats['with_spanish_subs'] += 1
                    elif result['extracted']:
                        stats['extracted'] += 1
                        if result['translated']:
                            stats['translated'] += 1

                    if result['error']:
                        stats['errors'] += 1

                self.logger.info(f"Progreso: {stats['processed']}/{stats['total_files']}")

        return stats
    
    def quick_check_needs_processing(self, video_file: Path) -> bool:
        """Verificación rápida para determinar si un archivo necesita procesamiento de subtítulos"""
        try:
            # 1. Verificar si ya tiene subtítulos en español
            if self.check_existing_spanish_subtitles(video_file):
                return False
            
            # 2. Verificación rápida de tamaño (archivos muy pequeños probablemente no tienen audio útil)
            if video_file.stat().st_size < 10 * 1024 * 1024:  # Menos de 10MB
                self.logger.debug(f"Archivo muy pequeño, omitiendo: {video_file.name}")
                return False
            
            # 3. Verificación rápida de extensión
            if not video_file.suffix.lower() in ['.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm']:
                self.logger.debug(f"Formato no soportado: {video_file.suffix}")
                return False
            
            return True
            
        except Exception as e:
            self.logger.warning(f"Error en verificación rápida de {video_file.name}: {e}")
            return False
    
    def update_progress_file(self, stats: dict):
        """Actualiza el archivo de progreso con estadísticas de subtítulos (thread-safe)"""
        progress_file = self.tmp_dir / "progress.json"
        
        with self.progress_lock:
            try:
                # Leer progreso existente
                if progress_file.exists():
                    with open(progress_file, 'r') as f:
                        progress_data = json.load(f)
                else:
                    progress_data = {}
                
                # Agregar estadísticas de subtítulos
                progress_data['subtitle_translation'] = {
                    'last_run': datetime.now().isoformat(),
                    'total_files': stats['total_files'],
                    'extracted': stats['extracted'],
                    'translated': stats['translated'],
                    'with_spanish_audio': stats['with_spanish_audio'],
                    'with_spanish_subs': stats['with_spanish_subs'],
                    'errors': stats['errors'],
                    'whisper_used': self.use_whisper and self.whisper_available
                }
                
                # Guardar
                with open(progress_file, 'w') as f:
                    json.dump(progress_data, f, indent=2)
                
                self.logger.info("Archivo de progreso actualizado con estadísticas de subtítulos")
                
            except Exception as e:
                self.logger.error(f"Error actualizando archivo de progreso: {e}")

def main():
    """Función principal"""
    import argparse

    parser = argparse.ArgumentParser(description='MediaJelly Subtitle Translator')
    parser.add_argument('paths', nargs='+', help='Directorio o archivos de video a procesar')
    parser.add_argument('--max-workers', type=int, default=2,
                       help='Máximo de archivos procesados simultáneamente (default: 2, se ajusta a 1 con Whisper)')
    parser.add_argument('--no-whisper', action='store_true',
                       help='Desactivar Whisper (solo subtítulos embebidos)')

    args = parser.parse_args()

    # Ajustar max_workers automáticamente cuando se usa Whisper
    if not args.no_whisper:
        args.max_workers = 1  # Whisper solo procesa 1 por 1
        print("ℹ️  Whisper habilitado: Procesamiento secuencial (1 archivo por vez)")

    # Crear traductor con parámetros
    translator = SubtitleTranslator(
        max_workers=args.max_workers,
        use_whisper=not args.no_whisper
    )

    # Corregir rutas para el contenedor Docker
    # Dentro del contenedor, las rutas están montadas en /mediajelly (minúsculas)
    corrected_paths = []
    for path_str in args.paths:
        path = Path(path_str)
        # Si estamos en contenedor y la ruta empieza con /mediaJelly, convertir a /mediajelly
        if translator.is_container and str(path).startswith('/mediaJelly'):
            corrected_path = Path(str(path).replace('/mediaJelly', '/mediajelly', 1))
            corrected_paths.append(str(corrected_path))
        else:
            corrected_paths.append(path_str)

    # Si el primer argumento es un directorio, procesar todo el directorio
    first_arg = Path(corrected_paths[0])

    if first_arg.is_dir():
        translator.logger.info(f"=== Iniciando traducción de subtítulos en directorio {first_arg} ===")
        translator.logger.info(f"Modo: {'secuencial (Whisper)' if translator.use_whisper else f'concurrente ({args.max_workers} workers)'}")
        stats = translator.process_directory(first_arg)
    else:
        # Procesar lista de archivos
        file_paths = [Path(p) for p in corrected_paths]
        translator.logger.info(f"=== Iniciando traducción de subtítulos para {len(file_paths)} archivo(s) ===")
        translator.logger.info(f"Modo: {'secuencial (Whisper)' if translator.use_whisper else f'concurrente ({args.max_workers} workers)'}")
        stats = translator.process_file_list(file_paths)

    # Actualizar archivo de progreso
    translator.update_progress_file(stats)

    # Resumen
    translator.logger.info("=== Resumen de traducción de subtítulos ===")
    translator.logger.info(f"Total archivos analizados: {stats['total_files']}")
    translator.logger.info(f"Con audio en español: {stats['with_spanish_audio']}")
    translator.logger.info(f"Con subtítulos en español: {stats['with_spanish_subs']}")
    translator.logger.info(f"Subtítulos extraídos: {stats['extracted']}")
    translator.logger.info(f"Subtítulos traducidos: {stats['translated']}")
    translator.logger.info(f"Errores: {stats['errors']}")

    # Imprimir para que el runner pueda capturar
    print(f"Subtítulos traducidos: {stats['translated']}")
    print(f"Errores: {stats['errors']}")

if __name__ == "__main__":
    main()
