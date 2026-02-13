from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import aiosqlite


def utc_now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


class Storage:
    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)

    async def init(self) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            # Legacy cleanup: event logs are no longer stored.
            await db.execute("DROP TABLE IF EXISTS events")
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tg_id INTEGER UNIQUE NOT NULL,
                    first_seen TEXT NOT NULL,
                    last_seen TEXT NOT NULL,
                    invited_by INTEGER,
                    username TEXT,
                    full_name TEXT
                )
                """
            )
            await self._ensure_users_columns(db)
            await db.execute("CREATE INDEX IF NOT EXISTS idx_users_last_seen ON users(last_seen)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_users_first_seen ON users(first_seen)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_users_invited_by ON users(invited_by)")
            await db.commit()

    async def _ensure_users_columns(self, db: aiosqlite.Connection) -> None:
        cursor = await db.execute("PRAGMA table_info(users)")
        rows = await cursor.fetchall()
        existing = {str(row[1]) for row in rows}

        if "invited_by" not in existing:
            await db.execute("ALTER TABLE users ADD COLUMN invited_by INTEGER")
        if "username" not in existing:
            await db.execute("ALTER TABLE users ADD COLUMN username TEXT")
        if "full_name" not in existing:
            await db.execute("ALTER TABLE users ADD COLUMN full_name TEXT")

    async def touch_user(
        self,
        tg_id: int,
        invited_by: int | None = None,
        username: str | None = None,
        full_name: str | None = None,
    ) -> None:
        now = utc_now_str()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO users (tg_id, first_seen, last_seen, invited_by, username, full_name)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(tg_id)
                DO UPDATE SET
                    last_seen = excluded.last_seen,
                    username = excluded.username,
                    full_name = excluded.full_name,
                    invited_by = COALESCE(users.invited_by, excluded.invited_by)
                """,
                (tg_id, now, now, invited_by, username, full_name),
            )
            await db.commit()

    async def count_users(self) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT COUNT(*) FROM users")
            row = await cursor.fetchone()
            return int(row[0] if row else 0)

    async def count_active_users_last_hours(self, hours: int = 24) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                SELECT COUNT(*)
                FROM users
                WHERE last_seen >= datetime('now', ?)
                """,
                (f"-{int(hours)} hours",),
            )
            row = await cursor.fetchone()
            return int(row[0] if row else 0)

    async def count_new_users_last_hours(self, hours: int = 24) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                SELECT COUNT(*)
                FROM users
                WHERE first_seen >= datetime('now', ?)
                """,
                (f"-{int(hours)} hours",),
            )
            row = await cursor.fetchone()
            return int(row[0] if row else 0)

    async def count_users_with_referrer(self) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT COUNT(*) FROM users WHERE invited_by IS NOT NULL")
            row = await cursor.fetchone()
            return int(row[0] if row else 0)

    async def count_invited_by(self, inviter_tg_id: int) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "SELECT COUNT(*) FROM users WHERE invited_by = ?",
                (inviter_tg_id,),
            )
            row = await cursor.fetchone()
            return int(row[0] if row else 0)

    async def get_top_referrers(self, limit: int = 10) -> list[tuple[int, int]]:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                SELECT invited_by, COUNT(*) as invited_count
                FROM users
                WHERE invited_by IS NOT NULL
                GROUP BY invited_by
                ORDER BY invited_count DESC, invited_by ASC
                LIMIT ?
                """,
                (int(limit),),
            )
            rows = await cursor.fetchall()
            return [(int(row[0]), int(row[1])) for row in rows]

    async def get_recent_users(self, limit: int = 15) -> list[dict[str, str | int | None]]:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                SELECT tg_id, username, full_name, first_seen, last_seen, invited_by
                FROM users
                ORDER BY last_seen DESC
                LIMIT ?
                """,
                (int(limit),),
            )
            rows = await cursor.fetchall()

        result: list[dict[str, str | int | None]] = []
        for row in rows:
            result.append(
                {
                    "tg_id": int(row[0]),
                    "username": row[1],
                    "full_name": row[2],
                    "first_seen": row[3],
                    "last_seen": row[4],
                    "invited_by": int(row[5]) if row[5] is not None else None,
                }
            )
        return result

    async def get_users_page(self, page: int, page_size: int = 10) -> list[dict[str, str | int | None]]:
        safe_page = max(1, int(page))
        safe_page_size = max(1, int(page_size))
        offset = (safe_page - 1) * safe_page_size

        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                SELECT tg_id, username, full_name, first_seen, last_seen, invited_by
                FROM users
                ORDER BY last_seen DESC
                LIMIT ? OFFSET ?
                """,
                (safe_page_size, offset),
            )
            rows = await cursor.fetchall()

        result: list[dict[str, str | int | None]] = []
        for row in rows:
            result.append(
                {
                    "tg_id": int(row[0]),
                    "username": row[1],
                    "full_name": row[2],
                    "first_seen": row[3],
                    "last_seen": row[4],
                    "invited_by": int(row[5]) if row[5] is not None else None,
                }
            )
        return result

    async def get_user_by_tg_id(self, tg_id: int) -> dict[str, str | int | None] | None:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                SELECT tg_id, username, full_name, first_seen, last_seen, invited_by
                FROM users
                WHERE tg_id = ?
                LIMIT 1
                """,
                (int(tg_id),),
            )
            row = await cursor.fetchone()

        if row is None:
            return None

        return {
            "tg_id": int(row[0]),
            "username": row[1],
            "full_name": row[2],
            "first_seen": row[3],
            "last_seen": row[4],
            "invited_by": int(row[5]) if row[5] is not None else None,
        }

    async def search_users(self, query: str, limit: int = 10) -> list[dict[str, str | int | None]]:
        q = (query or "").strip()
        if not q:
            return []

        like_value = f"%{q.lower()}%"
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                SELECT tg_id, username, full_name, first_seen, last_seen, invited_by
                FROM users
                WHERE CAST(tg_id AS TEXT) LIKE ?
                   OR LOWER(COALESCE(username, '')) LIKE ?
                   OR LOWER(COALESCE(full_name, '')) LIKE ?
                ORDER BY last_seen DESC
                LIMIT ?
                """,
                (like_value, like_value, like_value, int(limit)),
            )
            rows = await cursor.fetchall()

        result: list[dict[str, str | int | None]] = []
        for row in rows:
            result.append(
                {
                    "tg_id": int(row[0]),
                    "username": row[1],
                    "full_name": row[2],
                    "first_seen": row[3],
                    "last_seen": row[4],
                    "invited_by": int(row[5]) if row[5] is not None else None,
                }
            )
        return result

    async def get_all_user_ids(self) -> list[int]:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT tg_id FROM users")
            rows = await cursor.fetchall()
            return [int(row[0]) for row in rows]
