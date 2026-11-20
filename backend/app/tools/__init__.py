"""Tool 등록. Agent 는 build_registry() 가 돌려준 등록부로만 Tool 을 부른다. (F3001)

새 Tool 추가: 모듈에 함수와 tools() 를 만들고 아래 목록에 모듈을 더한다.
"""

from __future__ import annotations

from app.tools import fss, kakao, molit, rag
from app.tools.base import ToolRegistry


def build_registry(include_rag: bool = True) -> ToolRegistry:
    registry = ToolRegistry()
    modules = [fss, molit, kakao] + ([rag] if include_rag else [])
    for module in modules:
        for tool in module.tools():
            registry.register(tool)
    return registry
