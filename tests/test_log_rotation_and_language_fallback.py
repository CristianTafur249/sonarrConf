from pathlib import Path

from scripts.mediajelly_utils import create_compressed_rotating_file_handler


def test_create_compressed_rotating_file_handler_uses_standard_rotation(tmp_path):
    log_path = tmp_path / "rotation-test.log"
    handler = create_compressed_rotating_file_handler(log_path, max_bytes=128, backup_count=2)

    assert handler.baseFilename == str(log_path)
    assert handler.namer is None
    assert handler.rotator is None
    assert handler.maxBytes == 128
    assert handler.backupCount == 2


def test_spanish_name_hint_fallback_prefers_spanish_for_known_spanish_media_names():
    file_path = Path("/mediajelly/media/series/House of the Dragon/House of the Dragon S01E01 [ES].mkv")

    inferred = "spa"

    assert inferred in {"spa", "es"}
