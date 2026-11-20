# Tool 정의서

[README](../README.md) · [요구사항](01_요구사항정의서.md) · [Agent 설계](02_Agent_설계문서.md) · **Tool 정의서** · [API 명세](04_API_명세서.md) · [검증 결과](05_검증결과.md)

Tool 표의 이름·설명·입력은 코드(`backend/app/tools/`)의 등록 내용과 같습니다. 서버 실행 중 `GET /api/tools` 로 같은 목록을 볼 수 있습니다.

## 1. 공통 약속

```json
{"ok": true,  "data": { ... }}
{"ok": false, "error": {"code": "NO_RATE", "message": "사용자에게 보여 줄 수 있는 짧은 사유"}}
```

- Tool 은 예외를 밖으로 던지지 않는다. `Tool.run()` 이 필수 입력 누락·명세에 없는 입력(`BAD_ARGUMENT`), 함수 예외(`UPSTREAM_ERROR`)를 실패 응답으로 바꾼다.
- 오류 문구의 키는 `scrub()` 이 값과 파라미터 이름(`auth=`, `serviceKey=`, `KakaoAK`) 두 방식으로 가린다.
- Agent(LLM)에는 `spec()` = 이름·설명·입력 스키마만 넘긴다. 설명에는 기능이 아니라 **언제 쓰는지**를 적는다.

| 실패 코드 | 뜻 | Agent 처리 |
| :--- | :--- | :--- |
| `INVALID_KEY` | API 키가 없거나 올바르지 않음 — 재시도·재계획하지 않고 설정 안내 | 치명적 실패. 재계획하지 않고 키 설정 안내 |
| `BAD_ARGUMENT` | 입력값이 명세와 다름 | 사유 기록 후 다음 단계 |
| `NOT_FOUND` | 지목한 대상이 없음 | 그 상품 제외(사유 표시) |
| `NO_RESULT` | 조건에 맞는 결과가 없음 | 후보 조회면 재계획 대상, 지점·시세면 안내 후 진행 |
| `NO_RATE` | 판단에 필요한 금리가 공시되지 않아 판단하지 않음 | 그 상품은 판단하지 않고 제외(사유 표시) |
| `UNSUPPORTED_REGION` | 지원하지 않는 지역 | 시세 없이 진행, 집값을 물음 |
| `UPSTREAM_ERROR` | 외부 서버 또는 네트워크 오류 | 사유 기록 후 다음 단계 |
| `NO_INDEX` | 검색 색인이 아직 없음 | '아직 모으지 못했다'고 답함 |

## 2. Tool 목록

| 이름 | 종류 | 요구사항 | 출처 |
| :--- | :--- | :--- | :--- |
| `search_mortgage_products` | 외부 API 툴 | F3002 | 금융감독원 금융상품한눈에 Open API (주택담보대출) |
| `get_mortgage_product_detail` | 외부 API 툴 | F3003 | 금융감독원 금융상품한눈에 Open API (주택담보대출) |
| `get_apartment_prices` | 외부 API 툴 | F3004(보조: 시세) | 국토교통부 아파트 매매 실거래가 자료 (공공데이터포털) |
| `search_bank_branches` | 외부 API 툴 | F3004(보조: 위치) | 카카오 로컬 API (키워드로 장소 검색) |
| `search_loan_docs` | 로컬 서버 API 툴 | F3022 (심화) | 2회차 fin-product-rag Chroma 색인 (loan_docs: 금융 용어사전·한국어 위키백과) |

### `search_mortgage_products` (외부 API 툴)

**설명(Agent 가 읽는 문장)**: 주택담보대출 후보 상품 목록이 필요할 때 쓴다. 아직 상품을 정하지 않았거나 다른 권역(보험·저축은행 등)의 대안을 찾을 때 부른다. 권역을 주지 않으면 은행 권역이다. 결과의 product_id 를 상세 조회에 넘긴다.

**입력**

