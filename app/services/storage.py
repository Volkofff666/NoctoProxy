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
                    username TEXT,
                    full_name TEXT,
                    is_blocked INTEGER NOT NULL DEFAULT 0,
                    is_proxy_connected INTEGER NOT NULL DEFAULT 0,
                    proxy_connected_at TEXT
                )
                """
            )
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS share_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tg_id INTEGER NOT NULL,
                    source TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            await self._ensure_users_columns(db)
            await db.execute("CREATE INDEX IF NOT EXISTS idx_users_last_seen ON users(last_seen)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_users_first_seen ON users(first_seen)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_share_events_tg_id ON share_events(tg_id)")
            await db.execute("CREATE INDEX IF NOT EXISTS idx_share_events_created_at ON share_events(created_at)")
            await db.commit()

    async def _ensure_users_columns(self, db: aiosqlite.Connection) -> None:
        cursor = await db.execute("PRAGMA table_info(users)")
        rows = await cursor.fetchall()
        existing = {str(row[1]) for row in rows}

        if "username" not in existing:
            await db.execute("ALTER TABLE users ADD COLUMN username TEXT")
        if "full_name" not in existing:
            await db.execute("ALTER TABLE users ADD COLUMN full_name TEXT")
        if "is_blocked" not in existing:
            await db.execute("ALTER TABLE users ADD COLUMN is_blocked INTEGER NOT NULL DEFAULT 0")
        if "is_proxy_connected" not in existing:
            await db.execute("ALTER TABLE users ADD COLUMN is_proxy_connected INTEGER NOT NULL DEFAULT 0")
        if "proxy_connected_at" not in existing:
            await db.execute("ALTER TABLE users ADD COLUMN proxy_connected_at TEXT")

    async def touch_user(
        self,
        tg_id: int,
        username: str | None = None,
        full_name: str | None = None,
    ) -> None:
        now = utc_now_str()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO users (tg_id, first_seen, last_seen, username, full_name)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(tg_id)
                DO UPDATE SET
                    username = excluded.username,
                    full_name = excluded.full_name
                """,
                (tg_id, now, now, username, full_name),
            )
            await db.commit()

    async def record_share(self, tg_id: int, source: str | None = None) -> None:
        now = utc_now_str()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO share_events (tg_id, source, created_at)
                VALUES (?, ?, ?)
                """,
                (int(tg_id), source, now),
            )
            await db.commit()

    async def set_user_proxy_connected(self, tg_id: int, connected: bool = True) -> bool:
        connected_at = utc_now_str() if connected else None
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                UPDATE users
                SET is_proxy_connected = ?, proxy_connected_at = ?
                WHERE tg_id = ?
                """,
                (1 if connected else 0, connected_at, int(tg_id)),
            )
            await db.commit()
            return cursor.rowcount > 0

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

    async def count_unique_sharers(self) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT COUNT(DISTINCT tg_id) FROM share_events")
            row = await cursor.fetchone()
            return int(row[0] if row else 0)

    async def count_total_shares(self) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT COUNT(*) FROM share_events")
            row = await cursor.fetchone()
            return int(row[0] if row else 0)

    async def count_shares_last_hours(self, hours: int = 24) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                SELECT COUNT(*)
                FROM share_events
                WHERE created_at >= datetime('now', ?)
                """,
                (f"-{int(hours)} hours",),
            )
            row = await cursor.fetchone()
            return int(row[0] if row else 0)

    async def get_top_sharers_last_hours(
        self,
        hours: int = 24,
        limit: int = 5,
    ) -> list[dict[str, str | int | None]]:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                SELECT
                    s.tg_id,
                    u.username,
                    u.full_name,
                    COUNT(*) AS share_count
                FROM share_events s
                LEFT JOIN users u ON u.tg_id = s.tg_id
                WHERE s.created_at >= datetime('now', ?)
                GROUP BY s.tg_id, u.username, u.full_name
                ORDER BY share_count DESC, s.tg_id ASC
                LIMIT ?
                """,
                (f"-{int(hours)} hours", int(limit)),
            )
            rows = await cursor.fetchall()

        result: list[dict[str, str | int | None]] = []
        for row in rows:
            result.append(
                {
                    "tg_id": int(row[0]),
                    "username": row[1],
                    "full_name": row[2],
                    "share_count": int(row[3]),
                }
            )
        return result

    async def get_recent_users(self, limit: int = 15) -> list[dict[str, str | int | None]]:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                SELECT tg_id, username, full_name, first_seen, last_seen
                FROM users
                ORDER BY first_seen DESC
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
                    "is_blocked": 0,
                    "is_proxy_connected": 0,
                    "proxy_connected_at": None,
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
                SELECT tg_id, username, full_name, first_seen, last_seen, is_blocked, is_proxy_connected, proxy_connected_at
                FROM users
                ORDER BY first_seen DESC
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
                    "is_blocked": bool(row[5]),
                    "is_proxy_connected": bool(row[6]),
                    "proxy_connected_at": row[7],
                }
            )
        return result

    async def get_user_by_tg_id(self, tg_id: int) -> dict[str, str | int | None] | None:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                SELECT tg_id, username, full_name, first_seen, last_seen, is_blocked, is_proxy_connected, proxy_connected_at
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
            "is_blocked": bool(row[5]),
            "is_proxy_connected": bool(row[6]),
            "proxy_connected_at": row[7],
        }

    async def search_users(self, query: str, limit: int = 10) -> list[dict[str, str | int | None]]:
        q = (query or "").strip()
        if not q:
            return []

        like_value = f"%{q.lower()}%"
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """
                SELECT tg_id, username, full_name, first_seen, last_seen, is_blocked, is_proxy_connected, proxy_connected_at
                FROM users
                WHERE CAST(tg_id AS TEXT) LIKE ?
                   OR LOWER(COALESCE(username, '')) LIKE ?
                   OR LOWER(COALESCE(full_name, '')) LIKE ?
                ORDER BY first_seen DESC
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
                    "is_blocked": bool(row[5]),
                    "is_proxy_connected": bool(row[6]),
                    "proxy_connected_at": row[7],
                }
            )
        return result

    async def set_user_blocked(self, tg_id: int, blocked: bool) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "UPDATE users SET is_blocked = ? WHERE tg_id = ?",
                (1 if blocked else 0, int(tg_id)),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def delete_user_by_tg_id(self, tg_id: int) -> bool:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("DELETE FROM users WHERE tg_id = ?", (int(tg_id),))
            await db.commit()
            return cursor.rowcount > 0

    async def get_all_user_ids(self) -> list[int]:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT tg_id FROM users")
            rows = await cursor.fetchall()
            return [int(row[0]) for row in rows]
