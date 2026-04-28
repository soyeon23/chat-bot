"""연구행정 AI — Streamlit 메인 채팅 페이지."""
from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

load_dotenv()

st.set_page_config(
    page_title="연구행정 AI",
    page_icon="🔬",
    layout="wide",
    initial_sidebar_state="expanded",
)

from ui.styles import GLOBAL_CSS
from ui.components import render_answer_card, render_quick_prompts

st.markdown(GLOBAL_CSS, unsafe_allow_html=True)

# ──────────────────────────────────────────────────────────────────
# 상수
# ──────────────────────────────────────────────────────────────────

_TOP_K = int(os.getenv("TOP_K", "5"))
_AUDIT_LOG = Path("data/audit_log.jsonl")

_NO_RESULT = {
    "verdict": "판단불가",
    "summary": "검색된 근거가 없어 답변할 수 없습니다.",
    "citations": [],
    "follow_up_needed": True,
    "follow_up_questions": ["관련 문서가 업로드·인덱싱되어 있는지 확인해 주세요."],
    "risk_notes": ["검색 결과 없음"],
}

# ──────────────────────────────────────────────────────────────────
# 캐시 리소스: 임베딩 모델
# ──────────────────────────────────────────────────────────────────

@st.cache_resource(show_spinner="임베딩 모델 로드 중...")
def _load_embedder():
    from langchain_huggingface import HuggingFaceEmbeddings
    return HuggingFaceEmbeddings(model_name="jhgan/ko-sroberta-multitask")


# ──────────────────────────────────────────────────────────────────
# 파이프라인
# ──────────────────────────────────────────────────────────────────

def _normalize(chunk: dict) -> dict:
    return {
        "doc_name":     chunk.get("document_name", ""),
        "doc_type":     chunk.get("document_type", ""),
        "article_no":   chunk.get("article_no", ""),
        "article_title": chunk.get("article_title", ""),
        "page":         chunk.get("page", 0),
        "text":         chunk.get("text", ""),
    }


def run_pipeline(
    question: str,
    doc_type_filter: str | None,
    use_mcp: bool,
    use_web: bool,
) -> tuple[dict, float, bool]:
    """질문 → (AnswerPayload dict, confidence %, web_used)."""
    from pipeline.retriever import search_chunks
    from pipeline.answerer import generate_answer

    embedder = _load_embedder()
    vec = embedder.embed_query(question)

    qdrant_chunks = search_chunks(vec, top_k=_TOP_K, doc_type=doc_type_filter)
    confidence = (
        sum(c["score"] for c in qdrant_chunks) / len(qdrant_chunks) * 100
        if qdrant_chunks else 0.0
    )

    normalized = [_normalize(c) for c in qdrant_chunks]

    if use_mcp:
        try:
            from pipeline.korean_law_client import fetch_law_chunks_from_mcp
            doc_names = [c.get("document_name", "") for c in qdrant_chunks]
            mcp_chunks = fetch_law_chunks_from_mcp(question, doc_names)
            normalized += mcp_chunks
        except Exception as exc:
            st.warning(f"법제처 MCP 조회 실패 (Qdrant 결과만 사용): {exc}")

    # 법제처 공식 API 보완: 토글 ON + Qdrant 신뢰도 낮을 때 자동 트리거
    web_used = False
    if use_web:
        from pipeline.official_law_searcher import should_trigger, search_official_sources
        scores = [c["score"] for c in qdrant_chunks]
        if should_trigger(scores):
            official_chunks = search_official_sources(question)
            if official_chunks:
                normalized += official_chunks
                web_used = True

    if not normalized:
        return _NO_RESULT, 0.0, False

    return generate_answer(question, normalized), confidence, web_used


def _ingest_file(uploaded_file, doc_name: str, doc_type: str) -> None:
    """업로드된 PDF를 파싱·임베딩·Qdrant 적재한다."""
    from pipeline.pdf_parser import parse_pdf
    from pipeline.chunker import chunk_document
    from pipeline.embedder import embed_chunks
    from pipeline.indexer import upsert_chunks, ensure_collection

    save_dir = Path("data/uploads")
    save_dir.mkdir(parents=True, exist_ok=True)
    file_path = save_dir / uploaded_file.name
    file_path.write_bytes(uploaded_file.getvalue())

    with st.status(f"인덱싱 중: {uploaded_file.name}", expanded=True) as status:
        st.write("📖 PDF 파싱 중...")
        parse_result = parse_pdf(str(file_path), save_raw=False)

        st.write("✂️ 청크 분할 중...")
        chunks = chunk_document(parse_result, doc_name, doc_type)
        if not chunks:
            status.update(label="❌ 청크 생성 실패", state="error")
            return

        st.write(f"🔢 임베딩 생성 중 ({len(chunks)}개 청크)...")
        chunk_dicts = [asdict(c) for c in chunks]
        embedded = embed_chunks(chunk_dicts)

        st.write("📤 Qdrant 적재 중...")
        ensure_collection()
        n = upsert_chunks(chunk_dicts, embedded)

        status.update(label=f"✅ 인덱싱 완료 — {n}개 포인트 적재", state="complete")


