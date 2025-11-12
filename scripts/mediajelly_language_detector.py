#!/usr/bin/env python3
"""
MediaJelly Language Detector
Pre-análisis de idiomas de pistas de audio sin etiquetas.
Ejecuta ANTES del procesador para evitar problemas de memoria.
"""

import os
import sys
import json
import subprocess
import tempfile
import random
import traceback
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional, Any, Union

# Importar módulo de emojis
from mediajelly_emoji import EmojiGenerator

# ============================================================================
# CONFIGURACIÓN
# ============================================================================

# Detectar si estamos en contenedor o host
if Path("/mediajelly").exists():
    MEDIA_PATHS = ["/mediajelly/media/anime", "/mediajelly/media/series", "/mediajelly/media/Peliculas"]
    CACHE_FILE_PATH = "/mediajelly/scripts/tmp/language_detection_cache.json"
    LOG_FILE_PATH = "/mediajelly/scripts/logs/language-detection.log"
else:
    MEDIA_PATHS = [
        "/home/tafurc/mediaJelly/media/anime",
        "/home/tafurc/mediaJelly/media/series",
        "/home/tafurc/mediaJelly/media/Peliculas",
    ]
    CACHE_FILE_PATH = "/home/tafurc/mediaJelly/scripts/tmp/language_detection_cache.json"
    LOG_FILE_PATH = "/home/tafurc/mediaJelly/scripts/logs/language-detection.log"

LANGUAGE_CODE_MAP = {
    "es": "spa",
    "en": "eng",
    "ja": "jpn",
    "fr": "fra",
    "de": "deu",
    "it": "ita",
    "pt": "por",
    "ru": "rus",
    "zh": "chi",
    "ko": "kor",
    "ar": "ara",
    "hi": "hin",
    "tr": "tur",
    "pl": "pol",
    "nl": "nld",
    "sv": "swe",
    "no": "nor",
    "da": "dan",
    "fi": "fin",
    "cs": "cze",
    "hu": "hun",
    "ro": "rum",
    "th": "tha",
    "vi": "vie",
    "id": "ind",
    "he": "heb",
    "el": "gre",
    "uk": "ukr",
    "ca": "cat",
    "hr": "hrv",
}

# ============================================================================
# UTILIDADES
# ============================================================================


def validate_file_path(file_path: Union[str, Path]) -> Path:
    """Valida que la ruta de archivo sea segura y exista."""
    if not file_path or not isinstance(file_path, (str, Path)):
        raise ValueError("Ruta de archivo inválida")

    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Archivo no encontrado: {file_path}")

    # Validar que no sea un directorio
    if path.is_dir():
        raise ValueError(f"La ruta es un directorio, no un archivo: {file_path}")

    return path.resolve()


def safe_subprocess_run(cmd: List[str], **kwargs) -> subprocess.CompletedProcess:
    """Ejecuta subprocess.run con validación adicional de seguridad."""
    if not cmd or not isinstance(cmd, list):
        raise ValueError("Comando inválido")

    # Validar que el comando ejecutable existe
    executable = cmd[0]
    if not executable or not isinstance(executable, str):
        raise ValueError("Ejecutable inválido")

    # Solo permitir comandos conocidos y seguros
    allowed_commands = ["ffprobe", "ffmpeg"]
    if executable not in allowed_commands:
        raise ValueError(f"Comando no permitido: {executable}")

    return subprocess.run(cmd, **kwargs)


