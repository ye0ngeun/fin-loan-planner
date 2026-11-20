# 《불빛 하나》 fin-loan-planner — 작업 규칙

AI 코딩 도구와 사람이 함께 따르는 규칙. 새 작업을 시작하면 이 파일과 [README](README.md)를 먼저 읽는다.

## 1. 한 줄 요약
금감원 주택담보대출 공시·국토부 실거래가·카카오 로컬을 Tool 로 쓰는 Agent 기반 내집마련 대출 플래너 (FastAPI + HTML/JS). SSAFY AI 코어 프로젝트 3회차. 2회차 저장소 fin-product-rag 의 도메인을 이어 간다.

## 2. 꼭 지킬 것
1. **금리·한도·상환액 숫자를 LLM 이 만들지 않는다.** 금리는 Tool 응답 그대로, 월 상환액·역산은 `backend/app/services/loan_calc.py`, 판단은 `agent/judge.py`. LLM 은 요청 분석과 인사·용어 답변에만 쓴다.
2. **Tool 은 예외를 던지지 않는다.** `ok()`/`fail(code, message)` 만 돌려주고, 오류 문구는 `scrub()` 로 키를 가린다. 비어 있는 값은 채우지 않는다(`NO_RATE`).
3. **메인 Agent 는 로깅 Agent 를 모른다.** `graph.py`·`service.py` 는 `emit()` 만 부른다. 로깅 Agent 는 LLM 을 쓰지 않고 이벤트를 원본 그대로 저장한다. 실행 로그 수정 API 를 만들지 않는다.
4. **모든 화면·응답에 면책 문구와 금감원 출처 문구**("금융상품통합비교공시시스템(금융감독원) 금융회사 상품정보", 약관 제9조 4항). `config.NOTICES`, `frontend/*.html` 하단, `tests/test_api.py` 가 확인한다.
5. **키는 `.env` 에만.** `.env.example` 은 빈 값. 실제 키로 만든 실행 로그(`docs/samples/raw/`)·화면 캡처(`docs/screenshots/`)·SQLite(`backend/data/`)는 커밋하지 않는다. 픽스처 값은 가상이다.
6. **새 출처는 `sources.md` 표에 먼저 적는다.**
7. **설정 값(재계획 상한·기본값·호출 간격)은 `backend/config/settings.yaml`, 프롬프트는 `backend/app/agent/prompts.py` 에만.**
8. **버전 고정** (`requirements.txt`, 명세 1.2-5): langgraph 1.2.10, langchain 1.3.14, langchain-openai 1.4.3, langchain-core 1.6.0, openai 2.54.0, fastapi 0.141.1, pydantic 2.13.5, chromadb 1.5.9.
9. **F3001~F3019, F3022, NF3001~NF3007 을 깨지 않는다.** 기능을 바꾸면 [요구사항 정의서](docs/01_요구사항정의서.md) 표와 [검증 결과](docs/05_검증결과.md)의 해당 줄도 고친다.

## 3. 환경과 명령
- 외부 API(금감원·국토부·카카오·GMS)는 클라우드 세션에서 막혀 있다. 실제 호출·화면 캡처는 로컬(Windows)에서, 클라우드에서는 픽스처 테스트와 연습용 모드만.

| 목적 | 명령 |
| :--- | :--- |
| 테스트 (네트워크·키 불필요) | `cd backend && python -m pytest -q` |
| 서버 + 화면 | `cd backend && uvicorn app.main:app --reload` → http://127.0.0.1:8000 |
| 키 없이 화면 확인 | `.env` 에 `PLANNER_FAKE_APIS=1`, `PLANNER_FAKE_LLM=1` |
| 외부 API 실제 호출 확인 | `python scripts/check_apis.py` |
| 대표 시나리오 기록 | `python scripts/run_scenarios.py` (연습용 `--fake`) |

## 4. 작업 방식
- 브랜치 → PR → `main` 머지. PR 본문에 관련 요구사항 번호와 확인한 테스트를 적는다.
- 완료 전에 `python -m pytest -q` 를 돌린다. 실행하지 못한 확인(키·네트워크 필요)은 "미확인"으로 적고 통과로 쓰지 않는다.
- 테스트 개수·시나리오 결과가 바뀌면 [검증 결과](docs/05_검증결과.md)도 고친다.

## 5. 문서 지도
| 문서 | 언제 보나 |
| :--- | :--- |
| [요구사항 정의서](docs/01_요구사항정의서.md) | 기능 범위, 요구사항 번호, 기획 검증 |
| [Agent 설계](docs/02_Agent_설계문서.md) | 단계·판단식·재계획·이벤트·맥락을 바꿀 때 |
| [Tool 정의서](docs/03_Tool_정의서.md) | Tool 을 추가·수정할 때 |
| [API 명세](docs/04_API_명세서.md) | 경로·스키마를 바꿀 때 |
| [검증 결과](docs/05_검증결과.md) | 제출 전 점검 |
| [출처와 이용 조건](sources.md) | 데이터 출처를 추가하거나 화면 문구를 바꿀 때 |
