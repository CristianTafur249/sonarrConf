import signal
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import mediajelly_cron_runner as runner_module  # noqa: E402
from mediajelly_cron_runner import MediaJellyCron  # noqa: E402


class _Logger:
    def __getattr__(self, name):
        return lambda *args, **kwargs: None


@pytest.fixture
def runner(tmp_path):
    # Sin __init__: evita tomar el lock real y configurar logging en disco
    cron = MediaJellyCron.__new__(MediaJellyCron)
    cron.logger = _Logger()
    cron.subtitle_script = tmp_path / "fake_translator.py"
    return cron


def _freeze_time(monkeypatch, hour, minute=0):
    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 10, 5, hour, minute, 0)

    monkeypatch.setattr(runner_module, "datetime", _FrozenDatetime)


def test_night_window_ends_at_six(runner, monkeypatch):
    _freeze_time(monkeypatch, 5, 59)
    assert runner._is_night_time()
    assert runner._seconds_until_night_end() == 60

    _freeze_time(monkeypatch, 6, 0)
    assert not runner._is_night_time()
    assert runner._seconds_until_night_end() == 0


def test_daytime_is_not_night(runner, monkeypatch):
    _freeze_time(monkeypatch, 14, 56)
    assert not runner._is_night_time()


def test_translator_not_started_after_window(runner, monkeypatch):
    _freeze_time(monkeypatch, 9, 0)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: pytest.fail("no debe lanzarse fuera de la ventana"))
    assert runner.run_subtitle_translator() is True


def test_translator_group_is_killed_at_deadline(runner, monkeypatch, tmp_path):
    child_pid_file = tmp_path / "child.pid"
    # El "traductor" ignora SIGTERM y lanza un hijo (como ffmpeg): ambos deben morir
    runner.subtitle_script.write_text(
        "import signal, subprocess, sys, time\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"open({str(child_pid_file)!r}, 'w').write(str(child.pid))\n"
        "time.sleep(60)\n"
    )
    monkeypatch.setattr(runner, "_seconds_until_night_end", lambda: 1.0)
    monkeypatch.setattr(runner_module, "SUBTITLE_KILL_GRACE_SECONDS", 1)

    job = runner._start_subtitle_translator()
    # Con la máquina cargada el arranque de Python puede tardar más que la hora límite:
    # esperar a que el "traductor" haya lanzado a su hijo antes de comprobar el corte
    import time

    deadline = time.monotonic() + 20
    while not (child_pid_file.exists() and child_pid_file.read_text()) and time.monotonic() < deadline:
        time.sleep(0.05)
    child_started = child_pid_file.exists() and child_pid_file.read_text()

    assert runner._finish_subtitle_translator(job) is True
    assert job.timed_out.is_set()
    if not child_started:
        pytest.skip("el corte llegó antes de que el proceso de prueba lanzara a su hijo")

    child_pid = int(child_pid_file.read_text())
    stat = Path(f"/proc/{child_pid}/stat")
    # Muerto (o zombie a la espera de ser recogido por init)
    assert not stat.exists() or stat.read_text().split(") ")[1][0] == "Z"


def test_translator_failure_is_reported(runner, monkeypatch):
    runner.subtitle_script.write_text("import sys; sys.exit(3)\n")
    monkeypatch.setattr(runner, "_seconds_until_night_end", lambda: 30.0)
    assert runner.run_subtitle_translator() is False


def test_extra_args_are_forwarded(runner, monkeypatch, tmp_path):
    out = tmp_path / "args.txt"
    runner.subtitle_script.write_text(f"import sys; open({str(out)!r}, 'w').write(' '.join(sys.argv[1:]))\n")
    monkeypatch.setattr(runner, "_seconds_until_night_end", lambda: 30.0)
    assert runner.run_subtitle_translator(["--redo-queue"]) is True
    assert out.read_text() == "--redo-queue"