| 파라미터 | 타입 | 필수 | 설명 |
| :--- | :--- | :--- | :--- |
| `groups` | string[] (020000·030200·030300·050000·060000) |  | 권역 코드 목록 (020000 은행, 050000 보험, 030300 저축은행, 030200 여신전문) |
| `as_of` | string |  | 기준일 YYYY-MM-DD (현재 공시와 다르면 추정임을 밝힘) |
| `company` | string |  | 금융회사 이름 일부 (예: 우리은행) |

**출력 `data`**

| 필드 | 내용 |
| :--- | :--- |
| `requested` | 요청한 권역·기준일 |
| `basis` | 실제 조회한 권역·공시월 |
| `is_estimated` | 요청 기준과 실제 기준이 다르면 true |
| `notes` | 추정 사유 |
| `products` | [{product_id, kor_co_nm, fin_prdt_nm, group, group_nm}] |

**실패 코드**: `INVALID_KEY`, `BAD_ARGUMENT`, `NO_RESULT`, `UPSTREAM_ERROR`

### `get_mortgage_product_detail` (외부 API 툴)

**설명(Agent 가 읽는 문장)**: 후보 상품의 금리(최저·최고·평균), 금리 유형, 상환 방식, 한도 문구가 필요할 때 쓴다. 월 상환액을 계산해 조건에 맞는지 판단하기 전에 상품마다 부른다. product_id 는 후보 조회 결과에서 받는다.

**입력**

| 파라미터 | 타입 | 필수 | 설명 |
| :--- | :--- | :--- | :--- |
| `product_id` | string | 예 | 후보 조회가 준 상품 식별자 |
| `mrtg_type` | string (아파트·아파트외) |  | 담보 유형 (아파트, 아파트외) |

**출력 `data`**

| 필드 | 내용 |
| :--- | :--- |
| `product_id` | 상품 식별자 |
| `kor_co_nm` | 금융회사 |
| `fin_prdt_nm` | 상품명 |
| `loan_lmt` | 대출 한도 공시 문구 |
| `options` | [{mrtg_type_nm, rpay_type_nm, lend_rate_type_nm, rate_min, rate_max, rate_avg}] (연 %, 공시값 그대로) |
| `dcls_month` | 공시월 |

**실패 코드**: `INVALID_KEY`, `BAD_ARGUMENT`, `NOT_FOUND`, `NO_RATE`, `UPSTREAM_ERROR`

### `get_apartment_prices` (외부 API 툴)

**설명(Agent 가 읽는 문장)**: 사려는 집의 지역 시세(최근 아파트 실거래가 중위값·사분위)가 필요할 때 쓴다. 사용자가 집값을 말하지 않아 필요 대출액을 정할 수 없거나, 말한 집값이 그 지역 시세와 맞는지 보여 줄 때 부른다. 서울 25개 구만 지원한다.

**입력**

| 파라미터 | 타입 | 필수 | 설명 |
| :--- | :--- | :--- | :--- |
| `region` | string | 예 | 서울 구 이름 (예: 마포구) |
| `months` | integer |  | 최근 몇 개월 (기본 3, 1~12) |
| `end_month` | string |  | 마지막 계약년월 YYYYMM (기본: 지난달) |
| `area_min` | number |  | 전용면적 하한 (㎡) |
| `area_max` | number |  | 전용면적 상한 (㎡) |

**출력 `data`**

| 필드 | 내용 |
| :--- | :--- |
| `basis` | 실제 조회한 지역·계약월 |
| `is_estimated` | 요청한 기간과 실제 기간이 다르면 true |
| `count` | 거래 수 |
| `median_manwon` | 중위 거래가(만원) |
| `p25_manwon/p75_manwon` | 사분위(만원) |
| `recent_deals` | 최근 거래 5건 |

**실패 코드**: `INVALID_KEY`, `BAD_ARGUMENT`, `UNSUPPORTED_REGION`, `NO_RESULT`, `UPSTREAM_ERROR`

### `search_bank_branches` (외부 API 툴)

**설명(Agent 가 읽는 문장)**: 상담·방문할 은행 지점의 이름·주소·전화·지도 링크가 필요할 때 쓴다. 사용자가 지역을 말했고 추천 상품의 금융회사 지점을 함께 안내할 때 부른다. bank_name 을 비우면 그 지역의 은행 지점 전체를 찾는다.

