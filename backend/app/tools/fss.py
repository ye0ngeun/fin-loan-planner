"""금융감독원 금융상품한눈에 Open API 를 감싼 외부 API 툴 두 개.

- search_mortgage_products (F3002 후보 조회): 권역별 주택담보대출 상품 목록 → 상품 식별자와 이름
- get_mortgage_product_detail (F3003 상세 조회): 상품 식별자 → 금리 옵션(최저·최고·평균), 상환방식, 한도 문구

상품 식별자: "{권역코드}:{금융회사코드}:{상품코드}" (예: 020000:0010001:1054)
금리는 API 응답 값을 그대로 쓰고, 비어 있으면 채우지 않는다 (NO_RATE).
"""

from __future__ import annotations

import json
import time
from datetime import date

from app.config import fake_mode, get_env, get_settings
from app.tools import http
from app.tools.base import (
    BAD_ARGUMENT,
    EXTERNAL,
    INVALID_KEY,
    NO_RATE,
    NO_RESULT,
    NOT_FOUND,
    UPSTREAM_ERROR,
    Tool,
    fail,
    ok,
    scrub,
)

SOURCE = "금융감독원 금융상품한눈에 Open API (주택담보대출)"
# 금감원 응답 err_cd 중 인증키 문제
_KEY_ERRORS = {"010", "011", "012", "020"}
_MRTG = {"아파트": "A", "아파트외": "E"}

# 같은 요청(권역, 금융회사)을 한 프로세스 안에서 다시 보내지 않는다. (호출 한도 보호)
_cache: dict[tuple, tuple[float, dict]] = {}
_CACHE_TTL = 600


def clear_cache() -> None:
    _cache.clear()


def _fetch_group(group: str, finance_cd: str | None = None) -> tuple[dict | None, dict | None]:
    """한 권역(필요하면 한 금융회사)의 모든 페이지를 받는다. (결과, 실패) 중 하나를 돌려준다."""
    api_key = get_env("FSS_API_KEY")
    if not api_key and not fake_mode():
        return None, fail(INVALID_KEY, "금감원 API 키(FSS_API_KEY)가 설정되지 않았습니다.")
    key = (group, finance_cd or "")
    now = time.time()
    exact = _cache.get(key)
    if exact and now - exact[0] < _CACHE_TTL:
        return {**exact[1], "from_cache": True}, None
    whole = _cache.get((group, "")) if finance_cd else None
    if whole and now - whole[0] < _CACHE_TTL:
        # 권역 전체를 이미 받았으면 그 안에서 금융회사만 거른다 (같은 API 의 같은 데이터)
        data = whole[1]
        return {
            "baseList": [r for r in data["baseList"] if r.get("fin_co_no") == finance_cd],
            "optionList": [r for r in data["optionList"] if r.get("fin_co_no") == finance_cd],
            "from_cache": True,
        }, None

    cfg = get_settings()["fss"]
    url = f"{cfg['base_url']}/{cfg['endpoint']}"
    base_list, option_list = [], []
    page, max_page = 1, 1
    while page <= min(max_page, cfg["max_pages"]):
        params = {"auth": api_key, "topFinGrpNo": group, "pageNo": page}
        if finance_cd:
            params["financeCd"] = finance_cd
        try:
            status, text = http.get("fss", url, params, timeout=cfg["timeout_sec"])
        except Exception as e:  # noqa: BLE001
            return None, fail(UPSTREAM_ERROR, f"금감원 API에 연결하지 못했습니다. ({scrub(e)[:120]})")
        if status != 200:
            return None, fail(UPSTREAM_ERROR, f"금감원 API가 오류로 답했습니다. (HTTP {status})")
        try:
            result = json.loads(text)["result"]
        except (ValueError, KeyError, TypeError):
            return None, fail(UPSTREAM_ERROR, "금감원 API 응답을 읽지 못했습니다.")
        err = str(result.get("err_cd", ""))
        if err in _KEY_ERRORS:
            return None, fail(INVALID_KEY, "금감원 API 키를 확인하세요.")
        if err != "000":
            return None, fail(UPSTREAM_ERROR, f"금감원 API 오류 {err}: {scrub(result.get('err_msg', ''))[:80]}")
        base_list += result.get("baseList") or []
        option_list += result.get("optionList") or []
        max_page = int(result.get("max_page_no") or 1)
        page += 1
        if page <= min(max_page, cfg["max_pages"]):
            time.sleep(cfg["request_interval_sec"])
    data = {"baseList": base_list, "optionList": option_list}
    _cache[key] = (time.time(), data)
    return {**data, "from_cache": False}, None


