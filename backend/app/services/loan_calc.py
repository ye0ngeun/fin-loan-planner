"""월 상환액과 역산 계산. 금융 숫자는 LLM 이 아니라 이 모듈이 공시 금리와 사용자 입력으로 만든다. (F3008)

2회차 fin-product-rag/src/services/loan_calc.py 의 repayment·methods_for 를 옮겨 왔고,
제약 판단용 역산(max_principal, max_rate)을 더했다. 금리는 연 %, 금액은 만원 단위.
"""

from __future__ import annotations

from dataclasses import dataclass

METHODS = ("원리금균등", "원금균등", "만기일시")

# 금감원 상환방식 이름 -> 계산 방식. 분할상환은 매달 내는 돈이 가장 일정한 원리금균등으로 판단한다.
RPAY_TO_METHOD = {"분할상환방식": "원리금균등", "만기일시상환방식": "만기일시"}


@dataclass
class Repayment:
    method: str
    annual_rate: float
    first_month: float  # 첫 달 상환액 (만원)
    last_month: float
    total_interest: float
    total_payment: float


def repayment(principal: float, annual_rate: float, months: int, method: str) -> Repayment:
    """대출금 principal(만원), 연금리 annual_rate(%), 기간 months 개월."""
    if method not in METHODS:
        raise ValueError(f"상환방식은 {METHODS} 중 하나: {method}")
    if principal <= 0 or months <= 0:
        return Repayment(method, annual_rate, 0, 0, 0, 0)
    r = annual_rate / 100 / 12
    if method == "원리금균등":
        pay = principal / months if r == 0 else principal * r * (1 + r) ** months / ((1 + r) ** months - 1)
        total = pay * months
        return Repayment(method, annual_rate, pay, pay, total - principal, total)
    if method == "원금균등":
        base = principal / months
        interest = principal * r * (months + 1) / 2
        return Repayment(method, annual_rate, base + principal * r, base + base * r, interest, principal + interest)
    interest = principal * r * months  # 만기일시: 매달 이자만, 마지막 달에 원금
    return Repayment(method, annual_rate, principal * r, principal * r + principal, interest, principal + interest)


def max_principal(monthly_cap: float, annual_rate: float, months: int) -> float:
    """원리금균등으로 월 monthly_cap 이하가 되는 최대 대출금 (만원). 역산 기준 제시용."""
    if monthly_cap <= 0 or months <= 0:
        return 0.0
    r = annual_rate / 100 / 12
    if r == 0:
        return monthly_cap * months
    return monthly_cap * ((1 + r) ** months - 1) / (r * (1 + r) ** months)


def max_rate(principal: float, monthly_cap: float, months: int, hi: float = 30.0) -> float | None:
    """원리금균등으로 월 monthly_cap 이하가 되려면 적용 금리가 최대 몇 % 여야 하는지 (이분 탐색).

    금리 0% 에서도 넘으면 None (기간을 늘리거나 대출금을 줄여야 한다).
    """
    if principal <= 0:
        return hi
    if repayment(principal, 0, months, "원리금균등").first_month > monthly_cap:
        return None
    lo = 0.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if repayment(principal, mid, months, "원리금균등").first_month <= monthly_cap:
            lo = mid
        else:
            hi = mid
    return lo
