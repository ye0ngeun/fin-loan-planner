"""LLM 연결. 메인 Agent 는 PlannerLLM 의 세 메서드만 쓴다.

- analyze(...)  요청 분석: 의도·이유·조건·필요한 Tool (F3005, F3006)
- chat(...)     인사·사용법 답변 (other)
- answer_term   용어 답변 (term, 심화 F3022)

GmsLLM: SSAFY GMS(OpenAI 호환). FakeLLM: 키·네트워크 없이 규칙으로 흉내 낸 연습용(테스트·시연).
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta

from app.agent import prompts
from app.config import get_env, get_settings

WEEKDAYS = "월화수목금토일"


class LLMKeyMissing(RuntimeError):
    """LLM 키가 없다. 화면에 설정 안내를 보여 준다. (F3019)"""


def parse_json_object(text: str) -> dict:
    match = re.search(r"\{.*\}", text or "", re.DOTALL)
    if not match:
        return {}
    try:
        value = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


class GmsLLM:
    name = "gms"

    def __init__(self) -> None:
        if not get_env("OPENAI_API_KEY"):
            raise LLMKeyMissing("OPENAI_API_KEY 가 설정되지 않았습니다.")
        from langchain_openai import ChatOpenAI

        s = get_settings()["llm"]
        self.model = ChatOpenAI(
            model=s["chat_model"],
            temperature=s["temperature"],
            max_retries=s["max_retries"],
            timeout=s["timeout_sec"],
            api_key=get_env("OPENAI_API_KEY"),
            base_url=get_env("OPENAI_BASE_URL") or None,
        )
        self.calls = 0

    def _invoke(self, system: str, user: str) -> str:
        self.calls += 1
        return self.model.invoke([("system", system), ("user", user)]).content

    def analyze(self, message: str, context: dict, tool_specs: list[dict], today: date) -> dict:
        system = prompts.ANALYZE_SYSTEM.format(
            today=today.isoformat(),
            weekday=WEEKDAYS[today.weekday()] + "요일",
            tool_specs="\n".join(f"- {t['name']}: {t['description']} 입력: {json.dumps(t['parameters']['properties'], ensure_ascii=False)}" for t in tool_specs),
        )
        user = prompts.ANALYZE_USER.format(
            conditions=json.dumps(context.get("conditions") or {}, ensure_ascii=False),
            candidates="\n".join(f"{i}. {c['name']}" for i, c in enumerate(context.get("candidates") or [], 1)) or "(없음)",
            history="\n".join(f"{h['role']}: {h['text']}" for h in context.get("history") or []) or "(없음)",
            message=message,
        )
        return parse_json_object(self._invoke(system, user))

    def chat(self, message: str) -> str:
        return self._invoke(prompts.OTHER_SYSTEM, message)

    def answer_term(self, question: str, documents: list[dict]) -> str:
        evidence = "\n\n".join(f"[근거 {i}] {d['title']}\n{d['text']}" for i, d in enumerate(documents, 1))
        return self._invoke(prompts.TERM_SYSTEM, prompts.TERM_USER.format(question=question, evidence=evidence))


# ── 연습용 규칙 분석기 ─────────────────────────────────────────
_GU = r"(종로|중|용산|성동|광진|동대문|중랑|성북|강북|도봉|노원|은평|서대문|마포|양천|강서|구로|금천|영등포|동작|관악|서초|강남|송파|강동)구"


def parse_amount(text: str) -> float | None:
    """'6억', '6억 5천', '6억5000만', '2,000만', '200만원' → 만원"""
    m = re.search(r"(\d+(?:\.\d+)?)\s*억(?:\s*(\d+(?:,\d{3})*)\s*(천)?\s*만?)?", text)
    if m:
        total = float(m.group(1)) * 10000
        if m.group(2):
            n = float(m.group(2).replace(",", ""))
            total += n * 1000 if m.group(3) else n
        return total
    m = re.search(r"(\d+(?:,\d{3})*(?:\.\d+)?)\s*(천)?\s*만", text)
    if m:
        n = float(m.group(1).replace(",", ""))
        return n * 1000 if m.group(2) else n
    return None


class FakeLLM:
    """키 없이 돌아가는 규칙 기반 대역. 실제 LLM 과 같은 출력 형식을 낸다."""

    name = "fake"

    def __init__(self) -> None:
        self.calls = 0

    def analyze(self, message: str, context: dict, tool_specs: list[dict], today: date) -> dict:
        self.calls += 1
        t = message
        cond: dict = {}
        # 금액은 '단어 + 금액' 모양으로 찾는다
        for key, pattern in [
            ("price_manwon", r"(?:집값|아파트|집|매매가)[^,.\n]{0,8}?(\d[\d,.]*\s*억[^,.\n]{0,8})"),
            ("price_manwon", r"(\d[\d,.]*\s*억[\d,\s천만]*)\s*(?:짜리|정도|대)?\s*(?:아파트|집)"),
            ("own_funds_manwon", r"(?:현금|보유|자금|모은 돈|자기자본)[^,.\n]{0,6}?(\d[\d,.]*\s*(?:억[\d,\s천만]*|천?\s*만))"),
            ("loan_amount_manwon", r"(\d[\d,.]*\s*(?:억[\d,\s천만]*|천?\s*만))\s*원?\s*(?:을|를)?\s*빌"),
            ("loan_amount_manwon", r"대출(?:금|액)?\s*(\d[\d,.]*\s*(?:억[\d,\s천만]*|천?\s*만))"),
            ("max_monthly_payment_manwon", r"(?:월|한\s*달)[^,.\n]{0,6}?(\d[\d,.]*\s*만)\s*원?\s*(?:이하|이내|안|까지|넘지)"),
            ("monthly_income_manwon", r"(?:월\s*)?(?:소득|월급|수입)[^,.\n]{0,4}?(\d[\d,.]*\s*만)"),
        ]:
            if key in cond:
                continue
            m = re.search(pattern, t)
            if m:
                n = parse_amount(m.group(1))
                if n:
                    cond[key] = n
        m = re.search(r"(\d{1,2})\s*년\s*(?:만기|동안|상환|으로|짜리)?", t)
        if m and "년" in m.group(0) and not re.search(r"\d{4}\s*년", m.group(0)):
            cond["term_years"] = int(m.group(1))
        m = re.search(r"(?:비율|DSR)[^\d]{0,6}(\d{1,2})\s*%", t, re.IGNORECASE)
        if m:
            cond["max_payment_ratio_pct"] = float(m.group(1))
        m = re.search(_GU, t) or re.search(r"(마포|강남|서초|송파|노원|관악|용산|성동|영등포)", t)
        if m:
            cond["region"] = m.group(1) + "구"
        if "고정" in t:
            cond["rate_type"] = "고정금리"
        elif "변동" in t:
            cond["rate_type"] = "변동금리"
        groups = [g for g in ("은행", "보험", "저축은행") if f"{g}권" in t or f"{g}까지" in t or f"{g}도" in t]
        if groups:
            cond["groups"] = groups
        m = re.search(r"([가-힣A-Z]{1,8}은행)", t)
        if m and m.group(1) not in ("저축은행",):
            cond["bank_name"] = m.group(1)
        # 날짜·시각
        m = re.search(r"(\d{4}-\d{2}-\d{2})", t)
        if m:
            cond["visit_date"] = m.group(1)
        elif "내일" in t:
            cond["visit_date"] = (today + timedelta(days=1)).isoformat()
        else:
            for i, wd in enumerate(WEEKDAYS):
                if f"{wd}요일" in t:
                    ahead = (i - today.weekday()) % 7 or 7
                    if "다음주" in t or "다음 주" in t:
                        ahead = ahead + 7 if ahead < 7 - today.weekday() else ahead
                    cond["visit_date"] = (today + timedelta(days=ahead)).isoformat()
        m = re.search(r"(오전|오후)?\s*(\d{1,2})\s*시\s*(?:(\d{1,2})\s*분|반)?", t)
        if m:
            h = int(m.group(2)) + (12 if m.group(1) == "오후" and int(m.group(2)) < 12 else 0)
            mm = int(m.group(3)) if m.group(3) else (30 if "반" in m.group(0) else 0)
            cond["visit_time"] = f"{h:02d}:{mm:02d}"
        m = re.search(r"(\d{1,2}):(\d{2})", t)
        if m:
            cond["visit_time"] = f"{int(m.group(1)):02d}:{m.group(2)}"

        rank = re.search(r"(\d)\s*번", t)
        if re.search(r"저장|확정|예약|등록", t):
            return {"intent": "save", "reason": "추천 상품으로 계획을 저장해 달라는 요청", "conditions": cond, "tools": [], "target_rank": int(rank.group(1)) if rank else 1}
        if re.search(r"안녕|사용법|도움|뭘 할 수|뭐 할 수|무엇을 할", t):
            return {"intent": "other", "reason": "인사나 사용법 질문이라 Tool 이 필요 없음", "conditions": {}, "tools": []}
        if re.search(r"뜻|뭐야|무엇인가|란\?|이란|의미", t) and not cond:
            q = re.sub(r"(이|가)?\s*(뭐야|뜻|무엇인가요?|의미|이란|란)\??", "", t).strip(" ?")
            return {"intent": "term", "reason": "용어의 뜻을 묻는 질문", "conditions": {}, "tools": ["search_loan_docs"], "question": q or t}
        if re.search(r"시세|얼마에 거래|실거래", t) and "region" in cond and not (set(cond) & {"own_funds_manwon", "max_monthly_payment_manwon"}):
            return {"intent": "fact", "reason": "지역 시세만 묻는 사실 조회", "conditions": cond, "tools": ["get_apartment_prices"]}
        if re.search(r"금리\s*(알려|어때|얼마)", t) and "bank_name" in cond and not (set(cond) & {"own_funds_manwon", "max_monthly_payment_manwon", "loan_amount_manwon"}):
            return {"intent": "fact", "reason": "특정 금융회사의 금리만 묻는 사실 조회", "conditions": cond, "tools": ["search_mortgage_products", "get_mortgage_product_detail"]}
        if not cond and not context.get("conditions"):
            return {"intent": "other", "reason": "대출 조건이 없는 일반 대화", "conditions": {}, "tools": []}
        tools = ["search_mortgage_products", "get_mortgage_product_detail"]
        merged = {**(context.get("conditions") or {}), **cond}
        if merged.get("region"):
            tools = ["get_apartment_prices", *tools, "search_bank_branches"]
        return {"intent": "plan", "reason": "조건에 맞는 대출 상품을 찾아 달라는 요청", "conditions": cond, "tools": tools}

    def chat(self, message: str) -> str:
        self.calls += 1
        return (
            "안녕하세요. 내집마련 주택담보대출 플래너입니다. 집값·보유 자금·월 상환 한도를 알려 주시면 금감원 공시 상품의 "
            "월 상환액을 계산해 조건에 맞는 상품을 찾아 드립니다. 예: \"마포구 6억 아파트, 현금 2억, 월 200만원 이하\""
        )

    def answer_term(self, question: str, documents: list[dict]) -> str:
        self.calls += 1
        d = documents[0]
        return f"{d['text'][:200].strip()} [근거 1]"


def get_llm():
    """PLANNER_FAKE_LLM=1 이면 규칙 대역, 아니면 GMS. 키가 없으면 LLMKeyMissing."""
    if get_env("PLANNER_FAKE_LLM") in ("1", "true", "yes"):
        return FakeLLM()
    return GmsLLM()
