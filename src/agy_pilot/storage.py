from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)


class SessionStore:
    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self._db: aiosqlite.Connection | None = None

    async def _get_conn(self) -> aiosqlite.Connection:
        if self._db is None:
            self._db = await aiosqlite.connect(self.db_path)
        return self._db

    async def init(self) -> None:
        db = await self._get_conn()
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS session (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS message_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                role TEXT,
                content TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        await db.commit()

    async def get_conversation_id(self) -> str | None:
        db = await self._get_conn()
        async with db.execute(
            "SELECT value FROM session WHERE key = 'current_conversation_id'"
        ) as cursor:
            row = await cursor.fetchone()
            if row and row[0] and row[0].strip():
                return row[0].strip()
        return None

    async def set_conversation_id(self, conv_id: str | None) -> None:
        db = await self._get_conn()
        if conv_id and conv_id.strip():
            await db.execute(
                "INSERT OR REPLACE INTO session (key, value) VALUES ('current_conversation_id', ?)",
                (conv_id.strip(),),
            )
        else:
            await db.execute("DELETE FROM session WHERE key = 'current_conversation_id'")
        await db.commit()

    async def get_project_id(self) -> str | None:
        db = await self._get_conn()
        async with db.execute(
            "SELECT value FROM session WHERE key = 'current_project_id'"
        ) as cursor:
            row = await cursor.fetchone()
            if row and row[0] and row[0].strip():
                return row[0].strip()
        return None

    async def set_project_id(self, project_id: str | None) -> None:
        db = await self._get_conn()
        if project_id and project_id.strip():
            await db.execute(
                "INSERT OR REPLACE INTO session (key, value) VALUES ('current_project_id', ?)",
                (project_id.strip(),),
            )
        else:
            await db.execute("DELETE FROM session WHERE key = 'current_project_id'")
        await db.commit()

    async def log_message(self, user_id: int, role: str, content: str) -> None:
        db = await self._get_conn()
        await db.execute(
            "INSERT INTO message_logs (user_id, role, content) VALUES (?, ?, ?)",
            (user_id, role, content),
        )
        await db.commit()

    async def get_recent_logs(self, limit: int = 10) -> list[dict[str, Any]]:
        db = await self._get_conn()
        async with db.execute(
            "SELECT id, user_id, role, content, timestamp FROM message_logs ORDER BY id ASC LIMIT ?",
            (limit,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [
                {
                    "id": r[0],
                    "user_id": r[1],
                    "role": r[2],
                    "content": r[3],
                    "timestamp": r[4],
                }
                for r in rows
            ]

    async def close(self) -> None:
        if self._db is not None:
            await self._db.close()
            self._db = None