def _to_float(value) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _latest_rows(rows: list[dict]) -> list[dict]:
    """같은 상품이 여러 공시월로 오면 가장 최근 공시월만 남긴다."""
    latest: dict[tuple, dict] = {}
    for r in rows:
        k = (r.get("fin_co_no"), r.get("fin_prdt_cd"))
        if k not in latest or str(r.get("dcls_month", "")) > str(latest[k].get("dcls_month", "")):
            latest[k] = r
    return list(latest.values())


# ── F3002 후보 조회 ───────────────────────────────────────────
def search_mortgage_products(groups: list[str] | None = None, as_of: str = "", company: str = "") -> dict:
    cfg = get_settings()["fss"]
    groups = groups or list(cfg["default_groups"])  # 기본값: 은행 권역
    bad = [g for g in groups if g not in cfg["group_names"]]
    if bad:
        return fail(BAD_ARGUMENT, f"권역 코드가 아닙니다: {', '.join(bad)} (가능: {', '.join(cfg['group_names'])})")
    if as_of:
        try:
            requested = date.fromisoformat(as_of)
        except ValueError:
            return fail(BAD_ARGUMENT, "as_of 는 YYYY-MM-DD 형식이어야 합니다.")
    else:
        requested = None

    products, months, cached = [], set(), True
    for g in groups:
        data, error = _fetch_group(g)
        if error:
            return error
        cached = cached and data["from_cache"]
        for r in _latest_rows(data["baseList"]):
            if company and company not in (r.get("kor_co_nm") or ""):
                continue
            months.add(str(r.get("dcls_month", "")))
            products.append(
                {
                    "product_id": f"{g}:{r.get('fin_co_no')}:{r.get('fin_prdt_cd')}",
                    "kor_co_nm": r.get("kor_co_nm"),
                    "fin_prdt_nm": r.get("fin_prdt_nm"),
                    "group": g,
                    "group_nm": cfg["group_names"].get(g, g),
                }
            )
    if not products:
        who = f" '{company}'" if company else ""
        return fail(NO_RESULT, f"{', '.join(cfg['group_names'][g] for g in groups)} 권역에서{who} 주택담보대출 상품을 찾지 못했습니다.")

    basis_month = max(months) if months else ""
    notes = []
    # 금감원 공시는 '현재 공시 중인 상품'만 준다. 다른 시점을 물으면 추정임을 밝힌다.
    is_estimated = False
    if requested is not None:
        req_month = requested.strftime("%Y%m")
        if basis_month and req_month != basis_month:
            is_estimated = True
            notes.append(f"요청 시점({requested.isoformat()})의 공시는 조회할 수 없어 현재 공시({basis_month[:4]}-{basis_month[4:]}월) 기준으로 답합니다.")
    return ok(
        {
            "requested": {"groups": groups, "as_of": as_of or None, "company": company or None},
            "basis": {"groups": groups, "dcls_month": basis_month},
            "is_estimated": is_estimated,
            "notes": notes,
            "count": len(products),
            "products": products,
            "from_cache": cached,
        }
    )


