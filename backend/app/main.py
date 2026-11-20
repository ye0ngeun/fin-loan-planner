"""FastAPI 앱. 실행: (backend 폴더에서) uvicorn app.main:app --reload

- 메인 Agent(PlannerService)와 로깅 Agent 는 이벤트 버스로만 연결된다. LOGGING_AGENT=0 이면 연결하지 않는다.
- 프론트엔드(../frontend)는 별도 폴더의 정적 파일이다. 편의상 같은 서버의 / 에서도 내보낸다.
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.agent.events import EventBus
from app.agent.events import bus as default_bus
from app.agent.llm import get_llm
from app.agent.logging_agent import LoggingAgent
from app.agent.service import PlannerService
from app.api.routes import chat_router, logs_router, meta_router, plans_router
from app.config import REPO_DIR, get_env, get_settings
from app.store.logs import LogStore
from app.store.plans import PlanStore
from app.tools import build_registry


def create_app(plans_db=None, logs_db=None, llm_factory=get_llm, event_bus: EventBus | None = None, attach_logger: bool | None = None) -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="불빛 하나 — 내집마련 대출 플래너 API",
        version="0.1.0",
        description="금감원 주택담보대출 공시·국토부 실거래가·카카오 로컬을 Tool 로 쓰는 Agent 플래너. 정보 제공용이며 금융 전문가의 조언을 대신하지 않습니다.",
    )
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    bus = event_bus or default_bus
    registry = build_registry()
    plans = PlanStore(plans_db or settings["paths"]["plans_db"])
    logs = LogStore(logs_db or settings["paths"]["logs_db"])
    logging_agent = LoggingAgent(logs)
    if attach_logger if attach_logger is not None else get_env("LOGGING_AGENT") != "0":
        logging_agent.attach(bus)

    app.state.registry = registry
    app.state.plans = plans
    app.state.logs = logs
    app.state.logging_agent = logging_agent
    app.state.planner = PlannerService(registry, plans, llm_factory, bus)

    for router in (chat_router, plans_router, logs_router, meta_router):
        app.include_router(router)

    frontend = REPO_DIR / "frontend"
    if frontend.exists():
        app.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")
    return app


app = create_app()
