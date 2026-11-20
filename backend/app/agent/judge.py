"""제약 적합성 판단. LLM 없이 Tool 결과(공시 금리)와 사용자 조건으로 계산한다. (F3008)

계산식
- 대출금 L = 대출 희망액, 없으면 집값 − 보유 자금 (집값이 없으면 실거래 중위값, 추정으로 표시)
- 월 상환액 = loan_calc.repayment(L, 평균금리, 기간×12, 상환방식) 의 첫 달 금액
  (평균금리가 비어 있으면 최저금리를 쓰고 그 사실을 표시. 둘 다 없으면 판단하지 않음 = NO_RATE)
- 소득 대비 상환 비율 = 월 상환액 / 월 소득 × 100 (제도상 DSR 과 다른 단순 비율)
- 충족: 월 상환액 ≤ 상한 그리고 비율 ≤ 상한 (사용자가 말한 제약만 본다)
역산 기준 (외부 API 로 확인할 수 없는 실제 적용 금리·한도 대신 제시)
- 이 대출금으로 상한을 지키려면 적용 금리가 최대 몇 % 여야 하는지
- 조회한 최저 금리로 상한 안에서 빌릴 수 있는 최대 대출금
"""

from __future__ import annotations

from app.services.loan_calc import RPAY_TO_METHOD, max_principal, max_rate, repayment


def loan_amount(cond: dict, price_info: dict | None) -> tuple[float | None, dict]:
    """필요 대출금과 그 근거. 계산할 수 없으면 (None, 근거)."""
    if cond.get("loan_amount_manwon"):
        return cond["loan_amount_manwon"], {"formula": "대출 희망액", "estimated": False}
    price, estimated = cond.get("price_manwon"), False
    if not price and price_info:
        price, estimated = price_info.get("median_manwon"), True
    if price and cond.get("own_funds_manwon") is not None:
        amount = max(price - cond["own_funds_manwon"], 0)
        src = "실거래 중위값(추정)" if estimated else "입력한 집값"
        return amount, {"formula": f"{src} {price:,.0f}만원 − 보유 자금 {cond['own_funds_manwon']:,.0f}만원", "price": price, "estimated": estimated}
    return None, {"formula": "", "estimated": estimated, "price": price}


def _limit_payment(cond: dict) -> float | None:
    """사용자가 말한 제약에서 월 상환액 상한을 정한다. 둘 다 있으면 작은 쪽."""
    caps = []
    if cond.get("max_monthly_payment_manwon"):
        caps.append(cond["max_monthly_payment_manwon"])
    if cond.get("max_payment_ratio_pct") and cond.get("monthly_income_manwon"):
        caps.append(cond["monthly_income_manwon"] * cond["max_payment_ratio_pct"] / 100)
    return min(caps) if caps else None


def judge(details: list[dict], cond: dict, principal: float, term_years: int, relax_rate_type: bool = False) -> dict:
    """details: [{"product": 후보 정보, "result": 상세 조회 Tool 응답}]"""
    months = term_years * 12
    cap = _limit_payment(cond)
    income = cond.get("monthly_income_manwon")
    want_rate = None if relax_rate_type else cond.get("rate_type")
    satisfied, excluded = [], []

    for item in details:
        product, result = item["product"], item["result"]
        name = f"{product.get('kor_co_nm', '')} {product.get('fin_prdt_nm', '')}".strip()
        base = {"product_id": product["product_id"], "name": name, "kor_co_nm": product.get("kor_co_nm"),
                "fin_prdt_nm": product.get("fin_prdt_nm"), "group_nm": product.get("group_nm")}
        if not result.get("ok"):
            code = result["error"]["code"]
            reason = "공시 금리가 없어 판단하지 않음" if code == "NO_RATE" else f"상세 조회 실패({code})"
            excluded.append({**base, "code": code, "reason": reason})
            continue
        data = result["data"]
        rows = []
        for opt in data["options"]:
            rate, rate_basis = opt.get("rate_avg"), "평균"
            if rate is None:
                rate, rate_basis = opt.get("rate_min"), "최저"
            if rate is None:
                continue
            if want_rate and opt.get("lend_rate_type_nm") != want_rate:
                continue
            method = RPAY_TO_METHOD.get(opt.get("rpay_type_nm") or "", "원리금균등")
            pay = repayment(principal, rate, months, method).first_month
            pay_max = repayment(principal, opt["rate_max"], months, method).first_month if opt.get("rate_max") is not None else None
            rows.append(
                {
                    **opt,
                    "method": method,
                    "rate_used": rate,
                    "rate_basis": rate_basis,
                    "monthly_payment": round(pay, 1),
                    "payment_at_max": round(pay_max, 1) if pay_max is not None else None,
                    "payment_ratio_pct": round(pay / income * 100, 1) if income else None,
                }
            )
        if not rows:
            reason = f"선호 금리 유형({want_rate}) 옵션이 없음" if want_rate else "판단할 수 있는 금리 옵션이 없음"
            excluded.append({**base, "code": "NO_MATCHING_OPTION", "reason": reason})
            continue
        # 만기일시는 매달 이자만 내므로 월 상환액이 낮게 보인다. 분할상환 옵션이 있으면 그쪽을 먼저 본다.
        rows.sort(key=lambda r: (r["method"] == "만기일시", r["monthly_payment"]))
        best = rows[0]
        entry = {
            **base,
            "dcls_month": data.get("dcls_month"),
            "loan_lmt": data.get("loan_lmt"),
            "option": best,
            "option_count": len(rows),
        }
        if cap is not None and best["monthly_payment"] > cap:
            excluded.append(
                {**entry, "code": "OVER_LIMIT", "reason": f"월 상환액 {best['monthly_payment']:,.1f}만원이 상한 {cap:,.1f}만원을 넘음"}
            )
        else:
            entry["checks"] = (
                [f"월 상환액 {best['monthly_payment']:,.1f}만원 ≤ 상한 {cap:,.1f}만원"] if cap is not None else ["제약 조건 없음 (월 상환액 순 정렬)"]
            )
            satisfied.append(entry)

    satisfied.sort(key=lambda e: (e["option"]["method"] == "만기일시", e["option"]["monthly_payment"]))
    rates = [e["option"]["rate_min"] for e in satisfied + excluded if e.get("option") and e["option"].get("rate_min") is not None]
    reverse = {}
    if cap is not None:
        need = max_rate(principal, cap, months)
        reverse["max_rate_for_cap"] = round(need, 2) if need is not None else None
        if rates:
            best_rate = min(rates)
            reverse["best_rate_min"] = best_rate
            reverse["max_principal_at_best_rate"] = round(max_principal(cap, best_rate, months), 0)
    return {
        "principal": principal,
        "term_years": term_years,
        "payment_cap": round(cap, 1) if cap is not None else None,
        "rate_type_filter": want_rate,
        "satisfied": satisfied,
        "excluded": excluded,
        "reverse": reverse,
    }
