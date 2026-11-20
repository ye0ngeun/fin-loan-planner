"""실행 로그 저장소 (SQLite). 세션 단위로 이벤트를 발생 순서대로 덧붙인다. (F3013, F3014)

수정 기능은 두지 않는다. 기록을 고칠 수 있으면 관찰의 의미가 사라진다. 목록·상세·삭제만 제공한다.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS run_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    turn INTEGER NOT NULL,
    seq INTEGER NOT NULL,
    ts TEXT NOT NULL,
    event TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_run_events_session ON run_events(session_id, id);
"""


class LogStore:
    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)

    def append(self, msg: dict) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO run_events (session_id, turn, seq, ts, event, payload) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    msg["session_id"],
                    msg.get("turn", 0),
                    msg.get("seq", 0),
                    msg["ts"],
                    msg["event"],
                    json.dumps(msg.get("payload", {}), ensure_ascii=False, default=str),
                ),
            )
            self._conn.commit()

    def list_sessions(self) -> list[dict]:
        rows = self._conn.execute(
            """
            SELECT session_id, MIN(ts) AS started_at, MAX(ts) AS last_at, COUNT(*) AS events,
                   MAX(turn) AS turns,
                   SUM(event = 'tool.request') AS tool_calls,
                   SUM(event = 'replan') AS replans,
                   SUM(event = 'tool.response' AND json_extract(payload, '$.ok') = 0) AS tool_failures
            FROM run_events GROUP BY session_id ORDER BY MAX(id) DESC
            """
        ).fetchall()
        out = []
        for r in rows:
            first = self._conn.execute(
                "SELECT payload FROM run_events WHERE session_id = ? AND event = 'session.start' ORDER BY id LIMIT 1",
                (r["session_id"],),
            ).fetchone()
            message = json.loads(first["payload"]).get("message", "") if first else ""
            out.append({**dict(r), "first_message": message[:80]})
        return out

    def get_session(self, session_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT turn, seq, ts, event, payload FROM run_events WHERE session_id = ? ORDER BY id", (session_id,)
        ).fetchall()
        return [{**dict(r), "payload": json.loads(r["payload"])} for r in rows]

    def delete_session(self, session_id: str) -> int:
        with self._lock:
            cur = self._conn.execute("DELETE FROM run_events WHERE session_id = ?", (session_id,))
            self._conn.commit()
            return cur.rowcount
