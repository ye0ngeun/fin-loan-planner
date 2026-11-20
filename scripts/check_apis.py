"""기획 검증 2.7: 외부 API 를 한 번씩 실제로 호출하고, 응답이 Tool 출력 구조를 채우는지 확인한다.

실행 (저장소 루트, .env 에 키를 넣은 뒤):
    python scripts/check_apis.py            # 4개 외부 API 툴 + LLM 1회
    python scripts/check_apis.py --no-llm   # LLM 호출 생략

결과는 화면에만 출력한다. 금감원 공시값이 든 출력은 커밋하지 않는다 (sources.md 3-1).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from app.config import key_status  # noqa: E402
from app.tools import build_registry  # noqa: E402


def show(title: str, res: dict, keys: list[str]) -> None:
    status = "성공" if res["ok"] else f"실패 {res['error']['code']}"
    print(f"\n[{title}] {status}")
    if not res["ok"]:
        print("  ", res["error"]["message"])
        return
    data = res["data"]
    for k in keys:
        print(f"   {k}: {'있음' if data.get(k) not in (None, [], '') else '비어 있음'}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--region", default="마포구")
    args = ap.parse_args()
    print("키 설정 여부:", key_status())
    reg = build_registry(include_rag=False)

    res = reg.call("search_mortgage_products")
    show("금감원 후보 조회", res, ["basis", "products", "is_estimated"])
    if res["ok"]:
        print(f"   후보 {res['data']['count']}건, 공시월 {res['data']['basis']['dcls_month']}")
        empty = 0
        for p in res["data"]["products"][:5]:
            d = reg.call("get_mortgage_product_detail", product_id=p["product_id"])
            empty += not d["ok"]
            if d["ok"]:
                o = d["data"]["options"][0]
                print(f"   상세 {p['product_id']}: 옵션 {len(d['data']['options'])}개, 첫 옵션 키 {sorted(k for k, v in o.items() if v is not None)}")
            else:
                print(f"   상세 {p['product_id']}: {d['error']['code']}")
        print(f"   상위 5개 중 상세 실패(금리 미공시 등) {empty}건")
    show("금감원 미래 기준일(추정 표시)", reg.call("search_mortgage_products", as_of="2099-01-01"), ["is_estimated", "notes"])

    show("국토부 실거래 시세", reg.call("get_apartment_prices", region=args.region), ["basis", "median_manwon", "recent_deals"])
    show("국토부 결과 없음 확인", reg.call("get_apartment_prices", region=args.region, area_min=500), [])

    show("카카오 은행 지점", reg.call("search_bank_branches", region=args.region, bank_name="우리은행"), ["branches"])
    show("카카오 결과 없음 확인", reg.call("search_bank_branches", region=args.region, bank_name="없는이름은행"), [])

    if not args.no_llm:
        from datetime import date

        from app.agent.llm import GmsLLM

        try:
            llm = GmsLLM()
            out = llm.analyze("마포구 6억 아파트, 현금 2억, 월 200만원 이하 주담대 찾아줘", {}, reg.specs(), date.today())
            print("\n[LLM 요청 분석]", out)
            print("   Tool 선택:", out.get("tools"))
        except Exception as e:  # noqa: BLE001
            print("\n[LLM 요청 분석] 실패:", type(e).__name__)


if __name__ == "__main__":
    main()
