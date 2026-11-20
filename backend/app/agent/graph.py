"""메인 Agent 실행 흐름 (LangGraph). (F3005 ~ F3011)

    START → analyze → plan ─┬─(Tool 필요)→ execute → judge ─┬─(충족 후보 없음)→ replan → execute …
                            │                               ├─(지점 검색 대기)→ execute
                            └─(Tool 불필요)────────────────→ respond ←─(그 밖)─┘
                                                              ↓
                                                             END

- analyze (LLM): 의도·이유·조건·필요한 Tool 을 뽑는다. 조건은 대화 맥락에 바뀐 값만 덮어쓴다.
- plan    (코드): LLM 이 고른 Tool 을 명세와 조건으로 검증해 호출 순서·인자 연결이 있는 작업 계획으로 만든다.
- execute (코드): 계획대로 Tool 을 부른다. 앞 Tool 결과를 다음 입력으로 잇고, 이미 받은 결과는 다시 부르지 않는다.
- judge   (코드): 공시 금리로 월 상환액을 계산해 제약과 비교하고, 다음 단계를 규칙으로 정한다.
- replan  (코드): 충족 후보가 없을 때 설정된 대안(선호 조건 완화 → 권역 확대)을 하나 적용한다. 상한 agent.max_replans.
- respond (코드, other·term 만 LLM): 추천·판단 기준·근거·제외 사유를 숫자 그대로 보여 준다.
"""

from __future__ import annotations

import json
import re
import time
from datetime import date, datetime, timedelta
from typing import Any, Callable, TypedDict

from langgraph.graph import END, START, StateGraph

from app.agent import conditions as C
from app.agent import judge as J
from app.agent.llm import LLMKeyMissing
from app.config import NOTICES, get_settings
from app.tools.base import FATAL_CODES, ToolRegistry

INTENTS = ("plan", "fact", "save", "term", "other")
SEARCH, DETAIL, PRICES, BRANCHES, DOCS = (
    "search_mortgage_products",
    "get_mortgage_product_detail",
    "get_apartment_prices",
    "search_bank_branches",
    "search_loan_docs",
)


TOOL_LABELS = {
    SEARCH: "상품 후보 조회",
    DETAIL: "상품 상세 조회",
    PRICES: "실거래 시세 조회",
    BRANCHES: "은행 지점 검색",
    DOCS: "용어 문서 검색",
}


class State(TypedDict, total=False):
    session_id: str
    turn: int
    message: str
    today: str
    context: dict  # 대화 맥락: conditions, candidates, history
    intent: str
    reason: str
    llm_tools: list[str]
    target_rank: int
    question: str
    conditions: dict
    changed: list[str]
    defaults: list[str]
    missing: list[str]
    steps: list[dict]
    results: dict  # 호출 서명 → Tool 응답 (재계획 때 재사용)
    calls: list[dict]  # 실행 순서대로 Tool 호출 이력
    products: list[dict]
    groups_searched: list[str]
    judged: dict
    next: str
    replan_count: int
    replans: list[dict]
    replan_used: list[str]
    relaxed: bool
    stop_reason: str
    fatal: dict
    error: dict
    answer: str
    view: dict


def _sig(tool: str, args: dict) -> str:
    return f"{tool}:{json.dumps(args, ensure_ascii=False, sort_keys=True)}"


def _summary(tool: str, result: dict) -> str:
    """Tool 응답 요약 (원본은 결과 저장소에만, 이벤트·맥락에는 요약만 남긴다)."""
    if not result.get("ok"):
        return f"{result['error']['code']}: {result['error']['message']}"
    d = result["data"]
    if tool == SEARCH:
        return f"후보 {d['count']}건 (권역 {','.join(d['basis']['groups'])}, 공시월 {d['basis']['dcls_month']})" + (" · 추정" if d["is_estimated"] else "")
    if tool == DETAIL:
        return f"{d['kor_co_nm']} {d['fin_prdt_nm']}: 금리 옵션 {len(d['options'])}개"
    if tool == PRICES:
        return f"{d['basis']['region']} 거래 {d['count']}건, 중위 {d['median_manwon']:,}만원" + (" · 추정" if d["is_estimated"] else "")
    if tool == BRANCHES:
        return f"지점 {d['count']}곳 ('{d['query']}')"
    if tool == DOCS:
        return f"문서 {d['count']}건"
    return "ok"


