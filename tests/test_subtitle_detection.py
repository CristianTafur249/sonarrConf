import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from mediajelly_scanner import MediaScanner  # noqa: E402
from mediajelly_subtitle_translator import SubtitleTranslator  # noqa: E402
from mediajelly_whisper_extractor import (  # noqa: E402
    MAX_CUE_SECONDS,
    MIN_CUE_SECONDS,
    WhisperAudioExtractorMixin,
)


class _Logger:
    def __getattr__(self, name):
        return lambda *args, **kwargs: None


@pytest.fixture
def translator(tmp_path):
    # Sin __init__: evita cargar configuración, logging y traductores externos
    instance = SubtitleTranslator.__new__(SubtitleTranslator)
    instance.logger = _Logger()
    instance.tmp_dir = tmp_path
    instance.allow_retranslate = False
    instance._probe_cache = {}
    return instance


def _stream(codec="subrip", lang=None, title=None, forced=0):
    tags = {}
    if lang:
        tags["language"] = lang
    if title:
        tags["title"] = title
    return {"codec_type": "subtitle", "codec_name": codec, "tags": tags, "disposition": {"forced": forced}}


def _write_srt(path, cues, spacing=4.0, text="Hello there"):
    def ts(seconds):
        return f"{int(seconds // 3600):02d}:{int(seconds % 3600 // 60):02d}:{int(seconds % 60):02d},000"

    blocks = [f"{i + 1}\n{ts(i * spacing)} --> {ts(i * spacing + 2)}\n{text}\n" for i in range(cues)]
    path.write_text("\n".join(blocks), encoding="utf-8")
    return path


def test_rank_prefers_full_english_and_drops_partial_tracks(translator):
    streams = [
        _stream("ass", "eng", "Signs/Songs [sam]", forced=1),
        _stream("ass", "enm", "Honorifics [sam]"),
        _stream("hdmv_pgs_subtitle", "eng", "Full PGS"),
        _stream("ass", "eng", "English SDH"),
        _stream("ass", "eng", "Full Subtitles [sam]"),
        _stream("subrip", "spa", "Español"),
        _stream("subrip"),
    ]

    ranked = translator._rank_embedded_subtitle_streams(streams)

    # Inglés completo, inglés SDH, otro idioma, sin etiqueta. Fuera: forzada, bitmap y español
    assert [index for index, _ in ranked] == [4, 3, 1, 6]


def test_signs_track_is_partial_even_without_forced_flag(translator):
    assert translator._is_partial_subtitle_stream(_stream("ass", "eng", "Signs & Songs"))
    assert translator._is_partial_subtitle_stream(_stream("ass", "eng", None, forced=1))
    assert not translator._is_partial_subtitle_stream(_stream("ass", "eng", "Full Subtitles"))


def test_embedded_spanish_ignores_forced_tracks(translator, monkeypatch):
    video = Path("/media/video.mkv")
    monkeypatch.setattr(translator, "_detect_embedded_subtitle_streams", lambda v: [_stream("subrip", "spa", forced=1)])
    assert not translator._has_embedded_spanish_subtitles(video)

    monkeypatch.setattr(translator, "_detect_embedded_subtitle_streams", lambda v: [_stream("hdmv_pgs_subtitle", "spa")])
    assert translator._has_embedded_spanish_subtitles(video)


def test_subtitle_covers_video(translator, tmp_path):
    duration = 1440.0  # 24 minutos

    full = _write_srt(tmp_path / "full.srt", cues=300)  # llega a ~20 min
    assert translator._subtitle_covers_video(full, duration)

    # Pocas líneas repartidas (carteles): cubre el tiempo pero no la densidad
    sparse = _write_srt(tmp_path / "sparse.srt", cues=20, spacing=70.0)
    assert not translator._subtitle_covers_video(sparse, duration)

    # Denso pero solo los primeros minutos (extracción truncada)
    truncated = _write_srt(tmp_path / "truncated.srt", cues=100)
    assert not translator._subtitle_covers_video(truncated, duration)

    # Sin duración conocida basta con el mínimo de cues
    assert translator._subtitle_covers_video(truncated, None)
    assert not translator._subtitle_covers_video(_write_srt(tmp_path / "tiny.srt", cues=2), None)


