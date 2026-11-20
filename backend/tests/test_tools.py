"""Tool 명세·정상/실패 응답 확인. (F3001 ~ F3004, F3022, NF3001, NF3003)"""

import pytest

from app.tools import http, rag
from app.tools.base import EXTERNAL, LOCAL, Tool, ToolRegistry, fail, ok, scrub
from tests.conftest import TEST_KEY

EXTERNAL_TOOLS = {"search_mortgage_products", "get_mortgage_product_detail", "get_apartment_prices", "search_bank_branches"}


def _is_fail(result, code=None):
    assert result["ok"] is False and set(result["error"]) == {"code", "message"}
    if code:
        assert result["error"]["code"] == code, result
    return result


# ── F3001 명세와 등록 ─────────────────────────────────────────
def test_registry_lists_external_and_local_tools(registry):
    cards = {c["name"]: c for c in registry.cards()}
    assert {n for n, c in cards.items() if c["kind"] == EXTERNAL} == EXTERNAL_TOOLS
    assert {n for n, c in cards.items() if c["kind"] == LOCAL} == {"search_loan_docs"}
    for c in cards.values():
        assert c["description"] and c["parameters"]["type"] == "object" and c["output"] and c["errors"] and c["source"]


def test_agent_gets_only_name_description_schema(registry):
    for spec in registry.specs():
        assert set(spec) == {"name", "description", "parameters"}


def test_registry_rejects_duplicates_and_unknown_tools(registry):
    with pytest.raises(ValueError):
        registry.register(registry.get("search_bank_branches"))
    _is_fail(registry.call("no_such_tool"), "BAD_ARGUMENT")


def test_tool_never_raises():
    def boom(**_):
        raise RuntimeError(f"connection failed auth={TEST_KEY}")

    reg = ToolRegistry()
    reg.register(Tool("boom", EXTERNAL, "d", {"type": "object", "properties": {"x": {"type": "string"}}, "required": []}, {}, boom))
    res = _is_fail(reg.call("boom"), "UPSTREAM_ERROR")
    assert TEST_KEY not in res["error"]["message"]
    _is_fail(reg.call("boom", y=1), "BAD_ARGUMENT")  # 명세에 없는 입력


def test_scrub_hides_keys_by_value_and_name(monkeypatch):
    monkeypatch.setenv("FSS_API_KEY", "abcdef123456")
    text = scrub("GET /x?auth=abcdef123456&serviceKey=zzz Authorization: KakaoAK qwerty")
    assert "abcdef123456" not in text and "zzz" not in text and "qwerty" not in text


def test_ok_fail_shapes():
    assert ok(1) == {"ok": True, "data": 1}
    assert fail("X", "m") == {"ok": False, "error": {"code": "X", "message": "m"}}


# ── F3002 후보 조회 ───────────────────────────────────────────
def test_search_default_group_is_bank(registry):
    res = registry.call("search_mortgage_products")
    assert res["ok"]
    d = res["data"]
    assert d["basis"]["groups"] == ["020000"] and d["requested"]["groups"] == ["020000"]
    assert d["is_estimated"] is False and d["count"] == 5
    assert {p["product_id"] for p in d["products"]} >= {"020000:0010001:1054", "020000:0010030:IB500"}


def test_search_marks_estimate_when_basis_differs(registry):
    res = registry.call("search_mortgage_products", as_of="2027-03-01")
    assert res["ok"] and res["data"]["is_estimated"] is True
    assert "현재 공시" in res["data"]["notes"][0]


def test_search_failures(registry, monkeypatch):
    _is_fail(registry.call("search_mortgage_products", groups=["999999"]), "BAD_ARGUMENT")
    _is_fail(registry.call("search_mortgage_products", as_of="내일"), "BAD_ARGUMENT")
    _is_fail(registry.call("search_mortgage_products", groups=["030200"]), "NO_RESULT")  # 픽스처에 상품 없음
    monkeypatch.delenv("FSS_API_KEY")
    _is_fail(registry.call("search_mortgage_products"), "INVALID_KEY")


def test_search_key_error_from_api(registry):
    http.set_transport(lambda *a: (200, '{"result": {"err_cd": "010", "err_msg": "미등록 인증키"}}'))
    _is_fail(registry.call("search_mortgage_products"), "INVALID_KEY")


def test_search_network_error_is_scrubbed(registry):
    def down(service, url, params, headers, timeout):
        raise ConnectionError(f"{url}?auth={params['auth']} refused")

    http.set_transport(down)
    res = _is_fail(registry.call("search_mortgage_products"), "UPSTREAM_ERROR")
    assert TEST_KEY not in res["error"]["message"]


# ── F3003 상세 조회 ───────────────────────────────────────────
def test_detail_follows_search_ids(registry):
    ids = [p["product_id"] for p in registry.call("search_mortgage_products")["data"]["products"]]
    res = registry.call("get_mortgage_product_detail", product_id=ids[0])
    assert res["ok"] and res["data"]["product_id"] == ids[0]
    assert res["data"]["options"] and {"rate_min", "rate_max", "rate_avg", "rpay_type_nm", "lend_rate_type_nm"} <= set(res["data"]["options"][0])


