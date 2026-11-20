"""FastAPI 경로, 로깅 Agent, 계획 관리, 화면 (F3013 ~ F3019, NF3001 ~ NF3003, NF3006)"""

import json

import pytest
from fastapi.testclient import TestClient

from app.agent.events import EventBus
from app.agent.llm import FakeLLM
from app.config import NOTICES, REPO_DIR
from app.main import create_app
from app.tools import http
from tests.conftest import PLAN_MSG, TEST_KEY


@pytest.fixture
def app(tmp_path):
    return create_app(plans_db=tmp_path / "plans.db", logs_db=tmp_path / "logs.db", llm_factory=FakeLLM, event_bus=EventBus(), attach_logger=True)


@pytest.fixture
def client(app):
    return TestClient(app)


def _chat(client, message=PLAN_MSG, sid=None):
    res = client.post("/api/chat", json={"message": message, "session_id": sid})
    assert res.status_code == 200, res.text
    return res.json()


# ── F3017 경로 분리와 형식 ───────────────────────────────────
def test_routes_are_separated(app):
    spec = app.openapi()
    paths = {p: set(m) for p, m in spec["paths"].items()}
    assert {"/api/chat", "/api/plans", "/api/plans/{plan_id}", "/api/logs", "/api/logs/{session_id}", "/api/tools", "/api/health"} <= set(paths)
    assert paths["/api/logs/{session_id}"] == {"get", "delete"} and paths["/api/logs"] == {"get"}  # 실행 로그 수정 API 없음 (F3014)
    schema = spec["components"]["schemas"]
    assert {"ChatRequest", "ChatResponse", "PlanCreate", "PlanSaveResult", "LogSession", "LogDetail"} <= set(schema)


def test_chat_response_shape(client):
    r = _chat(client)
    assert r["session_id"] and r["turn"] == 1 and r["intent"] == "plan"
    assert r["candidates"] and r["candidates"][0]["rank"] == 1
    assert r["trace"][0]["event"] == "session.start" and r["notices"] == NOTICES
    assert client.post("/api/chat", json={"message": ""}).status_code == 422


def test_tools_endpoint_and_health_hide_values(client, monkeypatch):
    tools = client.get("/api/tools").json()
    assert {t["kind"] for t in tools} == {"external", "local"}
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-value-123")
    h = client.get("/api/health")
    assert h.json()["keys"]["llm"] is True and "sk-secret-value-123" not in h.text


# ── F3013 로깅 Agent ────────────────────────────────────────
def test_logs_saved_per_session_in_order_as_emitted(client, app):
    r1 = _chat(client, sid="aaa")
    _chat(client, "안녕하세요", sid="bbb")
    d = client.get("/api/logs/aaa").json()["events"]
    assert [(e["event"], e["seq"], e["payload"]) for e in d] == [(e["event"], e["seq"], e["payload"]) for e in r1["trace"]]
    assert [e["seq"] for e in d] == sorted(e["seq"] for e in d)
    b = client.get("/api/logs/bbb").json()["events"]
    assert {e["event"] for e in b} >= {"session.start", "stage.enter", "decision", "session.end"}
    assert not {e["seq"] for e in b} & {e["seq"] for e in d}  # 세션끼리 섞이지 않음
    assert app.state.logging_agent.llm_calls == 0 and app.state.logging_agent.saved == len(d) + len(b)
    llm_decision = next(e for e in d if e["event"] == "decision" and e["payload"]["by"] == "llm")
    assert llm_decision["payload"]["intent"] == "plan" and llm_decision["payload"]["reason"]


def test_log_list_detail_delete(client):
    _chat(client, sid="s1")
    _chat(client, "월 190만원 이하로 바꿔줘", sid="s1")
    sessions = client.get("/api/logs").json()
    s = next(x for x in sessions if x["session_id"] == "s1")
    assert s["turns"] == 2 and s["tool_calls"] > 0 and s["first_message"].startswith("마포구")
    assert client.put("/api/logs/s1", json={}).status_code == 405
    assert client.delete("/api/logs/s1").json()["deleted"] > 0
    assert client.get("/api/logs/s1").status_code == 404


def test_logging_failure_does_not_break_chat(client, app):
    def broken(_msg):
        raise OSError("disk full")

    app.state.logs.append = broken
    assert _chat(client)["candidates"]
    assert app.state.logging_agent.failed > 0


def test_chat_works_without_logging_agent(tmp_path):
    app = create_app(plans_db=tmp_path / "p.db", logs_db=tmp_path / "l.db", llm_factory=FakeLLM, event_bus=EventBus(), attach_logger=False)
    c = TestClient(app)
    assert _chat(c)["candidates"]
    assert c.get("/api/logs").json() == []


# ── F3015 · F3016 계획 관리 ──────────────────────────────────
PLAN = {"title": "국민은행 KB주택담보대출 상담", "visit_date": "2026-11-21", "start_time": "14:00", "end_time": "15:00",
        "kor_co_nm": "국민은행", "fin_prdt_nm": "KB주택담보대출", "loan_amount_manwon": 40000, "term_years": 30, "monthly_payment_manwon": 189.8}


