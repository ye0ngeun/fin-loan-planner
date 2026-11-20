"""국토교통부 아파트 매매 실거래가를 감싼 외부 API 툴: get_apartment_prices (시세 조회).

목표 지역의 최근 거래가 분포(중위·사분위)를 돌려준다. 사용자가 집값을 말하지 않았을 때
필요 대출액을 계산하는 기준으로 쓰며, 이 경우 응답에 '과거 거래 기준 추정'임을 밝힌다.
지원 지역: 서울특별시 25개 구 (지역코드는 국토교통부 법정동코드 앞 5자리).
"""

from __future__ import annotations

import statistics
import time
import xml.etree.ElementTree as ET
from datetime import date
from urllib.parse import unquote

from app.config import fake_mode, get_env, get_settings
from app.tools import http
from app.tools.base import (
    BAD_ARGUMENT,
    EXTERNAL,
    INVALID_KEY,
    NO_RESULT,
    UNSUPPORTED_REGION,
    UPSTREAM_ERROR,
    Tool,
    fail,
    ok,
    scrub,
)

SOURCE = "국토교통부 아파트 매매 실거래가 자료 (공공데이터포털)"
SEOUL_GU = {
    "종로구": "11110", "중구": "11140", "용산구": "11170", "성동구": "11200", "광진구": "11215",
    "동대문구": "11230", "중랑구": "11260", "성북구": "11290", "강북구": "11305", "도봉구": "11320",
    "노원구": "11350", "은평구": "11380", "서대문구": "11410", "마포구": "11440", "양천구": "11470",
    "강서구": "11500", "구로구": "11530", "금천구": "11545", "영등포구": "11560", "동작구": "11590",
    "관악구": "11620", "서초구": "11650", "강남구": "11680", "송파구": "11710", "강동구": "11740",
}
# 공공데이터포털 게이트웨이의 인증키 관련 사유 코드
_KEY_REASONS = {"20", "30", "31", "32"}


def normalize_region(region: str) -> str | None:
    """'마포', '마포구', '서울 마포구', '서울특별시 마포구' → '마포구'. 서울 구가 아니면 None."""
    text = str(region or "").replace("서울특별시", "").replace("서울시", "").replace("서울", "").strip()
    for word in text.split() or [""]:
        name = word if word.endswith("구") else f"{word}구"
        if name in SEOUL_GU:
            return name
    return None


def _months(n: int, end_month: str = "", today: date | None = None) -> tuple[list[str], bool, str]:
    """end_month(YYYYMM)부터 거꾸로 n개월. 지난달보다 늦은 달은 집계가 덜 되어 지난달로 바꾸고 추정임을 표시한다."""
    today = today or date.today()
    last = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
    note = ""
    if end_month:
        y, m = int(end_month[:4]), int(end_month[4:6])
        if (y, m) > last:
            note = f"{end_month[:4]}-{end_month[4:]} 거래는 아직 신고 기간(30일)이라 {last[0]}-{last[1]:02d} 까지의 거래로 대신합니다."
            y, m = last
    else:
        y, m = last
    out = []
    for _ in range(n):
        out.append(f"{y}{m:02d}")
        y, m = (y, m - 1) if m > 1 else (y - 1, 12)
    return out[::-1], bool(note), note


def _num(value) -> float | None:
    try:
        return float(str(value).replace(",", "").strip())
    except (TypeError, ValueError):
        return None


def _parse(xml_text: str) -> tuple[list[dict] | None, dict | None]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return None, fail(UPSTREAM_ERROR, "실거래가 API 응답을 읽지 못했습니다.")
    if root.tag == "OpenAPI_ServiceResponse":
        reason = (root.findtext(".//returnReasonCode") or "").strip()
        if reason in _KEY_REASONS:
            return None, fail(INVALID_KEY, "국토교통부 실거래가 API 키(MOLIT_API_KEY)를 확인하세요.")
        return None, fail(UPSTREAM_ERROR, f"공공데이터포털 오류 {reason or '?'}")
    code = (root.findtext("header/resultCode") or "").strip()
    if code not in ("000", "00"):
        return None, fail(UPSTREAM_ERROR, f"실거래가 API 오류 {code}")
    return [{c.tag: (c.text or "").strip() for c in item} for item in root.iter("item")], None


