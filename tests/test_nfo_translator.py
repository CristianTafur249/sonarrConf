import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import mediajelly_nfo_translator as nfo_module  # noqa: E402
from mediajelly_nfo_translator import NFOTranslator  # noqa: E402
from repair_nfo_fields import repair_text  # noqa: E402

NFO = """<?xml version="1.0" encoding="utf-8"?>
<episodedetails>
  <plot>The scientist rebuilds civilization from nothing after everyone turned to stone.</plot>
  <outline>Ya está en español y no debe tocarse por ningún motivo.</outline>
  <title>Treasure Box</title>
  <watched>false</watched>
  <lockdata>false</lockdata>
  <director>Christopher Nolan</director>
  <thumb>https://example.org/poster/the-image.jpg</thumb>
  <fileinfo><streamdetails>
    <video><codec>h264</codec><scantype>progressive</scantype></video>
    <audio><codec>aac</codec><language>jpn</language></audio>
    <subtitle><language>eng</language><default>true</default><forced>false</forced></subtitle>
  </streamdetails></fileinfo>
</episodedetails>
"""


class _FakeTranslator:
    def __init__(self):
        self.calls = []

    def translate(self, text, target_lang):
        self.calls.append(text)
        return type("Result", (), {"result": f"[es] {text}"})()


@pytest.fixture
def translator(tmp_path, monkeypatch):
    monkeypatch.setattr(nfo_module, "Translator", _FakeTranslator)
    monkeypatch.setattr(nfo_module, "TRANSLATE_AVAILABLE", True)
    return NFOTranslator(tmp_dir=tmp_path / "state")


def _write_nfo(tmp_path, name="episode.nfo"):
    library = tmp_path / "library"
    library.mkdir(exist_ok=True)
    nfo = library / name
    nfo.write_text(NFO, encoding="utf-8")
    return nfo


def test_only_prose_tags_are_translated(translator, tmp_path):
    nfo = _write_nfo(tmp_path)

    assert translator.process_nfo_file(nfo) is True

    content = nfo.read_text(encoding="utf-8")
    assert "<plot>[es] The scientist rebuilds" in content
    # Solo se envió a traducir la sinopsis en inglés
    assert len(translator.translator.calls) == 1
    for untouched in (
        "<outline>Ya está en español y no debe tocarse por ningún motivo.</outline>",
        "<title>Treasure Box</title>",
        "<watched>false</watched>",
        "<lockdata>false</lockdata>",
        "<director>Christopher Nolan</director>",
        "<thumb>https://example.org/poster/the-image.jpg</thumb>",
        "<codec>h264</codec>",
        "<scantype>progressive</scantype>",
        "<codec>aac</codec>",
        "<language>jpn</language>",
        "<default>true</default>",
        "<forced>false</forced>",
    ):
        assert untouched in content


def test_unchanged_files_are_not_parsed_again(translator, tmp_path, monkeypatch):
    nfo = _write_nfo(tmp_path)
    library = nfo.parent

    translator.process_directory(str(library))
    assert translator.find_nfo_files(library) == []

    # Una segunda ejecución (estado recargado de disco) no vuelve a abrir el archivo
    second = NFOTranslator(tmp_dir=tmp_path / "state")
    monkeypatch.setattr(second, "process_nfo_file", lambda path: pytest.fail("no debe reprocesarse"))
    second.process_directory(str(library))


def test_modified_file_is_checked_again(translator, tmp_path):
    nfo = _write_nfo(tmp_path)
    translator.process_directory(str(nfo.parent))

    nfo.write_text(NFO.replace("Treasure Box", "Another Title, Longer"), encoding="utf-8")

    assert translator.find_nfo_files(nfo.parent) == [nfo]


def test_failed_translation_is_retried_next_run(translator, tmp_path, monkeypatch):
    nfo = _write_nfo(tmp_path)

    def fail(text, target_lang):
        raise ConnectionError("sin red")

    monkeypatch.setattr(translator.translator, "translate", fail)

    assert translator.process_nfo_file(nfo) is False
    assert nfo.read_text(encoding="utf-8") == NFO
    assert not translator.is_unchanged(nfo)


def test_invalid_xml_is_skipped_until_it_changes(translator, tmp_path):
    nfo = _write_nfo(tmp_path)
    nfo.write_text("<episodedetails><plot>Broken", encoding="utf-8")

    assert translator.process_nfo_file(nfo) is False
    assert translator.is_unchanged(nfo)


def test_repair_restores_translated_booleans():
    damaged = (
        "<watched>FALSO</watched><default>Verdadero</default><forced>falso</forced>"
        "<lockdata>false</lockdata><plot>Un caso FALSO y verdadero.</plot><codec>CAA</codec>"
    )

    repaired, count = repair_text(damaged)

    assert count == 3
    assert repaired == (
        "<watched>false</watched><default>true</default><forced>false</forced>"
        "<lockdata>false</lockdata><plot>Un caso FALSO y verdadero.</plot><codec>CAA</codec>"
    )
