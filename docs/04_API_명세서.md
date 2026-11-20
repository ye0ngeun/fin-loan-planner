# API 명세서

[README](../README.md) · [요구사항](01_요구사항정의서.md) · [Agent 설계](02_Agent_설계문서.md) · [Tool 정의서](03_Tool_정의서.md) · **API 명세** · [검증 결과](05_검증결과.md)

기본 주소 `http://127.0.0.1:8000`. 모든 요청·응답은 JSON(UTF-8). 스키마 원본은 `backend/app/schemas.py`, 실행 중 `/docs`(Swagger)·`/openapi.json` 에서도 볼 수 있다.
오류 응답은 FastAPI 기본 형식 `{"detail": "..."}` 이며 키·스택 같은 상세는 담지 않는다.

| 분류 | 메서드 | 경로 | 설명 |
| :--- | :--- | :--- | :--- |
| 대화 | POST | `/api/chat` | 질문 1건 실행 |
| 대화 | DELETE | `/api/chat/{session_id}` | 대화 맥락 지우기 (실행 로그는 남음) |
| 계획 관리 | GET | `/api/plans` | 계획 목록 (날짜·시작 시각 순) |
| 계획 관리 | POST | `/api/plans` | 계획 저장 (201, 겹침 안내) |
| 계획 관리 | GET | `/api/plans/{plan_id}` | 계획 상세 |
| 계획 관리 | PUT | `/api/plans/{plan_id}` | 계획 수정 (보낸 값만) |
| 계획 관리 | DELETE | `/api/plans/{plan_id}` | 계획 삭제 |
| 실행 로그 | GET | `/api/logs` | 세션 목록 |
| 실행 로그 | GET | `/api/logs/{session_id}` | 세션 상세 (실행 순서) |
| 실행 로그 | DELETE | `/api/logs/{session_id}` | 세션 로그 삭제 |
| 설정·명세 | GET | `/api/health` | 키 설정 여부·모드·로깅 Agent 연결 상태 |
| 설정·명세 | GET | `/api/tools` | 등록된 Tool 명세 |

실행 로그에는 수정(PUT·PATCH) 경로가 없다. 요청하면 405.

## POST /api/chat

요청 `ChatRequest`

| 필드 | 타입 | 필수 | 설명 |
| :--- | :--- | :--- | :--- |
| message | string (1~1000자) | 예 | 사용자 질문 |
| session_id | string | | 이어지는 대화면 이전 응답의 값. 없으면 새로 만든다 |

```json
{"message": "마포구 6억 아파트, 현금 2억 있어요. 월 200만원 이하로 갚을 주담대 찾아줘", "session_id": null}
```

응답 `ChatResponse` (200)

| 필드 | 타입 | 설명 |
| :--- | :--- | :--- |
| session_id | string | 세션 식별자 |
| turn | int | 세션 안 요청 번호 |
| answer | string | 마크다운 답변 |
| intent | string | plan · fact · save · term · other |
| view | object | 화면용 구조 (아래) |
| candidates | object[] | 저장할 수 있는 추천 (rank, product_id, name, kor_co_nm, fin_prdt_nm, principal, term_years, rate_type, rpay_type, rate, monthly_payment, branch_name, branch_address) |
| trace | TraceEvent[] | 이번 요청에서 발행된 이벤트 (event, session_id, turn, seq, ts, payload) |
| elapsed_ms | int | 처리 시간 |
| notices | object | disclaimer, fss_source, molit_source |
| error | object \| null | `{"code": "LLM_KEY_MISSING" \| "LLM_ERROR" \| "STAGE_ERROR", "message"}` |

`view` 의 주요 필드: `conditions{기준,선호,제약}`, `changed`(바뀐 조건 이름), `defaults`(기본값 사용 안내), `missing`(되물을 조건),
`judged{principal, term_years, payment_cap, satisfied[], excluded[], reverse{}, loan_basis{}}`, `price`(시세), `branches`(지점),
`calls[]`(step, tool, args, ok, code, reused, round, elapsed_ms, summary), `replans[]`(n, strategy, reason), `stop_reason`, `fatal`, `tool_failures[]`, `sources[]`(term), `saved_plan`(save).

## 계획 관리

`PlanCreate` (POST 요청)

| 필드 | 타입 | 필수 | 제약 |
| :--- | :--- | :--- | :--- |
| title | string | 예 | 1~100자 |
| visit_date | string | 예 | `YYYY-MM-DD` |
| start_time, end_time | string | 예 | `HH:MM`, 종료 > 시작 |
| product_id, kor_co_nm, fin_prdt_nm, branch_name, branch_address, rate_type, rpay_type, memo, session_id | string | | memo ≤ 500자 |
| loan_amount_manwon, annual_rate, monthly_payment_manwon | number | | ≥ 0, 금리 ≤ 30 |
| term_years | int | | 1~50 |

`PlanUpdate` (PUT 요청): 위 필드 모두 선택. 보낸 값만 고친다. 종료 ≤ 시작이 되면 422.

`PlanSaveResult` (POST 201 · PUT 200 응답)

```json
{
  "plan": {"id": 2, "title": "다른 상담", "visit_date": "2026-11-21", "start_time": "14:30", "end_time": "15:30", "...": "...",
           "created_at": "2026-11-20T15:01:58", "updated_at": "2026-11-20T15:01:58"},
  "overlaps": [{"id": 1, "title": "국민은행 KB주택담보대출 상담", "visit_date": "2026-11-21", "start_time": "14:00", "end_time": "15:00"}],
  "message": "저장했습니다. 같은 날짜에 시간이 겹치는 계획이 있습니다: #1 국민은행 KB주택담보대출 상담 14:00~15:00"
}
```

GET 목록·상세는 `Plan` (위 필드 + id, created_at, updated_at). 없는 id 는 404. DELETE 는 `{"deleted": 1}`.

## 실행 로그

`GET /api/logs` → `LogSession[]`

| 필드 | 설명 |
| :--- | :--- |
| session_id, started_at, last_at | 세션과 첫·마지막 이벤트 시각 |
| events, turns | 이벤트 수, 요청 수 |
| tool_calls, replans, tool_failures | Tool 요청 수, 재계획 수, 실패한 Tool 응답 수 |
| first_message | 첫 질문 앞 80자 |

`GET /api/logs/{session_id}` → `{"session_id", "events": [{"turn", "seq", "ts", "event", "payload"}]}` (발생 순서). 없으면 404.
`DELETE /api/logs/{session_id}` → `{"deleted": 지운 이벤트 수}`.

## 설정·명세

`GET /api/health`

```json
{"status": "ok", "keys": {"llm": true, "fss": true, "molit": false, "kakao": false},
 "fake_apis": false, "fake_llm": false, "logging_agent": true, "notices": {"disclaimer": "...", "fss_source": "...", "molit_source": "..."}}
```

키 **값**은 보내지 않고 설정 여부만 보낸다. 화면은 이 값으로 키 설정 안내를 띄운다.

`GET /api/tools` → Tool 카드 목록 (name, kind, description, parameters, output, errors, source). [Tool 정의서](03_Tool_정의서.md)와 같다.