def get_apartment_prices(region: str, months: int | None = None, end_month: str = "", area_min: float | None = None, area_max: float | None = None) -> dict:
    cfg = get_settings()["molit"]
    gu = normalize_region(region)
    if gu is None:
        return fail(UNSUPPORTED_REGION, f"'{region}' 은(는) 지원하는 지역(서울특별시 25개 구)이 아닙니다.")
    months = int(months or cfg["default_months"])
    if not 1 <= months <= 12:
        return fail(BAD_ARGUMENT, "months 는 1~12 사이여야 합니다.")
    if end_month and not (len(end_month) == 6 and end_month.isdigit()):
        return fail(BAD_ARGUMENT, "end_month 는 YYYYMM 형식이어야 합니다.")
    api_key = unquote(get_env("MOLIT_API_KEY"))
    if not api_key and not fake_mode():
        return fail(INVALID_KEY, "국토교통부 실거래가 API 키(MOLIT_API_KEY)가 설정되지 않았습니다.")

    lawd = SEOUL_GU[gu]
    month_list, estimated, note = _months(months, end_month)
    deals = []
    for i, ym in enumerate(month_list):
        params = {"serviceKey": api_key, "LAWD_CD": lawd, "DEAL_YMD": ym, "pageNo": 1, "numOfRows": cfg["num_of_rows"]}
        try:
            status, text = http.get("molit", cfg["base_url"], params, timeout=cfg["timeout_sec"])
        except Exception as e:  # noqa: BLE001
            return fail(UPSTREAM_ERROR, f"실거래가 API에 연결하지 못했습니다. ({scrub(e)[:120]})")
        if status != 200:
            return fail(UPSTREAM_ERROR, f"실거래가 API가 오류로 답했습니다. (HTTP {status})")
        items, error = _parse(text)
        if error:
            return error
        for it in items:
            if (it.get("cdealType") or "").upper() == "O":  # 해제된 거래 제외
                continue
            price, area = _num(it.get("dealAmount")), _num(it.get("excluUseAr"))
            if not price or not area:
                continue
            if (area_min and area < area_min) or (area_max and area > area_max):
                continue
            y, m, d = it.get("dealYear", ""), it.get("dealMonth", ""), it.get("dealDay", "")
            deals.append(
                {
                    "apt_nm": it.get("aptNm"),
                    "umd_nm": it.get("umdNm"),
                    "area_m2": area,
                    "floor": it.get("floor"),
                    "price_manwon": int(price),
                    "deal_date": f"{y}-{int(m):02d}-{int(d):02d}" if y and m.isdigit() and d.isdigit() else "",
                }
            )
        if i < len(month_list) - 1:
            time.sleep(cfg["request_interval_sec"])

    basis = {"region": f"서울특별시 {gu}", "lawd_cd": lawd, "months": month_list, "area_min": area_min, "area_max": area_max}
    if not deals:
        return fail(NO_RESULT, f"서울특별시 {gu}의 {month_list[0]}~{month_list[-1]} 조건에 맞는 아파트 거래가 없습니다.")
    prices = sorted(d["price_manwon"] for d in deals)
    q = statistics.quantiles(prices, n=4) if len(prices) >= 2 else [prices[0]] * 3
    recent = sorted(deals, key=lambda d: d["deal_date"], reverse=True)[:5]
    return ok(
        {
            "requested": {"region": region, "months": months, "end_month": end_month or None},
            "basis": basis,
            "is_estimated": estimated,
            "notes": [note] if note else [],
            "count": len(prices),
            "median_manwon": int(statistics.median(prices)),
            "p25_manwon": int(q[0]),
            "p75_manwon": int(q[2]),
            "min_manwon": prices[0],
            "max_manwon": prices[-1],
            "recent_deals": recent,
        }
    )


def tools() -> list[Tool]:
    return [
        Tool(
            name="get_apartment_prices",
            kind=EXTERNAL,
            description=(
                "사려는 집의 지역 시세(최근 아파트 실거래가 중위값·사분위)가 필요할 때 쓴다. 사용자가 집값을 말하지 않아 "
                "필요 대출액을 정할 수 없거나, 말한 집값이 그 지역 시세와 맞는지 보여 줄 때 부른다. 서울 25개 구만 지원한다."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "region": {"type": "string", "description": "서울 구 이름 (예: 마포구)"},
                    "months": {"type": "integer", "description": "최근 몇 개월 (기본 3, 1~12)"},
                    "end_month": {"type": "string", "description": "마지막 계약년월 YYYYMM (기본: 지난달)"},
                    "area_min": {"type": "number", "description": "전용면적 하한 (㎡)"},
                    "area_max": {"type": "number", "description": "전용면적 상한 (㎡)"},
                },
                "required": ["region"],
            },
            output={
                "basis": "실제 조회한 지역·계약월", "is_estimated": "요청한 기간과 실제 기간이 다르면 true", "count": "거래 수",
                "median_manwon": "중위 거래가(만원)", "p25_manwon/p75_manwon": "사분위(만원)", "recent_deals": "최근 거래 5건",
            },
            func=get_apartment_prices,
            source=SOURCE,
            errors=[INVALID_KEY, BAD_ARGUMENT, UNSUPPORTED_REGION, NO_RESULT, UPSTREAM_ERROR],
        )
    ]
