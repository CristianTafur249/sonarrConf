import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import mediajelly_processor as processor_module  # noqa: E402
from mediajelly_processor import MediaJellyProcessor  # noqa: E402


class _Logger:
    def __getattr__(self, name):
        return lambda *args, **kwargs: None


def test_small_mp4_skip_is_marked_completed(tmp_path, monkeypatch):
    """Un .mp4 < 50MB se omite, pero debe quedar en completados para salir de pendientes."""
    video = tmp_path / "Show S01E01 HDTV-1080p.mp4"
    video.write_bytes(b"\0" * 1024)

    marked = []
    monkeypatch.setattr(processor_module, "mark_completed", lambda p, *a, **k: marked.append(p))

    proc = MediaJellyProcessor.__new__(MediaJellyProcessor)
    proc.logger = _Logger()
    proc.completed_file = tmp_path / "completed.txt"
    proc.failed_file = tmp_path / "failed-compression.txt"
    proc._get_video_resolution = lambda p: None
    proc._is_uhd_resolution = lambda r: False
    proc.set_resource_limits = lambda **k: None

    result = proc.compress_single_file(str(video))

    assert result["success"] is True
    assert result["skipped"] is True
    assert marked == [str(video)]
    assert proc.completed_file.read_text().strip() == str(video)