def test_plan_crud_and_overlap(client):
    r = client.post("/api/plans", json=PLAN)
    assert r.status_code == 201 and r.json()["overlaps"] == []
    p = r.json()["plan"]
    assert p["created_at"] and p["visit_date"] == "2026-11-21"
    r2 = client.post("/api/plans", json={**PLAN, "title": "다른 상담", "start_time": "14:30", "end_time": "15:30"}).json()
    assert r2["overlaps"][0]["id"] == p["id"] and "겹치는" in r2["message"]  # 저장은 되고 알림
    assert len(client.get("/api/plans").json()) == 2
    u = client.put(f"/api/plans/{r2['plan']['id']}", json={"start_time": "16:00", "end_time": "17:00", "memo": "서류 준비"}).json()
    assert u["overlaps"] == [] and u["plan"]["memo"] == "서류 준비" and u["plan"]["updated_at"] >= p["created_at"]
    assert client.get(f"/api/plans/{p['id']}").json()["title"] == PLAN["title"]
    assert client.delete(f"/api/plans/{p['id']}").json() == {"deleted": 1}
    assert client.get(f"/api/plans/{p['id']}").status_code == 404
    assert [x["id"] for x in client.get("/api/plans").json()] == [r2["plan"]["id"]]


def test_plan_validation(client):
    assert client.post("/api/plans", json={**PLAN, "visit_date": "내일"}).status_code == 422
    assert client.post("/api/plans", json={**PLAN, "end_time": "13:00"}).status_code == 422
    pid = client.post("/api/plans", json=PLAN).json()["plan"]["id"]
    assert client.put(f"/api/plans/{pid}", json={"end_time": "13:00"}).status_code == 422
    assert client.put("/api/plans/999", json={"memo": "x"}).status_code == 404


def test_chat_save_shows_in_plan_list(client):
    sid = _chat(client)["session_id"]
    _chat(client, "1번으로 2026-11-28 10:00 상담 저장해줘", sid)
    plans = client.get("/api/plans").json()
    assert plans and plans[0]["visit_date"] == "2026-11-28" and plans[0]["session_id"] == sid


# ── F3018 · F3019 화면 ──────────────────────────────────────
@pytest.mark.parametrize("page", ["index.html", "plans.html", "logs.html"])
def test_pages_have_notices_and_call_api(client, page):
    html = client.get(f"/{page}").text
    assert NOTICES["disclaimer"].split(".")[0] in html
    assert "금융상품통합비교공시시스템(금융감독원) 금융회사 상품정보" in html
    js = {"index.html": "chat.js", "plans.html": "plans.js", "logs.html": "logs.js"}[page]
    assert f'src="{js}"' in html and "/api/" in (REPO_DIR / "frontend" / js).read_text(encoding="utf-8")


def test_trace_marks_replan_and_failures(client, monkeypatch):
    monkeypatch.delenv("KAKAO_REST_API_KEY")
    r = _chat(client, "마포구 6억 아파트, 현금 2억 있어요. 고정금리로 월 180만원 이하 주담대 찾아줘")
    events = [e["event"] for e in r["trace"]]
    assert "replan" in events and events.index("replan") < len(events) - 1
    fail = next(e for e in r["trace"] if e["event"] == "tool.response" and e["payload"]["tool"] == "search_bank_branches")
    assert fail["payload"]["code"] == "INVALID_KEY"


# ── NF3001 · NF3002 키·오류 상세 노출 ─────────────────────────
def test_secret_never_reaches_client(client):
    def leaky(service, url, params, headers, timeout):
        raise RuntimeError(f"GET {url}?auth={params.get('auth') or params.get('serviceKey')} failed {headers}")

    http.set_transport(leaky)
    r = client.post("/api/chat", json={"message": PLAN_MSG})
    assert r.status_code == 200 and TEST_KEY not in r.text
    assert "Traceback" not in r.text


def test_no_keys_committed():
    example = (REPO_DIR / ".env.example").read_text(encoding="utf-8")
    for line in example.splitlines():
        if "=" in line and not line.startswith("#") and "KEY" in line.split("=")[0]:
            assert line.split("=", 1)[1].strip() == "", line
    gitignore = (REPO_DIR / ".gitignore").read_text(encoding="utf-8")
    assert ".env" in gitignore and "data/" in gitignore


# ── NF3006 모듈 분리 ─────────────────────────────────────────
def test_frontend_backend_and_agent_tool_files_are_separated():
    backend = REPO_DIR / "backend" / "app"
    assert (REPO_DIR / "frontend" / "index.html").exists()
    for rel in ["agent/graph.py", "agent/logging_agent.py", "agent/events.py", "tools/fss.py", "tools/molit.py", "tools/kakao.py", "tools/rag.py", "store/plans.py", "store/logs.py", "api/routes.py"]:
        assert (backend / rel).exists(), rel
    assert "logging_agent" not in (backend / "agent" / "graph.py").read_text(encoding="utf-8")
    json.loads(json.dumps(NOTICES))
