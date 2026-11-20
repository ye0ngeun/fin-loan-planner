"""API 요청·응답 형식 (Pydantic). (F3017)"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

_TIME = r"^([01]\d|2[0-3]):[0-5]\d$"
_DATE = r"^\d{4}-\d{2}-\d{2}$"


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=1000, examples=["마포구 6억 아파트, 현금 2억, 월 200만원 이하로 갚을 주담대 찾아줘"])
    session_id: str | None = Field(None, max_length=64, description="이어지는 대화면 이전 응답의 session_id")


class TraceEvent(BaseModel):
    event: str
    session_id: str
    turn: int
    seq: int
    ts: str
    payload: dict[str, Any]


class ChatResponse(BaseModel):
    session_id: str
    turn: int
    answer: str = Field(..., description="마크다운 답변")
    intent: str | None
    view: dict[str, Any] = Field(..., description="조건·판단·Tool 호출 이력·재계획 등 화면 표시용 구조")
    candidates: list[dict[str, Any]] = Field(..., description="저장할 수 있는 추천 상품 (순번 순)")
    trace: list[TraceEvent] = Field(..., description="이번 요청에서 발행된 이벤트 (실행 순서)")
    elapsed_ms: int
    notices: dict[str, str]
    error: dict[str, Any] | None = None


class PlanBase(BaseModel):
    title: str | None = Field(None, max_length=100)
    product_id: str | None = None
    kor_co_nm: str | None = None
    fin_prdt_nm: str | None = None
    branch_name: str | None = None
    branch_address: str | None = None
    visit_date: str | None = Field(None, pattern=_DATE)
    start_time: str | None = Field(None, pattern=_TIME)
    end_time: str | None = Field(None, pattern=_TIME)
    loan_amount_manwon: float | None = Field(None, ge=0)
    term_years: int | None = Field(None, ge=1, le=50)
    rate_type: str | None = None
    rpay_type: str | None = None
    annual_rate: float | None = Field(None, ge=0, le=30)
    monthly_payment_manwon: float | None = Field(None, ge=0)
    memo: str | None = Field(None, max_length=500)


class PlanCreate(PlanBase):
    title: str = Field(..., min_length=1, max_length=100)
    visit_date: str = Field(..., pattern=_DATE)
    start_time: str = Field(..., pattern=_TIME)
    end_time: str = Field(..., pattern=_TIME)
    session_id: str | None = None

    @field_validator("end_time")
    @classmethod
    def _after_start(cls, v, info):
        start = info.data.get("start_time")
        if start and v <= start:
            raise ValueError("end_time 은 start_time 보다 늦어야 합니다.")
        return v


class PlanUpdate(PlanBase):
    """보낸 값만 고친다."""


class Plan(PlanBase):
    id: int
    title: str
    visit_date: str
    start_time: str
    end_time: str
    session_id: str | None = None
    created_at: str
    updated_at: str


class OverlapInfo(BaseModel):
    id: int
    title: str
    visit_date: str
    start_time: str
    end_time: str


class PlanSaveResult(BaseModel):
    plan: Plan
    overlaps: list[OverlapInfo] = Field(..., description="같은 날짜에 시간이 겹치는 다른 계획 (저장은 됨)")
    message: str


class LogSession(BaseModel):
    session_id: str
    started_at: str
    last_at: str
    events: int
    turns: int
    tool_calls: int
    replans: int
    tool_failures: int
    first_message: str


class LogEvent(BaseModel):
    turn: int
    seq: int
    ts: str
    event: str
    payload: dict[str, Any]


class LogDetail(BaseModel):
    session_id: str
    events: list[LogEvent]


class DeleteResult(BaseModel):
    deleted: int


class HealthResponse(BaseModel):
    status: str
    keys: dict[str, bool] = Field(..., description="키별 설정 여부 (값은 보내지 않음)")
    fake_apis: bool
    fake_llm: bool
    logging_agent: bool
    notices: dict[str, str]
