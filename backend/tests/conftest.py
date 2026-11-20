"""테스트 공통 준비. 네트워크·실제 키 없이 tests/fixtures 의 연습용 응답과 규칙 기반 LLM 대역으로 돈다."""

import os

os.environ["PLANNER_SKIP_DOTENV"] = "1"  # 개발자 .env 를 읽지 않는다
for _name in ("OPENAI_API_KEY", "FSS_API_KEY", "MOLIT_API_KEY", "KAKAO_REST_API_KEY", "PLANNER_FAKE_APIS", "PLANNER_FAKE_LLM", "LOGGING_AGENT"):
    os.environ.pop(_name, None)

from datetime import date  # noqa: E402

import pytest  # noqa: E402

from app.agent.events import EventBus  # noqa: E402
from app.agent.llm import FakeLLM  # noqa: E402
from app.agent.service import PlannerService  # noqa: E402
from app.store.plans import PlanStore  # noqa: E402
from app.tools import build_registry, fss, http, rag  # noqa: E402

TODAY = date(2026, 11, 20)  # 금요일
TEST_KEY = "test-key-0123456789"


@pytest.fixture(autouse=True)
def fixture_apis(monkeypatch):
    """외부 API 대신 픽스처 응답. 키는 가짜 값으로 채워 둔다 (키 없음은 각 테스트에서 지운다)."""
    for name in ("FSS_API_KEY", "MOLIT_API_KEY", "KAKAO_REST_API_KEY"):
        monkeypatch.setenv(name, TEST_KEY)
    http.set_transport(http.fixture_transport)
    monkeypatch.setattr("time.sleep", lambda *_: None)  # 호출 간격 대기 생략
    fss.clear_cache()
    rag.set_searcher(None)
    yield
    http.set_transport(None)
    fss.clear_cache()
    rag.set_searcher(None)


@pytest.fixture
def registry():
    return build_registry()


@pytest.fixture
def bus():
    return EventBus()


@pytest.fixture
def service(registry, bus):
    return PlannerService(registry, PlanStore(":memory:"), FakeLLM, bus, today=lambda: TODAY)


@pytest.fixture
def events(bus):
    log = []
    bus.subscribe(log.append)
    return log


PLAN_MSG = "마포구 6억 아파트, 현금 2억 있어요. 월 200만원 이하로 갚을 주담대 찾아줘"
