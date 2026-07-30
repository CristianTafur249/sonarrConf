#!/usr/bin/env python3
"""
Persistencia ligera para MediaJelly usando SQLite (WAL mode).

Provee funciones de conveniencia para reemplazar archivos de estado:
- mark_completed(path, label)
- is_completed(path) -> bool
- add_pending(path)
- remove_pending(path)
- get_pending_files() -> List[Path]
- save_progress(data: dict)
- load_progress() -> dict

Todas las operaciones usan transacciones y activan WAL para concurrencia.
Incluye reintentos automáticos ante bloqueos de base de datos.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import List, Dict, Optional
from functools import wraps

DB_LOCK = threading.Lock()


def retry_on_locked(max_retries=5, delay=1, backoff=2):
    """
    Decorador para reintentar operaciones SQLite cuando la base de datos está bloqueada.
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            retries = 0
            while retries < max_retries:
                try:
                    return func(*args, **kwargs)
                except sqlite3.OperationalError as e:
                    if "database is locked" in str(e):
                        retries += 1
                        wait = delay * (backoff ** (retries - 1))
                        time.sleep(wait)
                    else:
                        raise
            raise Exception(f"Max retries exceeded for {func.__name__}")
        return wrapper
    return decorator


def _get_db_path() -> Path:
    base = Path(__file__).parent / "tmp"
    base.mkdir(parents=True, exist_ok=True)
    return base / "mediajelly.db"


