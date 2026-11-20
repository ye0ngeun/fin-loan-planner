"""메인 Agent 실행 흐름과 이벤트 발행 (F3005 ~ F3012, F3022, NF3003, NF3004)"""

from collections import Counter

from app.agent.events import EventBus
from app.agent.graph import make_graph
from app.agent.llm import FakeLLM, LLMKeyMissing
from app.agent.service import PlannerService
from app.store.plans import PlanStore
from app.tools import rag
from tests.conftest import PLAN_MSG, TODAY


def _tools(r):
    return [c["tool"] for c in r["view"]["calls"]]


def _decision(trace, stage):
    return next(e["payload"] for e in trace if e["event"] == "decision" and e["payload"]["stage"] == stage)


# ── F3005 요청 분석 ──────────────────────────────────────────
def test_extracts_base_preference_constraint(service):
    r = service.chat(PLAN_MSG + " 고정금리 선호", "s")
    c = r["view"]["conditions"]
    assert c["기준"] == {"집값(만원)": 60000.0, "지역(서울 구)": "마포구", "보유 자금(만원)": 20000.0, "대출 기간(년)": 30}
    assert c["선호"] == {"금리 유형": "고정금리"} and c["제약"] == {"월 상환액 상한(만원)": 200.0}
    d = _decision(r["trace"], "analyze")
    assert d["by"] == "llm" and d["intent"] == "plan" and d["reason"]
    assert "term_years" not in d["extracted"]  # 말하지 않은 기간은 뽑지 않고, 계산 기본값은 따로 밝힌다
    assert any("기본값" in x for x in r["view"]["defaults"])


def test_missing_conditions_are_asked_not_filled(service):
    r = service.chat("월 150만원 이하로 갚을 주담대 찾아줘", "s")
    assert _tools(r) == [] and r["view"]["missing"]
    assert "보유 자금" in r["answer"]
    assert "price_manwon" not in service.memory("s").conditions


# ── F3006 작업 계획 ──────────────────────────────────────────
def test_plan_orders_tools_and_skips_for_greeting(service):
    r = service.chat(PLAN_MSG, "a")
    steps = [s["tool"] for s in _decision(r["trace"], "plan")["steps"]]
    assert steps == ["get_apartment_prices", "search_mortgage_products", "get_mortgage_product_detail", "search_bank_branches"]
    g = service.chat("안녕하세요", "b")
    assert g["intent"] == "other" and _tools(g) == [] and _decision(g["trace"], "plan")["steps"] == []
    assert not [e for e in g["trace"] if e["event"] == "stage.enter" and e["payload"]["stage"] == "execute"]


def test_graph_nodes():
    graph = make_graph(None, FakeLLM, lambda *a, **k: {}, None)
    assert {"analyze", "plan", "execute", "judge", "replan", "respond"} <= set(graph.get_graph().nodes)


# ── F3007 결과 연결과 실패 처리 ───────────────────────────────
def test_search_ids_feed_detail_calls(service):
    r = service.chat(PLAN_MSG, "s")
    calls = r["view"]["calls"]
    search = next(c for c in calls if c["tool"] == "search_mortgage_products")
    details = [c["args"]["product_id"] for c in calls if c["tool"] == "get_mortgage_product_detail"]
    assert len(details) == 5 and all(pid.startswith("020000:") for pid in details) and search["step"] < calls.index(search) + 3
    branch = next(c for c in calls if c["tool"] == "search_bank_branches")
    assert branch["args"]["bank_name"] == "국민은행"  # 판단 결과의 1순위 회사가 지점 검색 입력이 된다


def test_one_tool_failure_does_not_stop(service, monkeypatch):
    monkeypatch.delenv("KAKAO_REST_API_KEY")
    r = service.chat(PLAN_MSG, "s")
    failed = [c for c in r["view"]["calls"] if not c["ok"]]
    assert {c["tool"] for c in failed} == {"get_mortgage_product_detail", "search_bank_branches"}
    assert r["view"]["judged"]["satisfied"] and "은행 지점 검색 실패" in r["answer"]
    assert "공시 금리가 없어 판단하지 않음" in r["answer"]  # 실패한 상세 조회는 사유를 남기고 진행
    assert service.chat("안녕", "s")["answer"]  # 다음 요청도 정상


