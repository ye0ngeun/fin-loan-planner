"""카카오 로컬 키워드 장소 검색을 감싼 외부 API 툴: search_bank_branches (보조 정보 F3004).

추천 상품의 금융회사 지점(이름·주소·전화·링크)을 지역에서 찾는다.
지점 검색은 그 지점이 해당 상품을 취급하는지 알려 주지 않으므로 상품과 지점을 확정해 묶지 않는다.
"""

from __future__ import annotations

import json

from app.config import fake_mode, get_env, get_settings
from app.tools import http
from app.tools.base import BAD_ARGUMENT, EXTERNAL, INVALID_KEY, NO_RESULT, UPSTREAM_ERROR, Tool, fail, ok, scrub

SOURCE = "카카오 로컬 API (키워드로 장소 검색)"


def search_bank_branches(region: str, bank_name: str = "") -> dict:
    cfg = get_settings()["kakao"]
    region = str(region or "").strip()
    if len(region) < 2:
        return fail(BAD_ARGUMENT, "region 은 두 글자 이상의 지역 이름이어야 합니다.")
    api_key = get_env("KAKAO_REST_API_KEY")
    if not api_key and not fake_mode():
        return fail(INVALID_KEY, "카카오 REST API 키(KAKAO_REST_API_KEY)가 설정되지 않았습니다.")
    query = f"{region} {bank_name.strip() or '은행'}"
    params = {"query": query, "category_group_code": cfg["category_group_code"], "size": cfg["size"]}
    try:
        status, text = http.get("kakao", cfg["base_url"], params, {"Authorization": f"KakaoAK {api_key}"}, cfg["timeout_sec"])
    except Exception as e:  # noqa: BLE001
        return fail(UPSTREAM_ERROR, f"카카오 로컬 API에 연결하지 못했습니다. ({scrub(e)[:120]})")
    if status in (401, 403):
        return fail(INVALID_KEY, "카카오 REST API 키를 확인하세요.")
    if status != 200:
        return fail(UPSTREAM_ERROR, f"카카오 로컬 API가 오류로 답했습니다. (HTTP {status})")
    try:
        docs = json.loads(text).get("documents") or []
    except (ValueError, AttributeError):
        return fail(UPSTREAM_ERROR, "카카오 로컬 API 응답을 읽지 못했습니다.")
    if not docs:
        # 결과 없음은 실패 형식으로 돌려준다. 검색어를 바꾸면 결과가 달라질 수 있는 실패다.
        return fail(NO_RESULT, f"'{query}' 로 찾은 은행 지점이 없습니다.")
    return ok(
        {
            "query": query,
            "count": len(docs),
            "branches": [
                {
                    "name": d.get("place_name"),
                    "address": d.get("road_address_name") or d.get("address_name"),
                    "phone": d.get("phone") or None,
                    "url": d.get("place_url"),
                }
                for d in docs
            ],
            "notes": ["지점 검색 결과는 상품 취급 여부를 알려 주지 않습니다. 방문 전 지점에 확인하세요."],
        }
    )


def tools() -> list[Tool]:
    return [
        Tool(
            name="search_bank_branches",
            kind=EXTERNAL,
            description=(
                "상담·방문할 은행 지점의 이름·주소·전화·지도 링크가 필요할 때 쓴다. 사용자가 지역을 말했고 추천 상품의 "
                "금융회사 지점을 함께 안내할 때 부른다. bank_name 을 비우면 그 지역의 은행 지점 전체를 찾는다."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "region": {"type": "string", "description": "지역 이름 (예: 마포구, 공덕동)"},
                    "bank_name": {"type": "string", "description": "금융회사 이름 (예: 우리은행)"},
                },
                "required": ["region"],
            },
            output={"query": "실제 검색어", "branches": "[{name, address, phone, url}]", "notes": "주의 사항"},
            func=search_bank_branches,
            source=SOURCE,
            errors=[INVALID_KEY, BAD_ARGUMENT, NO_RESULT, UPSTREAM_ERROR],
        )
    ]
