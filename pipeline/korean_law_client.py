"""
korean-law MCP 서버 클라이언트 (Streamable HTTP 전송 방식).
https://korean-law-mcp.fly.dev (법제처 41개 API -> 16개 MCP 도구)

동기 인터페이스를 제공한다. 내부적으로 asyncio.run()을 사용하므로
이미 실행 중인 이벤트 루프 안에서 호출하지 않도록 주의.
"""
from __future__ import annotations

import asyncio
import os
import re
from typing import Optional

from dotenv import load_dotenv
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

load_dotenv()

_MCP_BASE = os.getenv("KOREAN_LAW_MCP_URL", "https://korean-law-mcp.fly.dev/mcp")
_OC = os.getenv("KOREAN_LAW_OC", "")

# 한 세션에서 처리할 최대 법령 수
_MAX_LAWS_PER_SESSION = 3


def _build_url() -> str:
    return f"{_MCP_BASE}?oc={_OC}" if _OC else _MCP_BASE


# ---------------------------------------------------------------------------
# 내부 비동기 헬퍼
# ---------------------------------------------------------------------------

async def _call_tool_async(tool_name: str, arguments: dict) -> str:
    """단일 MCP 도구 호출. 텍스트 결과를 문자열로 반환."""
    url = _build_url()
    async with streamablehttp_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool_name, arguments)
            texts = [
                item.text
                for item in (result.content or [])
                if hasattr(item, "text") and item.text
            ]
            return "\n".join(texts)


_MST_RE = re.compile(r"MST:\s*(\d+)")
_ARTICLE_NO_RE = re.compile(r"제\s*(\d+)\s*조")
_LAW_TEXT_LIMIT = 4000  # Claude 컨텍스트 절약을 위한 조문 글자 수 상한


def _parse_first_mst(search_text: str) -> str | None:
    """search_law 결과 텍스트에서 첫 번째 법률(法律) MST를 추출한다."""
    # 법률 항목 우선, 없으면 첫 번째 결과의 MST 반환
    m = _MST_RE.search(search_text)
    return m.group(1) if m else None


def _extract_article_no(question: str) -> str | None:
    """질문에서 '제N조' 패턴을 추출한다."""
    m = _ARTICLE_NO_RE.search(question)
    return f"제{m.group(1)}조" if m else None


async def _batch_fetch_law_text_async(queries: list[str], question: str = "") -> list[str]:
    """
    2단계 파이프라인: search_law → get_law_text.
    법령 목록이 아닌 실제 조문 내용을 반환한다.
    """
    url = _build_url()
    results: list[str] = []
    article_no = _extract_article_no(question)

    async with streamablehttp_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            for query in queries:
                try:
                    # 1단계: 법령 검색 → MST 확보
                    search_result = await session.call_tool("search_law", {"query": query})
                    search_text = "\n".join(
                        item.text for item in (search_result.content or [])
                        if hasattr(item, "text") and item.text
                    )
                    mst = _parse_first_mst(search_text)

                    if not mst:
                        # MST를 찾지 못하면 검색 결과 텍스트 그대로 사용
                        results.append(search_text)
                        continue

                    # 2단계: 조문 전문 가져오기
                    fetch_args: dict = {"mst": mst}
                    if article_no:
                        fetch_args["jo"] = article_no  # 특정 조문만 가져오기

                    law_result = await session.call_tool("get_law_text", fetch_args)
                    law_text = "\n".join(
                        item.text for item in (law_result.content or [])
                        if hasattr(item, "text") and item.text
                    )
                    results.append(law_text[:_LAW_TEXT_LIMIT])

                except Exception as exc:
                    print(f"  [korean-law] '{query}' 조회 실패: {exc}")
                    results.append("")

    return results


# ---------------------------------------------------------------------------
# 공개 동기 함수
# ---------------------------------------------------------------------------

def search_law(query: str) -> str:
    """법령명·키워드로 법령 검색. 결과 문자열 반환."""
    try:
        return asyncio.run(_call_tool_async("search_law", {"query": query}))
    except Exception as exc:
        print(f"  [korean-law] search_law 오류: {exc}")
        return ""


def get_law_text(law_name: str, article_no: Optional[str] = None) -> str:
    """법령 조문 전문 조회."""
    args: dict = {"law_name": law_name}
    if article_no:
        args["jo"] = article_no
    try:
        return asyncio.run(_call_tool_async("get_law_text", args))
    except Exception as exc:
        print(f"  [korean-law] get_law_text 오류: {exc}")
        return ""