def test_fatal_key_error_stops_without_replan(service, monkeypatch):
    monkeypatch.delenv("FSS_API_KEY")
    r = service.chat(PLAN_MSG, "s")
    assert r["view"]["replans"] == [] and r["view"]["fatal"]["code"] == "INVALID_KEY"
    assert "FSS_API_KEY" in r["answer"] and "재계획" in r["answer"]


# ── F3008 제약 판단과 역산 ───────────────────────────────────
def test_judgement_values_match_answer(service):
    r = service.chat(PLAN_MSG, "s")
    j = r["view"]["judged"]
    assert j["principal"] == 40000 and j["payment_cap"] == 200
    top = j["satisfied"][0]
    assert top["option"]["monthly_payment"] <= 200
    assert f"{top['option']['monthly_payment']:,.1f}만원" in r["answer"] and "4억원" in r["answer"]


def test_reverse_basis_when_nothing_fits(service):
    r = service.chat("마포구 6억 아파트, 현금 2억 있어요. 월 100만원 이하로 갚을 주담대 찾아줘", "s")
    assert "역산 기준" in r["answer"] and "금리가 0%여도 상한을 넘습니다" in r["answer"]  # 4억·30년은 0%여도 월 111만원
    assert "빌릴 수 있는 대출금" in r["answer"]
    r = service.chat("월 185만원 이하로 바꿔줘", "s")
    assert r["view"]["judged"]["satisfied"]  # 보험 권역까지 넓히면 충족
    r = service.chat("월 170만원 이하로 바꿔줘", "s")
    assert "연 3.06% 이하여야 합니다" in r["answer"]
    assert r["view"]["judged"]["reverse"]["max_principal_at_best_rate"] > 0


# ── F3009 재계획 ─────────────────────────────────────────────
def test_replan_reuses_results(service):
    r = service.chat("마포구 6억 아파트, 현금 2억 있어요. 고정금리로 월 180만원 이하 주담대 찾아줘", "s")
    replans = r["view"]["replans"]
    assert [x["strategy"] for x in replans] == ["relax_rate_type", "add_group:050000"]
    assert r["view"]["judged"]["satisfied"][0]["group_nm"] == "보험"
    sigs = Counter((c["tool"], str(sorted(c["args"].items()))) for c in r["view"]["calls"] if not c["reused"])
    assert max(sigs.values()) == 1  # 같은 입력으로 다시 부른 Tool 이 없다
    assert sum(1 for e in r["trace"] if e["event"] == "replan") == 2


def test_replan_limit(service):
    r = service.chat("마포구 6억 아파트, 현금 2억 있어요. 월 100만원 이하로 갚을 주담대 찾아줘", "s")
    assert len(r["view"]["replans"]) == 3
    assert "상한(3회)" in r["view"]["stop_reason"] and "상한(3회)" in r["answer"]
    assert "조정" in r["answer"]


# ── F3010 대화 맥락 ──────────────────────────────────────────
def test_follow_up_updates_only_changed(service):
    service.chat(PLAN_MSG, "s")
    before = dict(service.memory("s").conditions)
    r = service.chat("월 190만원 이하로 바꿔서 다시 봐줘", "s")
    after = service.memory("s").conditions
    assert after["max_monthly_payment_manwon"] == 190
    assert {k: v for k, v in after.items() if k != "max_monthly_payment_manwon"} == {k: v for k, v in before.items() if k != "max_monthly_payment_manwon"}
    assert r["view"]["changed"] == ["월 상환액 상한(만원)"]
    assert service.memory("other").conditions == {}  # 세션끼리 섞이지 않음


# ── F3011 응답 ───────────────────────────────────────────────
def test_answer_has_basis_evidence_and_exclusions(service):
    r = service.chat(PLAN_MSG, "s")
    a = r["answer"]
    assert "판단 기준" in a and "근거: 금감원 공시" in a and "제외한 후보" in a and "상담 지점 후보" in a
    assert r["notices"]["disclaimer"].startswith("본 서비스는 정보 제공용")


