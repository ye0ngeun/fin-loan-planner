"""Tool 공통 약속: 명세(이름·설명·입력 스키마), 성공/실패 응답 형식, 등록부, 키 가리기. (F3001)

모든 Tool 은 예외를 밖으로 던지지 않는다. 성공은 {"ok": True, "data": ...},
실패는 {"ok": False, "error": {"code": ..., "message": ...}} 로 돌려준다.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable

# ── 실패 코드 ─────────────────────────────────────────────────
INVALID_KEY = "INVALID_KEY"  # API 키 없음·틀림. 다시 불러도 같으므로 재시도·재계획하지 않는다
BAD_ARGUMENT = "BAD_ARGUMENT"  # 입력이 명세와 다름
NOT_FOUND = "NOT_FOUND"  # 지목한 대상(상품 등)이 없음
NO_RESULT = "NO_RESULT"  # 조건에 맞는 결과가 0건
NO_RATE = "NO_RATE"  # 상품은 있으나 판단에 필요한 금리가 공시되지 않음 (판단 보류)
UNSUPPORTED_REGION = "UNSUPPORTED_REGION"  # 지원하지 않는 지역
UPSTREAM_ERROR = "UPSTREAM_ERROR"  # 외부 서버·네트워크 오류
NO_INDEX = "NO_INDEX"  # (심화) RAG 색인이 아직 없음

ERROR_CODES = {
    INVALID_KEY: "API 키가 없거나 올바르지 않음 — 재시도·재계획하지 않고 설정 안내",
    BAD_ARGUMENT: "입력값이 명세와 다름",
    NOT_FOUND: "지목한 대상이 없음",
    NO_RESULT: "조건에 맞는 결과가 없음",
    NO_RATE: "판단에 필요한 금리가 공시되지 않아 판단하지 않음",
    UNSUPPORTED_REGION: "지원하지 않는 지역",
    UPSTREAM_ERROR: "외부 서버 또는 네트워크 오류",
    NO_INDEX: "검색 색인이 아직 없음",
}
FATAL_CODES = {INVALID_KEY}


def ok(data: Any) -> dict:
    return {"ok": True, "data": data}


def fail(code: str, message: str) -> dict:
    return {"ok": False, "error": {"code": code, "message": message}}


# ── 키 가리기 (NF3001, NF3002) ─────────────────────────────────
_SECRET_ENVS = ("OPENAI_API_KEY", "FSS_API_KEY", "MOLIT_API_KEY", "KAKAO_REST_API_KEY")
_PARAM = re.compile(r"(?i)\b(auth|key|servicekey|apikey|api_key|token)=([^&\s'\"]+)")
_HEADER = re.compile(r"(?i)(KakaoAK|Bearer)\s+[A-Za-z0-9._\-]+")


def scrub(text: Any) -> str:
    """오류 문구에 키가 실려 나가지 않도록 값과 이름 두 방식으로 가린다."""
    text = str(text)
    for env in _SECRET_ENVS:
        secret = (os.getenv(env) or "").strip()
        if len(secret) >= 6:
            text = text.replace(secret, "***")
    text = _PARAM.sub(lambda m: f"{m.group(1)}=***", text)
    return _HEADER.sub(lambda m: f"{m.group(1)} ***", text)


# ── Tool 명세와 등록부 ────────────────────────────────────────
EXTERNAL = "external"  # 외부 API 툴
LOCAL = "local"  # 로컬 서버 API 툴 (이 서비스가 가진 기능)


@dataclass
class Tool:
    name: str
    kind: str  # EXTERNAL | LOCAL
    description: str  # 언제 쓰는지. Agent(LLM)는 이것만 보고 Tool 을 고른다
    parameters: dict  # 입력 JSON Schema
    output: dict  # 출력 구조 설명 (Tool 정의서와 화면용)
    func: Callable[..., dict]
    source: str = ""  # 데이터 출처
    errors: list[str] = field(default_factory=list)  # 이 Tool 이 낼 수 있는 실패 코드

    def run(self, **kwargs) -> dict:
        missing = [p for p in self.parameters.get("required", []) if kwargs.get(p) in (None, "", [])]
        if missing:
            return fail(BAD_ARGUMENT, f"필수 입력이 없습니다: {', '.join(missing)}")
        unknown = set(kwargs) - set(self.parameters.get("properties", {}))
        if unknown:
            return fail(BAD_ARGUMENT, f"명세에 없는 입력입니다: {', '.join(sorted(unknown))}")
        try:
            result = self.func(**kwargs)
        except TypeError as e:
            return fail(BAD_ARGUMENT, f"입력값이 명세와 다릅니다: {scrub(e)}")
        except Exception as e:  # noqa: BLE001 — Tool 은 예외를 밖으로 던지지 않는다
            return fail(UPSTREAM_ERROR, f"Tool 실행 중 오류: {scrub(e)[:200]}")
        if not isinstance(result, dict) or "ok" not in result:
            return fail(UPSTREAM_ERROR, "Tool 응답 형식이 약속과 다릅니다.")
        return result

    def spec(self) -> dict:
        """Agent(LLM)에 넘기는 명세: 이름·설명·입력 스키마만."""
        return {"name": self.name, "description": self.description, "parameters": self.parameters}

    def card(self) -> dict:
        """Tool 정의서·화면용 전체 명세."""
        return {
            "name": self.name,
            "kind": self.kind,
            "description": self.description,
            "parameters": self.parameters,
            "output": self.output,
            "errors": {c: ERROR_CODES[c] for c in self.errors},
            "source": self.source,
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> Tool:
        if tool.name in self._tools:
            raise ValueError(f"이미 등록된 Tool 입니다: {tool.name}")
        if tool.kind not in (EXTERNAL, LOCAL):
            raise ValueError(f"Tool 종류는 external 또는 local: {tool.kind}")
        self._tools[tool.name] = tool
        return tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools)

    def specs(self) -> list[dict]:
        return [t.spec() for t in self._tools.values()]

    def cards(self) -> list[dict]:
        return [t.card() for t in self._tools.values()]

    def call(self, name: str, **kwargs) -> dict:
        tool = self.get(name)
        if tool is None:
            return fail(BAD_ARGUMENT, f"등록되지 않은 Tool 입니다: {name}")
        return tool.run(**kwargs)
