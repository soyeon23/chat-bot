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

# hangul_mcp(OLE2 HWP 파서) + claude-agent-sdk partial message 처리 등 일부
# 코드 경로가 Python 기본 recursion 한계 1000 을 초과한다. 모든 thread 에 영향
# 미치도록 streamlit 부팅 가장 이른 시점에 process 전역 한계를 끌어올린다.
sys.setrecursionlimit(max(sys.getrecursionlimit(), 20000))

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
# 온보딩 위저드 라우팅
# 최초 실행 시 자동 환경 검사 페이지로 보냄.
# ──────────────────────────────────────────────────────────────────
from pipeline.config_store import load_config

_cfg_on_start = load_config()
if not _cfg_on_start.onboarding_completed:
    st.switch_page("pages/00_⚙️_환경설정.py")

# HWP 페이지 캐시 사전 워밍업 — 백그라운드 스레드로 1회.
# 첫 article_lookup 에서 hwp-mcp 서브프로세스 + HWP 파싱(30~60s)을 사용자가
# 기다리는 대신, 앱 부팅 직후 백그라운드로 끝내 둠. 모듈 전역 _doc_cache 에
# 적재되므로 이후 read_page / get_article 호출은 메모리 히트.
# 세션당 1회 (Streamlit script 재실행마다 session_state 로 재진입 차단).
if not st.session_state.get("_hwp_warmup_started"):
    import threading as _wm_threading

    _WARMUP_DEBUG = os.getenv("CHATBOT_DEBUG_WARMUP") == "1"

    def _warmup_hwp_cache() -> None:
        n_ok = 0
        n_fail = 0
        try:
            from pipeline.local_doc_mcp import _scan_dirs, _load_pages
            for p in _scan_dirs():
                if p.suffix.lower() in (".hwp", ".hwpx"):
                    try:
                        _load_pages(p)
                        n_ok += 1
                    except Exception as e:  # noqa: BLE001
                        n_fail += 1
                        if _WARMUP_DEBUG:
                            print(
                                f"[warmup] {p.name} 파싱 실패: {type(e).__name__}: {e}",
                                file=sys.stderr,
                            )
            if n_fail:
                print(
                    f"[warmup] HWP 캐시: 성공 {n_ok}, 실패 {n_fail} "
                    f"(상세는 CHATBOT_DEBUG_WARMUP=1)",
                    file=sys.stderr,
                )
            elif n_ok:
                print(f"[warmup] HWP 캐시: {n_ok}개 캐싱 완료", file=sys.stderr)
        except Exception as e:  # noqa: BLE001
            print(f"[warmup] HWP 캐시 워밍 실패: {type(e).__name__}: {e}", file=sys.stderr)

    _wm_threading.Thread(target=_warmup_hwp_cache, daemon=True, name="hwp-warmup").start()
    st.session_state["_hwp_warmup_started"] = True

# 자동 동기화 (auto_sync_on_start) — 세션당 한 번만, 변경 없으면 0초.
# 변경이 발견되면 사이드바 알림으로 안내만 하고 실 인덱싱은 사용자 클릭 후.
if _cfg_on_start.auto_sync_on_start and not st.session_state.get("_auto_sync_done"):
    try:
        from pipeline.sync import (
            METADATA_PATH,
            init_metadata_from_qdrant,
            scan_changes,
            _resolve_default_roots,
        )
        roots = _resolve_default_roots()
        if not METADATA_PATH.exists():
            init_metadata_from_qdrant(roots=roots)
        _auto_changes = scan_changes(roots=roots)
        n_pending = (
            len(_auto_changes["added"]) + len(_auto_changes["modified"])
            + len(_auto_changes["deleted"]) + len(_auto_changes["stale_code"])
        )
        if n_pending > 0:
            st.sidebar.warning(
                f"📂 동기화 대기 {n_pending}개 — 환경설정에서 실행하세요."
            )
        st.session_state["_auto_sync_done"] = True
    except Exception as e:  # noqa: BLE001
        # 자동 sync 실패는 챗봇 자체에 영향 주지 말고 조용히 로깅
        st.session_state["_auto_sync_done"] = True
        st.session_state["_auto_sync_error"] = f"{type(e).__name__}: {e}"

