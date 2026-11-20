"""요청 조건의 정의, 정리, 대화 맥락과의 병합. (F3005, F3010)

조건은 세 갈래로 나눈다.
- 기준 조건: 계획의 출발점이 되는 값 (집값, 지역, 보유 자금, 대출금, 기간, 상담 날짜·시각)
- 선호 조건: 바꿀 수 있는 취향 (금리 유형, 권역, 금융회사, 담보 유형) — 재계획 때 풀 수 있다
- 제약 조건: 지켜야 하는 한도 (월 상환액 상한, 소득 대비 상환 비율 상한)
요청에 없는 값은 채우지 않고 비워 둔다.
"""

from __future__ import annotations

import re
from datetime import date

BASE = {
    "price_manwon": "집값(만원)",
    "region": "지역(서울 구)",
    "own_funds_manwon": "보유 자금(만원)",
    "loan_amount_manwon": "대출 희망액(만원)",
    "term_years": "대출 기간(년)",
    "visit_date": "상담 날짜",
    "visit_time": "상담 시각",
}
PREFERENCE = {
    "rate_type": "금리 유형",
    "groups": "권역",
    "bank_name": "금융회사",
    "mrtg_type": "담보 유형",
}
CONSTRAINT = {
    "max_monthly_payment_manwon": "월 상환액 상한(만원)",
    "monthly_income_manwon": "월 소득(만원)",
    "max_payment_ratio_pct": "소득 대비 상환 비율 상한(%)",
}
ALL_FIELDS = {**BASE, **PREFERENCE, **CONSTRAINT}
GROUP_CODES = {"은행": "020000", "보험": "050000", "저축은행": "030300", "여신전문": "030200", "카드": "030200", "캐피탈": "030200"}
RATE_TYPES = ("고정금리", "변동금리")
MRTG_TYPES = ("아파트", "아파트외")


def _num(value) -> float | None:
    if value in (None, "", []):
        return None
    try:
        n = float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def clean(raw: dict | None) -> dict:
    """LLM 이 준 조건에서 형식이 맞는 값만 남긴다. 모르는 키·잘못된 값은 버린다 (지어내지 않음)."""
    out: dict = {}
    for key, value in (raw or {}).items():
        if key not in ALL_FIELDS or value in (None, "", []):
            continue
        if key.endswith("_manwon") or key in ("term_years", "max_payment_ratio_pct"):
            n = _num(value)
            if n is None:
                continue
            if key == "term_years" and not 1 <= n <= 50:
                continue
            if key == "max_payment_ratio_pct" and not 0 < n <= 100:
                continue
            out[key] = int(n) if key == "term_years" else round(n, 1)
        elif key == "visit_date":
            try:
                out[key] = date.fromisoformat(str(value)).isoformat()
            except ValueError:
                continue
        elif key == "visit_time":
            m = re.fullmatch(r"(\d{1,2}):(\d{2})", str(value).strip())
            if m and int(m.group(1)) < 24 and int(m.group(2)) < 60:
                out[key] = f"{int(m.group(1)):02d}:{m.group(2)}"
        elif key == "rate_type":
            v = str(value).strip()
            v = v if v.endswith("금리") else f"{v}금리"
            if v in RATE_TYPES:
                out[key] = v
        elif key == "mrtg_type":
            if str(value) in MRTG_TYPES:
                out[key] = str(value)
        elif key == "groups":
            values = value if isinstance(value, list) else [value]
            codes = []
            for v in values:
                v = str(v).strip()
                code = v if v in GROUP_CODES.values() else GROUP_CODES.get(v)
                if code and code not in codes:
                    codes.append(code)
            if codes:
                out[key] = codes
        else:
            out[key] = str(value).strip()
    return out


def merge(previous: dict, changes: dict, cleared: list[str] | None = None) -> tuple[dict, list[str]]:
    """이전 조건에 바뀐 값만 덮어쓴다. 사용자가 지운 조건(cleared)만 뺀다. (F3010)"""
    merged = dict(previous or {})
    changed = []
    for key, value in changes.items():
        if merged.get(key) != value:
            changed.append(key)
        merged[key] = value
    for key in cleared or []:
        if key in merged:
            merged.pop(key)
            changed.append(key)
    return merged, changed


def split(conditions: dict) -> dict:
    """화면·로그용: 기준/선호/제약으로 나눠 이름표를 붙인다."""
    def pick(fields):
        return {fields[k]: conditions[k] for k in fields if k in conditions}

    return {"기준": pick(BASE), "선호": pick(PREFERENCE), "제약": pick(CONSTRAINT)}