def get_annexes(law_name: str) -> str:
    """별표·서식 조회."""
    try:
        return asyncio.run(_call_tool_async("get_annexes", {"law_name": law_name}))
    except Exception as exc:
        print(f"  [korean-law] get_annexes 오류: {exc}")
        return ""


# ---------------------------------------------------------------------------
# 고수준 헬퍼
# ---------------------------------------------------------------------------

# 법령명 끝에 오는 단어들
_LAW_SUFFIX = r'(?:에\s*관한\s*특별법|에\s*관한\s*법률|법률|법|시행령|시행규칙|기준|지침|고시|규정|요령)'
_LAW_RE = re.compile(r'[가-힣]{2,}' + _LAW_SUFFIX)
# 단독으로 쓰이면 너무 범용적인 단어들 (앞에 수식어 없이 단독 출현 시 제외)
_SKIP_ALONE = {"시행령", "시행규칙", "기준", "지침", "고시", "규정", "요령"}


def _extract_law_from_question(question: str) -> list[str]:
    """질문 텍스트에서 법령명 패턴을 추출한다. 예: '근로기준법' '노동법' '국가연구개발혁신법'."""
    matches = _LAW_RE.findall(question)
    seen: set[str] = set()
    result: list[str] = []
    for m in matches:
        m = re.sub(r"\s+", "", m)  # 공백 제거 ('에 관한 법률' → '에관한법률')
        if m not in _SKIP_ALONE and m not in seen:
            seen.add(m)
            result.append(m)
    return result


def _extract_law_names_from_docs(doc_names: list[str]) -> list[str]:
    """doc_name 목록에서 날짜·기관코드를 제거하고 고유 법령명을 추출한다."""
    seen: set[str] = set()
    result: list[str] = []
    for name in doc_names:
        base = re.split(r"[\(\[\<]", name)[0].strip()
        if base and base not in seen:
            seen.add(base)
            result.append(base)
    return result


def fetch_law_chunks_from_mcp(
    question: str,
    qdrant_doc_names: list[str],
    max_laws: int = _MAX_LAWS_PER_SESSION,
) -> list[dict]:
    """
    질문과 Qdrant 결과 법령명을 활용하여 MCP 법령 검색을 수행한다.

    검색 우선순위:
      1순위: 질문 텍스트에서 직접 추출한 법령명 (노동법, 근로기준법 등 어느 법이든)
      2순위: Qdrant 결과의 문서명 (R&D 관련 문서)
      3순위: 위 둘 다 없으면 질문 전체를 키워드로 사용

    Returns:
        answerer.generate_answer()에 바로 전달 가능한 chunk dict 리스트
    """
    # 1순위: 질문에서 법령명 추출
    question_laws = _extract_law_from_question(question)

    # 2순위: Qdrant 문서명 (중복 제거하며 추가)
    qdrant_laws = _extract_law_names_from_docs(qdrant_doc_names)
    seen = set(question_laws)
    for name in qdrant_laws:
        if name not in seen:
            seen.add(name)
            question_laws.append(name)

    # 3순위: 아무것도 없으면 질문 자체를 검색어로
    queries = question_laws[:max_laws] if question_laws else [question]

    print(f"  [korean-law] 법령 검색 대상: {queries}")

    try:
        raw_results = asyncio.run(_batch_fetch_law_text_async(queries, question))
    except Exception as exc:
        print(f"  [korean-law] 배치 검색 실패: {exc}")
        return []

    chunks: list[dict] = []
    for query, text in zip(queries, raw_results):
        if not text.strip():
            continue
        # MCP가 [NOT_FOUND]를 반환하면 건너뜀
        if "[NOT_FOUND]" in text:
            print(f"  [korean-law] '{query}' - 법령 없음 (건너뜀)")
            continue
        safe_text = text.encode("utf-8", errors="replace").decode("utf-8")
        chunks.append({
            "doc_name": f"[법제처] {query}",
            "doc_type": "공식법령(MCP)",
            "article_no": "법령 검색 결과",
            "article_title": "",
            "page": 0,
            "text": safe_text[:2000],
        })

    if chunks:
        print(f"  [korean-law] {len(chunks)}개 법령 컨텍스트 추가")
    else:
        print("  [korean-law] 검색 결과 없음")

    return chunks
