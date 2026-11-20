# Agent 설계 문서

[README](../README.md) · [요구사항](01_요구사항정의서.md) · **Agent 설계** · [Tool 정의서](03_Tool_정의서.md) · [API 명세](04_API_명세서.md) · [검증 결과](05_검증결과.md)

## 1. 메인 Agent 실행 단계 (`backend/app/agent/graph.py`)

```
START → analyze → plan ─┬─(Tool 필요)→ execute → judge ─┬─(충족 0, 상한 전)→ replan → execute …
                        │                               ├─(지점 검색 대기)──→ execute
                        └─(Tool 불필요·되묻기)──────────→ respond ←─(그 밖)─┘ → END
```

| 단계 | 주체 | 입력(State) | 출력(State) | 이벤트 |
| :--- | :--- | :--- | :--- | :--- |
| analyze | **LLM** (요청당 1회) | message, context(이전 조건·직전 추천·최근 대화) | intent, reason, llm_tools, conditions(병합), changed, target_rank, question | `decision{by:llm, kind:intent, intent, reason, tools, extracted, changed}` |
| plan | 규칙 | intent, conditions, llm_tools | steps[{id, tool, args, bind, status}], missing, defaults | `decision{by:rule, kind:plan, steps, missing, defaults, reason}` |
| execute | 규칙 | steps, results | calls(실행 순서 이력), results(호출 서명→응답), products, groups_searched, fatal | `tool.request`, `tool.response{ok, code, elapsed_ms, summary}`, `tool.reuse` |
| judge | 규칙 | conditions, products, results | judged{principal, satisfied, excluded, reverse, loan_basis}, next | `decision{by:rule, kind:next, next, reason, satisfied, excluded}` |
| replan | 규칙 | replan_count, replan_used, groups_searched | 새 steps 추가 또는 relaxed=true, replans 이력 | `replan{n, max, strategy, reason}` |
| respond | 규칙 (other·term 만 LLM) | 위 전부 | answer(마크다운), view(화면용 구조) | — |

모든 단계는 `traced()` 로 감싸 `stage.enter` / `stage.exit{elapsed_ms}` 를 발행하고, 단계에서 예외가 나도 `error` 이벤트를 남긴 뒤 응답으로 넘어간다 (NF3003).

### 계획 규칙 (plan)

LLM 은 Tool 설명을 보고 필요한 Tool 이름을 고르고(`tools`), 코드는 다음 규칙으로 검증·보완한다.

| 의도 | 계획 |
| :--- | :--- |
| other, save | Tool 없음 (save 는 계획 저장소에 직접 저장 — 사용자가 이미 고른 값이라 Tool 로 두지 않음) |
| term | `search_loan_docs(query)` |
| fact | LLM 이 고른 것만: 시세(지역 필요) / 후보(권역·회사) → 상세(후보마다) / 지점(지역 필요) |
| plan | ① 대출금 계산에 필요한 값 확인: 대출 희망액, 또는 보유 자금 + (집값 또는 지역). 없으면 **되묻기**(Tool 0회) ② 집값이 없고 지역이 있으면 시세 조회 ③ 후보 조회(권역: 선호 또는 기본 은행) ④ 상세 조회(`bind: each_product` — 앞 후보의 product_id 마다, 최대 12건) ⑤ 지역이 있으면 지점 검색(`bind: best_company`, `deferred` — 판단 뒤 1순위 회사로) |

LLM 이 고른 Tool 중 등록되지 않은 이름은 버리고(`dropped_unknown`), 이 요청에 필요 없는 것은 빼고 그 사실을 `notes` 에 남긴다.

## 2. 제약 판단 계산식 (`agent/judge.py`, `services/loan_calc.py`)

- **대출금** L = 대출 희망액, 없으면 집값 − 보유 자금. 집값이 없으면 실거래 **중위값**으로 정하고 "추정"으로 표시.
- **기간** = 말한 값, 없으면 `agent.default_term_years`(30년) — 응답에 "기본값" 명시.
- **월 상환액** = `repayment(L, 금리, 기간×12, 상환방식).first_month`
  - 금리: 옵션의 **평균금리**, 비어 있으면 최저금리(표시), 둘 다 없으면 판단하지 않음
  - 상환방식: 분할상환방식 → 원리금균등, 만기일시상환방식 → 만기일시(매달 이자만, 응답에 주의 문구)
  - 상품마다 선호 조건(금리 유형·담보 유형)에 맞는 옵션 중 분할상환 우선, 월 상환액이 가장 낮은 옵션을 대표로 쓴다.
- **상한** = 월 상환액 상한과 (월 소득 × 비율 상한) 중 작은 값. 사용자가 말한 제약만 본다.
- **충족**: 대표 옵션의 월 상환액 ≤ 상한. 최고금리일 때의 월 상환액도 함께 보여 준다.
- **역산 기준** (외부 API 로 실제 적용 금리·한도를 확인할 수 없으므로)
  - `max_rate(L, 상한, 개월)`: 상한을 지키려면 적용 금리가 최대 몇 % 여야 하는지 (0% 여도 넘으면 그렇게 안내)
  - `max_principal(상한, 조회한 최저 금리, 개월)`: 그 금리로 상한 안에서 빌릴 수 있는 최대 대출금

## 3. 재계획