# ── 계획 확정(저장은 Tool 이 아니라 흐름이 직접) ─────────────────
def test_save_from_chat_and_overlap(service):
    service.chat(PLAN_MSG, "s")
    r1 = service.chat("1번으로 토요일 오후 2시 상담 저장해줘", "s")
    assert r1["intent"] == "save" and _tools(r1) == []
    p = r1["view"]["saved_plan"]["plan"]
    assert (p["visit_date"], p["start_time"], p["end_time"]) == ("2026-11-21", "14:00", "15:00")
    assert p["kor_co_nm"] == "국민은행" and p["monthly_payment_manwon"] <= 200
    r2 = service.chat("2번으로 토요일 오후 2시 30분 저장해줘", "s")
    assert r2["view"]["saved_plan"]["overlaps"][0]["id"] == p["id"] and "겹치는" in r2["answer"]


def test_save_without_time_asks(service):
    service.chat(PLAN_MSG, "s")
    assert "상담 날짜" in service.chat("1번 저장해줘", "s")["answer"]


# ── F3012 이벤트 ─────────────────────────────────────────────
def test_events_cover_stages_tools_and_decisions(service, events):
    service.chat(PLAN_MSG, "s1")
    kinds = Counter(e["event"] for e in events)
    stages = {e["payload"]["stage"] for e in events if e["event"] == "stage.enter"}
    assert {"analyze", "plan", "execute", "judge", "respond"} <= stages
    assert kinds["stage.enter"] == kinds["stage.exit"] and kinds["tool.request"] == kinds["tool.response"] > 0
    assert all("elapsed_ms" in e["payload"] for e in events if e["event"] == "tool.response")
    assert all(e["session_id"] == "s1" for e in events)
    assert [e["seq"] for e in events] == sorted(e["seq"] for e in events)
    assert events[0]["event"] == "session.start" and events[-1]["event"] == "session.end"


def test_publisher_does_not_depend_on_subscribers(registry):
    lonely = PlannerService(registry, PlanStore(":memory:"), FakeLLM, EventBus(), today=lambda: TODAY)
    assert lonely.bus.subscriber_count == 0
    assert lonely.chat(PLAN_MSG, "s")["view"]["judged"]["satisfied"]
    broken = EventBus()
    broken.subscribe(lambda m: 1 / 0)
    svc = PlannerService(registry, PlanStore(":memory:"), FakeLLM, broken, today=lambda: TODAY)
    assert svc.chat(PLAN_MSG, "s")["view"]["judged"]["satisfied"]


# ── F3019 키 누락 안내 ───────────────────────────────────────
def test_llm_key_missing_is_reported(registry, bus):
    def no_key():
        raise LLMKeyMissing("x")

    svc = PlannerService(registry, PlanStore(":memory:"), no_key, bus, today=lambda: TODAY)
    r = svc.chat(PLAN_MSG, "s")
    assert r["error"]["code"] == "LLM_KEY_MISSING" and "설정 안내" in r["answer"]
    assert svc.memory("s").conditions == {}


# ── 사실 조회 ────────────────────────────────────────────────
def test_fact_prices_only(service):
    r = service.chat("마포구 아파트 시세 알려줘", "s")
    assert r["intent"] == "fact" and _tools(r) == ["get_apartment_prices"] and "중위" in r["answer"]


# ── 심화 F3022 ───────────────────────────────────────────────
def test_term_question_uses_rag_with_cited_sources(service):
    rag.set_searcher(lambda q, k: [("LTV는 주택 가격 대비 대출 비율이다.", {"title": "LTV", "source": "주택금융공사 단어사전", "source_url": "https://x"}, 0.9),
                                   ("DSR 설명", {"title": "DSR", "source": "위키백과", "source_url": "https://y"}, 0.8)])
    r = service.chat("LTV가 뭐야?", "s")
    assert r["intent"] == "term" and _tools(r) == ["search_loan_docs"]
    assert [s["title"] for s in r["view"]["sources"]] == ["LTV"]  # 인용한 근거만 출처로


def test_term_without_index(service, monkeypatch, tmp_path):
    from app.config import get_settings

    monkeypatch.setitem(get_settings()["rag"], "chroma_dir", tmp_path / "none")
    r = service.chat("LTV가 뭐야?", "s")
    assert r["view"]["calls"][0]["code"] == "NO_INDEX" and "아직 모으지 못했습니다" in r["answer"]
