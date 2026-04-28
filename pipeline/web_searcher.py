"""
DuckDuckGo 웹 검색 모듈.
Qdrant + MCP에 근거가 없을 때 보완 컨텍스트로 활용한다.
"""
from __future__ import annotations

_WEB_CHUNK_LIMIT = 1500
_LOW_CONFIDENCE_THRESHOLD = 0.65  # Qdrant 최고 유사도가 이 값 미만이면 웹 검색 트리거


def should_trigger_web(qdrant_scores: list[float]) -> bool:
    """Qdrant 결과 신뢰도가 낮으면 True."""
    if not qdrant_scores:
        return True
    return max(qdrant_scores) < _LOW_CONFIDENCE_THRESHOLD


def search_web(question: str, max_results: int = 4) -> list[dict]:
    """
    DuckDuckGo로 웹 검색 후 chunk 형식 리스트 반환.
    실패 시 빈 리스트 반환 (파이프라인 중단 없음).
    """
    try:
        from ddgs import DDGS
    except ImportError:
        try:
            from duckduckgo_search import DDGS
        except ImportError:
            print("  [web] ddgs 미설치. pip install ddgs 실행하세요.")
            return []

    try:
        with DDGS() as ddgs:
            raw = list(ddgs.text(question, max_results=max_results, region="kr-kr"))
    except Exception as exc:
        print(f"  [web] 검색 오류: {exc}")
        return []

    chunks: list[dict] = []
    for r in raw:
        title = r.get("title", "")
        body = r.get("body", "")
        url = r.get("href", "")
        text = f"{title}\n{body}".strip()
        if len(text) < 50:
            continue
        chunks.append({
            "doc_name": title or "웹 검색 결과",
            "doc_type": "웹검색",
            "article_no": url,
            "article_title": "",
            "page": 0,
            "text": text[:_WEB_CHUNK_LIMIT],
        })

    print(f"  [web] {len(chunks)}개 웹 결과 추가")
    return chunks