| 구분 | 내용 |
| :--- | :--- |
| 수행 조건 | 의도가 plan 이고, 대출금을 계산했고, **충족 후보가 0개**이며, 치명적 실패가 없을 때 |
| 수행하지 않는 조건 | 충족 후보가 1개 이상 / `INVALID_KEY` (다시 시도해도 같음 → 바로 키 안내) / 되묻기 상태 / 더 시도할 대안 없음 |
| 대안 순서 | `agent.replan_steps`: ① `relax_rate_type` 선호 금리 유형을 풀고 **이미 받은 상세정보로 다시 판단**(Tool 0회) ② 보험 ③ 저축은행 ④ 여신전문 권역 후보 조회 → 새 후보만 상세 조회 |
| 결과 재사용 | 호출 서명(`tool:정렬된 args JSON`) → 응답을 `results` 에 보관. 같은 서명은 다시 부르지 않고 `tool.reuse` 이벤트만 남김. 금감원 권역 응답은 프로세스 안에서 10분 캐시 |
| 상한 | `agent.max_replans: 3`. 도달하면 `stop_reason` 과 역산 기준을 보여 주고 "기간·월 상환 한도·집값 중 무엇을 조정할지" 되묻는다 |

## 4. 메인 Agent 와 로깅 Agent 의 관계

```
메인 Agent ──emit(event, **payload)──▶ Emitter ──publish──▶ EventBus ──▶ [구독자 0..n]
   (graph.py, service.py)            (세션 ID·턴 부착,                    └─ LoggingAgent.on_event → LogStore.append (SQLite)
                                     화면용 trace 수집)
```

- 메인 Agent 코드는 `logging_agent` 를 import 하지 않는다. 연결은 `app/main.py` 의 `logging_agent.attach(bus)` 한 줄이고, `LOGGING_AGENT=0` 이면 연결하지 않는다.
- `EventBus.publish` 는 구독자 예외를 삼킨다. 로그 저장이 실패해도 `LoggingAgent.failed` 만 늘고 대화는 계속된다.
- 로깅 Agent 는 LLM 을 갖지 않는다(`llm_calls = 0`). 받은 이벤트(JSON)를 가공·요약 없이 `run_events(session_id, turn, seq, ts, event, payload)` 에 덧붙인다.
- 같은 이벤트 목록이 `/api/chat` 응답의 `trace` 로도 화면에 전달된다 (실행 과정 타임라인).

### 이벤트 종류와 필드

공통: `event`, `session_id`, `turn`(세션 안 요청 번호), `seq`(전역 증가 번호), `ts`, `payload`

| event | payload |
| :--- | :--- |
| session.start | message, context_conditions |
| stage.enter / stage.exit | stage, (exit) elapsed_ms |
| decision | stage, by(`llm`·`rule`), kind(`intent`·`plan`·`next`·`limit`·`plan_saved`), reason, 그 밖 단계별 값 |
| tool.request | step, tool, args, round(재계획 회차) |
| tool.response | step, tool, ok, code, elapsed_ms, summary (원본 응답 대신 요약) |
| tool.reuse | step, tool, args |
| replan | n, max, strategy, reason |
| error | stage, error(예외 종류만) |
| session.end | intent, replans, tool_calls, elapsed_ms, answer_preview |

LLM 이 실행 흐름을 바꾸는 판단은 analyze 의 의도 분류뿐이며, `decision{by:"llm", intent, reason}` 으로 남는다. 재계획 여부·지점 검색 실행 등은 규칙 판단(`by:"rule"`)으로 이유와 함께 남는다.

## 5. 대화 맥락 (Memory, `agent/service.py`)

| 보관 | 보관하지 않음 |
| :--- | :--- |
| 세션별 병합 조건 (기준·선호·제약) | Tool 원본 응답 (요약만 이벤트에) |
| 직전 추천 상위 3개 요약: 순번, product_id, 회사·상품명, 대출금, 기간, 금리 유형, 상환 방식, 적용 금리, 월 상환액, 1순위 지점 | API 키, 오류 상세 |
| 최근 대화 6턴(질문·답변 앞 200자) | 계산 중간값 |

- **갱신 방식**: analyze 가 이번 메시지에서 **새로 말하거나 바꾼 값만** 뽑고(`conditions`), 지워 달라는 키는 `cleared` 로 받는다. `conditions.merge()` 가 이전 조건에 덮어써 바뀐 키 목록(`changed`)을 돌려주고 화면에 "(변경)" 으로 표시한다.
- LLM 키가 없거나 분석이 실패한 요청은 맥락을 바꾸지 않는다.
- "1번으로 저장해줘" 의 1번은 직전 추천 요약에서 찾는다.
- 세션은 서버 메모리에 있어 서버를 다시 시작하면 맥락은 비고, 실행 로그·계획은 SQLite 에 남는다.

## 6. LLM 호출 조건

| 상황 | LLM 호출 |
| :--- | :--- |
| 요청 분석 (모든 요청) | 1회 |
| other (인사·사용법) 답변 | 1회 |
| term (용어) 답변 | 1회 + 임베딩 1회 |
| 계획·판단·재계획·plan/fact/save 응답 | **없음** — 숫자는 공시값과 계산식으로만 만든다 |
| 로깅 Agent | 없음 |

연습용(`PLANNER_FAKE_LLM=1`)과 테스트에서는 같은 출력 형식을 내는 규칙 기반 `FakeLLM` 을 쓴다.
