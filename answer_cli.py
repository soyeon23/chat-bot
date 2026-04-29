"""
연구행정 RAG 질의응답 CLI.

사용법:
    python answer_cli.py --query "학생인건비로 노트북 구매 가능한가요?"
    python answer_cli.py --query "간접비 비율 초과 시 처리 방법" --doc-type 운영요령
    python answer_cli.py --query "국가연구개발혁신법 제N조" --no-mcp
"""

import argparse
import json
import os
import sys

from dotenv import load_dotenv

from pipeline.answerer import generate_answer
from pipeline.auth import auth_status_label, get_auth_source
from pipeline.embedder import embed_query
from pipeline.query_analyzer import analyze_query
from pipeline.retriever import search_chunks_smart

load_dotenv()

_TOP_K = int(os.getenv("TOP_K", "8"))

_NO_RESULT_RESPONSE = {
    "verdict": "판단불가",
    "summary": "검색된 근거가 없어 답변할 수 없습니다.",
    "citations": [],
    "follow_up_needed": True,
    "follow_up_questions": [
        "관련 공고문 또는 운영요령 PDF가 업로드되어 있는지 확인해 주세요."
    ],
    "risk_notes": ["검색 결과 없음"],
}


def _normalize_chunk(chunk: dict) -> dict:
    """retriever 반환 키를 answerer가 기대하는 키로 변환한다."""
    return {
        "doc_name": chunk.get("document_name", ""),
        "doc_type": chunk.get("document_type", ""),
        "article_no": chunk.get("article_no", ""),
        "article_title": chunk.get("article_title", ""),
        "page": chunk.get("page", 0),
        "text": chunk.get("text", ""),
    }


def _fetch_mcp_chunks(question: str, qdrant_chunks: list[dict]) -> list[dict]:
    """korean-law MCP에서 공식 법령 컨텍스트를 가져온다. 실패 시 빈 리스트 반환."""
    try:
        from pipeline.korean_law_client import fetch_law_chunks_from_mcp
        doc_names = [
            c.get("document_name", "")
            for c in qdrant_chunks
            if c.get("document_name")
        ]
        return fetch_law_chunks_from_mcp(question, doc_names)
    except Exception as exc:
        print(f"  [korean-law] MCP 호출 실패 (Qdrant 결과만 사용): {exc}", file=sys.stderr)
        return []


def main() -> None:
    parser = argparse.ArgumentParser(description="연구행정 RAG 질의응답 CLI")
    parser.add_argument("--query", required=True, help="검색할 질문")
    parser.add_argument("--doc-type", default=None, help="문서 유형 필터 (예: 법률, 운영요령)")
    parser.add_argument(
        "--no-mcp", action="store_true",
        help="korean-law MCP 법령 보완 비활성화 (Qdrant 결과만 사용)",
    )
    parser.add_argument(
        "--web", action="store_true",
        help="Qdrant·MCP에 없는 내용을 법제처 공식 API(법령·판례·행정규칙)로 보완",
    )
    parser.add_argument(
        "--prior-json", default=None,
        help=(
            "직전 대화 턴 (JSON 배열). 멀티턴 모드 테스트용. "
            "예: '[{\"role\":\"user\",\"content\":\"회의비 세미나?\"},"
            "{\"role\":\"assistant\",\"content\":\"가능합니다 — ...\"}]'"
        ),
    )
    args = parser.parse_args()

    # 멀티턴: --prior-json 으로 직전 대화 주입 (선택).
    prior_turns: list[dict] = []
    if args.prior_json:
        try:
            prior_turns = json.loads(args.prior_json)
            if not isinstance(prior_turns, list):
                raise ValueError("--prior-json 은 JSON 배열이어야 합니다.")
        except (json.JSONDecodeError, ValueError) as e:
            print(f"[--prior-json 파싱 실패] {e}", file=sys.stderr)
            sys.exit(2)

    # 시작 시 Claude Code 로그인 상태 확인
    try:
        get_auth_source()
        print(f"[인증] {auth_status_label()}", file=sys.stderr)
    except RuntimeError as e:
        print(f"[인증 실패]\n{e}", file=sys.stderr)
        sys.exit(1)

    print("[2a/4] 질의 의도 분석 중 (Claude)…", file=sys.stderr)
    hints = analyze_query(args.query, prior_turns=prior_turns or None)
    print(f"  → kind={hints.kind}  pages={hints.target_pages}  "
          f"articles={hints.article_nos}  doc_hint={hints.doc_name_hint!r}",
          file=sys.stderr)

    # 멀티턴: rewritten_query 가 있으면 그것으로 임베딩·검색.
    search_query = (hints.rewritten_query or "").strip() or args.query
    if search_query != args.query:
        print(
            f"  → rewritten_query={search_query!r} (original={args.query!r})",
            file=sys.stderr,
        )

    print(f"[1/4] 임베딩 생성 중: {search_query!r}", file=sys.stderr)
    query_vector = embed_query(search_query)

    print(f"[2/4] Qdrant 검색 중 (top_k={_TOP_K}, doc_type={args.doc_type})", file=sys.stderr)
    qdrant_chunks = search_chunks_smart(
        search_query, query_vector, top_k=_TOP_K, doc_type=args.doc_type, hints=hints,
    )
    normalized_qdrant = [_normalize_chunk(c) for c in qdrant_chunks]

    # korean-law MCP로 공식 법령 보완 (--no-mcp 플래그 없을 때)
    mcp_chunks: list[dict] = []
    if not args.no_mcp:
        print("[3/4] korean-law MCP 법령 검색 중...", file=sys.stderr)
        mcp_chunks = _fetch_mcp_chunks(args.query, qdrant_chunks)
    else:
        print("[3/4] MCP 비활성화 (--no-mcp)", file=sys.stderr)

    # 웹 검색 보완 (--web 플래그 + 신뢰도 낮을 때)
    web_chunks: list[dict] = []
    if args.web:
        from pipeline.official_law_searcher import should_trigger, search_official_sources
        scores = [c["score"] for c in qdrant_chunks]
        if should_trigger(scores):
            print("[법제처 API] 공식 법령·판례·행정규칙 검색 중...", file=sys.stderr)
            web_chunks = search_official_sources(args.query)
        else:
            print("[법제처 API] Qdrant 신뢰도 충분 — API 검색 생략", file=sys.stderr)

    all_chunks = normalized_qdrant + mcp_chunks + web_chunks

    if not all_chunks:
        print("[결과] 검색된 근거 없음 - 판단불가 반환", file=sys.stderr)
        print(json.dumps(_NO_RESULT_RESPONSE, ensure_ascii=False, indent=2))
        return

    src_summary = f"Qdrant {len(normalized_qdrant)}개 + MCP {len(mcp_chunks)}개 + 웹 {len(web_chunks)}개"
    print(f"[4/4] Claude 답변 생성 중 (근거 {src_summary})", file=sys.stderr)
    result = generate_answer(
        args.query, all_chunks, kind=hints.kind,
        prior_turns=prior_turns or None,
    )

    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