def test_insufficient_sidecar_is_not_used_and_is_backed_up(translator, tmp_path, monkeypatch):
    video = tmp_path / "Movie.mkv"
    video.touch()
    sidecar = _write_srt(tmp_path / "Movie.srt", cues=20, spacing=70.0)
    monkeypatch.setattr(translator, "_media_duration", lambda v: 1440.0)

    assert translator.find_existing_srt_file(video) is None
    assert not sidecar.exists()
    assert (tmp_path / "Movie.srt.bak").exists()


def test_sufficient_sidecar_is_used(translator, tmp_path, monkeypatch):
    video = tmp_path / "Movie.mkv"
    video.touch()
    sidecar = _write_srt(tmp_path / "Movie.en.srt", cues=300)
    monkeypatch.setattr(translator, "_media_duration", lambda v: 1440.0)

    assert translator.find_existing_srt_file(video) == sidecar


def test_embedded_extraction_takes_first_track_that_covers_video(translator, tmp_path, monkeypatch):
    video = tmp_path / "Show.mkv"
    video.touch()
    streams = [_stream("ass", "eng", "Dialogue (typeset only)"), _stream("ass", "eng", "Full")]
    extracted = []

    def fake_extract(video_file, relative_index, lang):
        extracted.append(relative_index)
        out = tmp_path / f"cand{relative_index}.srt"
        # La primera pista resulta ser parcial aunque su título no lo diga
        return _write_srt(out, cues=10, spacing=100.0) if relative_index == 0 else _write_srt(out, cues=300)

    monkeypatch.setattr(translator, "_detect_embedded_subtitle_streams", lambda v: streams)
    monkeypatch.setattr(translator, "_media_duration", lambda v: 1440.0)
    monkeypatch.setattr(translator, "_extract_embedded_subtitles", fake_extract)

    result = translator._try_extract_embedded_subtitles(video)

    assert extracted == [0, 1]
    assert result == tmp_path / "Show.srt"
    assert not (tmp_path / "cand0.srt").exists()


def test_videos_pending_compression_are_left_for_later(translator, tmp_path, monkeypatch):
    pending = tmp_path / "Pending.mkv"
    ready = tmp_path / "Ready.mkv"
    pending.touch()
    ready.touch()
    monkeypatch.setattr("mediajelly_subtitle_translator.get_pending_files", lambda: [pending])
    monkeypatch.setattr(translator, "_get_processed_files_info", lambda: {})
    stats = {"errors": 0}

    assert translator._filter_valid_files([pending, ready], stats) == [ready]
    assert stats["errors"] == 0  # no es un error: sigue en la cola de subtítulos


def test_probe_media_runs_ffprobe_once_per_file(translator, monkeypatch):
    calls = []

    class _Result:
        returncode = 0
        stderr = ""
        stdout = '{"format": {"duration": "60.0"}, "streams": [{"codec_type": "audio", "tags": {"language": "SPA"}}]}'

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return _Result()

    monkeypatch.setattr("mediajelly_subtitle_translator.subprocess.run", fake_run)
    video = Path("/media/video.mkv")

    assert translator._media_duration(video) == 60.0
    assert translator._detect_embedded_subtitle_streams(video) == []
    assert not translator._is_video_corrupted(video)
    assert len(calls) == 1


def test_split_words_into_cues_respects_limits():
    # 40 palabras de 0.5 s seguidas: 20 s de habla continua
    words = [(i * 0.5, i * 0.5 + 0.45, f" palabra{i}") for i in range(40)]
    # Pausa larga y una frase más
    words += [(30.0, 30.4, " Hola"), (30.4, 30.9, " mundo")]

    cues = WhisperAudioExtractorMixin._split_words_into_cues(words)

    assert all(cue["end"] - cue["start"] <= MAX_CUE_SECONDS for cue in cues)
    assert cues[-1] == {"start": 30.0, "end": 30.9, "text": "Hola mundo"}
    assert " ".join(cue["text"] for cue in cues[:-1]) == " ".join(f"palabra{i}" for i in range(40))


def test_split_words_joins_orphan_word_before_a_pause():
    # "聞" queda marcada antes del silencio y el resto de la frase llega 5 s después
    words = [(158.9, 159.1, "聞"), (164.2, 164.8, "かせろ"), (164.8, 165.5, "ルリー")]

    cues = WhisperAudioExtractorMixin._split_words_into_cues(words)

    assert cues == [{"start": 164.2, "end": 165.5, "text": "聞かせろルリー"}]


