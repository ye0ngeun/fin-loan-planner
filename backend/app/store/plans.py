"""상담 계획 저장소 (SQLite). 저장·목록·상세·수정·삭제. (F3015, F3016)

계획의 핵심 값: 상품(금융회사·상품명·식별자), 상담 날짜, 시작·종료 시각, 대출금·기간·금리·월 상환액, 생성 일시.
같은 날짜에 시간이 겹치는 계획이 있어도 저장은 막지 않고, 겹치는 계획을 결과와 함께 돌려준다.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime
from pathlib import Path

FIELDS = (
    "title", "product_id", "kor_co_nm", "fin_prdt_nm", "branch_name", "branch_address",
    "visit_date", "start_time", "end_time", "loan_amount_manwon", "term_years",
    "rate_type", "rpay_type", "annual_rate", "monthly_payment_manwon", "memo", "session_id",
)
EDITABLE = tuple(f for f in FIELDS if f != "session_id")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    product_id TEXT, kor_co_nm TEXT, fin_prdt_nm TEXT,
    branch_name TEXT, branch_address TEXT,
    visit_date TEXT NOT NULL, start_time TEXT NOT NULL, end_time TEXT NOT NULL,
    loan_amount_manwon REAL, term_years INTEGER,
    rate_type TEXT, rpay_type TEXT, annual_rate REAL, monthly_payment_manwon REAL,
    memo TEXT, session_id TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
"""


def _overlaps(a_start: str, a_end: str, b_start: str, b_end: str) -> bool:
    return a_start < b_end and b_start < a_end  # "HH:MM" 문자열 비교


class PlanStore:
    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)

    def _row(self, plan_id: int) -> dict | None:
        r = self._conn.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
        return dict(r) if r else None

    def find_overlaps(self, visit_date: str, start: str, end: str, exclude_id: int | None = None) -> list[dict]:
        rows = self._conn.execute("SELECT * FROM plans WHERE visit_date = ? ORDER BY start_time", (visit_date,)).fetchall()
        return [
            {k: r[k] for k in ("id", "title", "visit_date", "start_time", "end_time")}
            for r in rows
            if r["id"] != exclude_id and _overlaps(start, end, r["start_time"], r["end_time"])
        ]

    def create(self, data: dict) -> tuple[dict, list[dict]]:
        now = datetime.now().isoformat(timespec="seconds")
        values = {f: data.get(f) for f in FIELDS}
        overlaps = self.find_overlaps(values["visit_date"], values["start_time"], values["end_time"])
        with self._lock:
            cols = ", ".join(FIELDS)
            marks = ", ".join("?" for _ in FIELDS)
            cur = self._conn.execute(
                f"INSERT INTO plans ({cols}, created_at, updated_at) VALUES ({marks}, ?, ?)",
                (*[values[f] for f in FIELDS], now, now),
            )
            self._conn.commit()
        return self._row(cur.lastrowid), overlaps

    def list(self) -> list[dict]:
        rows = self._conn.execute("SELECT * FROM plans ORDER BY visit_date, start_time, id").fetchall()
        return [dict(r) for r in rows]

    def get(self, plan_id: int) -> dict | None:
        return self._row(plan_id)

    def update(self, plan_id: int, changes: dict) -> tuple[dict | None, list[dict]]:
        current = self._row(plan_id)
        if current is None:
            return None, []
        changes = {k: v for k, v in changes.items() if k in EDITABLE and v is not None}
        if changes:
            sets = ", ".join(f"{k} = ?" for k in changes)
            with self._lock:
                self._conn.execute(
                    f"UPDATE plans SET {sets}, updated_at = ? WHERE id = ?",
                    (*changes.values(), datetime.now().isoformat(timespec="seconds"), plan_id),
                )
                self._conn.commit()
        updated = self._row(plan_id)
        overlaps = self.find_overlaps(updated["visit_date"], updated["start_time"], updated["end_time"], exclude_id=plan_id)
        return updated, overlaps

    def delete(self, plan_id: int) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM plans WHERE id = ?", (plan_id,))
            self._conn.commit()
            return cur.rowcount > 0
