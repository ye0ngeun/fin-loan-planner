"""로깅 서브 Agent. 이벤트를 구독해 세션 단위 실행 로그로 저장한다. (F3013)

- 수집과 저장에 LLM 을 쓰지 않는다. 받은 이벤트를 가공·요약하지 않고 그대로 남긴다.
- 저장에 실패해도 예외를 메인 Agent 로 넘기지 않는다 (실패 수만 센다).
- 메인 Agent 는 이 모듈을 import 하지 않는다. 연결은 서버 시작 시 attach() 한 줄이다.
"""

from __future__ import annotations

from app.agent.events import EventBus
from app.store.logs import LogStore


class LoggingAgent:
    llm_calls = 0  # 수집·저장 경로에서 LLM 을 부르지 않는다 (검증용 표시)

    def __init__(self, store: LogStore) -> None:
        self.store = store
        self.saved = 0
        self.failed = 0
        self._bus: EventBus | None = None

    def on_event(self, msg: dict) -> None:
        try:
            self.store.append(msg)
            self.saved += 1
        except Exception:  # noqa: BLE001 — 저장 실패가 대화를 막지 않는다
            self.failed += 1

    def attach(self, bus: EventBus) -> "LoggingAgent":
        bus.subscribe(self.on_event)
        self._bus = bus
        return self

    def detach(self) -> None:
        if self._bus is not None:
            self._bus.unsubscribe(self.on_event)
            self._bus = None

    @property
    def attached(self) -> bool:
        return self._bus is not None
