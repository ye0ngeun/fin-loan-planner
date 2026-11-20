"""상환액 계산과 제약 판단 (F3008)"""

import pytest

from app.agent import conditions as C
from app.agent.judge import judge, loan_amount
from app.services.loan_calc import max_principal, max_rate, repayment


def test_repayment_equal_installment():
    # 4억, 연 4%, 30년 원리금균등 ≈ 190.97만원
    assert repayment(40000, 4.0, 360, "원리금균등").first_month == pytest.approx(190.97, abs=0.05)
    r = repayment(12000, 6.0, 12, "원금균등")
    assert r.first_month > r.last_month
    assert repayment(10000, 3.0, 12, "만기일시").first_month == pytest.approx(25.0)


def test_reverse_calculations_are_inverse():
    rate = max_rate(40000, 200, 360)
    assert repayment(40000, rate, 360, "원리금균등").first_month == pytest.approx(200, abs=0.01)
    p = max_principal(200, 4.0, 360)
    assert repayment(p, 4.0, 360, "원리금균등").first_month == pytest.approx(200, abs=0.01)
    assert max_rate(40000, 50, 360) is None  # 0% 여도 넘음


def test_loan_amount_sources():
    assert loan_amount({"loan_amount_manwon": 30000}, None)[0] == 30000
    amount, basis = loan_amount({"price_manwon": 60000, "own_funds_manwon": 20000}, None)
    assert amount == 40000 and basis["estimated"] is False
    amount, basis = loan_amount({"own_funds_manwon": 20000}, {"median_manwon": 90000})
    assert amount == 70000 and basis["estimated"] is True
    assert loan_amount({"price_manwon": 60000}, None)[0] is None  # 보유 자금 없이는 계산하지 않음


def _detail(pid, options, ok=True):
    product = {"product_id": pid, "kor_co_nm": "가은행", "fin_prdt_nm": pid, "group_nm": "은행"}
    if not ok:
        return {"product": product, "result": {"ok": False, "error": {"code": "NO_RATE", "message": "x"}}}
    return {"product": product, "result": {"ok": True, "data": {"options": options, "dcls_month": "202611", "loan_lmt": None}}}


def _opt(rate_type, avg, mn=None, mx=None, rpay="분할상환방식"):
    return {"lend_rate_type_nm": rate_type, "rpay_type_nm": rpay, "mrtg_type_nm": "아파트", "rate_avg": avg, "rate_min": mn, "rate_max": mx}


def test_judge_compares_payment_with_cap_and_explains_exclusions():
    details = [
        _detail("A", [_opt("고정금리", 3.8, 3.5, 4.5)]),
        _detail("B", [_opt("고정금리", 4.8, 4.5, 5.5)]),
        _detail("C", [], ok=False),
        _detail("D", [_opt("변동금리", None, 3.6, 4.0)]),
    ]
    out = judge(details, {"max_monthly_payment_manwon": 190}, 40000, 30)
    assert [e["product_id"] for e in out["satisfied"]] == ["D", "A"]
    assert out["satisfied"][0]["option"]["rate_basis"] == "최저"  # 평균이 비면 최저를 쓰고 표시
    reasons = {e["product_id"]: e["reason"] for e in out["excluded"]}
    assert "상한" in reasons["B"] and "판단하지 않음" in reasons["C"]
    assert out["reverse"]["max_rate_for_cap"] is not None


def test_judge_rate_type_preference_and_relax():
    details = [_detail("A", [_opt("변동금리", 3.6)])]
    strict = judge(details, {"rate_type": "고정금리"}, 40000, 30)
    assert not strict["satisfied"] and "선호 금리 유형" in strict["excluded"][0]["reason"]
    assert judge(details, {"rate_type": "고정금리"}, 40000, 30, relax_rate_type=True)["satisfied"]


def test_judge_ratio_constraint_uses_income():
    details = [_detail("A", [_opt("고정금리", 4.0)])]
    out = judge(details, {"monthly_income_manwon": 500, "max_payment_ratio_pct": 30}, 40000, 30)
    assert out["payment_cap"] == 150 and not out["satisfied"]
    assert judge(details, {}, 40000, 30)["satisfied"][0]["checks"] == ["제약 조건 없음 (월 상환액 순 정렬)"]


def test_conditions_clean_and_merge():
    raw = {"price_manwon": "60000", "region": "마포구", "rate_type": "고정", "groups": ["보험", "x"], "visit_time": "14:00",
           "visit_date": "2026-13-01", "term_years": 80, "unknown": 1}
    c = C.clean(raw)
    assert c == {"price_manwon": 60000.0, "region": "마포구", "rate_type": "고정금리", "groups": ["050000"], "visit_time": "14:00"}
    merged, changed = C.merge({"price_manwon": 60000.0, "own_funds_manwon": 20000.0}, {"max_monthly_payment_manwon": 150.0}, ["own_funds_manwon"])
    assert merged == {"price_manwon": 60000.0, "max_monthly_payment_manwon": 150.0}
    assert set(changed) == {"max_monthly_payment_manwon", "own_funds_manwon"}