def test_normalize_cues_removes_overlaps_and_long_cues():
    segments = [
        {"start": 10.0, "end": 40.0, "text": " estirado sobre un silencio "},
        {"start": 0.0, "end": 3.0, "text": "primero"},
        {"start": 2.0, "end": 2.1, "text": "solapado"},
        {"start": 5.0, "end": 6.0, "text": "   "},
    ]

    cues = WhisperAudioExtractorMixin._normalize_cues(segments)

    assert [cue["text"] for cue in cues] == ["primero", "solapado", "estirado sobre un silencio"]
    assert cues[0]["end"] == 2.0  # recortado al inicio del siguiente
    assert cues[1]["end"] == pytest.approx(2.0 + MIN_CUE_SECONDS)
    assert cues[2]["end"] == 10.0 + MAX_CUE_SECONDS


def test_scanner_keeps_non_spanish_generic_srt_pending(tmp_path, monkeypatch):
    scanner = MediaScanner.__new__(MediaScanner)
    scanner.logger = _Logger()

    class _NoEmbedded:
        returncode = 0
        stdout = '{"streams": []}'

    monkeypatch.setattr("mediajelly_scanner.subprocess.run", lambda *a, **k: _NoEmbedded())

    english = tmp_path / "English.mkv"
    english.touch()
    _write_srt(tmp_path / "English.srt", cues=30, text="I think we should go there with the others")
    assert not scanner._has_spanish_subtitle(english)

    spanish = tmp_path / "Spanish.mkv"
    spanish.touch()
    _write_srt(tmp_path / "Spanish.srt", cues=30, text="Creo que el plan de la casa es para los que van con un amigo")
    assert scanner._has_spanish_subtitle(spanish)


def test_scanner_ignores_forced_embedded_spanish(tmp_path, monkeypatch):
    scanner = MediaScanner.__new__(MediaScanner)
    scanner.logger = _Logger()
    video = tmp_path / "Video.mkv"
    video.touch()

    class _Probe:
        returncode = 0
        stdout = '{"streams": [{"tags": {"language": "spa"}, "disposition": {"forced": 1}}]}'

    monkeypatch.setattr("mediajelly_scanner.subprocess.run", lambda *a, **k: _Probe())
    assert not scanner._has_spanish_subtitle(video)

    _Probe.stdout = '{"streams": [{"tags": {"language": "spa"}, "disposition": {"forced": 0}}]}'
    assert scanner._has_spanish_subtitle(video)


def test_cues_stay_on_screen_long_enough_to_read():
    text = "Aunque dijiste que Kattegat se dedicaría al comercio"  # 52 caracteres, ~3 s de lectura
    segments = [
        {"start": 10.0, "end": 11.2, "text": text},
        {"start": 30.0, "end": 30.4, "text": "Sí."},
        {"start": 30.9, "end": 31.5, "text": "Vamos."},
    ]

    cues = WhisperAudioExtractorMixin._normalize_cues(segments)

    assert cues[0]["end"] == pytest.approx(10.0 + len(text) / 17.0)
    assert cues[1]["end"] == pytest.approx(30.9)  # querría 1 s, pero no pisa al siguiente
    assert cues[2]["end"] == pytest.approx(30.9 + MIN_CUE_SECONDS)


def test_spanish_source_is_saved_without_translating(translator, tmp_path, monkeypatch):
    srt = _write_srt(tmp_path / "Show S01E01.srt", cues=40, text="Creo que el plan de la casa es para los que van con un amigo")
    monkeypatch.setattr(translator, "_translation_dependencies_available", lambda: pytest.fail("no debe traducir"))
    monkeypatch.setattr(translator, "_improve_translated_subtitles_quality", lambda path: None)

    result = translator.translate_srt(srt)

    assert result == tmp_path / "Show S01E01.es.srt"
    assert result.read_text(encoding="utf-8") == srt.read_text(encoding="utf-8")


def test_non_spanish_source_still_goes_to_translation(translator, tmp_path, monkeypatch):
    srt = _write_srt(tmp_path / "Show S01E02.srt", cues=40, text="I think we should go there with the others tonight")
    called = []
    monkeypatch.setattr(translator, "_translation_dependencies_available", lambda: called.append(1) or False)

    assert translator.translate_srt(srt) is None
    assert called == [1]