def log(message: str, level: str = "INFO") -> None:
    """Registra mensaje en el log con timestamp y emoji."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    emoji_map = {
        "INFO": EmojiGenerator.info(),
        "SUCCESS": EmojiGenerator.success(),
        "ERROR": EmojiGenerator.error(),
        "WARNING": EmojiGenerator.warning_msg(),
        "DETECT": EmojiGenerator.audio(),
        "SKIP": EmojiGenerator.next_track(),
    }
    emoji = emoji_map.get(level, "•")
    log_message = f"[{timestamp}] {emoji} {message}"

    print(log_message)

    try:
        os.makedirs(os.path.dirname(LOG_FILE_PATH), exist_ok=True)
        with open(LOG_FILE_PATH, "a", encoding="utf-8") as f:
            f.write(log_message + "\n")
    except Exception as e:
        print(f"{EmojiGenerator.error()} Error escribiendo log: {e}")


def load_cache() -> Dict[str, Any]:
    """Carga el caché de idiomas detectados."""
    if os.path.exists(CACHE_FILE_PATH):
        try:
            with open(CACHE_FILE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            log(f"Error cargando caché: {e}", "ERROR")
    return {}


def save_cache(cache: Dict[str, Any]) -> None:
    """Guarda el caché de idiomas detectados."""
    try:
        os.makedirs(os.path.dirname(CACHE_FILE_PATH), exist_ok=True)
        with open(CACHE_FILE_PATH, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2, ensure_ascii=False)
        log(f"Caché guardado: {len(cache)} archivos", "SUCCESS")
    except Exception as e:
        log(f"Error guardando caché: {e}", "ERROR")


# ============================================================================
# DETECCIÓN DE STREAMS
# ============================================================================


def get_streams_needing_detection(file_path):
    """
    Detecta streams de audio sin etiqueta de idioma.
    Retorna lista de tuplas (stream_index_absoluto, codec, duration).
    """
    try:
        # Validar entrada
        validated_path = validate_file_path(file_path)

        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "stream=index,codec_type,codec_name:stream_tags=language",
            "-of",
            "json",
            str(validated_path),
        ]

        result = safe_subprocess_run(cmd, capture_output=True, text=True, timeout=30)

        if result.returncode != 0:
            log(f"Error en ffprobe: {result.stderr}", "ERROR")
            return []

        data = json.loads(result.stdout)
        streams = data.get("streams", [])

        streams_to_detect = []
        audio_index = 0

        for stream in streams:
            if stream.get("codec_type") == "audio":
                tags = stream.get("tags", {})
                language = tags.get("language", "").lower()

                # Buscar idioma en mayúsculas también
                if not language:
                    language = tags.get("LANGUAGE", "").lower()

                if not language or language in ["und", "unknown", "zxx"]:
                    absolute_index = stream.get("index", audio_index)
                    codec_name = stream.get("codec_name", "unknown")

                    # Obtener duración del archivo
                    duration = get_file_duration(file_path)

                    streams_to_detect.append((absolute_index, codec_name, duration))
                    log(f"  Stream {absolute_index} (audio #{audio_index}): {codec_name} - necesita detección", "INFO")

                audio_index += 1

        return streams_to_detect

    except Exception as e:
        log(f"Error detectando streams: {e}", "ERROR")
        traceback.print_exc()
        return []


def get_file_duration(file_path: Union[str, Path]) -> float:
    """Obtiene la duración del archivo en segundos."""
    try:
        # Validar entrada
        validated_path = validate_file_path(file_path)

        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(validated_path),
        ]

        result = safe_subprocess_run(cmd, capture_output=True, text=True, timeout=30)

        if result.returncode == 0 and result.stdout.strip():
            return float(result.stdout.strip())

        return 0.0

    except Exception as e:
        log(f"Error obteniendo duración: {e}", "WARNING")
        return 0.0


# ============================================================================
# DETECCIÓN DE IDIOMA CON WHISPER
# ============================================================================


def load_whisper_model():
    """Carga el modelo Whisper."""
    try:
        import whisper

        log(f"{EmojiGenerator.refresh()} Cargando modelo Whisper (base)...", "INFO")
        model = whisper.load_model("base")
        log(f"{EmojiGenerator.success()} Modelo Whisper cargado exitosamente", "SUCCESS")
        return model
    except Exception as e:
        log(f"Error cargando modelo Whisper: {e}", "ERROR")
        traceback.print_exc()
        return None


def extract_audio_sample(file_path: Union[str, Path], stream_index: int, start_time: Union[int, float], duration: int = 60) -> Optional[str]:
    """Extrae una muestra de audio desde un timestamp específico."""
    try:
        # Validar entrada
        validated_path = validate_file_path(file_path)
        if not isinstance(stream_index, int) or stream_index < 0:
            raise ValueError("Índice de stream inválido")
        if not isinstance(start_time, (int, float)) or start_time < 0:
            raise ValueError("Tiempo de inicio inválido")
        if not isinstance(duration, (int, float)) or duration <= 0:
            raise ValueError("Duración inválida")

        temp_file = tempfile.NamedTemporaryFile(suffix=".wav", delete=False, dir="/mediajelly/scripts/tmp")
        temp_path = temp_file.name
        temp_file.close()

        cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            str(start_time),
            "-i",
            str(validated_path),
            "-t",
            str(duration),
            "-map",
            f"0:{stream_index}",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-vn",
            temp_path,
        ]

        result = safe_subprocess_run(cmd, capture_output=True, text=True, timeout=120)

        if result.returncode != 0:
            log(f"Error extrayendo muestra: {result.stderr}", "ERROR")
            if os.path.exists(temp_path):
                os.remove(temp_path)
            return None

        return temp_path

    except Exception as e:
        log(f"Error en extract_audio_sample: {e}", "ERROR")
        return None


def detect_language_with_whisper(model, audio_file):
    """Detecta el idioma de un archivo de audio usando Whisper."""
    try:
        # Transcribir solo para detección de idioma (sin transcripción completa)
        audio = model.transcribe(audio_file, language=None, task="transcribe", fp16=False)
        detected_language = audio.get("language", "unknown")

        # Obtener probabilidades si están disponibles
        probs = {}

        # La API nueva de Whisper maneja la detección de idioma internamente
        # No necesitamos llamar a get_mel directamente

        return detected_language, probs

    except Exception as e:
        log(f"Error en detección Whisper: {e}", "ERROR")
        traceback.print_exc()
        return None, {}


def detect_language_with_multiple_samples(model, file_path, stream_index, total_duration):
    """
    Detecta el idioma usando múltiples muestras aleatorias y votación.
    Retorna el idioma detectado en formato ISO 639-2 (3 letras).
    """
    if total_duration < 60:
        log(f"Archivo muy corto ({total_duration:.1f}s), usando muestra única", "WARNING")
        return detect_single_sample(model, file_path, stream_index, total_duration)

    # Calcular número de muestras (máximo 10)
    num_samples = min(10, int(total_duration / 60))
    log(f"🎙️ Analizando {num_samples} muestras aleatorias de 60s...", "DETECT")

    # Generar timestamps aleatorios (evitar primeros y últimos 30s)
    safe_start = 30
    safe_end = total_duration - 90  # -60s para la muestra, -30s de margen

    if safe_end <= safe_start:
        safe_end = total_duration - 60 if total_duration > 60 else 0

    timestamps = sorted(random.sample(range(int(safe_start), int(safe_end)), num_samples))

    # Analizar cada muestra
    language_votes = {}
    temp_files = []

    for i, timestamp in enumerate(timestamps, 1):
        temp_audio = extract_audio_sample(file_path, stream_index, timestamp, 60)

        if not temp_audio:
            continue

        temp_files.append(temp_audio)

        detected_lang, probs = detect_language_with_whisper(model, temp_audio)

        if detected_lang:
            language_votes[detected_lang] = language_votes.get(detected_lang, 0) + 1

            # Mostrar top 3 probabilidades
            if probs:
                sorted_probs = sorted(probs.items(), key=lambda x: x[1], reverse=True)[:3]
                probs_str = ", ".join([f"{lang}: {prob*100:.1f}%" for lang, prob in sorted_probs])
                log(f"  Muestra {i-1}/{num_samples} (min {timestamp//60}): {detected_lang} ({probs_str})", "INFO")
            else:
                log(f"  Muestra {i-1}/{num_samples} (min {timestamp//60}): {detected_lang}", "INFO")

    # Limpiar archivos temporales
    for temp_file in temp_files:
        try:
            if os.path.exists(temp_file):
                os.remove(temp_file)
        except Exception as e:
            log(f"Error eliminando temporal {temp_file}: {e}", "WARNING")

    # Determinar idioma ganador
    if not language_votes:
        log("No se pudieron analizar muestras, usando 'spa' por defecto", "WARNING")
        return "spa"

    # Ordenar por votos
    sorted_votes = sorted(language_votes.items(), key=lambda x: x[1], reverse=True)
    winner_lang, winner_votes = sorted_votes[0]
    total_votes = sum(language_votes.values())
    winner_percentage = (winner_votes / total_votes) * 100

    # Reglas especiales para español
    spanish_votes = language_votes.get("es", 0)
    spanish_percentage = (spanish_votes / total_votes) * 100 if total_votes > 0 else 0

    # Si español tiene ≥30% de votos, es español
    if spanish_percentage >= 30:
        final_lang = "es"
        log(
            f"{EmojiGenerator.success()} Votación: Español {spanish_percentage:.1f}% (≥30% umbral) - {sorted_votes}",
            "SUCCESS",
        )
    # Si el ganador tiene <50% y español está presente, usar español
    elif winner_percentage < 50 and spanish_votes > 0:
        final_lang = "es"
        log(
            f"{EmojiGenerator.success()} Votación: {winner_lang} {winner_percentage:.1f}% (<50%), Español presente → 'spa' - {sorted_votes}",
            "SUCCESS",
        )
    else:
        final_lang = winner_lang
        log(f"{EmojiGenerator.success()} Votación: {winner_lang} {winner_percentage:.1f}% - {sorted_votes}", "SUCCESS")

    # Convertir a código ISO 639-2 (3 letras)
    return LANGUAGE_CODE_MAP.get(final_lang, final_lang)


def detect_single_sample(model, file_path, stream_index, total_duration):
    """Detecta idioma con una sola muestra (archivos cortos)."""
    start_time = min(10, total_duration / 2) if total_duration > 15 else 0

    temp_audio = extract_audio_sample(file_path, stream_index, start_time, min(60, total_duration))

    if not temp_audio:
        return "spa"

    try:
        detected_lang, probs = detect_language_with_whisper(model, temp_audio)

        if detected_lang:
            final_lang = LANGUAGE_CODE_MAP.get(detected_lang, detected_lang)

            if probs:
                sorted_probs = sorted(probs.items(), key=lambda x: x[1], reverse=True)[:3]
                probs_str = ", ".join([f"{lang}: {prob*100:.1f}%" for lang, prob in sorted_probs])
                log(f"✅ Idioma detectado: {final_lang} ({probs_str})", "SUCCESS")
            else:
                log(f"✅ Idioma detectado: {final_lang}", "SUCCESS")

            return final_lang

        return "spa"

    finally:
        try:
            if os.path.exists(temp_audio):
                os.remove(temp_audio)
        except Exception as e:
            log(f"Error eliminando temporal: {e}", "WARNING")


# ============================================================================
# PROCESO PRINCIPAL
# ============================================================================


def validate_media_paths():
    """Valida que los directorios de medios existan."""
    missing_paths = []
    for path in MEDIA_PATHS:
        if not Path(path).exists():
            missing_paths.append(path)

    if missing_paths:
        log(f"Directorios de medios no encontrados: {missing_paths}", "WARNING")
        return False
    return True


def scan_files_needing_detection():
    """Escanea archivos pendientes de procesamiento y detecta cuáles necesitan análisis de idioma."""
    # Validar que los directorios de medios existan
    if not validate_media_paths():
        log("Algunos directorios de medios no existen", "ERROR")
        return []

    files_to_analyze = []

    # Detectar entorno y definir ruta del archivo pending
    if Path("/mediajelly").exists():
        pending_file = Path("/mediajelly/scripts/tmp/pending-compression.txt")
    else:
        pending_file = Path("/home/tafurc/mediaJelly/scripts/tmp/pending-compression.txt")

    if not pending_file.exists():
        log(f"Archivo pending no encontrado: {pending_file}", "WARNING")
        return files_to_analyze

    log(f"Leyendo archivos pendientes de: {pending_file}", "INFO")

    # Leer archivos pendientes
    try:
        with open(pending_file, "r", encoding="utf-8") as f:
            pending_paths = [line.strip() for line in f if line.strip()]
    except Exception as e:
        log(f"Error leyendo archivo pending: {e}", "ERROR")
        return files_to_analyze

    log(f"Encontrados {len(pending_paths)} archivos pendientes", "INFO")

    # Procesar cada archivo pendiente
    for file_path_str in pending_paths:
        file_path = Path(file_path_str)

        # Verificar que el archivo existe
        if not file_path.exists():
            log(f"Archivo pendiente no existe: {file_path}", "WARNING")
            continue

        # Verificar que es un archivo de video
        if not file_path.suffix.lower() in [".mkv", ".mp4", ".avi", ".m4v", ".mov"]:
            log(f"Archivo pendiente no es video: {file_path}", "WARNING")
            continue

        # Verificar si necesita detección de idioma
        streams = get_streams_needing_detection(str(file_path))

        if streams:
            # Mantener ruta absoluta para acceso al archivo, pero usar relativa para cache
            absolute_path = str(file_path.resolve())
            # Convertir a ruta relativa al directorio media para consistencia en cache
            if "/media/" in absolute_path:
                relative_path = absolute_path.split("/media/", 1)[1]
            else:
                # Fallback: usar el nombre del archivo como identificador
                relative_path = file_path.name

            files_to_analyze.append(
                {"absolute_path": absolute_path, "relative_path": relative_path, "streams": streams}
            )
            log(f"{file_path.name}: {len(streams)} stream(s) sin idioma", "INFO")
        else:
            log(f"{file_path.name}: ya tiene etiquetas de idioma", "SKIP")

    return files_to_analyze


def process_files(files_to_analyze, cache):
    """Procesa los archivos detectando idiomas de sus streams."""
    if not files_to_analyze:
        log("No hay archivos que necesiten análisis de idioma", "INFO")
        return cache

    log(f"Total de archivos a analizar: {len(files_to_analyze)}", "INFO")

    # Cargar modelo Whisper UNA SOLA VEZ
    model = load_whisper_model()

    if not model:
        log("No se pudo cargar Whisper, abortando análisis", "ERROR")
        return cache

    # Procesar cada archivo
    for idx, file_info in enumerate(files_to_analyze, 1):
        absolute_path = file_info["absolute_path"]
        relative_path = file_info["relative_path"]
        streams = file_info["streams"]

        log(f"\n{'='*80}", "INFO")
        log(f"[{idx}/{len(files_to_analyze)}] {os.path.basename(absolute_path)}", "INFO")
        log(f"{'='*80}", "INFO")

        # Inicializar entrada en caché usando ruta absoluta resuelta
        if absolute_path not in cache:
            cache[absolute_path] = {}

        # Analizar cada stream
        for stream_index, codec, duration in streams:
            log(f"\n🎙️ Analizando stream {stream_index} ({codec}, {duration:.1f}s)...", "DETECT")

            detected_lang = detect_language_with_multiple_samples(model, absolute_path, stream_index, duration)

            cache[absolute_path][str(stream_index)] = detected_lang
            log(f"Stream {stream_index}: {detected_lang}", "SUCCESS")

        # Guardar caché cada 5 archivos
        if idx % 5 == 0:
            save_cache(cache)

    # Guardar caché final
    save_cache(cache)

    return cache


def main():
    """Función principal."""
    try:
        log("\n" + "=" * 80, "INFO")
        log("MEDIAJELLY LANGUAGE DETECTOR", "INFO")
        log("=" * 80 + "\n", "INFO")

        # Cargar caché existente
        cache = load_cache()
        log(f"Caché cargado: {len(cache)} archivos previamente analizados", "INFO")

        # Escanear archivos
        files_to_analyze = scan_files_needing_detection()

        # Filtrar archivos ya analizados
        files_pending = []
        for file_info in files_to_analyze:
            absolute_path = file_info["absolute_path"]
            streams = file_info["streams"]

            # Verificar si todos los streams ya están en caché
            if absolute_path in cache:
                pending_streams = [s for s in streams if str(s[0]) not in cache[absolute_path]]

                if pending_streams:
                    files_pending.append(
                        {
                            "absolute_path": absolute_path,
                            "relative_path": file_info["relative_path"],
                            "streams": pending_streams,
                        }
                    )
            else:
                files_pending.append(file_info)

        if files_pending:
            log(f"Archivos pendientes de análisis: {len(files_pending)}", "INFO")
            process_files(files_pending, cache)
        else:
            log("Todos los archivos ya están analizados", "SUCCESS")

        log("\n" + "=" * 80, "INFO")
        log("ANÁLISIS COMPLETADO", "SUCCESS")
        log("=" * 80 + "\n", "INFO")

    except KeyboardInterrupt:
        log("Ejecución interrumpida por el usuario", "WARNING")
        sys.exit(1)
    except Exception as e:
        log(f"Error fatal en la ejecución: {e}", "ERROR")
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