# ──────────────────────────────────────────────────────────────────
# 상수
# ──────────────────────────────────────────────────────────────────

_TOP_K = int(os.getenv("TOP_K", "8"))
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


def _compute_confidence(qdrant_chunks: list[dict]) -> tuple[float | None, list[str]]:
    """Qdrant 청크 목록에서 벡터 유사도 기반 신뢰도와 부스트 신호 라벨을 산출한다.

    retriever.py 의 부스트 로직 특성:
    - 순수 벡터 코사인 점수: 통상 0.55~0.85 (≤ 1.0)
    - page lookup 부스트: 0.5 + 1.0 = 1.5 (≥ 1.0)
    - 구조적 매칭 부스트: vec + 0.50 (≥ 1.0 가능)
    - kw_and 부스트: vec + 0.25 (보통 ≤ 1.0)
    - phrase 부스트: vec + 0.35 (일부 1.0 초과 가능)

    threshold 1.0 을 기준으로:
    - score < 1.0  → 벡터 주도 청크 (vec_confidence 산정에 포함)
    - score ≥ 1.0 → 부스트 주도 청크 (벡터 신뢰도 산정 제외)

    Returns:
        (confidence_pct, signal_labels)
        confidence_pct: 벡터 청크가 있으면 그 평균 score * 100,
                        없으면 None (부스트/키워드 매칭만 존재함을 의미)
        signal_labels: UI 표시용 부스트 신호 라벨 리스트
    """
    if not qdrant_chunks:
        return None, []

    # 점수 기반 분류
    _PAGE_BOOST_THRESHOLD = 1.3   # page lookup(1.5)은 항상 이 이상
    _STRUCTURAL_THRESHOLD = 1.0   # structural boost(0.5+0.5=1.0) 경계

    vec_scores: list[float] = []
    has_page_boost = False
    has_structural_boost = False
    has_phrase_boost = False

    for c in qdrant_chunks:
        score = c.get("score", 0.0)
        if score >= _PAGE_BOOST_THRESHOLD:
            has_page_boost = True
        elif score >= _STRUCTURAL_THRESHOLD:
            has_structural_boost = True
        else:
            # score < 1.0: 순수 벡터 또는 소형 부스트 포함 — 벡터 주도로 간주
            vec_scores.append(score)
            # kw_and(+0.25) / phrase(+0.35) 부스트 감지 — 0.75 초과 + 비교적 고점
            if score > 0.75:
                has_phrase_boost = True

    # 벡터 주도 청크의 평균 score → confidence %
    if vec_scores:
        avg = sum(vec_scores) / len(vec_scores)
        # 작은 부스트가 섞여 있을 수 있으므로 최대 95% 로 캡
        confidence_pct: float | None = min(avg * 100, 95.0)
    elif has_page_boost:
        confidence_pct = 92.0   # 페이지 직접 매칭 → 고신뢰
    elif has_structural_boost:
        confidence_pct = 87.0   # 조문 직접 매칭 → 고신뢰
    else:
        confidence_pct = None  # 청크 없음(예외 경로)

    # 신호 라벨 구성
    signals: list[str] = []
    if has_page_boost:
        signals.append("page")
    if has_structural_boost:
        signals.append("structural")
    if has_phrase_boost and not has_page_boost and not has_structural_boost:
        signals.append("phrase")

    return confidence_pct, signals


def _estimate_tokens(text: str) -> int:
    """텍스트의 근사 토큰 수를 반환한다.

    한국어 문자: 1글자 ≈ 1토큰 (cl100k/claude tokenizer 기준).
    ASCII 공백/구두점 등 영문 토큰: 평균 4자 ≈ 1토큰.
    혼합 텍스트의 실용적 근사: 전체 글자 수를 1.0 으로 나눈 값.
    (과추정이지만 운영 가시성 목적에는 충분히 보수적.)
    """
    return len(text)


