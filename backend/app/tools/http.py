"""외부 API 호출 통로. 실제 호출, 연습용 응답(PLANNER_FAKE_APIS=1), 테스트 대역을 같은 모양으로 맞춘다.

transport(service, url, params, headers, timeout) -> (HTTP 상태 코드, 본문 문자열)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

import requests

from app.config import BACKEND_DIR, fake_mode

Transport = Callable[[str, str, dict, dict, float], tuple[int, str]]
FIXTURE_DIR = BACKEND_DIR / "tests" / "fixtures"

_override: Transport | None = None


def real_transport(service: str, url: str, params: dict, headers: dict, timeout: float) -> tuple[int, str]:
    resp = requests.get(url, params=params, headers=headers, timeout=timeout)
    return resp.status_code, resp.text


def fixture_transport(service: str, url: str, params: dict, headers: dict, timeout: float) -> tuple[int, str]:
    """tests/fixtures 의 응답을 돌려준다. 파일 이름은 서비스별 규칙으로 찾고, 없으면 빈 결과를 흉내 낸다."""
    if service == "fss":
        grp = params.get("topFinGrpNo", "")
        path = FIXTURE_DIR / "fss" / f"mortgage_{grp}.json"
        if not path.exists():
            return 200, json.dumps({"result": {"err_cd": "000", "max_page_no": 1, "baseList": [], "optionList": []}})
        body = json.loads(path.read_text(encoding="utf-8"))
        finance_cd = params.get("financeCd")
        if finance_cd:  # 금융회사 코드로 거르는 실제 API 동작을 흉내 낸다
            res = body["result"]
            res["baseList"] = [r for r in res["baseList"] if r.get("fin_co_no") == finance_cd]
            res["optionList"] = [r for r in res["optionList"] if r.get("fin_co_no") == finance_cd]
        return 200, json.dumps(body, ensure_ascii=False)
    if service == "molit":
        path = FIXTURE_DIR / "molit" / f"{params.get('LAWD_CD')}.xml"
        if not path.exists():
            path = FIXTURE_DIR / "molit" / "empty.xml"
        text = path.read_text(encoding="utf-8")
        ym = str(params.get("DEAL_YMD", ""))
        # 계약년월만 요청 값으로 바꿔 여러 달을 흉내 낸다
        return 200, text.replace("<dealYear>2026</dealYear>", f"<dealYear>{ym[:4]}</dealYear>").replace(
            "<dealMonth>9</dealMonth>", f"<dealMonth>{int(ym[4:6]) if ym[4:6].isdigit() else 9}</dealMonth>"
        )
    if service == "kakao":
        path = FIXTURE_DIR / "kakao" / "bank_search.json"
        body = json.loads(path.read_text(encoding="utf-8"))
        tokens = str(params.get("query", "")).split()
        region = tokens[0] if tokens else ""
        bank = tokens[1] if len(tokens) > 1 and tokens[1] != "은행" else ""
        docs = [d for d in body["documents"] if region in d["address_name"] and bank in d["place_name"]]
        return 200, json.dumps({"documents": docs, "meta": {"total_count": len(docs)}}, ensure_ascii=False)
    return 404, "{}"


def set_transport(transport: Transport | None) -> None:
    """테스트에서 대역을 끼운다. None 이면 원래대로."""
    global _override
    _override = transport


def has_override() -> bool:
    return _override is not None


def get(service: str, url: str, params: dict, headers: dict | None = None, timeout: float = 10) -> tuple[int, str]:
    if _override is not None:
        return _override(service, url, params, headers or {}, timeout)
    transport = fixture_transport if fake_mode() else real_transport
    return transport(service, url, params, headers or {}, timeout)


def fixture_path(*parts: str) -> Path:
    return FIXTURE_DIR.joinpath(*parts)
