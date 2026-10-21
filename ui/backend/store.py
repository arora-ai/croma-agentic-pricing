"""Conversation history for the UI: conversations, messages and the per-turn telemetry (so reopening a chat restores the admin view too).
SQLite file next to this module (CHAT_DB_PATH moves it, for example onto a mounted volume). Each conversation belongs to one user: behind the API gateway
that is the signed-in user's id, otherwise the empty id of the single default user. In the end state this is Cosmos DB keyed by the signed-in user."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path

DB_PATH = Path(os.getenv("CHAT_DB_PATH") or Path(__file__).parent / "data" / "chat.db")
_lock = threading.Lock()
_db: sqlite3.Connection | None = None


def _conn() -> sqlite3.Connection:
    global _db
    if _db is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _db = sqlite3.connect(DB_PATH, check_same_thread=False)
        _db.row_factory = sqlite3.Row
        _db.executescript(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY, title TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT, conversation_id TEXT NOT NULL, turn INTEGER NOT NULL, role TEXT NOT NULL,
                text TEXT NOT NULL, status TEXT NOT NULL, seconds REAL, created_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS telemetry (
                conversation_id TEXT NOT NULL, turn INTEGER NOT NULL, events TEXT NOT NULL, logs TEXT NOT NULL,
                PRIMARY KEY (conversation_id, turn));
            CREATE INDEX IF NOT EXISTS messages_by_conversation ON messages (conversation_id, turn);
            """
        )
        # Histories written before users existed have no owner: they get the empty id, which is the single default user.
        if "user_id" not in {r["name"] for r in _db.execute("PRAGMA table_info(conversations)")}:
            _db.execute("ALTER TABLE conversations ADD COLUMN user_id TEXT NOT NULL DEFAULT ''")
        _db.execute("CREATE INDEX IF NOT EXISTS conversations_by_user ON conversations (user_id, updated_at)")
        _db.commit()
    return _db


def owner_of(conversation_id: str) -> str | None:
    """The user a conversation belongs to ("" for the default user), or None if there is no such conversation."""
    with _lock:
        row = _conn().execute("SELECT user_id FROM conversations WHERE id = ?", [conversation_id]).fetchone()
    return None if row is None else row["user_id"]


def list_conversations(user_id: str = "") -> list[dict]:
    with _lock:
        rows = _conn().execute(
            "SELECT c.id, c.title, c.created_at, c.updated_at, "
            "(SELECT text FROM messages m WHERE m.conversation_id = c.id AND m.role = 'user' ORDER BY m.turn DESC LIMIT 1) AS last_question "
            "FROM conversations c WHERE c.user_id = ? ORDER BY c.updated_at DESC", [user_id]
        ).fetchall()
    return [dict(r) for r in rows]


def get_conversation(conversation_id: str, user_id: str = "") -> dict | None:
    with _lock:
        db = _conn()
        c = db.execute("SELECT id, title, created_at, updated_at FROM conversations WHERE id = ? AND user_id = ?", [conversation_id, user_id]).fetchone()
        if not c:
            return None
        messages = [dict(r) for r in db.execute(
            "SELECT turn, role, text, status, seconds FROM messages WHERE conversation_id = ? ORDER BY turn, role = 'assistant'", [conversation_id])]
        turns = [{"turn": r["turn"], "events": json.loads(r["events"]), "logs": json.loads(r["logs"])} for r in db.execute(
            "SELECT turn, events, logs FROM telemetry WHERE conversation_id = ? ORDER BY turn", [conversation_id])]
    return {**dict(c), "messages": messages, "turns": turns}


def ensure_conversation(conversation_id: str, title: str, user_id: str = "") -> bool:
    """Creates the conversation, owned by user_id, if new. Returns True when it was created."""
    now = time.time()
    with _lock:
        db = _conn()
        if db.execute("SELECT 1 FROM conversations WHERE id = ?", [conversation_id]).fetchone():
            return False
        db.execute("INSERT INTO conversations (id, title, created_at, updated_at, user_id) VALUES (?, ?, ?, ?, ?)", [conversation_id, title, now, now, user_id])
        db.commit()
        return True


def next_turn(conversation_id: str) -> int:
    with _lock:
        row = _conn().execute("SELECT COALESCE(MAX(turn), 0) AS t FROM messages WHERE conversation_id = ?", [conversation_id]).fetchone()
    return row["t"] + 1


def add_turn(conversation_id: str, turn: int, question: str) -> None:
    """Stores the user's message and an empty assistant message that is filled in when the turn ends."""
    now = time.time()
    with _lock:
        db = _conn()
        db.execute("INSERT INTO messages (conversation_id, turn, role, text, status, created_at) VALUES (?, ?, 'user', ?, 'done', ?)", [conversation_id, turn, question, now])
        db.execute("INSERT INTO messages (conversation_id, turn, role, text, status, created_at) VALUES (?, ?, 'assistant', '', 'streaming', ?)", [conversation_id, turn, now])
        db.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", [now, conversation_id])
        db.commit()


def finish_turn(conversation_id: str, turn: int, text: str, status: str, seconds: float | None, events: list, logs: list) -> None:
    with _lock:
        db = _conn()
        db.execute("UPDATE messages SET text = ?, status = ?, seconds = ? WHERE conversation_id = ? AND turn = ? AND role = 'assistant'",
                   [text, status, seconds, conversation_id, turn])
        db.execute("INSERT OR REPLACE INTO telemetry (conversation_id, turn, events, logs) VALUES (?, ?, ?, ?)",
                   [conversation_id, turn, json.dumps(events, default=str), json.dumps(logs, default=str)])
        db.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", [time.time(), conversation_id])
        db.commit()


def rename(conversation_id: str, title: str, user_id: str = "") -> bool:
    with _lock:
        db = _conn()
        cur = db.execute("UPDATE conversations SET title = ? WHERE id = ? AND user_id = ?", [title.strip()[:120] or "New chat", conversation_id, user_id])
        db.commit()
        return cur.rowcount > 0


def delete(conversation_id: str, user_id: str = "") -> bool:
    with _lock:
        db = _conn()
        if not db.execute("SELECT 1 FROM conversations WHERE id = ? AND user_id = ?", [conversation_id, user_id]).fetchone():
            return False
        for table in ("telemetry", "messages"):
            db.execute(f"DELETE FROM {table} WHERE conversation_id = ?", [conversation_id])
        cur = db.execute("DELETE FROM conversations WHERE id = ?", [conversation_id])
        db.commit()
        return cur.rowcount > 0


def mark_interrupted() -> None:
    """Turns left streaming by a server restart can never finish; mark them so the UI does not show them as running."""
    with _lock:
        db = _conn()
        db.execute("UPDATE messages SET status = 'error', text = 'This answer was interrupted.' WHERE status = 'streaming'")
        db.commit()
