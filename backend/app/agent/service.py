"""요청 1건 실행과 대화 맥락(Memory) 관리. (F3010, F3012)

맥락에 보관하는 것: 병합된 조건, 직전 추천 요약(순번·상품·금리·월 상환액·지점), 최근 대화 몇 턴의 짧은 글.
보관하지 않는 것: Tool 원본 응답(요약만 남김), API 키, 오류 상세.
"""

from __future__ import annotations

import threading
import time
import uuid
from datetime import date
from typing import Any, Callable

from app.agent.events import Emitter, EventBus
from app.agent.events import bus as default_bus
from app.agent.graph import make_graph
from app.agent.llm import get_llm
from app.config import NOTICES, get_settings
from app.tools.base import ToolRegistry


class Memory:
    def __init__(self) -> None:
        self.conditions: dict = {}
        self.candidates: list[dict] = []
        self.history: list[dict] = []
        self.turn = 0

    def context(self) -> dict:
        return {"conditions": dict(self.conditions), "candidates": list(self.candidates), "history": list(self.history)}


class PlannerService:
    def __init__(
        self,
        registry: ToolRegistry,
        plan_store=None,
        llm_factory: Callable[[], Any] = get_llm,
        event_bus: EventBus | None = None,
        today: Callable[[], date] = date.today,
    ) -> None:
        self.registry = registry
        self.plan_store = plan_store
        self.llm_factory = llm_factory
        self.bus = event_bus or default_bus
        self.today = today
        self.sessions: dict[str, Memory] = {}
        self._lock = threading.Lock()

    def memory(self, session_id: str) -> Memory:
        with self._lock:
            return self.sessions.setdefault(session_id, Memory())

    def reset(self, session_id: str) -> None:
        with self._lock:
            self.sessions.pop(session_id, None)

    def chat(self, message: str, session_id: str | None = None) -> dict:
        session_id = session_id or uuid.uuid4().hex[:12]
        mem = self.memory(session_id)
        mem.turn += 1
        emit = Emitter(session_id, mem.turn, self.bus)
        t0 = time.perf_counter()
        emit("session.start", message=message, context_conditions=mem.conditions)
        graph = make_graph(self.registry, self.llm_factory, emit, self.plan_store)
        state = graph.invoke(
            {"session_id": session_id, "turn": mem.turn, "message": message, "today": self.today().isoformat(), "context": mem.context()},
            {"recursion_limit": 60},
        )
        view = state.get("view") or {}
        answer = state.get("answer") or ""
        self._remember(mem, message, answer, state, view)
        elapsed = round((time.perf_counter() - t0) * 1000)
        emit("session.end", intent=state.get("intent"), replans=len(state.get("replans") or []),
             tool_calls=len([c for c in state.get("calls") or [] if not c.get("reused")]), elapsed_ms=elapsed,
             answer_preview=answer[:80])
        return {
            "session_id": session_id,
            "turn": mem.turn,
            "answer": answer,
            "intent": state.get("intent"),
            "view": view,
            "candidates": mem.candidates if state.get("intent") == "plan" else [],
            "trace": emit.trace,
            "elapsed_ms": elapsed,
            "notices": NOTICES,
            "error": view.get("error"),
        }

    def _remember(self, mem: Memory, message: str, answer: str, state: dict, view: dict) -> None:
        if not state.get("error"):
            mem.conditions = dict(state.get("conditions") or mem.conditions)
        judged = view.get("judged") or {}
        if state.get("intent") == "plan" and judged.get("principal") is not None:
            branches = (view.get("branches") or {}).get("branches") or []
            show = get_settings()["agent"]["show_top"]
            mem.candidates = [
                {
                    "rank": i,
                    "product_id": e["product_id"],
                    "name": e["name"],
                    "kor_co_nm": e.get("kor_co_nm"),
                    "fin_prdt_nm": e.get("fin_prdt_nm"),
                    "principal": judged["principal"],
                    "term_years": judged["term_years"],
                    "rate_type": e["option"]["lend_rate_type_nm"],
                    "rpay_type": e["option"]["rpay_type_nm"],
                    "rate": e["option"]["rate_used"],
                    "monthly_payment": e["option"]["monthly_payment"],
                    "branch_name": branches[0]["name"] if i == 1 and branches else None,
                    "branch_address": branches[0]["address"] if i == 1 and branches else None,
                }
                for i, e in enumerate((judged.get("satisfied") or [])[:show], 1)
            ]
        mem.history = (mem.history + [{"role": "user", "text": message[:200]}, {"role": "assistant", "text": answer[:200]}])[
            -2 * get_settings()["agent"]["memory_turns"]:
        ]
