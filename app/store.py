"""Dialogs and leads in SQLite: history for the model, weekly reports, and a copy of every lead."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path


class Store:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.Lock()
        with self.lock:
            self.conn.executescript("""
                CREATE TABLE IF NOT EXISTS messages (
                    site TEXT, session TEXT, role TEXT, text TEXT, lang TEXT, ts REAL);
                CREATE INDEX IF NOT EXISTS messages_session ON messages(site, session, ts);
                CREATE TABLE IF NOT EXISTS leads (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, site TEXT, session TEXT, name TEXT, phone TEXT,
                    card TEXT, page TEXT, lang TEXT, delivered TEXT, ts REAL);
            """)

    def add_message(self, site: str, session: str, role: str, text: str, lang: str = "") -> None:
        with self.lock:
            self.conn.execute("INSERT INTO messages VALUES (?,?,?,?,?,?)",
                              (site, session, role, text, lang, time.time()))
            self.conn.commit()

    def history(self, site: str, session: str, limit: int = 16) -> list[dict]:
        with self.lock:
            rows = self.conn.execute(
                "SELECT role, text FROM messages WHERE site=? AND session=? ORDER BY ts DESC LIMIT ?",
                (site, session, limit)).fetchall()
        return [{"role": r, "content": t} for r, t in reversed(rows)]

    def count_user_messages(self, site: str, session: str) -> int:
        with self.lock:
            return self.conn.execute("SELECT COUNT(*) FROM messages WHERE site=? AND session=? AND role='user'",
                                     (site, session)).fetchone()[0]

    def recent_lead(self, site: str, session: str, phone: str, within_s: float = 600) -> bool:
        with self.lock:
            return self.conn.execute(
                "SELECT 1 FROM leads WHERE site=? AND session=? AND phone=? AND ts>?",
                (site, session, phone, time.time() - within_s)).fetchone() is not None

    def add_lead(self, site: str, session: str, name: str, phone: str, card: dict, page: str, lang: str,
                 delivered: dict) -> int:
        with self.lock:
            cur = self.conn.execute(
                "INSERT INTO leads (site, session, name, phone, card, page, lang, delivered, ts) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (site, session, name, phone, json.dumps(card, ensure_ascii=False), page, lang,
                 json.dumps(delivered), time.time()))
            self.conn.commit()
            return cur.lastrowid

    def stats(self, site: str, days: int) -> dict:
        since = time.time() - days * 86400
        with self.lock:
            dialogs = self.conn.execute(
                "SELECT COUNT(DISTINCT session) FROM messages WHERE site=? AND ts>? AND role='user'",
                (site, since)).fetchone()[0]
            messages = self.conn.execute(
                "SELECT COUNT(*) FROM messages WHERE site=? AND ts>? AND role='user'", (site, since)).fetchone()[0]
            leads = self.conn.execute("SELECT COUNT(*) FROM leads WHERE site=? AND ts>?", (site, since)).fetchone()[0]
            first_questions = [r[0] for r in self.conn.execute(
                """SELECT text FROM messages m WHERE site=? AND ts>? AND role='user' AND ts = (
                       SELECT MIN(ts) FROM messages WHERE site=m.site AND session=m.session AND role='user')
                   ORDER BY ts DESC LIMIT 10""", (site, since)).fetchall()]
        return {"days": days, "dialogs": dialogs, "messages": messages, "leads": leads,
                "conversion_pct": round(leads / dialogs * 100, 1) if dialogs else 0.0,
                "first_questions": first_questions}

    def purge(self, keep_days: int) -> int:
        """Personal data is kept only as long as needed (152-ФЗ): old dialogs are deleted."""
        with self.lock:
            cur = self.conn.execute("DELETE FROM messages WHERE ts<?", (time.time() - keep_days * 86400,))
            self.conn.commit()
            return cur.rowcount
