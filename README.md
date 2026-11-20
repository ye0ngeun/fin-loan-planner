# 《불빛 하나》 내집마련 대출 플래너 (AI 코어 프로젝트 3회차)

집값·보유 자금·월 상환 한도를 말하면 Agent 가 **금감원 주택담보대출 공시**에서 후보를 찾고, 상품마다 공시 금리로 **월 상환액을 계산해 조건과 비교**하고,
맞는 상품이 없으면 **대안을 찾아 재계획**합니다. 고른 상품으로 **은행 상담 계획**을 저장·수정·삭제할 수 있고,
Agent 의 실행 과정은 별도의 **로깅 Agent** 가 이벤트로 받아 세션 단위 실행 로그로 남깁니다.

2회차 [fin-product-rag](https://github.com/ye0ngeun/fin-product-rag)(대출 상품 RAG)의 도메인을 이어 갑니다. 2회차의 상환액 계산식을 옮겨 왔고, 심화 F3022 에서 2회차 Vector DB 를 Tool 로 씁니다.

> 본 서비스는 정보 제공용이며 금융 전문가의 조언을 대신하지 않습니다. 상품 정보는 금융상품통합비교공시시스템(금융감독원) 금융회사 상품정보를 사용한 결과이며, 금융감독원은 정보의 정확성·최신성을 보증하지 않습니다.

## 한눈에 보기

```
사용자 ──▶ frontend (HTML/CSS/JS: 대화 · 계획 관리 · 실행 로그)
              │ fetch
              ▼
         backend (FastAPI) ── /api/chat · /api/plans · /api/logs · /api/tools · /api/health
              │
              ▼
   메인 Agent (LangGraph)  analyze → plan → execute → judge ─(충족 없음)→ replan → execute …
              │                                           └─────────────→ respond
              │ emit(event)                     Tool 등록부
              ▼                                  ├ 외부 API 툴: 금감원 후보·상세, 국토부 실거래가, 카카오 지점
          EventBus ──구독──▶ 로깅 Agent (LLM 없음) ──▶ SQLite 실행 로그
                                                 └ 로컬 서버 API 툴: 2회차 Chroma 문서 검색 (심화)
```

대표 요청: **"마포구 6억 아파트, 현금 2억 있어요. 월 200만원 이하로 갚을 주담대 찾아줘"**

1. 분석(LLM): 기준(집값 6억, 마포구, 보유 2억) · 선호(없음) · 제약(월 200만원) 추출, 필요한 Tool 선택
2. 계획(규칙): 시세 조회 → 후보 조회 → 후보마다 상세 조회 → (판단 뒤) 1순위 금융회사 지점 검색
3. 실행: 후보 조회의 `product_id` 가 상세 조회 입력으로 이어짐. 하나가 실패해도 사유를 남기고 계속
4. 판단(규칙): 대출금 4억 × 공시 평균금리 × 30년(기본값, 응답에 밝힘) → 월 상환액 ≤ 200만원 인지 비교
5. 충족 상품이 없으면 재계획: 선호 금리 유형 완화 → 보험 → 저축은행 → 여신전문 권역 순(최대 3회, 받은 결과는 다시 부르지 않음)
6. 응답: 추천 상품·월 상환액·판단 근거(공시월)·제외 사유·재계획 이력·역산 기준·지점, 면책 문구

## 기능

| 구분 | 요구사항 | 구현 |
| :--- | :--- | :--- |
| 필수 | F3001 Tool 명세·등록 | `backend/app/tools/base.py` 공통 응답 `{ok,data}` / `{ok:false,error{code,message}}`, 외부·로컬 구분 |
| 필수 | F3002 후보 조회 | `search_mortgage_products` (금감원, 기본 은행 권역, 기준 시점이 다르면 추정 표시) |
| 필수 | F3003 상세 조회 | `get_mortgage_product_detail` (금리 옵션, 금리 없으면 `NO_RATE` 로 판단 보류) |
| 필수 | F3004 보조 정보 | `search_bank_branches` (카카오 로컬: 이름·주소·전화·링크), `get_apartment_prices` (국토부 시세) |
| 필수 | F3005~F3011 메인 Agent | `backend/app/agent/graph.py` 분석·계획·실행·판단·재계획·응답, `service.py` 대화 맥락 |
| 필수 | F3012~F3014 로깅 | `events.py` 발행, `logging_agent.py` 구독·저장(LLM 없음), `/api/logs` 목록·상세·삭제(수정 없음) |
| 필수 | F3015~F3016 계획 | `store/plans.py`, `/api/plans` 저장·조회·수정·삭제, 시간 겹침 안내 |
| 필수 | F3017~F3019 서버·화면 | FastAPI + `frontend/` 대화·계획 관리·실행 로그 화면, 실행 과정 타임라인, 키 누락 안내 |
| 심화 | F3022 RAG 검색 Tool | `search_loan_docs` — 2회차 Chroma `loan_docs` 검색, 색인 없으면 `NO_INDEX` |

선택하지 않은 심화: F3020(이상 징후), F3021(Planner·Worker), F3023(캘린더).

## 실행 방법

Python 3.10 이상. 명령은 저장소 루트에서 실행합니다.

```bash
python -m venv .venv
```

```bash
.venv\Scripts\activate
```
(macOS·Linux 는 `source .venv/bin/activate`)

```bash
pip install -r requirements.txt pytest httpx
```

```bash
copy .env.example .env
```
(macOS·Linux 는 `cp .env.example .env`) 후 키를 채웁니다.

서버 실행 (화면과 API 를 함께 띄움):

```bash
cd backend && uvicorn app.main:app --reload --port 8000
```

브라우저에서 http://127.0.0.1:8000 (대화), http://127.0.0.1:8000/plans.html (계획 관리), http://127.0.0.1:8000/logs.html (실행 로그), http://127.0.0.1:8000/docs (API 문서).

키 없이 화면만 확인할 때는 `.env` 에 `PLANNER_FAKE_APIS=1`, `PLANNER_FAKE_LLM=1` 을 넣습니다. 가상 응답과 규칙 기반 LLM 대역으로 돌고, 화면 위에 "연습용 모드" 안내가 나옵니다.

| 목적 | 명령 (저장소 루트) |
| :--- | :--- |
| 테스트 (키·네트워크 불필요) | `cd backend && python -m pytest -q` |
| 외부 API 실제 호출 확인 (기획 검증) | `python scripts/check_apis.py` |
| 대표 시나리오 실행 기록 | `python scripts/run_scenarios.py` (연습용은 `--fake`) |
| 로깅 Agent 없이 실행 | `.env` 에 `LOGGING_AGENT=0` 후 서버 실행 |

## API 키 발급과 설정

| 환경변수 | 발급처 | 쓰임 |
| :--- | :--- | :--- |
| `OPENAI_API_KEY`, `OPENAI_BASE_URL` | SSAFY GMS (https://gms.ssafy.io) | 요청 분석, 인사·용어 답변, 심화 RAG 임베딩 |
| `FSS_API_KEY` | 금융감독원 금융상품한눈에 오픈API (https://finlife.fss.or.kr/finlife/api/main.do) 회원가입 → 인증키 신청 | 상품 후보·상세 |
| `MOLIT_API_KEY` | 공공데이터포털 "국토교통부_아파트 매매 실거래가 자료" 활용 신청 (Decoding 키) | 시세 |
| `KAKAO_REST_API_KEY` | 카카오 디벨로퍼스 → 내 애플리케이션 → 앱 키의 REST API 키, 카카오맵 사용 설정 | 은행 지점 검색 |

키는 `.env` 에만 둡니다. `.env.example` 은 빈 값이고 `.env` 는 git 에서 제외됩니다. 키가 없으면 해당 Tool 이 `INVALID_KEY` 로 실패하고, 화면 위에 어떤 키가 없는지 안내합니다. 오류 문구의 키는 `***` 로 가립니다.

## 외부 API 출처와 이용 조건 (요약, 자세히는 [sources.md](sources.md))

| 출처 | 호출 한도 | 지키는 방법 |
| :--- | :--- | :--- |
| 금융감독원 금융상품한눈에 Open API | 개인 1일 10,000건 (약관 제7조 4항) | 권역 단위 요청을 10분 캐시, 화면·답변에 출처 문구(제9조 4항) 고정 |
| 국토교통부 아파트 매매 실거래가 (공공데이터포털) | 개발계정 1일 10,000건 | 시세 1회 = 계약월 3회 호출, 과거 거래 사례임을 표시 |
| 카카오 로컬 API (키워드 장소 검색) | 앱별 일일 쿼터 (카카오 디벨로퍼스 쿼터 표 기준) | 판단 뒤 1순위 회사 1회만 검색, 상품 취급 여부와 묶지 않음 |
| 2회차 Chroma 색인 (위키백과 CC BY-SA 4.0, 공공데이터 용어사전) | — | 근거 문서 제목·출처·링크 표시 |

## 폴더 구조

```
backend/
  app/
    main.py              FastAPI 앱 (로깅 Agent 연결은 여기 한 줄)
    config.py            설정·키 로딩, 안내 문구
    schemas.py           요청·응답 Pydantic 모델
    api/routes.py        /api/chat, /api/plans, /api/logs, /api/tools, /api/health
    agent/
      graph.py           메인 Agent (LangGraph)
      conditions.py      기준·선호·제약 조건 정리와 맥락 병합
      judge.py           제약 판단·역산 (LLM 없음)
      llm.py, prompts.py GMS 연결과 규칙 기반 대역, 프롬프트
      service.py         요청 실행·대화 맥락
      events.py          이벤트 버스 (발행)
      logging_agent.py   로깅 서브 Agent (구독·저장)
    tools/               Tool 공통(base), 금감원(fss), 국토부(molit), 카카오(kakao), RAG(rag)
    services/loan_calc.py 상환액·역산 계산
    store/               SQLite 저장소: plans, logs
  config/settings.yaml   재계획 상한·기본값·호출 간격
  tests/                 pytest (픽스처 기반, 금리 값은 가상)
frontend/                index.html(대화) · plans.html(계획 관리) · logs.html(실행 로그)
scripts/                 check_apis.py, run_scenarios.py
docs/                    제출 문서
```

## 문서

| 문서 | 내용 |
| :--- | :--- |
| [요구사항 정의서](docs/01_요구사항정의서.md) | 도메인 확정 근거, 시나리오, 요구사항 표, 기획 검증 |
| [Agent 설계 문서](docs/02_Agent_설계문서.md) | 실행 단계·상태, 판단식, 재계획, 이벤트, 맥락 |
| [Tool 정의서](docs/03_Tool_정의서.md) | Tool 별 입출력·실패 코드, 등록·확장 방법 |
| [API 명세서](docs/04_API_명세서.md) | 경로, 요청·응답 형식 |
| [검증 결과](docs/05_검증결과.md) | 확인 항목별 결과, 대표 시나리오, 셀프 체크리스트 |
| [출처와 이용 조건](sources.md) | 외부 API 출처·약관·호출 한도 |
| [실행 로그 샘플](docs/samples/scenarios.md) | 대표 요청 7건 (연습용 모드 기록) |