# ── F3003 상세 조회 ───────────────────────────────────────────
def get_mortgage_product_detail(product_id: str, mrtg_type: str = "") -> dict:
    parts = str(product_id).split(":")
    if len(parts) != 3 or not all(parts):
        return fail(BAD_ARGUMENT, "product_id 는 '권역코드:금융회사코드:상품코드' 형식이어야 합니다.")
    if mrtg_type and mrtg_type not in _MRTG:
        return fail(BAD_ARGUMENT, f"mrtg_type 은 {', '.join(_MRTG)} 중 하나입니다.")
    group, fin_co_no, fin_prdt_cd = parts
    data, error = _fetch_group(group, fin_co_no)
    if error:
        return error
    bases = [r for r in _latest_rows(data["baseList"]) if r.get("fin_prdt_cd") == fin_prdt_cd and r.get("fin_co_no") == fin_co_no]
    if not bases:
        return fail(NOT_FOUND, f"상품 {product_id} 을(를) 현재 공시에서 찾지 못했습니다.")
    base = bases[0]
    options = []
    for o in data["optionList"]:
        if o.get("fin_prdt_cd") != fin_prdt_cd or o.get("fin_co_no") != fin_co_no:
            continue
        if o.get("dcls_month") not in (None, "", base.get("dcls_month")):
            continue
        if mrtg_type and o.get("mrtg_type") != _MRTG[mrtg_type]:
            continue
        options.append(
            {
                "mrtg_type_nm": o.get("mrtg_type_nm"),
                "rpay_type_nm": o.get("rpay_type_nm"),
                "lend_rate_type_nm": o.get("lend_rate_type_nm"),
                "rate_min": _to_float(o.get("lend_rate_min")),
                "rate_max": _to_float(o.get("lend_rate_max")),
                "rate_avg": _to_float(o.get("lend_rate_avg")),
            }
        )
    info = {
        "product_id": product_id,
        "kor_co_nm": base.get("kor_co_nm"),
        "fin_prdt_nm": base.get("fin_prdt_nm"),
        "loan_lmt": base.get("loan_lmt"),
        "erly_rpay_fee": base.get("erly_rpay_fee"),
        "dly_rate": base.get("dly_rate"),
        "join_way": base.get("join_way"),
        "dcls_month": base.get("dcls_month"),
        "dcls_strt_day": base.get("dcls_strt_day"),
        "dcls_end_day": base.get("dcls_end_day"),
        "options": options,
        "from_cache": data["from_cache"],
    }
    if not any(o["rate_min"] is not None or o["rate_avg"] is not None for o in options):
        # 판단에 필요한 금리가 없다. 값을 채우지 않고 판단하지 않은 채 둔다.
        error = fail(NO_RATE, f"{info['kor_co_nm']} {info['fin_prdt_nm']}: 공시된 금리 옵션이 없어 판단할 수 없습니다.")
        error["data"] = {k: info[k] for k in ("product_id", "kor_co_nm", "fin_prdt_nm")}
        return error
    return ok(info)


def tools() -> list[Tool]:
    group_enum = list(get_settings()["fss"]["group_names"])
    return [
        Tool(
            name="search_mortgage_products",
            kind=EXTERNAL,
            description=(
                "주택담보대출 후보 상품 목록이 필요할 때 쓴다. 아직 상품을 정하지 않았거나 다른 권역(보험·저축은행 등)의 "
                "대안을 찾을 때 부른다. 권역을 주지 않으면 은행 권역이다. 결과의 product_id 를 상세 조회에 넘긴다."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "groups": {"type": "array", "items": {"type": "string", "enum": group_enum}, "description": "권역 코드 목록 (020000 은행, 050000 보험, 030300 저축은행, 030200 여신전문)"},
                    "as_of": {"type": "string", "description": "기준일 YYYY-MM-DD (현재 공시와 다르면 추정임을 밝힘)"},
                    "company": {"type": "string", "description": "금융회사 이름 일부 (예: 우리은행)"},
                },
                "required": [],
            },
            output={
                "requested": "요청한 권역·기준일", "basis": "실제 조회한 권역·공시월", "is_estimated": "요청 기준과 실제 기준이 다르면 true",
                "notes": "추정 사유", "products": "[{product_id, kor_co_nm, fin_prdt_nm, group, group_nm}]",
            },
            func=search_mortgage_products,
            source=SOURCE,
            errors=[INVALID_KEY, BAD_ARGUMENT, NO_RESULT, UPSTREAM_ERROR],
        ),
        Tool(
            name="get_mortgage_product_detail",
            kind=EXTERNAL,
            description=(
                "후보 상품의 금리(최저·최고·평균), 금리 유형, 상환 방식, 한도 문구가 필요할 때 쓴다. 월 상환액을 계산해 "
                "조건에 맞는지 판단하기 전에 상품마다 부른다. product_id 는 후보 조회 결과에서 받는다."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "product_id": {"type": "string", "description": "후보 조회가 준 상품 식별자"},
                    "mrtg_type": {"type": "string", "enum": list(_MRTG), "description": "담보 유형 (아파트, 아파트외)"},
                },
                "required": ["product_id"],
            },
            output={
                "product_id": "상품 식별자", "kor_co_nm": "금융회사", "fin_prdt_nm": "상품명", "loan_lmt": "대출 한도 공시 문구",
                "options": "[{mrtg_type_nm, rpay_type_nm, lend_rate_type_nm, rate_min, rate_max, rate_avg}] (연 %, 공시값 그대로)",
                "dcls_month": "공시월",
            },
            func=get_mortgage_product_detail,
            source=SOURCE,
            errors=[INVALID_KEY, BAD_ARGUMENT, NOT_FOUND, NO_RATE, UPSTREAM_ERROR],
        ),
    ]
