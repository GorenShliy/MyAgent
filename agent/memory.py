"""
对话记忆：SQLite 轻量持久化，多会话管理。
程序重启后历史对话不丢失；支持指定会话 ID 恢复上下文。
"""
import json
import os
import sqlite3
from contextlib import closing

import config


class Memory:
    """会话与消息的 SQLite 存取。"""

    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or config.DB_PATH
        parent = os.path.dirname(self.db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with closing(self._connect()) as conn, conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    title       TEXT    NOT NULL DEFAULT '新会话',
                    created_at  TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id  INTEGER NOT NULL,
                    role        TEXT    NOT NULL,
                    content     TEXT    NOT NULL,
                    trace       TEXT,
                    created_at  TEXT    NOT NULL DEFAULT (datetime('now', 'localtime'))
                )
                """
            )
            # 兼容旧库：messages 表缺少 trace 列时补上（trace 存工具调用轨迹 JSON）
            cols = {r[1] for r in conn.execute("PRAGMA table_info(messages)")}
            if "trace" not in cols:
                conn.execute("ALTER TABLE messages ADD COLUMN trace TEXT")

    # ---------------- 会话 ----------------
    def create_session(self, title: str = "新会话") -> int:
        """新建会话，返回会话 ID。"""
        with closing(self._connect()) as conn, conn:
            cur = conn.execute("INSERT INTO sessions (title) VALUES (?)", (title,))
            return cur.lastrowid

    def list_sessions(self) -> list[sqlite3.Row]:
        """按创建时间倒序列出全部会话。"""
        with closing(self._connect()) as conn:
            return conn.execute(
                "SELECT id, title, created_at FROM sessions ORDER BY id DESC"
            ).fetchall()

    # ---------------- 消息 ----------------
    def save_message(
        self, session_id: int, role: str, content: str, trace: str | None = None
    ):
        """保存一条消息（role: user / assistant）；trace 为可选的工具调用轨迹 JSON。"""
        with closing(self._connect()) as conn, conn:
            conn.execute(
                "INSERT INTO messages (session_id, role, content, trace) VALUES (?, ?, ?, ?)",
                (session_id, role, content, trace),
            )

    def load_history(
        self, session_id: int, max_turns: int = config.MAX_HISTORY_TURNS
    ) -> list[dict]:
        """
        读取最近 max_turns 轮的对话（1 轮 = user 问题 + assistant 回答）。
        :return: 按时间正序的 [{"role": ..., "content": ...}, ...]
        """
        limit = max_turns * 2 if max_turns else -1
        with closing(self._connect()) as conn:
            rows = conn.execute(
                """
                SELECT role, content FROM messages
                WHERE session_id = ? AND role IN ('user', 'assistant')
                ORDER BY id DESC LIMIT ?
                """,
                (session_id, limit),
            ).fetchall()
        rows = list(reversed(rows))  # 还原为时间正序
        return [{"role": r["role"], "content": r["content"]} for r in rows]

    def load_messages(self, session_id: int) -> list[dict]:
        """
        读取会话全部消息（含工具调用轨迹），供界面回放展示。
        :return: 按时间正序的 [{"role", "content", "trace"}, ...]；
                 trace 为轨迹列表（解析失败或无轨迹时为 None）。
        """
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT role, content, trace FROM messages WHERE session_id = ? ORDER BY id",
                (session_id,),
            ).fetchall()
        result = []
        for r in rows:
            trace = None
            if r["trace"]:
                try:
                    trace = json.loads(r["trace"])
                except json.JSONDecodeError:
                    trace = None
            result.append({"role": r["role"], "content": r["content"], "trace": trace})
        return result