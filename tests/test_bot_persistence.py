import json
import time
from pathlib import Path

from scripts.mediajelly_telegram_bot import MediaJellyTelegramBot
from scripts.mediajelly_db import (
    cleanup_stale_sessions,
    delete_download,
    delete_session,
    get_pending_downloads,
    load_sessions,
    save_download,
    save_session,
    update_download_status,
)


def test_save_and_load_session_round_trip():
    chat_id = "chat-123"
    session = {
        "file_path": "/tmp/example.mkv",
        "media_type": "series",
        "raw_title": "The Expanse",
        "season": 1,
        "episode": 2,
        "step": "waiting_season_episode",
        "selected_folder": "The Expanse",
    }

    save_session(chat_id, session)
    loaded = load_sessions()

    assert loaded[chat_id]["raw_title"] == "The Expanse"
    assert loaded[chat_id]["season"] == 1
    assert loaded[chat_id]["episode"] == 2
    assert loaded[chat_id]["step"] == "waiting_season_episode"

    delete_session(chat_id)
    assert chat_id not in load_sessions()


def test_save_download_and_restore_pending_status():
    file_id = "telegram-file-42"
    chat_id = "chat-456"
    save_download(chat_id, file_id, "/tmp/downloaded.mkv", "downloaded.mkv", status="downloading")

    pending = get_pending_downloads(chat_id)
    assert any(item["file_id"] == file_id for item in pending)

    update_download_status(file_id, "downloaded")
    pending_after = get_pending_downloads(chat_id)
    assert any(item["file_id"] == file_id and item["status"] == "downloaded" for item in pending_after)

    delete_download(file_id)
    assert not any(item["file_id"] == file_id for item in get_pending_downloads(chat_id))


def test_cleanup_stale_sessions_removes_old_entries():
    old_chat = "old-chat"
    save_session(old_chat, {"step": "waiting_title", "updated_at": time.time() - 3 * 86400})
    fresh_chat = "fresh-chat"
    save_session(fresh_chat, {"step": "waiting_type", "updated_at": time.time()})

    cleanup_stale_sessions(hours=24)

    loaded = load_sessions()
    assert old_chat not in loaded
    assert fresh_chat in loaded


def test_auto_series_match_unique_folder(tmp_path):
    bot = object.__new__(MediaJellyTelegramBot)
    series_root = tmp_path / "series"
    (series_root / "The Expanse").mkdir(parents=True)
    (series_root / "The Expanse UK").mkdir(parents=True)
    (series_root / "Other Show").mkdir(parents=True)

    result = bot._get_series_auto_match("The Expanse 1x02", series_root)

    assert result == "The Expanse"


def test_auto_series_match_ambiguous_folder(tmp_path):
    bot = object.__new__(MediaJellyTelegramBot)
    series_root = tmp_path / "series"
    (series_root / "Vikingos").mkdir(parents=True)
    (series_root / "Vikings").mkdir(parents=True)

    result = bot._get_series_auto_match("Vikingos 6x10", series_root)

    assert result is None