**입력**

| 파라미터 | 타입 | 필수 | 설명 |
| :--- | :--- | :--- | :--- |
| `region` | string | 예 | 지역 이름 (예: 마포구, 공덕동) |
| `bank_name` | string |  | 금융회사 이름 (예: 우리은행) |

**출력 `data`**

| 필드 | 내용 |
| :--- | :--- |
| `query` | 실제 검색어 |
| `branches` | [{name, address, phone, url}] |
| `notes` | 주의 사항 |

**실패 코드**: `INVALID_KEY`, `BAD_ARGUMENT`, `NO_RESULT`, `UPSTREAM_ERROR`

### `search_loan_docs` (로컬 서버 API 툴)

**설명(Agent 가 읽는 문장)**: LTV·DSR·중도상환수수료·고정/변동금리 같은 대출 용어나 제도의 뜻을 물을 때 쓴다. 금리·상품 목록·시세처럼 외부 API 로 확인할 수 있는 값에는 쓰지 않는다.

**입력**

| 파라미터 | 타입 | 필수 | 설명 |
| :--- | :--- | :--- | :--- |
| `query` | string | 예 | 찾을 용어나 질문 |
| `top_k` | integer |  | 가져올 문서 수 (기본 4) |

**출력 `data`**

| 필드 | 내용 |
| :--- | :--- |
| `query` | 검색어 |
| `documents` | [{title, source, url, license, score, text}] |

**실패 코드**: `BAD_ARGUMENT`, `NO_INDEX`, `NO_RESULT`, `INVALID_KEY`, `UPSTREAM_ERROR`

## 3. Tool 사이의 입력 연결

| 앞 Tool 출력 | → 다음 Tool 입력 | 연결 방식 (`graph.py`) |
| :--- | :--- | :--- |
| `search_mortgage_products.products[].product_id` | `get_mortgage_product_detail.product_id` | 계획의 `bind: each_product` — 그 후보 조회 단계가 준 상품마다 호출 (최대 `agent.max_products_per_round`) |
| 판단 결과 1순위 상품의 `kor_co_nm` | `search_bank_branches.bank_name` | `bind: best_company`, 판단 뒤 실행되도록 `deferred` |
| `get_apartment_prices.median_manwon` | (Tool 아님) 판단의 집값 | 집값을 말하지 않았을 때만, "추정"으로 표시 |
| 요청 분석의 `region`, `groups`, `bank_name`, `mrtg_type` | 각 Tool 입력 | 계획 단계에서 채움 |

모델 판단이 필요 없는 작업은 Tool 로 두지 않는다: 월 상환액 계산(`judge.py`), 계획 저장(사용자가 고른 값 → `PlanStore` 직접), 조건 병합.

## 4. 등록과 확장

1. `backend/app/tools/<모듈>.py` 에 함수를 만든다. 성공은 `ok(data)`, 실패는 `fail(code, message)` 로만 돌려준다. 키가 없으면 `INVALID_KEY`.
2. 같은 모듈에 `tools()` 를 두고 `Tool(name, kind=EXTERNAL|LOCAL, description, parameters(JSON Schema), output, func, source, errors)` 목록을 돌려준다.
3. `backend/app/tools/__init__.py` 의 `build_registry()` 모듈 목록에 더한다. 같은 이름은 등록되지 않는다.
4. 외부 호출은 `app.tools.http.get(service, …)` 로 한다 — 테스트·연습용 모드에서 픽스처로 바뀐다. `http.fixture_transport` 에 서비스별 픽스처 규칙을 더한다.
5. Agent 가 새 Tool 을 계획에 넣게 하려면 `graph.py::plan` 의 의도별 규칙에 단계를 더한다 (LLM 은 설명을 보고 고르지만, 순서·입력 연결은 규칙이 정한다).
6. 출처를 `sources.md` 표에 먼저 적고, `tests/test_tools.py` 에 정상 1회·실패(잘못된 입력, 키 없음) 테스트를 더한다.