def test_detail_uses_latest_disclosure_only(registry):
    d = registry.call("get_mortgage_product_detail", product_id="020000:0010001:1054")["data"]
    assert d["dcls_month"] == "202611"
    assert 2.2 not in [o["rate_avg"] for o in d["options"]]  # 지난달 공시 옵션은 버림


def test_detail_mrtg_filter(registry):
    d = registry.call("get_mortgage_product_detail", product_id="020000:0010001:1054", mrtg_type="아파트")["data"]
    assert {o["mrtg_type_nm"] for o in d["options"]} == {"아파트"}


def test_detail_missing_rate_is_not_filled(registry):
    res = _is_fail(registry.call("get_mortgage_product_detail", product_id="020000:0010030:IB500"), "NO_RATE")
    assert "rate" not in str(res.get("data", {}))


def test_detail_failures(registry, monkeypatch):
    _is_fail(registry.call("get_mortgage_product_detail"), "BAD_ARGUMENT")
    _is_fail(registry.call("get_mortgage_product_detail", product_id="1054"), "BAD_ARGUMENT")
    _is_fail(registry.call("get_mortgage_product_detail", product_id="020000:0010001:NOPE"), "NOT_FOUND")
    monkeypatch.delenv("FSS_API_KEY")
    _is_fail(registry.call("get_mortgage_product_detail", product_id="020000:0010001:1054"), "INVALID_KEY")


# ── 시세 조회 ────────────────────────────────────────────────
def test_prices_default_three_months(registry):
    res = registry.call("get_apartment_prices", region="마포")
    assert res["ok"]
    d = res["data"]
    assert d["basis"]["region"] == "서울특별시 마포구" and len(d["basis"]["months"]) == 3
    assert d["count"] == 24  # 달마다 8건, 해제 거래 제외
    assert d["p25_manwon"] <= d["median_manwon"] <= d["p75_manwon"]
    assert 55000 not in [x["price_manwon"] for x in d["recent_deals"]]


def test_prices_future_month_is_estimated(registry):
    d = registry.call("get_apartment_prices", region="마포구", end_month="209912")["data"]
    assert d["is_estimated"] is True and "신고 기간" in d["notes"][0]


def test_prices_failures(registry, monkeypatch):
    _is_fail(registry.call("get_apartment_prices", region="해운대구"), "UNSUPPORTED_REGION")
    _is_fail(registry.call("get_apartment_prices", region="마포구", months=20), "BAD_ARGUMENT")
    _is_fail(registry.call("get_apartment_prices", region="강남구"), "NO_RESULT")  # 픽스처에 거래 없음
    http.set_transport(lambda *a: (200, http.fixture_path("molit", "key_error.xml").read_text(encoding="utf-8")))
    _is_fail(registry.call("get_apartment_prices", region="마포구"), "INVALID_KEY")
    monkeypatch.delenv("MOLIT_API_KEY")
    _is_fail(registry.call("get_apartment_prices", region="마포구"), "INVALID_KEY")


# ── F3004 보조 정보 ──────────────────────────────────────────
def test_branches_return_name_address_link(registry):
    res = registry.call("search_bank_branches", region="마포구", bank_name="우리은행")
    assert res["ok"] and res["data"]["count"] == 2
    b = res["data"]["branches"][0]
    assert b["name"].startswith("우리은행") and b["address"] and b["url"].startswith("http")
    assert "취급" in res["data"]["notes"][0]  # 지점과 상품을 임의로 묶지 않는다


def test_branches_failures(registry, monkeypatch):
    _is_fail(registry.call("search_bank_branches", region="마포구", bank_name="없는은행"), "NO_RESULT")
    _is_fail(registry.call("search_bank_branches", region="마"), "BAD_ARGUMENT")
    http.set_transport(lambda *a: (401, "{}"))
    _is_fail(registry.call("search_bank_branches", region="마포구"), "INVALID_KEY")
    monkeypatch.delenv("KAKAO_REST_API_KEY")
    _is_fail(registry.call("search_bank_branches", region="마포구"), "INVALID_KEY")


# ── F3022 RAG 검색 ───────────────────────────────────────────
def test_rag_no_index(registry, monkeypatch, tmp_path):
    from app.config import get_settings

    monkeypatch.setitem(get_settings()["rag"], "chroma_dir", tmp_path / "none")
    _is_fail(registry.call("search_loan_docs", query="LTV"), "NO_INDEX")


def test_rag_returns_sources_and_filters_low_scores(registry):
    rag.set_searcher(lambda q, k: [
        ("LTV는 주택담보대출비율이다.", {"title": "LTV", "source": "한국주택금융공사 단어사전", "source_url": "https://www.data.go.kr/x"}, 0.82),
        ("관계없는 문서", {"title": "기타"}, 0.1),
    ])
    res = registry.call("search_loan_docs", query="LTV 뜻")
    assert res["ok"] and res["data"]["count"] == 1
    assert res["data"]["documents"][0]["source"] and res["data"]["documents"][0]["url"]
    rag.set_searcher(lambda q, k: [("x", {}, 0.1)])
    _is_fail(registry.call("search_loan_docs", query="LTV"), "NO_RESULT")
    _is_fail(registry.call("search_loan_docs", query="L"), "BAD_ARGUMENT")
