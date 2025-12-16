import os
import psycopg2
from dataclasses import dataclass
from typing import List
from datetime import datetime
import hashlib
import psycopg2
from psycopg2 import errors

@dataclass
class Note:
    id: str
    title: str
    description: str
    created_at: datetime
    updated_at: datetime

class Storage:
    """
    Шардированное хранилище на двух Postgres (PG_DSN_1, PG_DSN_2).
    """
    def __init__(self):
        dsn1 = os.getenv("PG_DSN_1")
        dsn2 = os.getenv("PG_DSN_2")
        if not dsn1 or not dsn2:
            raise RuntimeError("PG_DSN_1 and PG_DSN_2 must be set")

        self.conns = [
            psycopg2.connect(dsn1),
            psycopg2.connect(dsn2),
        ]
        for conn in self.conns:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_lock(777001);")
                try:
                    cur.execute("""
                    CREATE TABLE IF NOT EXISTS notes (
                        id TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        description TEXT NOT NULL,
                        created_at TIMESTAMPTZ NOT NULL,
                        updated_at TIMESTAMPTZ NOT NULL
                    );
                    """)
                except (errors.UniqueViolation, errors.DuplicateTable, errors.DuplicateObject):
                    pass
                finally:
                    cur.execute("SELECT pg_advisory_unlock(777001);")


    def _shard_index(self, note_id: str) -> int:
        h = hashlib.sha1(note_id.encode("utf-8")).digest()
        return h[0] % len(self.conns)

    def _now(self) -> datetime:
        return datetime.utcnow()

    def create_note(self, title: str, description: str) -> Note:
        now = self._now()
        note_id = hashlib.sha1(f"{title}{now}".encode()).hexdigest()
        idx = self._shard_index(note_id)
        conn = self.conns[idx]

        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO notes(id, title, description, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s)
            """, (note_id, title, description, now, now))
        return Note(note_id, title, description, now, now)

    def get_note(self, note_id: str) -> Note | None:
        idx = self._shard_index(note_id)
        conn = self.conns[idx]
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, title, description, created_at, updated_at
                FROM notes WHERE id = %s
            """, (note_id,))
            row = cur.fetchone()
        if not row:
            return None
        return Note(*row)

    def list_notes(self) -> List[Note]:
        result: List[Note] = []
        for conn in self.conns:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT id, title, description, created_at, updated_at
                    FROM notes
                """)
                for row in cur.fetchall():
                    result.append(Note(*row))
        result.sort(key=lambda n: n.created_at)
        return result

    def update_description(self, note_id: str, description: str) -> Note | None:
        idx = self._shard_index(note_id)
        conn = self.conns[idx]
        now = self._now()
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE notes SET description = %s, updated_at = %s
                WHERE id = %s
            """, (description, now, note_id))
            if cur.rowcount == 0:
                return None
        return self.get_note(note_id)

    def delete_note(self, note_id: str) -> bool:
        idx = self._shard_index(note_id)
        conn = self.conns[idx]
        with conn.cursor() as cur:
            cur.execute("DELETE FROM notes WHERE id = %s", (note_id,))
            return cur.rowcount > 0

    def health(self) -> bool:
        try:
            for conn in self.conns:
                with conn.cursor() as cur:
                    cur.execute("SELECT 1")
                    cur.fetchone()
            return True
        except Exception:
            return False