def _save_audit(question: str, result: dict, confidence: float, use_mcp: bool, web_used: bool = False) -> None:
    _AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp":      datetime.now().isoformat(timespec="seconds"),
        "question":       question,
        "verdict":        result.get("verdict", ""),
        "confidence":     round(confidence, 1),
        "citations_count": len(result.get("citations", [])),
        "follow_up":      result.get("follow_up_needed", False),
        "mcp_used":       use_mcp,
        "web_used":       web_used,
    }
    with open(_AUDIT_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ──────────────────────────────────────────────────────────────────
# 세션 초기화
# ──────────────────────────────────────────────────────────────────

if "messages" not in st.session_state:
    st.session_state.messages = []
if "pending_question" not in st.session_state:
    st.session_state.pending_question = None

# ──────────────────────────────────────────────────────────────────
# 사이드바
# ──────────────────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("""
    <div style="padding:16px 0 8px 0;">
        <div style="font-size:20px;font-weight:700;color:#e2e8f0;">🔬 연구행정 AI</div>
        <div style="font-size:11px;color:#94a3b8;margin-top:4px;">보수형 RAG 챗봇 v0.8</div>
    </div>
    """, unsafe_allow_html=True)

    if st.button("🗑️ 새 분석 시작", use_container_width=True):
        st.session_state.messages = []
        st.session_state.pending_question = None
        st.rerun()

    st.divider()

    # 검색 필터
    st.markdown(
        '<div style="color:#94a3b8;font-size:11px;font-weight:600;'
        'text-transform:uppercase;letter-spacing:0.05em;margin-bottom:6px;">'
        '검색 필터</div>',
        unsafe_allow_html=True,
    )
    _DOC_TYPES = ["전체", "법률", "시행령", "시행규칙", "운영요령", "공고문", "FAQ", "가이드"]
    sel_type = st.selectbox("문서 유형", _DOC_TYPES, label_visibility="collapsed")
    doc_type_filter = None if sel_type == "전체" else sel_type

    use_mcp = st.toggle(
        "법제처 MCP 보완",
        value=True,
        help="국가법령정보센터에서 공식 법령을 추가 검색합니다",
    )
    use_web = st.toggle(
        "법제처 API 보완",
        value=True,
        help="PDF·MCP에 없는 내용을 법제처 공식 API(법령·판례·행정규칙)로 보완합니다 (신뢰도 낮을 때 자동 실행)",
    )

    st.divider()

    # PDF 업로드
    st.markdown(
        '<div style="color:#94a3b8;font-size:11px;font-weight:600;'
        'text-transform:uppercase;letter-spacing:0.05em;margin-bottom:6px;">'
        'PDF 업로드</div>',
        unsafe_allow_html=True,
    )
    uploaded_files = st.file_uploader(
        "PDF",
        type="pdf",
        accept_multiple_files=True,
        label_visibility="collapsed",
    )
    if uploaded_files:
        for uf in uploaded_files:
            with st.expander(f"📄 {uf.name}"):
                u_type = st.selectbox(
                    "문서 유형",
                    ["법률", "시행령", "시행규칙", "운영요령", "공고문", "FAQ", "가이드"],
                    key=f"ut_{uf.name}",
                )
                u_name = st.text_input(
                    "문서명",
                    value=Path(uf.name).stem,
                    key=f"un_{uf.name}",
                )
                if st.button("인덱싱 시작", key=f"ingest_{uf.name}"):
                    _ingest_file(uf, u_name, u_type)

    st.divider()

    # Qdrant 포인트 수 표시
    try:
        from pipeline.indexer import get_collection_count
        cnt = get_collection_count()
        st.markdown(
            f'<div style="color:#94a3b8;font-size:12px;">📊 인덱스: {cnt:,}개 청크</div>',
            unsafe_allow_html=True,
        )
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────────
# 메인 영역
# ──────────────────────────────────────────────────────────────────

st.markdown('<div class="app-header">연구행정 AI 질의응답</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="app-subheader">'
    '법령·운영요령·지침 기반 보수형 답변 &nbsp;|&nbsp; 근거 없으면 답하지 않습니다'
    '</div>',
    unsafe_allow_html=True,
)

# 채팅 히스토리 렌더링
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        if msg["role"] == "assistant":
            render_answer_card(msg["result"], msg.get("confidence", 0.0))
        else:
            st.markdown(msg["content"])

# Quick Prompt 칩
clicked_prompt = render_quick_prompts()
if clicked_prompt:
    st.session_state.pending_question = clicked_prompt
    st.rerun()

st.markdown("")  # 여백

# 질문 입력
chat_input = st.chat_input("연구행정 관련 질문을 입력하세요...")

# 처리할 질문 결정 (quick prompt 우선)
question: str | None = None
if st.session_state.pending_question:
    question = st.session_state.pending_question
    st.session_state.pending_question = None
elif chat_input:
    question = chat_input

if question:
    # 사용자 메시지
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    # 답변 생성
    with st.chat_message("assistant"):
        with st.spinner("근거 검색 및 답변 생성 중..."):
            try:
                result, confidence, web_used = run_pipeline(question, doc_type_filter, use_mcp, use_web)
            except Exception as exc:
                st.error(f"답변 생성 오류: {exc}")
                st.stop()

        if web_used:
            st.caption("📋 법제처 공식 API(법령·판례·행정규칙) 결과가 보완 근거로 사용되었습니다.")
        render_answer_card(result, confidence)
        st.session_state.messages.append({
            "role":       "assistant",
            "result":     result,
            "confidence": confidence,
        })
        _save_audit(question, result, confidence, use_mcp, web_used)
