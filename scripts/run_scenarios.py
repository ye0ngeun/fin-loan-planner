"""대표 시나리오 검증 4.7: 대표 요청을 차례로 실행하고 요청별 결과·실행 로그·처리 시간을 남긴다.

실행 (저장소 루트):
    python scripts/run_scenarios.py                 # .env 의 실제 키로 (로컬 PC)
    python scripts/run_scenarios.py --fake          # 키·네트워크 없이 가상 응답 + 규칙 대역 LLM
    python scripts/run_scenarios.py --fake --out docs/samples

출력: <out>/scenarios.md (요약표), <out>/<번호>_<이름>.json (요청별 최종 응답·이벤트 전체)
실제 키로 만든 결과에는 금감원 공시값이 들어가므로 저장소에 올리지 않고 제출 zip 에만 넣는다.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

# (번호, 이름, 기대 동작, 같은 세션에서 보낼 메시지들, 키를 지울 환경변수)
SCENARIOS = [
    ("1", "한번에_충족", "재계획 없이 충족 상품 추천, 지점 안내", ["마포구 6억 아파트, 현금 2억 있어요. 월 200만원 이하로 갚을 주담대 찾아줘"], []),
    ("2", "재계획", "고정금리·180만원 → 금리 유형 완화 → 보험 권역 추가 후 충족", ["마포구 6억 아파트, 현금 2억 있어요. 고정금리로 월 180만원 이하 주담대 찾아줘"], []),
    ("3", "상한_도달", "재계획 3회 후 멈추고 역산 기준과 함께 조정 요청", ["마포구 6억 아파트, 현금 2억 있어요. 월 100만원 이하로 갚을 주담대 찾아줘"], []),
    ("4", "Tool_실패", "카카오 키 없음 → 지점 검색만 실패, 추천은 정상", ["마포구 6억 아파트, 현금 2억 있어요. 월 200만원 이하로 갚을 주담대 찾아줘"], ["KAKAO_REST_API_KEY"]),
    ("5", "Tool_불필요", "인사 → Tool 호출 없이 응답", ["안녕하세요, 뭘 할 수 있어요?"], []),
    ("6", "되묻기", "보유 자금·집값이 없어 Tool 호출 전에 되묻기", ["월 150만원 이하로 갚을 주담대 찾아줘"], []),
    ("7", "후속_요청_저장", "조건 하나만 바꾸고 1순위로 상담 계획 저장", [
        "마포구 6억 아파트, 현금 2억 있어요. 월 200만원 이하로 갚을 주담대 찾아줘",
        "월 190만원 이하로 바꿔줘",
        "1번으로 토요일 오후 2시 상담 저장해줘",
    ], []),
]


def _headline(answer: str) -> str:
    """요약표용: 결과를 말하는 줄(충족·못 찾음·저장)을 먼저, 없으면 첫 줄."""
    lines = [x.strip() for x in answer.split("\n") if x.strip()]
    for key in ("**조건을", "상담 계획을 저장", "재계획 상한", "더 필요합니다"):
        for x in lines:
            if key in x:
                return x.replace("**", "")[:70]
    return lines[0].replace("**", "")[:70] if lines else ""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fake", action="store_true", help="가상 응답 + 규칙 대역 LLM")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()
    if args.fake:
        os.environ["PLANNER_FAKE_APIS"] = "1"
        os.environ["PLANNER_FAKE_LLM"] = "1"
    out = Path(args.out or (ROOT / ("docs/samples" if args.fake else "docs/samples/raw")))
    out.mkdir(parents=True, exist_ok=True)

    from app.agent.events import EventBus
    from app.agent.llm import get_llm
    from app.agent.service import PlannerService
    from app.store.plans import PlanStore
    from app.tools import build_registry, fss

    rows = []
    for no, name, expect, messages, drop in SCENARIOS:
        saved = {k: os.environ.pop(k, None) for k in drop}
        fss.clear_cache()
        svc = PlannerService(build_registry(), PlanStore(":memory:"), get_llm, EventBus())
        turns = []
        for msg in messages:
            r = svc.chat(msg, f"scenario-{no}")
            v = r["view"]
            turns.append({
                "message": msg, "answer": r["answer"], "intent": r["intent"], "elapsed_ms": r["elapsed_ms"],
                "conditions": v.get("conditions"), "calls": v.get("calls"), "replans": v.get("replans"),
                "stop_reason": v.get("stop_reason"), "error": r.get("error"), "events": r["trace"],
            })
        for k, val in saved.items():
            if val is not None:
                os.environ[k] = val
        last = turns[-1]
        (out / f"{no}_{name}.json").write_text(json.dumps({"scenario": name, "expect": expect, "turns": turns}, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        calls = [c for t in turns for c in t["calls"] or [] if not c.get("reused")]
        rows.append({
            "no": no, "name": name, "expect": expect, "intent": last["intent"],
            "tools": len(calls), "fails": sum(1 for c in calls if not c["ok"]),
            "replans": sum(len(t["replans"] or []) for t in turns),
            "elapsed": max(t["elapsed_ms"] for t in turns), "answer": _headline(last["answer"]),
        })
        print(f"[{no}] {name}: 의도 {last['intent']}, Tool {rows[-1]['tools']}회(실패 {rows[-1]['fails']}), 재계획 {rows[-1]['replans']}회, {rows[-1]['elapsed']} ms")

    mode = "연습용(가상 응답·규칙 대역 LLM)" if args.fake else "실제 API·GMS"
    lines = [f"# 대표 시나리오 실행 결과 ({mode})", "", "`python scripts/run_scenarios.py" + (" --fake" if args.fake else "") + "` 로 생성. 요청별 전체 이벤트는 같은 폴더의 JSON.", "",
             "| # | 시나리오 | 기대 동작 | 의도 | Tool 호출(실패) | 재계획 | 최대 처리 시간 | 최종 응답 첫 줄 |", "|---|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append(f"| {r['no']} | {r['name']} | {r['expect']} | {r['intent']} | {r['tools']}({r['fails']}) | {r['replans']} | {r['elapsed']} ms | {r['answer'].replace('|', '/')} |")
    (out / "scenarios.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n저장: {out}")


if __name__ == "__main__":
    main()
