"""백엔드 API. 대화(/api/chat), 계획 관리(/api/plans), 실행 로그(/api/logs), Tool 명세(/api/tools)를 경로로 나눈다. (F3017)

실행 로그에는 수정(PUT/PATCH) 경로를 두지 않는다 (F3014).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.config import NOTICES, fake_mode, get_env, key_status
from app.schemas import (
    ChatRequest,
    ChatResponse,
    DeleteResult,
    HealthResponse,
    LogDetail,
    LogSession,
    Plan,
    PlanCreate,
    PlanSaveResult,
    PlanUpdate,
)

chat_router = APIRouter(prefix="/api/chat", tags=["대화"])
plans_router = APIRouter(prefix="/api/plans", tags=["계획 관리"])
logs_router = APIRouter(prefix="/api/logs", tags=["실행 로그"])
meta_router = APIRouter(prefix="/api", tags=["설정·명세"])


def _svc(request: Request):
    return request.app.state


# ── 대화 ─────────────────────────────────────────────────────
@chat_router.post("", response_model=ChatResponse, summary="질문 1건 실행")
def chat(body: ChatRequest, request: Request):
    return _svc(request).planner.chat(body.message.strip(), body.session_id)


@chat_router.delete("/{session_id}", response_model=DeleteResult, summary="대화 맥락 지우기 (실행 로그는 그대로)")
def reset_chat(session_id: str, request: Request):
    _svc(request).planner.reset(session_id)
    return {"deleted": 1}


# ── 계획 관리 ────────────────────────────────────────────────
def _overlap_message(overlaps: list[dict]) -> str:
    if not overlaps:
        return "저장했습니다."
    return "저장했습니다. 같은 날짜에 시간이 겹치는 계획이 있습니다: " + ", ".join(
        f"#{o['id']} {o['title']} {o['start_time']}~{o['end_time']}" for o in overlaps
    )


@plans_router.get("", response_model=list[Plan], summary="계획 목록")
def list_plans(request: Request):
    return _svc(request).plans.list()


@plans_router.post("", response_model=PlanSaveResult, status_code=201, summary="계획 저장 (겹치는 계획 안내)")
def create_plan(body: PlanCreate, request: Request):
    plan, overlaps = _svc(request).plans.create(body.model_dump())
    return {"plan": plan, "overlaps": overlaps, "message": _overlap_message(overlaps)}


@plans_router.get("/{plan_id}", response_model=Plan, summary="계획 상세")
def get_plan(plan_id: int, request: Request):
    plan = _svc(request).plans.get(plan_id)
    if plan is None:
        raise HTTPException(404, "계획을 찾을 수 없습니다.")
    return plan


@plans_router.put("/{plan_id}", response_model=PlanSaveResult, summary="계획 수정 (보낸 값만)")
def update_plan(plan_id: int, body: PlanUpdate, request: Request):
    changes = body.model_dump(exclude_unset=True)
    current = _svc(request).plans.get(plan_id)
    if current is None:
        raise HTTPException(404, "계획을 찾을 수 없습니다.")
    start, end = changes.get("start_time", current["start_time"]), changes.get("end_time", current["end_time"])
    if end <= start:
        raise HTTPException(422, "종료 시각은 시작 시각보다 늦어야 합니다.")
    plan, overlaps = _svc(request).plans.update(plan_id, changes)
    return {"plan": plan, "overlaps": overlaps, "message": _overlap_message(overlaps).replace("저장", "수정")}


@plans_router.delete("/{plan_id}", response_model=DeleteResult, summary="계획 삭제")
def delete_plan(plan_id: int, request: Request):
    if not _svc(request).plans.delete(plan_id):
        raise HTTPException(404, "계획을 찾을 수 없습니다.")
    return {"deleted": 1}


# ── 실행 로그 (목록·상세·삭제만) ─────────────────────────────
@logs_router.get("", response_model=list[LogSession], summary="실행 로그 세션 목록")
def list_logs(request: Request):
    return _svc(request).logs.list_sessions()


@logs_router.get("/{session_id}", response_model=LogDetail, summary="세션 실행 로그 상세 (실행 순서)")
def get_log(session_id: str, request: Request):
    events = _svc(request).logs.get_session(session_id)
    if not events:
        raise HTTPException(404, "실행 로그를 찾을 수 없습니다.")
    return {"session_id": session_id, "events": events}


@logs_router.delete("/{session_id}", response_model=DeleteResult, summary="세션 실행 로그 삭제")
def delete_log(session_id: str, request: Request):
    n = _svc(request).logs.delete_session(session_id)
    if not n:
        raise HTTPException(404, "실행 로그를 찾을 수 없습니다.")
    return {"deleted": n}


# ── 설정·명세 ────────────────────────────────────────────────
@meta_router.get("/health", response_model=HealthResponse, summary="키 설정 여부와 연결 상태")
def health(request: Request):
    return {
        "status": "ok",
        "keys": key_status(),
        "fake_apis": fake_mode(),
        "fake_llm": get_env("PLANNER_FAKE_LLM") in ("1", "true", "yes"),
        "logging_agent": _svc(request).logging_agent.attached,
        "notices": NOTICES,
    }


@meta_router.get("/tools", summary="등록된 Tool 명세 (외부 API 툴 / 로컬 서버 API 툴)")
def list_tools(request: Request):
    return _svc(request).registry.cards()
