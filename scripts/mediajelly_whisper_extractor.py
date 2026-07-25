#!/usr/bin/env python3
"""
mediajelly_whisper_extractor.py

Mixin con la extracción de subtítulos vía transcripción de audio con Whisper:
extracción/normalización del audio (con manejo de videos corruptos y
fallback a remux), transcripción, generación del .srt y su traslado a la
carpeta del video con nombre canónico.

Extraído de SubtitleTranslator (mediajelly_subtitle_translator.py) para
reducir el tamaño de esa clase y aislar esta responsabilidad.

Requiere que la clase que lo use tenga disponibles (provistos por
SubtitleTranslator.__init__): `self.logger`, `self.tmp_dir`,
`self.whisper_model`, `self.force_language`, y el método
`self._improve_extracted_subtitles_quality` (definido en la clase principal).
"""

import re
import subprocess
import shutil
from pathlib import Path
from typing import Any, Dict, Optional

from mediajelly_emoji import EmojiGenerator


class WhisperAudioExtractorMixin:
    """Extracción de subtítulos desde audio mediante transcripción con Whisper."""

    def extract_subtitles_from_audio(self, video_file: Path) -> Optional[Path]:
        """
        Extrae subtítulos del audio usando Whisper (procesamiento secuencial con lock global).

        Args:
            video_file: Ruta del archivo de video.

        Returns:
            Optional[Path]: Ruta del archivo SRT generado o None si falló.
        """
        if not WHISPER_AVAILABLE:
            self.logger.warning("Whisper no disponible, no se pueden extraer subtítulos del audio")
            return None

        audio_file = None
        output_srt = None

        try:
            # Preparar archivos temporales
            temp_files = self._prepare_temp_files(video_file)
            audio_file, output_srt = temp_files

            # Extraer audio del video (sin límite de duración)
            if not self._extract_audio_from_video(video_file, audio_file):
                return None

            # Transcribir audio con Whisper
            transcription_result = self._transcribe_audio_with_whisper(audio_file)
            if not transcription_result:
                return None

            # Generar archivo SRT desde la transcripción (aún en tmp_dir, nombre temporal)
            temp_srt = self._generate_srt_from_transcription(transcription_result, output_srt)
            if not temp_srt:
                return None

            # CORRECCIÓN: mover el SRT a la carpeta del video con nombre canónico
            # (antes se dejaba en tmp_dir con el sufijo "_{mtime_ns}", por lo que
            # check_existing_spanish_subtitles()/find_existing_srt_file() nunca lo
            # encontraban en la siguiente ejecución y el video se re-procesaba
            # indefinidamente, además de acumular huérfanos en scripts/tmp).
            return self._move_srt_to_video_folder(temp_srt, video_file)

        except subprocess.TimeoutExpired:
            self.logger.error("Timeout extrayendo audio (15 minutos)")
            return None
        except Exception as e:
            self.logger.error(f"Error extrayendo subtítulos del audio: {e}")
            return None
        finally:
            # LIMPIEZA: Eliminar archivos temporales SIEMPRE, incluso en errores
            self._cleanup_temp_files(audio_file)

    def _move_srt_to_video_folder(self, temp_srt: Path, video_file: Path) -> Optional[Path]:
        """
        Mueve un SRT generado en tmp_dir a la carpeta del video, con el nombre
        canónico "{video_stem}.srt" (mismo esquema que usa la extracción de
        subtítulos embebidos). Evita nombres volátiles basados en mtime_ns que
        rompen la detección de subtítulos ya procesados.

        Args:
            temp_srt: Ruta del SRT en tmp_dir (nombre con sufijo mtime_ns).
            video_file: Ruta del video original.

        Returns:
            Optional[Path]: Ruta final en la carpeta del video, o None si falló el movimiento.
        """
        final_srt = video_file.parent / f"{video_file.stem}.srt"
        try:
            final_tmp = final_srt.with_suffix(final_srt.suffix + ".tmp")
            if final_tmp.exists():
                final_tmp.unlink()
            shutil.move(str(temp_srt), str(final_tmp))
            if final_srt.exists():
                final_srt.unlink()
            final_tmp.rename(final_srt)
            self.logger.info(f"{EmojiGenerator.check_mark()} SRT de Whisper movido a carpeta del video: {final_srt.name}")
            return final_srt
        except Exception as e:
            self.logger.error(f"No se pudo mover el SRT generado por Whisper a la carpeta del video: {e}")
            # Como último recurso, dejar el archivo en tmp_dir para no perder el trabajo,
            # pero esto seguirá causando reprocesamiento en la siguiente ejecución.
            return temp_srt

    def _prepare_temp_files(self, video_file: Path) -> tuple[Path, Path]:
        """
        Prepara archivos temporales únicos para el procesamiento.

        Args:
            video_file: Ruta del archivo de video.

        Returns:
            tuple[Path, Path]: (Ruta archivo audio temporal, Ruta archivo SRT salida).
        """
        temp_prefix = f"{video_file.stem}_{video_file.stat().st_mtime_ns}"
        audio_file = self.tmp_dir / f"{temp_prefix}_temp.wav"
        # Guardar candidatos en tmp_dir para evitar conflictos y condiciones de carrera
        try:
            self.tmp_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        # Usar nombre único por proceso/tiempo para evitar condiciones de carrera
        output_srt = self.tmp_dir / f"{temp_prefix}.srt"
        return audio_file, output_srt

    def _extract_audio_from_video(self, video_file: Path, audio_file: Path) -> bool:
        """
        Extrae audio completo del video usando ffmpeg (con fallback para archivos corruptos).

        Args:
            video_file: Ruta del archivo de video.
            audio_file: Ruta del archivo de audio temporal.

        Returns:
            bool: True si se extrajo correctamente.
        """
        self.logger.info(f"Extrayendo audio temporal: {audio_file.name}")

        # Intentar primero con opciones normales
        if self._extract_audio_normal(video_file, audio_file):
            return True

        # Si falla, intentar con opciones tolerantes a corrupción
        self.logger.warning("Extracción normal falló, intentando con opciones tolerantes a corrupción...")
        if self._extract_audio_corruption_tolerant(video_file, audio_file):
            return True

        # Si aún falla, intentar método alternativo con codec copy y re-mux
        self.logger.warning("Extracción tolerante falló, intentando método de re-mux...")
        return self._extract_audio_remux(video_file, audio_file)

    def _extract_audio_normal(self, video_file: Path, audio_file: Path) -> bool:
        """
        Extrae audio con opciones normales de FFmpeg.

        Args:
            video_file: Ruta del archivo de video.
            audio_file: Ruta del archivo de audio temporal.

        Returns:
            bool: True si se extrajo correctamente.
        """
        cmd = [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(video_file),
            "-ar",
            "16000",  # Whisper requiere 16kHz
            "-ac",
            "1",  # Mono
            "-c:a",
            "pcm_s16le",
            str(audio_file),
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=21600)  # 6 horas timeout

        if result.returncode != 0:
            self.logger.error(f"Error extrayendo audio (modo normal): ffmpeg código {result.returncode}")
            if result.stderr:
                self.logger.error(f"FFmpeg stderr: {result.stderr[:500]}...")
            return False

        return self._validate_extracted_audio(audio_file)

    def _extract_audio_corruption_tolerant(self, video_file: Path, audio_file: Path) -> bool:
        """
        Extrae audio con opciones tolerantes a corrupción.

        Args:
            video_file: Ruta del archivo de video.
            audio_file: Ruta del archivo de audio temporal.

        Returns:
            bool: True si se extrajo correctamente.
        """
        cmd = [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-fflags",
            "+discardcorrupt+genpts+igndts",  # Ignorar corrupción y generar timestamps
            "-err_detect",
            "ignore_err",  # Ignorar errores de decodificación
            "-i",
            str(video_file),
            "-ar",
            "16000",  # Whisper requiere 16kHz
            "-ac",
            "1",  # Mono
            "-c:a",
            "pcm_s16le",
            str(audio_file),
        ]

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=21600)  # 6 horas timeout

        if result.returncode != 0:
            self.logger.error(f"Error extrayendo audio (modo tolerante): ffmpeg código {result.returncode}")
            if result.stderr:
                self.logger.error(f"FFmpeg stderr: {result.stderr[:500]}...")
            return False

        return self._validate_extracted_audio(audio_file)

    def _extract_audio_remux(self, video_file: Path, audio_file: Path) -> bool:
        """
        Extrae audio usando re-mux para evitar decodificación de streams corruptos.

        Args:
            video_file: Ruta del archivo de video.
            audio_file: Ruta del archivo de audio temporal.

        Returns:
            bool: True si se extrajo correctamente.
        """
        # Crear archivo temporal intermedio
        temp_audio = audio_file.with_suffix(".temp.aac")

        try:
            # Paso 1: Extraer audio sin re-encodear (codec copy)
            cmd1 = [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-fflags",
                "+discardcorrupt",
                "-i",
                str(video_file),
                "-vn",  # Sin video
                "-acodec",
                "copy",  # Copiar codec sin re-encodear
                "-map",
                "0:a:0",  # Solo primera pista de audio
                str(temp_audio),
            ]

            result1 = subprocess.run(cmd1, capture_output=True, text=True, timeout=3600)

            if result1.returncode != 0 or not temp_audio.exists() or temp_audio.stat().st_size == 0:
                self.logger.error(f"Error en re-mux paso 1: ffmpeg código {result1.returncode}")
                return False

            # Paso 2: Convertir a WAV desde el archivo extraído
            cmd2 = [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-fflags",
                "+discardcorrupt+igndts",
                "-err_detect",
                "ignore_err",
                "-i",
                str(temp_audio),
                "-ar",
                "16000",  # Whisper requiere 16kHz
                "-ac",
                "1",  # Mono
                "-c:a",
                "pcm_s16le",
                str(audio_file),
            ]

            result2 = subprocess.run(cmd2, capture_output=True, text=True, timeout=3600)

            if result2.returncode != 0:
                self.logger.error(f"Error en re-mux paso 2: ffmpeg código {result2.returncode}")
                if result2.stderr:
                    self.logger.error(f"FFmpeg stderr: {result2.stderr[:500]}...")
                return False

            return self._validate_extracted_audio(audio_file)

        finally:
            # Limpiar archivo temporal
            if temp_audio.exists():
                temp_audio.unlink()

    def _validate_extracted_audio(self, audio_file: Path) -> bool:
        """
        Valida que el audio extraído sea usable.

        Args:
            audio_file: Ruta del archivo de audio.

        Returns:
            bool: True si el audio es válido.
        """
        if not audio_file.exists() or audio_file.stat().st_size == 0:
            self.logger.error("Archivo de audio temporal no se creó o está vacío")
            return False

        # VALIDACIÓN: Verificar que el archivo de audio no esté corrupto
        if not self._validate_audio_file(audio_file):
            self.logger.error("Archivo de audio temporal está corrupto o vacío")
            return False

        self.logger.info(f"Audio extraído ({audio_file.stat().st_size} bytes), transcribiendo con Whisper...")
        return True

    def _detect_language_with_whisper_cpp(self, audio_file: Path, timeout: int = 600) -> Optional[str]:
        """
        Detecta el idioma de un archivo de audio usando `whisper-cli --detect-language`.

        Returns:
            Optional[str]: Código ISO de dos letras (ej: 'ja', 'en') o None.
        """
        try:
            if not WHISPER_CPP_BIN.exists() or not WHISPER_CPP_DEFAULT_MODEL.exists():
                return None

            cmd = [str(WHISPER_CPP_BIN), "-m", str(WHISPER_CPP_DEFAULT_MODEL), "--detect-language", "-f", str(audio_file)]
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            out = (proc.stdout or "") + "\n" + (proc.stderr or "")

            # Buscar patrón como: "auto-detected language: ja (p = 0.999300)"
            m = re.search(r"auto-detected language:\s*([a-z]{2})", out, flags=re.IGNORECASE)
            if m:
                return m.group(1).lower()

            m2 = re.search(r"Detected language:\s*([a-z]{2})", out, flags=re.IGNORECASE)
            if m2:
                return m2.group(1).lower()

            return None
        except Exception as e:
            self.logger.debug(f"Error detectando idioma con whisper-cli: {e}")
            return None

    def _transcribe_audio_with_whisper(self, audio_file: Path) -> Optional[dict]:
        """
        Transcribe audio usando whisper.cpp (si está disponible) o cae al modelo Python.

        - Detecta idioma con `whisper-cli --detect-language` (a menos que se haya forzado con `force_language`).
        - Ejecuta `whisper-cli` para generar un archivo SRT temporal y lo parsea a segmentos.

        Args:
            audio_file: Ruta del archivo de audio.

        Returns:
            Optional[dict]: Resultado de la transcripción con claves `segments` y `language`.
        """
        # Intentar usar whisper.cpp binario si está disponible (PoC CPU)
        try:
            if WHISPER_CPP_BIN.exists() and WHISPER_CPP_DEFAULT_MODEL.exists():
                # Determinar idioma: forzado o detectado
                language = None
                if getattr(self, "force_language", None):
                    language = self.force_language
                    self.logger.info(f"Idioma forzado por parámetro: {language}")
                else:
                    detected = self._detect_language_with_whisper_cpp(audio_file)
                    if detected:
                        language = detected
                        self.logger.info(f"Idioma detectado por whisper-cli: {language}")
                    else:
                        language = "auto"

                # Preparar path base de salida (sin extensión)
                suffix = audio_file.suffix or ""
                base = str(audio_file)
                if suffix:
                    base = str(audio_file)[: -len(suffix)]

                cmd = [
                    str(WHISPER_CPP_BIN),
                    "-m",
                    str(WHISPER_CPP_DEFAULT_MODEL),
                    "-f",
                    str(audio_file),
                    "-l",
                    language,
                    "-osrt",
                    "-of",
                    base,
                ]

                self.logger.info(f"Ejecutando whisper-cli: {' '.join(cmd[:6])} ...")
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=21600)

                if proc.returncode != 0:
                    self.logger.warning(
                        "whisper-cli devolvió error (returncode=%s): %s",
                        proc.returncode,
                        (proc.stderr or proc.stdout)[:1000],
                    )
                else:
                    srt_path = Path(base + ".srt")
                    if srt_path.exists() and srt_path.stat().st_size > 0:
                        # Parsear SRT a segmentos
                        segments: List[Dict[str, Union[float, str]]] = []
                        try:
                            with open(srt_path, "r", encoding="utf-8", errors="ignore") as sf:
                                content = sf.read()

                            # Separar bloques por doble salto de línea
                            blocks = [b.strip() for b in re.split(r"\n\s*\n", content) if b.strip()]
                            for block in blocks:
                                parts = block.splitlines()
                                if len(parts) >= 2:
                                    # parts[0] = index, parts[1] = times, rest = text
                                    times = parts[1].strip()
                                    m = re.match(r"(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})", times)
                                    if not m:
                                        continue

                                    def _to_seconds(ts: str) -> float:
                                        hh, mm, ss_ms = ts.split(":")
                                        ss, ms = ss_ms.split(",")
                                        return int(hh) * 3600 + int(mm) * 60 + int(ss) + int(ms) / 1000.0

                                    start = _to_seconds(m.group(1))
                                    end = _to_seconds(m.group(2))
                                    text = " ".join([line.strip() for line in parts[2:]]).strip()
                                    segments.append({"start": start, "end": end, "text": text})

                            # Si obtuvimos segmentos, retornar
                            if segments:
                                # Intentar eliminar el archivo SRT temporal (no crítico)
                                try:
                                    srt_path.unlink(missing_ok=True)
                                except Exception:
                                    pass
                                return {"segments": segments, "language": language}
                        except Exception as e:
                            self.logger.warning(f"Error parseando SRT generado por whisper-cli: {e}")

                    else:
                        self.logger.warning("whisper-cli no generó SRT válido o archivo vacío: %s", str(srt_path))

        except Exception as e:
            self.logger.warning("Error ejecutando whisper.cpp PoC: %s", e)

        # LOCK GLOBAL: Solo un proceso puede usar Whisper (fallback Python)
        with whisper_lock:
            self.logger.info(f"{EmojiGenerator.lock()} Adquiriendo lock global de Whisper...")

            # Cargar modelo si no está cargado
            self._ensure_whisper_model_loaded()

            if self.whisper_model is None:
                raise RuntimeError("No se pudo cargar el modelo Whisper")

            # Transcribir con Whisper (Python) con manejo específico de errores
            try:
                result = self.whisper_model.transcribe(
                    str(audio_file),
                    language=(self.force_language if getattr(self, "force_language", None) else None),
                    task="transcribe",
                    fp16=False,  # CPU mode
                    verbose=False,
                )
            except Exception as whisper_error:
                return self._handle_whisper_error(whisper_error)

            self.logger.info(f"{EmojiGenerator.unlock()} Liberando lock global de Whisper...")

        # Verificar que tenemos segmentos
        if not result.get("segments"):
            self.logger.warning("Whisper no generó segmentos de transcripción (fallback)")
            return None

        return result

    def _ensure_whisper_model_loaded(self) -> None:
        """Asegura que el modelo Whisper esté cargado."""
        if self.whisper_model is None:
            if not WHISPER_AVAILABLE:
                self.logger.error("Intentando cargar modelo Whisper pero la librería no está disponible")
                self.whisper_model = None
                return

            self.logger.info("Cargando modelo Whisper (small)...")
            try:
                # Importar localmente para evitar usar el objeto global que puede ser None
                import whisper as _whisper  # type: ignore

                self.whisper_model = _whisper.load_model("small")
            except Exception as e:
                self.logger.error(f"No se pudo cargar el modelo Whisper: {e}")
                self.whisper_model = None

    def _handle_whisper_error(self, whisper_error: Exception) -> Optional[Dict[str, Any]]:
        """
        Maneja errores específicos de Whisper.

        Args:
            whisper_error: Excepción capturada.

        Returns:
            Optional[Dict[str, Any]]: None (siempre retorna None o lanza excepción).
        """
        error_msg = str(whisper_error).lower()
        if "cannot reshape tensor" in error_msg or "tensor" in error_msg:
            self.logger.error(f"Error de tensor en Whisper (archivo posiblemente corrupto): {whisper_error}")
        elif "nan" in error_msg:
            self.logger.error(f"Error NaN en Whisper (datos de audio inválidos): {whisper_error}")
        else:
            # Re-lanzar otros errores
            raise RuntimeError(f"Error inesperado en Whisper: {whisper_error}") from whisper_error
        return None

    def _generate_srt_from_transcription(self, result: dict, output_srt: Path) -> Optional[Path]:
        """
        Genera archivo SRT desde el resultado de la transcripción.

        Args:
            result: Resultado de Whisper.
            output_srt: Ruta de salida para el SRT.

        Returns:
            Optional[Path]: Ruta del archivo SRT generado o None.
        """
        self.logger.info(f"Generando archivo SRT con {len(result['segments'])} segmentos")

        with open(output_srt, "w", encoding="utf-8") as f:
            for i, segment in enumerate(result["segments"], start=1):
                start_time = self._format_timestamp(segment["start"])
                end_time = self._format_timestamp(segment["end"])
                text = segment["text"].strip()

                # Solo escribir si hay texto
                if text:
                    f.write(f"{i}\n")
                    f.write(f"{start_time} --> {end_time}\n")
                    f.write(f"{text}\n\n")

        # Verificar que el archivo SRT se creó correctamente
        if not output_srt.exists() or output_srt.stat().st_size == 0:
            self.logger.error("Archivo SRT no se creó o está vacío")
            return None

        detected_lang = result.get("language", "unknown")
        self.logger.info(
            f"{EmojiGenerator.check_mark()} Subtítulos extraídos del audio (idioma: {detected_lang}): {output_srt.name}"
        )

        # Aplicar mejora de calidad a los subtítulos generados por Whisper
        self._improve_extracted_subtitles_quality(output_srt, detected_lang)

        return output_srt

    def _cleanup_temp_files(self, *temp_files):
        """
        Limpia archivos temporales de forma segura.

        Args:
            *temp_files: Archivos temporales a eliminar.
        """
        for temp_file in temp_files:
            if temp_file and temp_file.exists():
                try:
                    temp_file.unlink()
                    self.logger.debug(f"Archivo temporal eliminado: {temp_file.name}")
                except Exception as e:
                    self.logger.warning(f"No se pudo eliminar archivo temporal {temp_file}: {e}")

    def _validate_audio_file(self, audio_file: Path) -> bool:
        """
        Valida que el archivo de audio sea válido antes de procesarlo con Whisper.

        Args:
            audio_file: Ruta del archivo de audio.

        Returns:
            bool: True si el archivo es válido.
        """
        try:
            # Verificar tamaño mínimo (al menos header WAV + algo de audio)
            if audio_file.stat().st_size < 1024:  # Al menos 1KB
                self.logger.warning(f"Archivo de audio muy pequeño: {audio_file.stat().st_size} bytes")
                return False

            # Verificar que sea un archivo WAV válido leyendo el header
            with open(audio_file, "rb") as f:
                header = f.read(44)  # Leer header WAV (44 bytes)

                # Verificar firma RIFF
                if header[:4] != b"RIFF":
                    self.logger.error("Archivo de audio no tiene firma RIFF válida")
                    return False

                # Verificar firma WAVE
                if header[8:12] != b"WAVE":
                    self.logger.error("Archivo de audio no tiene firma WAVE válida")
                    return False

                # Buscar la sección de datos (puede estar en diferentes posiciones)
                data_found = False
                data_size = 0

                # Buscar "data" en el archivo
                f.seek(0)
                file_content = f.read()
                data_pos = file_content.find(b"data")
                if data_pos >= 0 and data_pos < 100:  # Encontrado en una posición razonable
                    data_size = int.from_bytes(file_content[data_pos + 4 : data_pos + 8], byteorder="little")
                    data_found = True

                if not data_found or data_size == 0:
                    self.logger.warning(f"No se encontró sección de datos válida en el WAV (data_size: {data_size})")
                    # En lugar de rechazar, ser más permisivo - verificar que haya contenido
                    if len(file_content) > 1024:  # Si el archivo es lo suficientemente grande
                        self.logger.warning(
                            "Archivo WAV tiene formato no estándar pero tamaño aceptable, continuando..."
                        )
                    else:
                        return False

                # Verificar formato de audio (si está disponible)
                try:
                    channels = int.from_bytes(header[22:24], byteorder="little")
                    sample_rate = int.from_bytes(header[24:28], byteorder="little")

                    # Ser más flexible con los formatos
                    if sample_rate not in [8000, 16000, 22050, 44100, 48000]:
                        self.logger.warning(f"Sample rate inusual: {sample_rate}Hz")
                    if channels not in [1, 2]:
                        self.logger.warning(f"Número de canales inusual: {channels}")
                except (ValueError, IndexError) as e:
                    self.logger.warning(f"No se pudo leer información de formato del WAV: {e}")

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
        """
        Convierte segundos a formato SRT (HH:MM:SS,mmm).

        Args:
            seconds: Tiempo en segundos.

        Returns:
            str: Timestamp formateado.
        """
        try:
            hours = int(seconds // 3600)
            minutes = int((seconds % 3600) // 60)
            secs = int(seconds % 60)
            millis = int((seconds % 1) * 1000)
            return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"
        except Exception as e:
            self.logger.error(f"Error formateando timestamp {seconds}: {e}")
            return "00:00:00,000"

