"""(심화 F3022) 2회차 Vector DB 를 검색하는 로컬 서버 API 툴: search_loan_docs.

2회차(fin-product-rag)의 Chroma 컬렉션 loan_docs(용어사전·위키백과 청크)를 그대로 읽는다.
외부 API 로 확인할 수 없는 용어·제도 질문(LTV, DSR, 중도상환수수료 등)에 쓴다.
색인이 없으면 NO_INDEX 로 구분한다.
"""

from __future__ import annotations

from pathlib import Path

from app.config import get_env, get_settings
from app.tools.base import BAD_ARGUMENT, INVALID_KEY, LOCAL, NO_INDEX, NO_RESULT, UPSTREAM_ERROR, Tool, fail, ok, scrub

SOURCE = "2회차 fin-product-rag Chroma 색인 (loan_docs: 금융 용어사전·한국어 위키백과)"

# 테스트에서 검색기를 바꿔 끼운다: (query, top_k) -> [(page_content, metadata, score)]
_searcher = None


def set_searcher(fn) -> None:
    global _searcher
    _searcher = fn


def _index_ready(chroma_dir: Path, collection: str) -> bool:
    if not chroma_dir.exists():
        return False
    import chromadb

    try:
        return chromadb.PersistentClient(path=str(chroma_dir)).get_collection(collection).count() > 0
    except Exception:  # noqa: BLE001 — 컬렉션이 없으면 색인 없음
        return False


def _chroma_search(query: str, top_k: int) -> list[tuple[str, dict, float]]:
    from langchain_chroma import Chroma
    from langchain_openai import OpenAIEmbeddings

    s = get_settings()
    emb = OpenAIEmbeddings(
        model=s["llm"]["embedding_model"],
        api_key=get_env("OPENAI_API_KEY"),
        base_url=get_env("OPENAI_BASE_URL") or None,
        check_embedding_ctx_length=False,
        max_retries=s["llm"]["max_retries"],
    )
    store = Chroma(
        collection_name=s["rag"]["collection"],
        embedding_function=emb,
        persist_directory=str(s["rag"]["chroma_dir"]),
        collection_metadata={"hnsw:space": "cosine"},
    )
    return [(d.page_content, d.metadata, score) for d, score in store.similarity_search_with_relevance_scores(query, k=top_k)]


def search_loan_docs(query: str, top_k: int | None = None) -> dict:
    s = get_settings()["rag"]
    query = str(query or "").strip()
    if len(query) < 2:
        return fail(BAD_ARGUMENT, "query 는 두 글자 이상이어야 합니다.")
    top_k = int(top_k or s["top_k"])
    searcher = _searcher
    if searcher is None:
        if not _index_ready(Path(s["chroma_dir"]), s["collection"]):
            return fail(NO_INDEX, "용어·제도 문서 색인을 아직 만들지 않았습니다. 2회차 색인(index_docs)을 먼저 실행하세요.")
        if not get_env("OPENAI_API_KEY"):
            return fail(INVALID_KEY, "검색에 필요한 임베딩 API 키(OPENAI_API_KEY)가 설정되지 않았습니다.")
        searcher = _chroma_search
    try:
        hits = searcher(query, top_k)
    except Exception as e:  # noqa: BLE001
        return fail(UPSTREAM_ERROR, f"문서 검색 중 오류: {scrub(e)[:120]}")
    if hits is None:
        return fail(NO_INDEX, "용어·제도 문서 색인을 아직 만들지 않았습니다.")
    docs = []
    for text, meta, score in hits:
        if score is not None and score < s["min_relevance"]:
            continue
        meta = meta or {}
        docs.append(
            {
                "title": meta.get("title") or meta.get("term") or "(제목 없음)",
                "source": meta.get("source") or meta.get("doc_type") or "",
                "url": meta.get("source_url") or meta.get("url") or "",
                "license": meta.get("license") or "",
                "score": round(float(score), 3) if score is not None else None,
                "text": text[:800],
            }
        )
    if not docs:
        return fail(NO_RESULT, f"'{query}' 와 관련된 문서를 찾지 못했습니다.")
    return ok({"query": query, "count": len(docs), "documents": docs})


def tools() -> list[Tool]:
    return [
        Tool(
            name="search_loan_docs",
            kind=LOCAL,
            description=(
                "LTV·DSR·중도상환수수료·고정/변동금리 같은 대출 용어나 제도의 뜻을 물을 때 쓴다. 금리·상품 목록·시세처럼 "
                "외부 API 로 확인할 수 있는 값에는 쓰지 않는다."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "찾을 용어나 질문"},
                    "top_k": {"type": "integer", "description": "가져올 문서 수 (기본 4)"},
                },
                "required": ["query"],
            },
            output={"query": "검색어", "documents": "[{title, source, url, license, score, text}]"},
            func=search_loan_docs,
            source=SOURCE,
            errors=[BAD_ARGUMENT, NO_INDEX, NO_RESULT, INVALID_KEY, UPSTREAM_ERROR],
        )
    ]
