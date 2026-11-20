"""config/settings.yaml 과 .env 를 읽는다. 키 값은 화면·로그로 내보내지 않는다. (NF3001, NF3002)"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent
SETTINGS_PATH = BACKEND_DIR / "config" / "settings.yaml"

# 화면과 응답에 항상 붙이는 안내 문구 (F3011, 금감원 오픈API 이용약관 제9조 4항)
NOTICES = {
    "disclaimer": "본 서비스는 정보 제공용이며 금융 전문가의 조언을 대신하지 않습니다. 실제 금리·한도는 금융회사 심사로 정해집니다.",
    "fss_source": (
        "상품 정보는 금융상품통합비교공시시스템(금융감독원) 금융회사 상품정보를 사용한 결과이며, "
        "금융감독원은 정보의 정확성·최신성을 보증하지 않습니다."
    ),
    "molit_source": "시세는 국토교통부 아파트 매매 실거래가(공공데이터포털)의 과거 거래 사례이며 현재 매물이 아닙니다.",
}

# 화면에 "설정되었는지"만 알려 줄 키 이름 (값은 절대 내보내지 않는다)
KEY_NAMES = {
    "llm": "OPENAI_API_KEY",
    "fss": "FSS_API_KEY",
    "molit": "MOLIT_API_KEY",
    "kakao": "KAKAO_REST_API_KEY",
}


_env_loaded = False


def _load_env() -> None:
    # 저장소 루트와 backend/ 어디에 두어도 읽는다. 이미 있는 환경변수는 덮어쓰지 않는다. 프로세스에서 한 번만 읽는다.
    global _env_loaded
    if _env_loaded:
        return
    _env_loaded = True
    if os.getenv("PLANNER_SKIP_DOTENV") == "1":  # 테스트: 개발자 .env 와 분리
        return
    load_dotenv(REPO_DIR / ".env")
    load_dotenv(BACKEND_DIR / ".env")


@lru_cache
def get_settings() -> dict:
    _load_env()
    with open(SETTINGS_PATH, encoding="utf-8") as f:
        settings = yaml.safe_load(f)
    for key, rel in settings["paths"].items():
        settings["paths"][key] = (BACKEND_DIR / rel).resolve()
    rag_dir = Path(settings["rag"]["chroma_dir"])
    settings["rag"]["chroma_dir"] = rag_dir if rag_dir.is_absolute() else (BACKEND_DIR / rag_dir).resolve()
    return settings


def get_env(name: str) -> str:
    """값이 없으면 빈 문자열. 없을 때의 처리(실패 코드, 안내)는 부르는 쪽이 정한다."""
    _load_env()
    return (os.getenv(name) or "").strip()


def key_status() -> dict[str, bool]:
    """키별 설정 여부만 돌려준다. 화면의 설정 안내용 (F3019)."""
    return {name: bool(get_env(env)) for name, env in KEY_NAMES.items()}


def fake_mode() -> bool:
    """PLANNER_FAKE_APIS=1 이면 외부 API 대신 tests/fixtures 의 연습용 응답을 쓴다."""
    return get_env("PLANNER_FAKE_APIS") in ("1", "true", "yes")
