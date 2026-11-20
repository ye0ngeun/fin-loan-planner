"""실행 이벤트 발행과 구독. (F3012)

메인 Agent 는 emit(...) 만 부른다. 누가 구독하는지 모르고, 구독자가 없거나 고장 나도 그대로 진행한다.

이벤트 종류
- session.start / session.end      요청 1건의 시작과 끝 (질문, 최종 의도, 재계획 수, 소요 시간)
- stage.enter / stage.exit         단계(analyze, plan, execute, judge, replan, respond) 진입·종료
- tool.request / tool.response     Tool 호출 요청과 응답(ok, 실패 코드, 소요 ms, 요약)
- decision                         실행 흐름을 바꾸는 판단과 이유 (by: llm | rule)
- replan                           재계획 (회차, 전략, 이유)
- error                            단계 실행 중 오류 (사용자에게는 안내만)
"""

from __future__ import annotations

import itertools
import threading
from datetime import datetime
from typing import Callable

Subscriber = Callable[[dict], None]


class EventBus:
    def __init__(self) -> None:
        self._subs: list[Subscriber] = []
        self._seq = itertools.count(1)
        self._lock = threading.Lock()

    def subscribe(self, fn: Subscriber) -> None:
        if fn not in self._subs:
            self._subs.append(fn)

    def unsubscribe(self, fn: Subscriber) -> None:
        if fn in self._subs:
            self._subs.remove(fn)

    @property
    def subscriber_count(self) -> int:
        return len(self._subs)

    def publish(self, event: str, session_id: str, turn: int, **payload) -> dict:
        with self._lock:
            seq = next(self._seq)
        msg = {
            "event": event,
            "session_id": session_id,
            "turn": turn,
            "seq": seq,
            "ts": datetime.now().isoformat(timespec="milliseconds"),
            "payload": payload,
        }
        for fn in list(self._subs):
            try:
                fn(msg)
            except Exception:  # noqa: BLE001 — 구독자가 고장 나도 메인 흐름은 계속
                pass
        return msg


bus = EventBus()


class Emitter:
    """한 요청(세션·턴)에 묶인 발행기. 발행한 이벤트를 따로 모아 화면 응답(trace)에도 쓴다."""

    def __init__(self, session_id: str, turn: int, event_bus: EventBus | None = None) -> None:
        self.session_id = session_id
        self.turn = turn
        self.bus = event_bus or bus
        self.trace: list[dict] = []

    def __call__(self, event: str, **payload) -> dict:
        msg = self.bus.publish(event, self.session_id, self.turn, **payload)
        self.trace.append(msg)
        return msg