def run_pipeline(
    question: str,
    doc_type_filter: str | None,
    use_mcp: bool,
    use_web: bool,
    prior_turns: list[dict] | None = None,
    progress_cb=None,
) -> tuple[dict, float, bool, dict]:
    """질문 → (AnswerPayload dict, confidence %, web_used, ctx_stats).

    Args:
        prior_turns: 직전 대화 턴 (UI session_state.messages 의 마지막 N개).
                     analyzer 가 후속 질문에서 페이지·문서 컨텍스트를 이어받기 위함.
        progress_cb: Optional[Callable[[dict], None]]. UI 진행상황 표시용.
                     이벤트 종류: stage(검색 시작), tool_use(도구 호출),
                     tool_result(도구 응답), stage_done(최종 답변 생성 중).

    Returns:
        result: AnswerPayload dict
        confidence: 벡터 유사도 평균 (0~95). None 이면 UI 에서 N/A 표시.
        web_used: 법제처 공식 API 사용 여부
        ctx_stats: 컨텍스트 사용량 통계 dict
                   {"n_chunks": int, "n_chars": int, "n_tokens": int,
                    "limit_tokens": int, "signals": list[str]}
    """
    from pipeline.retriever import search_chunks_smart
    from pipeline.answerer import generate_answer, build_context, get_model
    from pipeline.query_analyzer import analyze_query
    from pipeline import answer_cache

    def _stage(name: str) -> None:
        """단계 이벤트를 progress_cb 로 통지 (예외 안전)."""
        if progress_cb is None:
            return
        try:
            progress_cb({"type": "stage", "name": name})
        except Exception:
            pass

    _stage("질의 분석")
    embedder = _load_embedder()

    # Claude 기반 의도 분석 — 페이지·문서·조문·비교 의도를 자유 표현에서 추출.
    # 직전 대화가 있으면 컨텍스트로 함께 넘긴다 (후속 질문 라우팅 + rewritten_query).
    # 실패 시 정규식 fallback (analyze_query 내부에서 처리).
    hints = analyze_query(question, prior_turns=prior_turns)

    # 일상 대화로 분류된 경우 — retrieval/answerer 모두 스킵하고 분석기의 즉답을 그대로 사용.
    if hints.kind == "chat" and hints.chat_response:
        chat_payload = {
            "kind": "chat",
            "summary": hints.chat_response,
        }
        _empty_ctx: dict = {"n_chunks": 0, "n_chars": 0, "n_tokens": 0,
                            "limit_tokens": 200_000, "signals": []}
        return chat_payload, 0.0, False, _empty_ctx

    # 멀티턴: rewritten_query 가 있으면 임베딩·검색 입력을 거기에 맞춘다.
    # 후속 질문 ("실제 사례 있어?") 도 직전 주제 ("회의비 세미나") 가 흡수된 self-contained
    # 질의 ("회의비로 세미나 개최한 실제 사례") 로 검색돼 무관 청크 회수를 막는다.
    # 첫 질문(prior_turns 없음) 또는 LLM 폴백이면 rewritten_query == question 이라
    # 회귀 영향 0.
    search_query = (hints.rewritten_query or "").strip() or question
    if search_query != question:
        # 멀티턴 디버깅 신호 — 어떻게 다시 작성됐는지 stderr 에 남긴다.
        print(
            f"[run_pipeline] rewritten_query={search_query!r} "
            f"(original={question!r})",
            file=sys.stderr,
        )

    # 답변 캐시 — analyzer 의 rewritten_query 는 멀티턴 컨텍스트마다 미세
    # 변동(예: "전문" 부착 여부)이 있어 키로 부적합. 대신 *사용자 원문*
    # + *의도 구조* (kind, doc_name_hint) 를 키로 사용 — 같은 질문 같은
    # 의도면 어떤 시점이든 hit. 판단불가 미저장 정책으로 회귀 영향 최소화.
    cache_key_args = dict(
        query=question.strip(),
        doc_type_filter=doc_type_filter,
        use_mcp=use_mcp,
        use_web=use_web,
        claude_model=get_model(kind=hints.kind),
        kind=hints.kind or "",
        doc_hint=(hints.doc_name_hint or "").strip(),
    )
    cached = answer_cache.get(**cache_key_args)
    if cached is not None:
        print(f"[run_pipeline] 캐시 HIT — query={search_query!r}", file=sys.stderr)
        _stage("캐시 적중")
        return (cached.result, cached.confidence, cached.web_used, cached.ctx_stats)

    # page_lookup retrieval 스킵 — 사용자가 페이지를 명시한 경우 (예: "151p")
    # Qdrant 검색 자체가 불필요. doc 추정도 hints 가 채워둠 → 도구 모드로 직행.
    skip_retrieval = (
        hints.kind == "page_lookup"
        and bool(hints.target_pages)
    )

    if skip_retrieval:
        _stage("페이지 직접 조회")
        qdrant_chunks = []
        confidence = 0.0
        boost_signals = ["page_lookup_skip_retrieval"]
        normalized = []
        print(
            f"[run_pipeline] retrieval 스킵 — kind={hints.kind} "
            f"target_pages={hints.target_pages} doc_hint={hints.doc_name_hint!r}",
            file=sys.stderr,
        )
    else:
        _stage("문서 검색")
        vec = embedder.embed_query(search_query)

        qdrant_chunks = search_chunks_smart(
            search_query, vec, top_k=_TOP_K, doc_type=doc_type_filter, hints=hints,
        )

        # 벡터 유사도 기반 confidence 재산출 (부스트 청크 분리)
        confidence_raw, boost_signals = _compute_confidence(qdrant_chunks)
        # None → 0.0 으로 표시 (UI 에서 signals 로 구분)
        confidence = confidence_raw if confidence_raw is not None else 0.0

        normalized = [_normalize(c) for c in qdrant_chunks]

    # page_lookup retrieval 스킵 시엔 외부 보완도 의미 없음 (페이지 직접 조회용).
    if use_mcp and not skip_retrieval:
        try:
            from pipeline.korean_law_client import fetch_law_chunks_from_mcp
            doc_names = [c.get("document_name", "") for c in qdrant_chunks]
            _stage("법제처 MCP 조회")
            mcp_chunks = fetch_law_chunks_from_mcp(question, doc_names)
            normalized += mcp_chunks
        except Exception as exc:
            st.warning(f"법제처 MCP 조회 실패 (Qdrant 결과만 사용): {exc}")

    # 법제처 공식 API 보완: 토글 ON + Qdrant 신뢰도 낮을 때 자동 트리거
    web_used = False
    if use_web and not skip_retrieval:
        from pipeline.official_law_searcher import should_trigger, search_official_sources
        scores = [c["score"] for c in qdrant_chunks]
        if should_trigger(scores):
            _stage("법제처 공식 API 보완")
            official_chunks = search_official_sources(question)
            if official_chunks:
                normalized += official_chunks
                web_used = True

    # Phase H — page_lookup / article_lookup 경로는 retrieval 이 비어도
    # Claude 가 도구로 PDF 를 직접 읽어 답할 수 있다. 빈 chunks 로는
    # generate_answer 가 ValueError 를 던지므로 stub 1개를 넣어 도구 모드로
    # 진입시킨다. 도구가 모두 실패하면 모델이 verdict=판단불가 로 응답한다.
    if not normalized and hints.kind in {"page_lookup", "article_lookup"}:
        normalized = [{
            "doc_name": hints.doc_name_hint or "",
            "doc_type": "",
            "article_no": "",
            "article_title": "",
            "page": (hints.target_pages[0] if hints.target_pages else 0),
            "text": (
                "(검색된 근거 없음 — 사용자가 요청한 페이지/조문을 mcp__local_doc__* "
                "도구로 직접 조회하세요.)"
            ),
        }]

    if not normalized:
        _empty_ctx2: dict = {"n_chunks": 0, "n_chars": 0, "n_tokens": 0,
                             "limit_tokens": 200_000, "signals": boost_signals}
        return _NO_RESULT, 0.0, False, _empty_ctx2

    # 컨텍스트 사용량 산출 — build_context 와 동일한 포맷으로 추정
    ctx_text = build_context(normalized)
    ctx_chars = len(ctx_text)
    ctx_tokens = _estimate_tokens(ctx_text)
    _MODEL_LIMIT = 200_000  # Sonnet 4.6 컨텍스트 한도 (토큰)
    ctx_stats: dict = {
        "n_chunks": len(normalized),
        "n_chars": ctx_chars,
        "n_tokens": ctx_tokens,
        "limit_tokens": _MODEL_LIMIT,
        "signals": boost_signals,
    }

    _stage("답변 생성")
    result = generate_answer(
        question, normalized, kind=hints.kind, prior_turns=prior_turns,
        progress_cb=progress_cb,
    )

    # 캐시 저장 — 단발·멀티턴 모두. 판단불가는 다음 시도 여지를 위해 스킵.
    verdict = (result.get("verdict") or "").strip()
    if verdict and verdict != "판단불가":
        try:
            answer_cache.put(
                **cache_key_args,
                entry=answer_cache.CacheEntry(
                    result=result,
                    confidence=confidence,
                    web_used=web_used,
                    ctx_stats=ctx_stats,
                ),
            )
            print(f"[run_pipeline] 캐시 저장 — query={search_query!r}", file=sys.stderr)
        except Exception as exc:
            print(f"[run_pipeline] 캐시 저장 실패: {exc}", file=sys.stderr)

    return (result, confidence, web_used, ctx_stats)


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