def _get_conn() -> sqlite3.Connection:
    db_path = _get_db_path()
    conn = sqlite3.connect(str(db_path), timeout=30, check_same_thread=False)
    # Enable WAL and ensure foreign keys
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Inicializa el esquema si no existe."""
    with DB_LOCK:
        conn = _get_conn()
        with conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS completed_files (
                    path TEXT PRIMARY KEY,
                    label TEXT,
                    completed_at TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS pending_files (
                    path TEXT PRIMARY KEY,
                    label TEXT,
                    added_at TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS progress (
                    key TEXT PRIMARY KEY,
                    data TEXT,
                    updated_at TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS bot_sessions (
                    chat_id TEXT PRIMARY KEY,
                    data TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS bot_downloads (
                    file_id TEXT PRIMARY KEY,
                    chat_id TEXT NOT NULL,
                    local_path TEXT NOT NULL,
                    file_name TEXT,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
        _bootstrap_from_legacy_files(conn)
        conn.close()


@retry_on_locked()
def save_session(chat_id: str, session_data: dict) -> None:
    """Guarda o actualiza una sesión interactiva activa del bot."""
    init_db()
    payload = dict(session_data or {})
    payload["updated_at"] = time.time()
    serialized = json.dumps(payload, ensure_ascii=False)
    with DB_LOCK:
        conn = _get_conn()
        with conn:
            conn.execute(
                "REPLACE INTO bot_sessions(chat_id, data, updated_at) VALUES (?, ?, datetime('now'))",
                (str(chat_id), serialized),
            )
        conn.close()


@retry_on_locked()
def load_sessions() -> Dict[str, dict]:
    """Carga todas las sesiones activas y devuelve un dict {chat_id: data}."""
    init_db()
    with DB_LOCK:
        conn = _get_conn()
        rows = conn.execute("SELECT chat_id, data FROM bot_sessions ORDER BY updated_at DESC").fetchall()
        conn.close()

    sessions: Dict[str, dict] = {}
    for row in rows:
        try:
            data = json.loads(row["data"])
            if isinstance(data, dict):
                sessions[str(row["chat_id"])] = data
        except Exception:
            continue
    return sessions


@retry_on_locked()
def delete_session(chat_id: str) -> None:
    """Elimina una sesión activa para un chat."""
    init_db()
    with DB_LOCK:
        conn = _get_conn()
        with conn:
            conn.execute("DELETE FROM bot_sessions WHERE chat_id = ?", (str(chat_id),))
        conn.close()


@retry_on_locked()
def save_download(chat_id: str, file_id: str, local_path: str, file_name: str, status: str = "downloading") -> None:
    """Registra o actualiza un archivo pendiente de organización."""
    init_db()
    with DB_LOCK:
        conn = _get_conn()
        with conn:
            conn.execute(
                """
                INSERT INTO bot_downloads(file_id, chat_id, local_path, file_name, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, datetime('now'), datetime('now'))
                ON CONFLICT(file_id) DO UPDATE SET
                    chat_id = excluded.chat_id,
                    local_path = excluded.local_path,
                    file_name = excluded.file_name,
                    status = excluded.status,
                    updated_at = datetime('now')
                """,
                (str(file_id), str(chat_id), str(local_path), str(file_name), status),
            )
        conn.close()


@retry_on_locked()
def update_download_status(file_id: str, status: str) -> None:
    """Actualiza el estado de una descarga en la base de datos."""
    init_db()
    with DB_LOCK:
        conn = _get_conn()
        with conn:
            conn.execute(
                "UPDATE bot_downloads SET status = ?, updated_at = datetime('now') WHERE file_id = ?",
                (status, str(file_id)),
            )
        conn.close()


@retry_on_locked()
def get_pending_downloads(chat_id: Optional[str] = None) -> List[dict]:
    """Devuelve las descargas pendientes o no organizadas, opcionalmente filtradas por chat."""
    init_db()
    with DB_LOCK:
        conn = _get_conn()
        if chat_id is not None:
            rows = conn.execute(
                "SELECT file_id, chat_id, local_path, file_name, status, created_at, updated_at FROM bot_downloads WHERE chat_id = ? AND status != 'organized' ORDER BY updated_at DESC",
                (str(chat_id),),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT file_id, chat_id, local_path, file_name, status, created_at, updated_at FROM bot_downloads WHERE status != 'organized' ORDER BY updated_at DESC"
            ).fetchall()
        conn.close()
    return [dict(r) for r in rows]


@retry_on_locked()
def delete_download(file_id: str) -> None:
    """Elimina una entrada de descarga cuando ya fue organizada o cancelada."""
    init_db()
    with DB_LOCK:
        conn = _get_conn()
        with conn:
            conn.execute("DELETE FROM bot_downloads WHERE file_id = ?", (str(file_id),))
        conn.close()


@retry_on_locked()
def cleanup_stale_sessions(hours: int = 24) -> None:
    """Elimina sesiones inactivas por más de X horas."""
    init_db()
    with DB_LOCK:
        conn = _get_conn()
        with conn:
            conn.execute(
                "DELETE FROM bot_sessions WHERE updated_at < datetime('now', '-' || ? || ' hours')",
                (str(hours),),
            )
        conn.close()


def _bootstrap_from_legacy_files(conn: sqlite3.Connection) -> None:
    """Importa el estado legacy si alguna tabla sigue vacía."""
    try:
        tmp_dir = _get_db_path().parent
        completed_file = tmp_dir / "completed.txt"
        pending_file = tmp_dir / "pending-compression.txt"
        progress_file = tmp_dir / "progress.json"

        with conn:
            completed_count = conn.execute("SELECT COUNT(*) FROM completed_files").fetchone()[0]
            if completed_count == 0 and completed_file.exists():
                completed_rows = []
                with open(completed_file, "r", encoding="utf-8") as handle:
                    for line in handle:
                        path = line.strip()
                        if path:
                            completed_rows.append((path, None))
                if completed_rows:
                    conn.executemany(
                        "REPLACE INTO completed_files(path, label, completed_at) VALUES (?, ?, datetime('now'))",
                        completed_rows,
                    )

            pending_count = conn.execute("SELECT COUNT(*) FROM pending_files").fetchone()[0]
            if pending_count == 0 and pending_file.exists():
                pending_rows = []
                with open(pending_file, "r", encoding="utf-8") as handle:
                    for line in handle:
                        path = line.strip()
                        if path:
                            pending_rows.append((path, None))
                if pending_rows:
                    conn.executemany(
                        "REPLACE INTO pending_files(path, label, added_at) VALUES (?, ?, datetime('now'))",
                        pending_rows,
                    )

            progress_count = conn.execute("SELECT COUNT(*) FROM progress").fetchone()[0]
            if progress_count == 0 and progress_file.exists():
                try:
                    progress_data = json.loads(progress_file.read_text(encoding="utf-8"))
                    conn.execute(
                        "REPLACE INTO progress(key, data, updated_at) VALUES ('main', ?, datetime('now'))",
                        (json.dumps(progress_data, ensure_ascii=False),),
                    )
                except Exception:
                    pass
    except Exception:
        pass


@retry_on_locked()
def mark_completed(path, label: Optional[str] = None) -> None:
    """Marca un archivo como completado (INSERT/REPLACE)."""
    init_db()
    path_s = str(path)
    with DB_LOCK:
        conn = _get_conn()
        with conn:
            conn.execute(
                "REPLACE INTO completed_files(path, label, completed_at) VALUES (?, ?, datetime('now'))",
                (path_s, label),
            )
        conn.close()


@retry_on_locked()
def is_completed(path) -> bool:
    """Devuelve True si el path está en completed_files."""
    init_db()
    path_s = str(path)
    with DB_LOCK:
        conn = _get_conn()
        cur = conn.execute("SELECT 1 FROM completed_files WHERE path = ? LIMIT 1", (path_s,))
        row = cur.fetchone()
        conn.close()
    return row is not None


@retry_on_locked()
def add_pending(path, label: Optional[str] = None) -> None:
    """Añade o reemplaza un path en pending_files."""
    init_db()
    path_s = str(path)
    with DB_LOCK:
        conn = _get_conn()
        with conn:
            conn.execute(
                "REPLACE INTO pending_files(path, label, added_at) VALUES (?, ?, datetime('now'))",
                (path_s, label),
            )
        conn.close()


@retry_on_locked()
def remove_pending(path) -> None:
    """Elimina un path de pending_files."""
    init_db()
    path_s = str(path)
    with DB_LOCK:
        conn = _get_conn()
        with conn:
            conn.execute("DELETE FROM pending_files WHERE path = ?", (path_s,))
        conn.close()


@retry_on_locked()
def get_pending_files() -> List[Path]:
    """Devuelve la lista de paths pendientes como objetos Path."""
    init_db()
    with DB_LOCK:
        conn = _get_conn()
        cur = conn.execute("SELECT path FROM pending_files ORDER BY added_at ASC")
        rows = cur.fetchall()
        conn.close()
    return [Path(r["path"]) for r in rows]


@retry_on_locked()
def get_all_completed_files() -> List[Path]:
    """Devuelve todos los paths marcados como completados."""
    init_db()
    with DB_LOCK:
        conn = _get_conn()
        cur = conn.execute("SELECT path FROM completed_files ORDER BY completed_at ASC")
        rows = cur.fetchall()
        conn.close()
    return [Path(r["path"]) for r in rows]


@retry_on_locked()
def save_progress(data: Dict) -> None:
    """Guarda un dict serializado en la fila 'main' de la tabla progress."""
    init_db()
    serialized = json.dumps(data, ensure_ascii=False)
    with DB_LOCK:
        conn = _get_conn()
        with conn:
            conn.execute(
                "REPLACE INTO progress(key, data, updated_at) VALUES ('main', ?, datetime('now'))",
                (serialized,),
            )
        conn.close()


@retry_on_locked()
def load_progress() -> Dict:
    """Carga y devuelve el dict de progreso; devuelve {} si no existe."""
    init_db()
    with DB_LOCK:
        conn = _get_conn()
        cur = conn.execute("SELECT data FROM progress WHERE key = 'main' LIMIT 1")
        row = cur.fetchone()
        conn.close()
    if not row:
        return {}
    try:
        return json.loads(row["data"])
    except Exception:
        return {}
