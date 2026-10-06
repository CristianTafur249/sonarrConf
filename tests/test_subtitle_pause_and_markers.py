import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import mediajelly_subtitle_translator as translator_module  # noqa: E402
from mediajelly_scanner import MediaScanner  # noqa: E402
from mediajelly_subtitle_translator import (  # noqa: E402
    SubtitleQualityImprover,
    SubtitleTranslator,
    TranslationStalledError,
    _signal_handler,
)
from mediajelly_utils import MediaJellyPaths  # noqa: E402
from repair_subtitle_accents import repair_text  # noqa: E402


class _Logger:
    def __getattr__(self, name):
        return lambda *args, **kwargs: None


@pytest.fixture
def translator(tmp_path):
    instance = SubtitleTranslator.__new__(SubtitleTranslator)
    instance.logger = _Logger()
    instance.tmp_dir = tmp_path
    instance.allow_retranslate = False
    instance._probe_cache = {}
    instance.current_stats = None
    return instance


# --- Aviso al pausar ---


def test_signal_sends_progress_before_exiting(translator, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        translator_module.subprocess,
        "run",
        lambda cmd, **kwargs: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0, "", ""),
    )
    lock = tmp_path / "subtitle_translator.lock"
    lock.write_text("1")
    translator.current_stats = {
        "total_files": 101,
        "processed": 35,
        "with_spanish_audio": 2,
        "with_spanish_subs": 20,
        "extracted": 9,
        "translated": 8,
        "errors": 1,
    }
    state = {"translator": translator, "env_config": {"scripts_dir": tmp_path}}

    with pytest.raises(SystemExit):
        _signal_handler(15, lock, state)

    assert len(calls) == 1
    assert calls[0][2:] == ["subtitle_paused", "101", "35", "9", "8", "2", "20", "1"]
    assert not lock.exists()


def test_signal_without_progress_just_cleans_up(translator, tmp_path, monkeypatch):
    monkeypatch.setattr(translator_module.subprocess, "run", lambda *a, **k: pytest.fail("no hay nada que avisar"))
    lock = tmp_path / "subtitle_translator.lock"
    lock.write_text("1")

    for state in (None, {"translator": None, "env_config": {}}, {"translator": translator, "env_config": {}}):
        lock.write_text("1")
        with pytest.raises(SystemExit):
            _signal_handler(15, lock, state)
        assert not lock.exists()


def test_notifier_builds_pause_message(monkeypatch):
    from mediajelly_notifier import TelegramNotifier

    notifier = TelegramNotifier.__new__(TelegramNotifier)
    sent = []
    notifier.send_long_message = lambda message: sent.append(message) or True

    assert notifier.notify_subtitle_paused(
        {
            "total_files": 101,
            "processed": 35,
            "with_spanish_audio": 0,
            "with_spanish_subs": 20,
            "extracted": 9,
            "translated": 8,
            "errors": 0,
        }
    )
    assert "pausada" in sent[0]
    assert "Procesados: 35 de 101" in sent[0]
    assert "Subtítulos traducidos: 8" in sent[0]
    assert "Se reanuda la próxima noche" in sent[0]


# --- Traducción sin bloqueos ---


class _HangingTranslator:
    def translate(self, text, lang):
        time.sleep(30)


class _FailingTranslator:
    def __init__(self):
        self.calls = 0

    def translate(self, text, lang):
        self.calls += 1
        raise ConnectionError("sin respuesta")


def test_hanging_translation_request_is_abandoned(translator, monkeypatch):
    monkeypatch.setattr(translator_module, "TRANSLATE_REQUEST_TIMEOUT", 0.3)
    started = time.monotonic()

    with pytest.raises(TimeoutError):
        translator._translate_with_timeout(_HangingTranslator(), "hello")

    assert time.monotonic() - started < 5


def test_file_is_aborted_after_consecutive_failures(translator, monkeypatch):
    monkeypatch.setattr(translator_module, "MAX_TRANSLATE_FAILURES", 3)
    failing = _FailingTranslator()
    # Bloques de texto separados por timestamps: cada uno es una petición
    lines = []
    for i in range(10):
        lines += [f"{i + 1}\n", "00:00:01,000 --> 00:00:02,000\n", f"line number {i}\n", "\n"]

    with pytest.raises(TranslationStalledError):
        translator._translate_lines_batch(lines, failing)

    assert failing.calls == 3


def test_failed_blocks_do_not_count_as_translated(translator):
    lines = ["1\n", "00:00:01,000 --> 00:00:02,000\n", "hello there\n", "\n"]

    translated, made = translator._translate_lines_batch(lines, _FailingTranslator())

    assert made == 0
    assert "hello there\n" in translated


# --- Tildes ---


def test_spanish_improver_no_longer_adds_accents():
    improver = SubtitleQualityImprover(logger=None)
    text = "Todo lo que haces es como un sueño cuando llegas donde vives"

    assert improver._improve_spanish_text(text) == text


def test_accent_repair_keeps_questions_and_exclamations():
    content = (
        "1\n00:00:01,000 --> 00:00:02,000\nTodo lo qué haces es cómo un sueño.\n\n"
        "2\n00:00:03,000 --> 00:00:04,000\n¿Qué haces? No sé dónde está.\n\n"
        "3\n00:00:05,000 --> 00:00:06,000\n¡Cómo te atreves!\n\n"
        "4\n00:00:07,000 --> 00:00:08,000\nQué puede ser cuándo llegue\n"
    )

    repaired, count = repair_text(content)

    assert count == 4
    assert "Todo lo que haces es como un sueño." in repaired
    assert "¿Qué haces? No sé dónde está." in repaired
    assert "¡Cómo te atreves!" in repaired
    assert "Que puede ser cuando llegue" in repaired


# --- Marca .nosubs ---


def _library(tmp_path):
    season = tmp_path / "media" / "anime" / "Show" / "Season 4"
    season.mkdir(parents=True)
    video = season / "Show S04E05.mp4"
    video.write_bytes(b"0" * (11 * 1024 * 1024))
    return season, video


def test_marker_applies_to_folder_and_subfolders(tmp_path):
    season, video = _library(tmp_path)
    assert not MediaJellyPaths.has_nosubs_marker(video)

    (season.parent / ".nosubs").touch()  # marca en la serie: cubre todas las temporadas
    assert MediaJellyPaths.has_nosubs_marker(video)


def test_marker_above_media_is_ignored(tmp_path):
    _, video = _library(tmp_path)
    (tmp_path / ".nosubs").touch()

    assert not MediaJellyPaths.has_nosubs_marker(video)


def test_translator_skips_marked_videos(translator, tmp_path, monkeypatch):
    season, video = _library(tmp_path)
    monkeypatch.setattr(translator, "check_existing_spanish_subtitles", lambda v: False)
    assert translator.quick_check_needs_processing(video)

    (season / ".nosubs").touch()
    assert not translator.quick_check_needs_processing(video)


def test_scanner_skips_marked_videos(tmp_path, monkeypatch):
    season, video = _library(tmp_path)
    other = season.parent / "Season 5"
    other.mkdir()
    other_video = other / "Show S05E01.mp4"
    other_video.touch()
    (season / ".nosubs").touch()

    scanner = MediaScanner.__new__(MediaScanner)
    scanner.logger = _Logger()
    monkeypatch.setattr(scanner, "_has_spanish_subtitle", lambda v: False)

    assert scanner._find_files_needing_subtitles_fresh([video, other_video]) == [str(other_video)]