def _save_audit(
    question: str,
    result: dict,
    confidence: float,
    use_mcp: bool,
    web_used: bool = False,
    started_at: datetime | None = None,
    ended_at: datetime | None = None,
) -> None:
    _AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    end = ended_at or datetime.now()
    start = started_at or end
    duration_sec = round((end - start).total_seconds(), 2)
    entry = {
        # 호환성: 기존 필드명 timestamp는 종료 시각으로 유지.
        "timestamp":       end.isoformat(timespec="seconds"),
        "started_at":      start.isoformat(timespec="seconds"),
        "ended_at":        end.isoformat(timespec="seconds"),
        "duration_sec":    duration_sec,
        "question":        question,
        "verdict":         result.get("verdict", ""),
        "confidence":      round(confidence, 1),
        "citations_count": len(result.get("citations", [])),
        "follow_up":       result.get("follow_up_needed", False),
        "mcp_used":        use_mcp,
        "web_used":        web_used,
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

    if st.button("⚙️ 환경/경로 설정", use_container_width=True):
        st.switch_page("pages/00_⚙️_환경설정.py")

    st.divider()

    # 모델 선택 (rate limit 즉시 회피용)
    from pipeline.answerer import get_model
    from pipeline.config_store import load_config as _load_cfg, update_config as _update_cfg

    _MODELS = {
        "Haiku 4.5 (가벼움 · 빠름 · rate limit 여유)": "claude-haiku-4-5-20251001",
        "Sonnet 4.5 (균형)": "claude-sonnet-4-5",
        "Sonnet 4.6 (최신 균형)": "claude-sonnet-4-6",
        "Opus 4.7 (최고 품질 · 한도 빨리 소진)": "claude-opus-4-7",
    }
    _MODEL_LABELS = list(_MODELS.keys())
    _current_model = get_model()
    try:
        _current_idx = list(_MODELS.values()).index(_current_model)
    except ValueError:
        _current_idx = 0  # default to Haiku
    sel_label = st.selectbox(
        "Claude 모델",
        _MODEL_LABELS,
        index=_current_idx,
        help="429 자주 뜨면 Haiku로 변경. 변경 즉시 적용 (재시작 불필요).",
    )
    sel_model = _MODELS[sel_label]
    if sel_model != _current_model and _current_model in _MODELS.values():
        _update_cfg(claude_model=sel_model)
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

    # Claude Code 인증 상태 표시
    try:
        from pipeline.auth import auth_status_label, get_auth_source
        get_auth_source()
        st.markdown(
            f'<div style="color:#22c55e;font-size:12px;margin-top:4px;">'
            f'🔐 인증: {auth_status_label()}</div>',
            unsafe_allow_html=True,
        )
    except RuntimeError:
        st.markdown(
            '<div style="color:#ef4444;font-size:12px;margin-top:4px;">'
            '🔐 인증 미설정 — `claude` CLI 로그인 필요</div>',
            unsafe_allow_html=True,
        )


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
            res = msg["result"]
            if isinstance(res, dict) and res.get("kind") == "chat":
                st.markdown(res.get("summary", ""))
            else:
                render_answer_card(
                    res,
                    msg.get("confidence", 0.0),
                    ctx_stats=msg.get("ctx_stats"),
                )
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
    # 분석기에 넘길 직전 대화 — 사용자 메시지 append 전에 캡처해야 한다
    # (현재 질문이 prior_turns 에 들어가면 안 됨).
    # 메시지 스키마:
    #   user      → {"role":"user", "content": str}
    #   assistant → {"role":"assistant", "result": dict, "confidence": float}
    prior_turns_for_analyzer: list[dict] = []
    for msg in st.session_state.messages[-3:]:
        role = msg.get("role")
        if role == "user":
            text = msg.get("content")
        elif role == "assistant":
            res = msg.get("result") or {}
            text = str(res.get("summary", ""))
        else:
            continue
        if not isinstance(text, str) or not text.strip():
            continue
        prior_turns_for_analyzer.append({"role": role, "content": text[:1000]})

    # 사용자 메시지
    st.session_state.messages.append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    # 답변 생성 — 취소 버튼 + 백그라운드 워커 + 진행상황 라이브 표시
    import threading

    from pipeline.answerer import RateLimitError

    started_at = datetime.now()
    cancel_event = threading.Event()
    result_holder: dict = {}

    # 진행상황 이벤트 큐 (워커 → 메인 스레드).
    # progress_cb 는 워커 스레드에서 호출되므로 list + lock 으로 단순 보호.
    progress_events: list[dict] = []
    progress_lock = threading.Lock()

    def _on_progress(event: dict) -> None:
        with progress_lock:
            progress_events.append(event)

    def _worker():
        try:
            result_holder["value"] = run_pipeline(
                question, doc_type_filter, use_mcp, use_web,
                prior_turns=prior_turns_for_analyzer,
                progress_cb=_on_progress,
            )
        except RateLimitError as exc:
            result_holder["rate_limit"] = str(exc)
        except Exception as exc:  # noqa: BLE001
            result_holder["error"] = f"{type(exc).__name__}: {exc}"

    def _format_tool_label(name: str, args: dict) -> str:
        """MCP 도구 이름 + 인자를 한국어 진행 메시지로."""
        bare = name.replace("mcp__local_doc__", "")
        doc = (args.get("doc_name") or "").strip()
        if bare == "read_page":
            page = args.get("page_num") or args.get("page") or "?"
            return f"📖 {doc or '문서'} p.{page} 읽는 중"
        if bare == "get_article":
            art = args.get("article_no") or "?"
            return f"📄 {doc or '문서'} {art} 조회"
        if bare == "search_text":
            q = (args.get("query") or "").strip()
            return f"🔍 {doc or '문서'}에서 '{q}' 검색"
        if bare == "list_articles":
            return f"📋 {doc or '문서'} 조문 목록"
        if bare == "list_documents":
            return "📂 문서 목록 조회"
        return f"🛠 {bare}"

    import html as _html_mod
    import re as _re_mod
    _SUMMARY_STREAM_RE = _re_mod.compile(r'"summary"\s*:\s*"((?:[^"\\]|\\.)*)')

    def _render_progress_html(events: list[dict]) -> str:
        """진행 이벤트 리스트 → HTML. 도구 호출 ✓/⚠ + 답변 stream 미리보기."""
        rendered_lines: list[str] = []
        pending_tool: dict | None = None
        stream_chunks: list[str] = []
        for ev in events:
            t = ev.get("type")
            if t == "stage":
                rendered_lines.append(
                    f'<div style="color:#94a3b8;font-size:13px;">'
                    f'⏳ {ev.get("name", "")}…</div>'
                )
            elif t == "tool_use":
                label = _format_tool_label(ev.get("name", ""), ev.get("input") or {})
                rendered_lines.append(
                    f'<div style="color:#cbd5e1;font-size:13px;margin-left:14px;">'
                    f'  • {label}…</div>'
                )
                pending_tool = ev
            elif t == "tool_result":
                # 마지막 tool_use 라벨 옆에 ✓/⚠ 갱신
                if rendered_lines and pending_tool is not None:
                    last = rendered_lines[-1]
                    mark = "⚠" if ev.get("is_error") else "✓"
                    color = "#ef4444" if ev.get("is_error") else "#22c55e"
                    last = last.replace(
                        "…</div>",
                        f' <span style="color:{color};">{mark}</span></div>',
                    )
                    rendered_lines[-1] = last
                    pending_tool = None
            elif t == "text_delta":
                stream_chunks.append(ev.get("text", ""))

        # 답변 토큰 stream 미리보기 — JSON 의 summary 필드를 추출해 보여줌.
        # 추출 실패하면 글자 수 카운터로 대체.
        if stream_chunks:
            full = "".join(stream_chunks)
            m = _SUMMARY_STREAM_RE.search(full)
            if m:
                # JSON escape 디코딩 (최소): \\n → \n, \\" → ", \\\\ → \\
                preview = (
                    m.group(1)
                    .replace('\\n', ' ')
                    .replace('\\"', '"')
                    .replace('\\\\', '\\')
                )
                if preview.strip():
                    safe = _html_mod.escape(preview[-400:])
                    rendered_lines.append(
                        f'<div style="color:#cbd5e1;font-size:13px;line-height:1.55;'
                        f'margin:8px 0 0;background:#0f172a;padding:10px 12px;'
                        f'border-radius:6px;border-left:3px solid #38bdf8;'
                        f'white-space:pre-wrap;">'
                        f'✍️ {safe}</div>'
                    )
            else:
                rendered_lines.append(
                    f'<div style="color:#94a3b8;font-size:12px;margin:6px 0;">'
                    f'✍️ 답변 작성 중… ({len(full):,}자 수신)</div>'
                )

        if not rendered_lines:
            rendered_lines.append(
                '<div style="color:#94a3b8;font-size:13px;">'
                '🔍 근거 검색 및 답변 생성 중…</div>'
            )
        return "<div>" + "".join(rendered_lines) + "</div>"


    with st.chat_message("assistant"):
        spinner_box = st.empty()
        cancel_box = st.empty()

        thread = threading.Thread(target=_worker, daemon=True)
        thread.start()

        spinner_box.markdown(_render_progress_html([]), unsafe_allow_html=True)
        if cancel_box.button("⛔ 취소", key=f"cancel_{len(st.session_state.messages)}"):
            cancel_event.set()

        # 워커 완료 또는 취소까지 폴링 — 매 사이클 진행상황 갱신
        last_event_count = 0
        while thread.is_alive():
            if cancel_event.is_set():
                break
            thread.join(timeout=0.4)
            with progress_lock:
                snapshot = list(progress_events)
            if len(snapshot) != last_event_count:
                spinner_box.markdown(
                    _render_progress_html(snapshot), unsafe_allow_html=True,
                )
                last_event_count = len(snapshot)

        spinner_box.empty()
        cancel_box.empty()

        if cancel_event.is_set() and not result_holder:
            st.warning("취소되었습니다. (이미 발송된 API 호출은 백그라운드에서 완료될 수 있음)")
            st.session_state.messages.pop()  # 사용자 메시지 롤백
            st.stop()

        if "rate_limit" in result_holder:
            st.error(
                f"⏳ {result_holder['rate_limit']}\n\n"
                "Claude Code 구독 사용량 한도에 도달했습니다. "
                "잠시 후 다시 시도하거나, 다른 모델을 `.env`의 `CLAUDE_MODEL`로 지정해 보세요."
            )
            st.session_state.messages.pop()
            st.stop()

        if "error" in result_holder:
            st.error(f"답변 생성 오류: {result_holder['error']}")
            st.session_state.messages.pop()
            st.stop()

        result, confidence, web_used, ctx_stats = result_holder["value"]

        # 일상 대화는 답변 카드 대신 가벼운 마크다운으로 렌더 (verdict/citations 없음).
        if isinstance(result, dict) and result.get("kind") == "chat":
            st.markdown(result.get("summary", ""))
        else:
            if web_used:
                st.caption("📋 법제처 공식 API(법령·판례·행정규칙) 결과가 보완 근거로 사용되었습니다.")
            render_answer_card(result, confidence, ctx_stats=ctx_stats)
        st.session_state.messages.append({
            "role":       "assistant",
            "result":     result,
            "confidence": confidence,
            "ctx_stats":  ctx_stats,
        })
        ended_at = datetime.now()
        _save_audit(
            question, result, confidence, use_mcp, web_used,
            started_at=started_at, ended_at=ended_at,
        )
        # UI 하단에 처리 시간 표시 (사용자 가시성)
        st.caption(
            f"⏱ {started_at.strftime('%H:%M:%S')} → {ended_at.strftime('%H:%M:%S')} "
            f"({(ended_at - started_at).total_seconds():.1f}s)"
        )