def _fmt(n: float | None, unit: str = "만원") -> str:
    if n is None:
        return "-"
    return f"{n:.2f}%" if unit == "%" else f"{n:,.1f}{unit}"


def _eok(manwon: float | None) -> str:
    if manwon is None:
        return "-"
    eok, rest = divmod(round(manwon), 10000)
    if not eok:
        return f"{rest:,}만원"
    return f"{eok}억 {rest:,}만원" if rest else f"{eok}억원"


def _short_company(name: str) -> str:
    """지점 검색어용 회사 이름: '농협은행주식회사' → '농협은행', '삼성생명보험주식회사' → '삼성생명'"""
    name = (name or "").replace("주식회사", "").replace("(주)", "").strip()
    return name[:-2] if name.endswith("생명보험") or name.endswith("화재보험") else name


def make_graph(registry: ToolRegistry, llm_factory: Callable[[], Any], emit: Callable[..., dict], plan_store=None):
    settings = get_settings()
    acfg, fcfg = settings["agent"], settings["fss"]
    llm_holder: dict = {}

    def llm():
        if "llm" not in llm_holder:
            llm_holder["llm"] = llm_factory()
        return llm_holder["llm"]

    # ── ① analyze ─────────────────────────────────────────────
    def analyze(s: State) -> dict:
        ctx = s.get("context") or {}
        try:
            raw = llm().analyze(s["message"], ctx, registry.specs(), date.fromisoformat(s["today"]))
        except LLMKeyMissing:
            return {"error": {"code": "LLM_KEY_MISSING", "message": "LLM 키(OPENAI_API_KEY)가 설정되지 않았습니다."}, "intent": "other"}
        except Exception as e:  # noqa: BLE001
            return {"error": {"code": "LLM_ERROR", "message": type(e).__name__}, "intent": "other"}
        intent = raw.get("intent") if raw.get("intent") in INTENTS else "other"
        changes = C.clean(raw.get("conditions"))
        cleared = [k for k in raw.get("cleared") or [] if k in C.ALL_FIELDS]
        merged, changed = C.merge(ctx.get("conditions") or {}, changes, cleared)
        tools = [t for t in raw.get("tools") or [] if isinstance(t, str)]
        out = {
            "intent": intent,
            "reason": str(raw.get("reason") or "")[:200],
            "llm_tools": tools,
            "conditions": merged,
            "changed": changed,
            "target_rank": int(raw.get("target_rank") or 1) if str(raw.get("target_rank") or "1").isdigit() else 1,
            "question": str(raw.get("question") or s["message"]),
        }
        emit("decision", stage="analyze", by="llm", kind="intent", intent=intent, reason=out["reason"],
             tools=tools, extracted=changes, changed=changed, conditions=C.split(merged))
        return out

    # ── ② plan ────────────────────────────────────────────────
    def plan(s: State) -> dict:
        intent, cond = s["intent"], dict(s.get("conditions") or {})
        chosen = [t for t in s.get("llm_tools") or [] if registry.get(t)]
        dropped = [t for t in s.get("llm_tools") or [] if not registry.get(t)]
        steps: list[dict] = []
        missing: list[str] = []
        defaults: list[str] = []
        notes: list[str] = []

        def add(tool, args=None, bind=None, deferred=False):
            steps.append({"id": len(steps) + 1, "tool": tool, "args": args or {}, "bind": bind, "status": "deferred" if deferred else "pending", "round": 0})

        if s.get("error"):
            pass
        elif intent == "term":
            add(DOCS, {"query": s.get("question") or s["message"]})
        elif intent == "fact":
            if PRICES in chosen:
                if cond.get("region"):
                    add(PRICES, {"region": cond["region"]})
                else:
                    missing.append("시세를 볼 지역(서울 구)")
            if SEARCH in chosen or DETAIL in chosen:
                args = {"groups": cond.get("groups") or list(fcfg["default_groups"])}
                if cond.get("bank_name"):
                    args["company"] = cond["bank_name"]
                add(SEARCH, args)
                add(DETAIL, bind="each_product")
            if BRANCHES in chosen:
                if cond.get("region"):
                    add(BRANCHES, {"region": cond["region"], **({"bank_name": cond["bank_name"]} if cond.get("bank_name") else {})})
                else:
                    missing.append("지점을 찾을 지역")
            if not steps and not missing:
                missing.append("무엇을 조회할지 (시세·금리·지점)")
        elif intent == "plan":
            if not cond.get("term_years"):
                cond["term_years"] = acfg["default_term_years"]
                defaults.append(f"대출 기간을 말하지 않아 기본값 {acfg['default_term_years']}년으로 계산했습니다.")
            has_loan = bool(cond.get("loan_amount_manwon"))
            if not has_loan:
                if cond.get("own_funds_manwon") is None:
                    missing.append("보유 자금(또는 대출 희망액)")
                if not cond.get("price_manwon") and not cond.get("region"):
                    missing.append("집값(또는 시세를 볼 서울 구)")
            if not missing:
                need_price = not has_loan and not cond.get("price_manwon")
                if cond.get("region") and (need_price or PRICES in chosen):
                    add(PRICES, {"region": cond["region"]})
                add(SEARCH, {"groups": cond.get("groups") or list(fcfg["default_groups"])})
                add(DETAIL, bind="each_product")
                if cond.get("region"):
                    add(BRANCHES, {"region": cond["region"]}, bind="best_company", deferred=True)
                skipped = [t for t in chosen if t not in {st["tool"] for st in steps}]
                if skipped:
                    notes.append(f"요청 처리에 필요 없어 뺀 Tool: {', '.join(skipped)}")
        # save, other: Tool 없이 응답
        emit("decision", stage="plan", by="rule", kind="plan",
             steps=[{"id": st["id"], "tool": st["tool"], "args": st["args"], "bind": st["bind"], "deferred": st["status"] == "deferred"} for st in steps],
             llm_tools=s.get("llm_tools") or [], dropped_unknown=dropped, missing=missing, defaults=defaults, notes=notes,
             reason="필요한 조건이 모자라 Tool 을 부르기 전에 되묻는다" if missing else ("Tool 이 필요 없는 요청" if not steps else "Tool 호출 순서를 정함"))
        return {"steps": steps, "missing": missing, "defaults": defaults, "conditions": cond,
                "results": s.get("results") or {}, "calls": [], "products": [], "groups_searched": [],
                "replan_count": 0, "replans": [], "replan_used": [], "relaxed": False}

    def after_plan(s: State) -> str:
        return "execute" if s.get("steps") else "respond"

    # ── ③ execute ─────────────────────────────────────────────
    def call_tool(s: State, step: dict, args: dict) -> dict:
        results, calls = s["results"], s["calls"]
        sig = _sig(step["tool"], args)
        if sig in results:  # 이미 확보한 결과는 다시 부르지 않는다 (F3009)
            calls.append({"step": step["id"], "tool": step["tool"], "args": args, "ok": results[sig]["ok"], "reused": True,
                          "round": s.get("replan_count", 0), "elapsed_ms": 0, "summary": _summary(step["tool"], results[sig])})
            emit("tool.reuse", step=step["id"], tool=step["tool"], args=args)
            return results[sig]
        emit("tool.request", step=step["id"], tool=step["tool"], args=args, round=s.get("replan_count", 0))
        t0 = time.perf_counter()
        result = registry.call(step["tool"], **args)
        elapsed = round((time.perf_counter() - t0) * 1000)
        results[sig] = result
        code = None if result["ok"] else result["error"]["code"]
        summary = _summary(step["tool"], result)
        emit("tool.response", step=step["id"], tool=step["tool"], ok=result["ok"], code=code, elapsed_ms=elapsed, summary=summary)
        calls.append({"step": step["id"], "tool": step["tool"], "args": args, "ok": result["ok"], "code": code, "reused": False,
                      "round": s.get("replan_count", 0), "elapsed_ms": elapsed, "summary": summary})
        if code in FATAL_CODES and not s.get("fatal"):
            s["fatal"] = {"tool": step["tool"], "code": code, "message": result["error"]["message"]}
        return result

    def execute(s: State) -> dict:
        products = list(s.get("products") or [])
        groups = list(s.get("groups_searched") or [])
        limit = acfg["max_products_per_round"]
        for step in s["steps"]:
            if step["status"] != "pending":
                continue
            step["status"] = "done"
            if step["bind"] == "each_product":
                # 앞선 후보 조회 결과(product_id)를 상세 조회 입력으로 잇는다
                src = [p for p in products if p.get("from_step") == step.get("source_step", step["id"] - 1)]
                for p in src[:limit]:
                    args = {"product_id": p["product_id"]}
                    if s["conditions"].get("mrtg_type"):
                        args["mrtg_type"] = s["conditions"]["mrtg_type"]
                    call_tool(s, step, args)
                if len(src) > limit:
                    emit("decision", stage="execute", by="rule", kind="limit", reason=f"후보 {len(src)}건 중 앞의 {limit}건만 상세 조회 (agent.max_products_per_round)")
                continue
            args = dict(step["args"])
            if step["bind"] == "best_company":
                best = (s.get("judged") or {}).get("satisfied") or []
                if best:
                    args["bank_name"] = _short_company(best[0]["kor_co_nm"])
            result = call_tool(s, step, args)
            if step["tool"] == SEARCH and result["ok"]:
                known = {p["product_id"] for p in products}
                for p in result["data"]["products"]:
                    if p["product_id"] not in known:
                        products.append({**p, "from_step": step["id"]})
                groups += [g for g in result["data"]["basis"]["groups"] if g not in groups]
            elif step["tool"] == SEARCH:
                groups += [g for g in args.get("groups", []) if g not in groups]
        return {"steps": s["steps"], "products": products, "groups_searched": groups, "results": s["results"],
                "calls": s["calls"], "fatal": s.get("fatal")}

    # ── ④ judge ───────────────────────────────────────────────
    def _result_of(s: State, tool: str) -> dict | None:
        for c in reversed(s["calls"]):
            if c["tool"] == tool:
                return s["results"][_sig(tool, c["args"])]
        return None

    def judge(s: State) -> dict:
        cond = s["conditions"]
        out: dict = {}
        if s["intent"] in ("plan", "fact") and s.get("products"):
            price = _result_of(s, PRICES)
            price_info = price["data"] if price and price["ok"] else None
            principal, basis = J.loan_amount(cond, price_info)
            details = []
            for p in s["products"]:
                for c in s["calls"]:
                    if c["tool"] == DETAIL and c["args"].get("product_id") == p["product_id"]:
                        details.append({"product": p, "result": s["results"][_sig(DETAIL, c["args"])]})
                        break
            if principal is not None and s["intent"] == "plan":
                judged = J.judge(details, cond, principal, cond.get("term_years") or acfg["default_term_years"], s.get("relaxed", False))
            else:
                judged = {"principal": None, "satisfied": [], "excluded": [], "reverse": {}, "rates_only": details}
            judged["loan_basis"] = basis
            out["judged"] = judged
            if s["intent"] == "plan" and principal is None and not s.get("missing"):
                out["missing"] = ["집값 (시세 조회에 실패해 대출금을 정할 수 없습니다)"]
        judged = out.get("judged") or s.get("judged") or {}

        nxt, reason = "respond", "응답 생성"
        deferred = [st for st in s["steps"] if st["status"] == "deferred"]
        if s.get("fatal"):
            reason = f"{s['fatal']['tool']} 가 {s['fatal']['code']} 로 실패: 다시 시도해도 같으므로 재계획하지 않음"
            for st in deferred:
                st["status"] = "skipped"
        elif s["intent"] == "plan" and judged.get("principal") is not None and not judged.get("satisfied"):
            if s.get("replan_count", 0) >= acfg["max_replans"]:
                nxt, reason = "respond", f"재계획 상한({acfg['max_replans']}회)에 도달해 멈춤"
                out["stop_reason"] = reason
            elif _next_strategy(s) is None:
                nxt, reason = "respond", "더 시도할 대안이 없어 멈춤"
                out["stop_reason"] = reason
            else:
                nxt, reason = "replan", "제약을 충족하는 후보가 하나도 없음"
        elif deferred and judged.get("satisfied"):
            for st in deferred:
                st["status"] = "pending"
            nxt, reason = "execute", "추천 상품이 정해져 지점 검색을 이어서 실행"
        else:
            for st in deferred:
                st["status"] = "skipped"
        out["next"] = nxt
        out["steps"] = s["steps"]
        emit("decision", stage="judge", by="rule", kind="next", next=nxt, reason=reason,
             satisfied=len(judged.get("satisfied") or []), excluded=len(judged.get("excluded") or []),
             principal=judged.get("principal"), payment_cap=judged.get("payment_cap"))
        return out

    def after_judge(s: State) -> str:
        return s.get("next", "respond")

    # ── ⑤ replan ──────────────────────────────────────────────
    def _next_strategy(s: State) -> str | None:
        used = s.get("replan_used") or []
        for strategy in acfg["replan_steps"]:
            if strategy in used:
                continue
            if strategy == "relax_rate_type" and s["conditions"].get("rate_type") and not s.get("relaxed"):
                return strategy
            if strategy.startswith("add_group:") and strategy.split(":", 1)[1] not in (s.get("groups_searched") or []):
                return strategy
        return None

    def replan(s: State) -> dict:
        strategy = _next_strategy(s)
        n = s.get("replan_count", 0) + 1
        steps = s["steps"]
        out: dict = {"replan_count": n, "replan_used": (s.get("replan_used") or []) + [strategy]}
        if strategy == "relax_rate_type":
            out["relaxed"] = True
            reason = f"선호 금리 유형({s['conditions']['rate_type']})을 풀고 이미 받은 상세정보로 다시 판단"
        else:
            group = strategy.split(":", 1)[1]
            sid = len(steps) + 1
            steps.append({"id": sid, "tool": SEARCH, "args": {"groups": [group]}, "bind": None, "status": "pending", "round": n})
            steps.append({"id": sid + 1, "tool": DETAIL, "args": {}, "bind": "each_product", "source_step": sid, "status": "pending", "round": n})
            # 지점 검색은 새 판단 뒤로 다시 미룬다
            for st in steps:
                if st["tool"] == BRANCHES and st["status"] == "skipped":
                    st["status"] = "deferred"
            reason = f"{fcfg['group_names'].get(group, group)} 권역 상품을 더 조회 (이미 조회한 상품은 다시 부르지 않음)"
        replans = (s.get("replans") or []) + [{"n": n, "strategy": strategy, "reason": reason, "after_call": len(s["calls"])}]
        out["replans"] = replans
        out["steps"] = steps
        emit("replan", n=n, max=acfg["max_replans"], strategy=strategy, reason=reason)
        return out

    # ── ⑥ respond ─────────────────────────────────────────────
    def respond(s: State) -> dict:
        view = _view(s)
        try:
            answer = _answer(s, view)
        except LLMKeyMissing:
            answer = "LLM 키가 설정되지 않아 답변을 만들 수 없습니다."
            view["error"] = {"code": "LLM_KEY_MISSING", "message": "LLM 키(OPENAI_API_KEY)가 설정되지 않았습니다."}
        except Exception as e:  # noqa: BLE001
            answer = "답변을 만드는 중 문제가 생겼습니다. 잠시 뒤 다시 시도해 주세요."
            view["error"] = {"code": "LLM_ERROR", "message": type(e).__name__}
        return {"answer": answer, "view": view}

    def _view(s: State) -> dict:
        judged = s.get("judged") or {}
        price = _result_of(s, PRICES) if s.get("calls") else None
        branches = _result_of(s, BRANCHES) if s.get("calls") else None
        return {
            "intent": s.get("intent"),
            "reason": s.get("reason"),
            "conditions": C.split(s.get("conditions") or {}),
            "changed": [C.ALL_FIELDS.get(k, k) for k in s.get("changed") or []],
            "defaults": s.get("defaults") or [],
            "missing": s.get("missing") or [],
            "judged": judged,
            "price": price["data"] if price and price["ok"] else None,
            "branches": branches["data"] if branches and branches["ok"] else None,
            "calls": s.get("calls") or [],
            "replans": s.get("replans") or [],
            "stop_reason": s.get("stop_reason"),
            "fatal": s.get("fatal"),
            "error": s.get("error"),
            "tool_failures": [c for c in s.get("calls") or [] if not c["ok"]],
        }

    def _answer(s: State, view: dict) -> str:
        if s.get("error"):
            if s["error"]["code"] == "LLM_KEY_MISSING":
                return "LLM 키가 설정되지 않아 요청을 분석할 수 없습니다. 화면 위쪽의 설정 안내를 확인해 주세요."
            return "요청을 분석하는 중 LLM 호출에 실패했습니다. 잠시 뒤 다시 시도해 주세요."
        intent = s["intent"]
        if intent == "other":
            return llm().chat(s["message"])
        if intent == "term":
            return _term_answer(s, view)
        if intent == "save":
            return _save(s, view)
        if s.get("missing"):
            return "계산을 시작하려면 다음 조건이 더 필요합니다: " + ", ".join(s["missing"]) + ".\n예) \"현금 2억 있어요\", \"집값은 6억이에요\""
        if intent == "fact":
            return _fact_answer(s, view)
        return _plan_answer(s, view)

    def _term_answer(s: State, view: dict) -> str:
        res = _result_of(s, DOCS)
        if not res or not res["ok"]:
            code = res["error"]["code"] if res else "NO_RESULT"
            if code == "NO_INDEX":
                return "용어·제도 문서를 아직 모으지 못했습니다(색인 없음). 상품·금리·시세 질문은 그대로 도와드릴 수 있습니다."
            return "관련 문서를 찾지 못해 확인되지 않습니다."
        docs = res["data"]["documents"]
        text = llm().answer_term(s.get("question") or s["message"], docs)
        cited = sorted({int(n) for n in re.findall(r"\[근거 (\d+)\]", text) if 0 < int(n) <= len(docs)})
        view["sources"] = [{"n": n, **{k: docs[n - 1][k] for k in ("title", "source", "url", "license")}} for n in cited]
        if not cited:
            return text + "\n\n(근거 문서에서 확인되지 않은 내용이 섞였을 수 있습니다.)"
        return text

    def _save(s: State, view: dict) -> str:
        cands = (s.get("context") or {}).get("candidates") or []
        cond = s.get("conditions") or {}
        rank = s.get("target_rank") or 1
        if not cands:
            return "저장할 추천 상품이 아직 없습니다. 먼저 조건을 알려 주시면 상품을 찾아 드릴게요."
        if not 1 <= rank <= len(cands):
            return f"추천 순번은 1~{len(cands)} 중에서 골라 주세요."
        need = [label for key, label in (("visit_date", "상담 날짜"), ("visit_time", "상담 시각")) if not cond.get(key)]
        if need:
            return f"계획을 저장하려면 {', '.join(need)}가 필요합니다. 예) \"토요일 오후 2시로 1번 저장해줘\""
        if plan_store is None:
            return "계획 저장소가 연결되지 않았습니다."
        c = cands[rank - 1]
        start = cond["visit_time"]
        end = (datetime.strptime(start, "%H:%M") + timedelta(minutes=acfg["consult_minutes"])).strftime("%H:%M")
        if end < start:
            end = "23:59"
        data = {
            "title": f"{c['name']} 상담", "product_id": c["product_id"], "kor_co_nm": c.get("kor_co_nm"),
            "fin_prdt_nm": c.get("fin_prdt_nm"), "branch_name": c.get("branch_name"), "branch_address": c.get("branch_address"),
            "visit_date": cond["visit_date"], "start_time": start, "end_time": end,
            "loan_amount_manwon": c.get("principal"), "term_years": c.get("term_years"), "rate_type": c.get("rate_type"),
            "rpay_type": c.get("rpay_type"), "annual_rate": c.get("rate"), "monthly_payment_manwon": c.get("monthly_payment"),
            "session_id": s["session_id"],
        }
        saved, overlaps = plan_store.create(data)
        view["saved_plan"] = {"plan": saved, "overlaps": overlaps}
        emit("decision", stage="respond", by="rule", kind="plan_saved", plan_id=saved["id"], overlaps=[o["id"] for o in overlaps])
        lines = [f"상담 계획을 저장했습니다 (계획 #{saved['id']}).",
                 f"- {saved['title']} · {saved['visit_date']} {saved['start_time']}~{saved['end_time']}"
                 f" (종료 시각은 기본 상담 시간 {acfg['consult_minutes']}분으로 정함)"]
        if overlaps:
            lines.append("⚠ 같은 날짜에 시간이 겹치는 계획이 있습니다: " + ", ".join(f"#{o['id']} {o['title']} {o['start_time']}~{o['end_time']}" for o in overlaps))
        lines.append("계획 관리 화면에서 날짜·시각을 고치거나 삭제할 수 있습니다.")
        return "\n".join(lines)

    def _fact_answer(s: State, view: dict) -> str:
        parts = []
        if view["price"]:
            p = view["price"]
            parts.append(f"**{p['basis']['region']} 아파트 실거래 시세** ({p['basis']['months'][0]}~{p['basis']['months'][-1]}, {p['count']}건)\n"
                         f"- 중위 {_eok(p['median_manwon'])} · 하위 25% {_eok(p['p25_manwon'])} · 상위 25% {_eok(p['p75_manwon'])}")
            parts += [f"- 참고: {n}" for n in p["notes"]]
        judged = view["judged"] or {}
        rows = judged.get("rates_only") or []
        if rows:
            parts.append("**공시 금리** (금감원 공시값 그대로)")
            for d in rows:
                r = d["result"]
                if not r["ok"]:
                    parts.append(f"- {d['product']['kor_co_nm']} {d['product']['fin_prdt_nm']}: {r['error']['message']}")
                    continue
                for o in r["data"]["options"]:
                    parts.append(f"- {r['data']['kor_co_nm']} {r['data']['fin_prdt_nm']} · {o['mrtg_type_nm']} · {o['rpay_type_nm']} · {o['lend_rate_type_nm']}: "
                                 f"최저 {_fmt(o['rate_min'], '%')} / 최고 {_fmt(o['rate_max'], '%')} / 평균 {_fmt(o['rate_avg'], '%')}")
        if view["branches"]:
            parts.append("**지점**")
            parts += [f"- {b['name']} · {b['address']}" for b in view["branches"]["branches"]]
        for c in view["tool_failures"]:
            parts.append(f"- ⚠ {TOOL_LABELS.get(c['tool'], c['tool'])} 실패 ({c['summary']})")
        return "\n".join(parts) or "조회 결과가 없습니다."

    def _plan_answer(s: State, view: dict) -> str:
        judged, cond = view["judged"] or {}, s["conditions"]
        lines: list[str] = []
        fatal = s.get("fatal")
        if fatal and not judged.get("satisfied"):
            lines.append(f"⚠ {fatal['message']} 다시 시도해도 결과가 같아 대안 탐색(재계획)을 하지 않았습니다.")
        basis = judged.get("loan_basis") or {}
        if judged.get("principal") is not None:
            lines.append(f"**판단 기준**: 대출금 {_eok(judged['principal'])} ({basis.get('formula') or '입력값'}) · 기간 {judged['term_years']}년 · "
                         + (f"월 상환액 상한 {_fmt(judged['payment_cap'])}" if judged.get("payment_cap") is not None else "월 상환 제약 없음"))
            if judged.get("rate_type_filter"):
                lines.append(f"- 선호 금리 유형: {judged['rate_type_filter']}")
            if basis.get("estimated"):
                lines.append("- 집값은 입력하지 않아 최근 실거래 중위값으로 추정했습니다.")
        lines += [f"- {d}" for d in s.get("defaults") or []]

        sat = judged.get("satisfied") or []
        top = sat[: acfg["show_top"]]
        if top:
            lines.append(f"\n**조건을 충족하는 상품 {len(sat)}개 중 상위 {len(top)}개** (월 상환액 낮은 순)")
            for i, e in enumerate(top, 1):
                o = e["option"]
                lines.append(
                    f"{i}. **{e['name']}** ({e.get('group_nm') or ''}) — {o['lend_rate_type_nm']} · {o['rpay_type_nm']}({o['method']})\n"
                    f"   - {o['rate_basis']}금리 {o['rate_used']:.2f}% (공시 최저 {_fmt(o['rate_min'], '%')} ~ 최고 {_fmt(o['rate_max'], '%')})\n"
                    f"   - 월 상환액 약 {_fmt(o['monthly_payment'])}" + (f" (최고금리면 {_fmt(o['payment_at_max'])})" if o.get("payment_at_max") else "")
                    + (f" · 소득 대비 {o['payment_ratio_pct']}%" if o.get("payment_ratio_pct") is not None else "") + "\n"
                    f"   - 판단: {'; '.join(e['checks'])} · 근거: 금감원 공시 {e.get('dcls_month') or '-'}월"
                    + (f" · 한도 공시: {e['loan_lmt']}" if e.get("loan_lmt") else "")
                )
            if top[0]["option"]["method"] == "만기일시":
                lines.append("- 만기일시상환은 매달 이자만 내고 만기에 원금을 한 번에 갚아야 합니다.")
        elif judged.get("principal") is not None:
            lines.append("\n**조건을 충족하는 상품을 찾지 못했습니다.**")
            if s.get("stop_reason"):
                lines.append(f"- {s['stop_reason']}")
            rev = judged.get("reverse") or {}
            if rev:
                lines.append("**역산 기준** (실제 적용 금리·한도는 공시로 확인할 수 없어 계산으로 제시)")
                if "max_rate_for_cap" in rev:
                    lines.append(
                        f"- 이 대출금으로 상한을 지키려면 적용 금리가 연 {rev['max_rate_for_cap']:.2f}% 이하여야 합니다."
                        if rev["max_rate_for_cap"] is not None else "- 금리가 0%여도 상한을 넘습니다. 기간을 늘리거나 대출금을 줄여야 합니다."
                    )
                if rev.get("max_principal_at_best_rate") is not None:
                    lines.append(f"- 조회한 최저 금리 {rev['best_rate_min']:.2f}% 기준, 상한 안에서 빌릴 수 있는 대출금은 약 {_eok(rev['max_principal_at_best_rate'])}입니다.")
            lines.append("- 기간·월 상환 한도·집값 중 무엇을 조정할 수 있을지 알려 주세요.")

        if view["replans"]:
            lines.append("\n**재계획 이력**")
            lines += [f"- {r['n']}차: {r['reason']}" for r in view["replans"]]

        exc = judged.get("excluded") or []
        if exc:
            lines.append(f"\n**제외한 후보 {len(exc)}개**")
            lines += [f"- {e['name']}: {e['reason']}" for e in exc[:6]]
            if len(exc) > 6:
                lines.append(f"- 외 {len(exc) - 6}개 (실행 과정에서 전체 확인)")

        if view["price"]:
            p = view["price"]
            lines.append(f"\n**참고 시세** {p['basis']['region']} 최근 {len(p['basis']['months'])}개월 거래 {p['count']}건 중위 {_eok(p['median_manwon'])}")
            lines += [f"- {n}" for n in p["notes"]]
        if view["branches"] and top:
            b = view["branches"]
            lines.append(f"\n**상담 지점 후보** ('{b['query']}' 검색)")
            lines += [f"- [{x['name']}]({x['url']}) · {x['address']}" + (f" · {x['phone']}" if x.get("phone") else "") for x in b["branches"][:3]]
            lines.append(f"- {b['notes'][0]}")
        failures = [c for c in view["tool_failures"] if c["tool"] != DETAIL and not (fatal and not judged.get("satisfied") and c["tool"] == fatal["tool"])]
        if failures:
            lines.append("")
        for c in failures:
            lines.append(f"- ⚠ {TOOL_LABELS.get(c['tool'], c['tool'])} 실패 ({c['summary']}) — 나머지 단계는 계속 진행했습니다.")
        if top:
            lines.append("\n저장하려면 \"1번으로 토요일 오후 2시 상담 저장해줘\"처럼 말해 주세요.")
        return "\n".join(lines).strip()

    # ── 그래프 ───────────────────────────────────────────────
    def traced(name: str, fn):
        def run(s: State) -> dict:
            emit("stage.enter", stage=name)
            t0 = time.perf_counter()
            try:
                out = fn(s)
            except Exception as e:  # noqa: BLE001 — 한 단계의 오류로 서비스 전체가 멈추지 않게 (NF3003)
                emit("error", stage=name, error=type(e).__name__)
                out = {"error": {"code": "STAGE_ERROR", "message": f"{name} 단계 오류"}, "next": "respond", "steps": s.get("steps") or []}
                if name == "respond":
                    out = {"answer": "처리 중 문제가 생겼습니다. 잠시 뒤 다시 시도해 주세요.", "view": {"error": out["error"]}}
            emit("stage.exit", stage=name, elapsed_ms=round((time.perf_counter() - t0) * 1000))
            return out

        return run

    g = StateGraph(State)
    for name, fn in [("analyze", analyze), ("plan", plan), ("execute", execute), ("judge", judge), ("replan", replan), ("respond", respond)]:
        g.add_node(name, traced(name, fn))
    g.add_edge(START, "analyze")
    g.add_edge("analyze", "plan")
    g.add_conditional_edges("plan", after_plan, {"execute": "execute", "respond": "respond"})
    g.add_edge("execute", "judge")
    g.add_conditional_edges("judge", after_judge, {"replan": "replan", "execute": "execute", "respond": "respond"})
    g.add_edge("replan", "execute")
    g.add_edge("respond", END)
    return g.compile()


def notices() -> dict:
    return dict(NOTICES)
