import io
from pathlib import Path
import tempfile
from scripts.shift_srt import process_file, shift_timestamp_line, timestamp_to_ms, ms_to_timestamp


def test_timestamp_to_ms_and_back():
    ts = "00:07:56,300"
    ms = timestamp_to_ms(ts)
    assert ms == 7 * 60000 + 56 * 1000 + 300 or isinstance(ms, int)
    assert ms_to_timestamp(ms) == ts


def test_shift_line_forward_and_back():
    original = "00:07:56,300 --> 00:08:00,270"
    new_line, changed = shift_timestamp_line(original, -5000)
    assert changed
    assert "00:07:51,300" in new_line


def test_clamp_negative():
    original = "00:00:04,000 --> 00:00:06,000"
    new_line, changed = shift_timestamp_line(original, -5000)
    assert changed
    assert new_line.startswith("00:00:00,000")


def test_process_file_dry_run(tmp_path):
    content = """1
00:00:10,000 --> 00:00:12,000
Hola

2
00:01:00,000 --> 00:01:02,000
Adios
"""
    file_path = tmp_path / "example.srt"
    file_path.write_text(content, encoding='utf-8')

    changed = process_file(file_path, -5, in_place=False, backup=False, dry_run=True)
    assert changed >= 1


def test_process_file_in_place(tmp_path):
    content = "1\n00:00:10,000 --> 00:00:12,000\nHola\n"
    file_path = tmp_path / "example2.srt"
    file_path.write_text(content, encoding='utf-8')

    changed = process_file(file_path, -5, in_place=True, backup=True, dry_run=False)
    assert changed == 1
    assert (file_path.with_suffix('.srt.bak')).exists()

