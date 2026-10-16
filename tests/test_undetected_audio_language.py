import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import mediajelly_language_detection as detection_module  # noqa: E402
import mediajelly_language_detector as detector_module  # noqa: E402
from mediajelly_language_detection import LanguageDetectionMixin  # noqa: E402


class _Logger:
    def __getattr__(self, name):
        return lambda *args, **kwargs: None


def _detector(monkeypatch, ffprobe_stdout):
    instance = LanguageDetectionMixin.__new__(LanguageDetectionMixin)
    instance.logger = _Logger()
    instance.detect_audio_language = lambda *a, **k: None
    monkeypatch.setattr(
        detection_module.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=ffprobe_stdout, stderr=""),
    )
    return instance


def test_undetected_audio_is_not_assumed_spanish(monkeypatch, tmp_path):
    """Audio 'und' sin detección no debe etiquetarse como español."""
    instance = _detector(monkeypatch, "0,video,und\n1,audio,und\n2,subtitle,ita\n")

    has_spanish, audio_indices, _, audio_languages = instance.detect_language_streams(tmp_path / "x.mp4")

    assert audio_languages == {0: "und"}
    assert audio_indices == []
    assert has_spanish is False


def test_audio_without_tag_stays_unknown(monkeypatch, tmp_path):
    instance = _detector(monkeypatch, "0,video,\n1,audio,\n")

    _, _, _, audio_languages = instance.detect_language_streams(tmp_path / "x.mp4")

    assert audio_languages == {0: "und"}


def test_detector_returns_none_when_no_sample_can_be_analyzed(monkeypatch, tmp_path):
    monkeypatch.setattr(detector_module, "extract_audio_sample", lambda *a, **k: None)

    assert detector_module.detect_language_with_multiple_samples(None, tmp_path / "x.mp4", 1, 1200) is None
    assert detector_module.detect_single_sample(None, tmp_path / "x.mp4", 1, 30) is None