def test_background_job_does_not_block_and_reports_exit_code(runner, tmp_path):
    import time

    script = tmp_path / "slow.py"
    script.write_text("import time; time.sleep(1.5)\n")

    started = time.monotonic()
    job = runner._start_background("lento", [sys.executable, str(script)], timeout=30)
    assert time.monotonic() - started < 1.0  # no espera al subproceso

    assert runner._finish_background(job) == 0
    assert not job.timed_out.is_set()


def test_subtitles_are_cut_at_deadline_while_processor_keeps_running(runner, monkeypatch):
    import time

    runner.subtitle_script.write_text("import time; time.sleep(60)\n")
    monkeypatch.setattr(runner, "_seconds_until_night_end", lambda: 1.0)
    monkeypatch.setattr(runner_module, "SUBTITLE_KILL_GRACE_SECONDS", 1)

    job = runner._start_subtitle_translator()
    # El ciclo sigue ocupado "comprimiendo" más allá de la hora límite
    time.sleep(2.5)

    assert job.proc.poll() is not None  # el temporizador ya lo cortó sin esperar al ciclo
    assert runner._finish_subtitle_translator(job) is True
    assert job.timed_out.is_set()


def test_night_cycle_runs_processor_while_subtitles_are_running(runner, monkeypatch, tmp_path):
    events = []
    _freeze_time(monkeypatch, 1, 0)
    runner.tmp_path = tmp_path
    runner.handlers = []
    runner.logger.handlers = []

    class _Job:
        timed_out = type("E", (), {"is_set": staticmethod(lambda: False)})()

    monkeypatch.setattr(runner, "run_scanner", lambda: True)
    monkeypatch.setattr(runner, "run_language_detector", lambda: True)
    monkeypatch.setattr(runner, "_start_nfo_translator", lambda: events.append("nfo:start"))
    monkeypatch.setattr(runner, "_finish_nfo_translator", lambda job: events.append("nfo:finish"))
    monkeypatch.setattr(runner, "_start_subtitle_translator", lambda extra_args=None: events.append("subs:start") or _Job())
    monkeypatch.setattr(
        runner, "_finish_subtitle_translator", lambda job: (events.append("subs:finish") if job else None) or True
    )
    monkeypatch.setattr(runner, "run_processor", lambda: events.append("processor") or True)
    monkeypatch.setattr(runner, "_release_lock", lambda: events.append("lock:release"))
    runner.lock_file = tmp_path / "cron_runner.lock"

    assert runner.run_cycle() is True
    assert events == ["nfo:start", "subs:start", "processor", "subs:finish", "nfo:finish", "lock:release"]


def test_day_cycle_does_not_start_subtitles(runner, monkeypatch, tmp_path):
    events = []
    _freeze_time(monkeypatch, 14, 0)
    runner.tmp_path = tmp_path
    runner.logger.handlers = []
    runner.lock_file = tmp_path / "cron_runner.lock"

    monkeypatch.setattr(runner, "run_scanner", lambda: True)
    monkeypatch.setattr(runner, "run_language_detector", lambda: True)
    monkeypatch.setattr(runner, "_start_nfo_translator", lambda: None)
    monkeypatch.setattr(runner, "_start_subtitle_translator", lambda extra_args=None: pytest.fail("de día no"))
    monkeypatch.setattr(runner, "run_processor", lambda: events.append("processor") or True)
    monkeypatch.setattr(runner, "_release_lock", lambda: events.append("lock:release"))

    assert runner.run_cycle() is True
    assert events == ["processor", "lock:release"]


def test_job_that_finished_early_is_not_reported_as_cut(runner, tmp_path):
    import time

    script = tmp_path / "fast.py"
    script.write_text("pass\n")

    job = runner._start_background("rápido", [sys.executable, str(script)], timeout=1.0)
    # El ciclo sigue ocupado en otra etapa hasta después de la hora límite del trabajo
    time.sleep(2.0)

    assert runner._finish_background(job) == 0
    assert not job.timed_out.is_set()
