"""
database.py — работа с SQLite базой данных.
Хранит информацию о пользователях и их задеплоенных ботах.
"""

import sqlite3
import logging
from contextlib import contextmanager

logger = logging.getLogger(__name__)

DB_PATH = "bothost.db"


@contextmanager
def get_conn():
    """Контекстный менеджер для безопасной работы с БД."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Создаёт таблицы при первом запуске."""
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id    INTEGER PRIMARY KEY,
                username   TEXT,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS bots (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id  TEXT UNIQUE NOT NULL,
                user_id     INTEGER NOT NULL,
                repo_url    TEXT NOT NULL,
                entry_file  TEXT NOT NULL,
                status      TEXT DEFAULT 'stopped',
                created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(user_id)
            );
        """)
    logger.info("База данных инициализирована.")


# ── Пользователи ──────────────────────────────────────────────────────────────

def upsert_user(user_id: int, username: str):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)",
            (user_id, username)
        )


def count_user_bots(user_id: int) -> int:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM bots WHERE user_id = ?", (user_id,)
        ).fetchone()
        return row["cnt"] if row else 0


# ── Боты ──────────────────────────────────────────────────────────────────────

def add_bot(project_id: str, user_id: int, repo_url: str, entry_file: str):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO bots (project_id, user_id, repo_url, entry_file, status)
               VALUES (?, ?, ?, ?, 'running')""",
            (project_id, user_id, repo_url, entry_file)
        )


def get_user_bots(user_id: int) -> list[sqlite3.Row]:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM bots WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,)
        ).fetchall()


def get_bot(project_id: str) -> sqlite3.Row | None:
    with get_conn() as conn:
        return conn.execute(
            "SELECT * FROM bots WHERE project_id = ?", (project_id,)
        ).fetchone()


def update_bot_status(project_id: str, status: str):
    with get_conn() as conn:
        conn.execute(
            "UPDATE bots SET status = ? WHERE project_id = ?",
            (status, project_id)
        )


def delete_bot(project_id: str):
    with get_conn() as conn:
        conn.execute("DELETE FROM bots WHERE project_id = ?", (project_id,))
