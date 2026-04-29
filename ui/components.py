"""재사용 UI 컴포넌트."""
from __future__ import annotations

import streamlit as st

QUICK_PROMPTS = [
    "학생인건비 지급 기준",
    "간접비 비율 확인",
    "회의비 증빙서류 목록",
]

_VERDICT_MAP = {
    "가능":       ("✅ 가능",        "#22c55e"),
    "불가":       ("❌ 불가",        "#ef4444"),
    "조건부 가능": ("⚠️ 조건부 가능", "#f59e0b"),
    "판단불가":   ("❓ 판단불가",    "#6b7280"),
}

# 부스트 신호 → 사용자 표시 라벨
_SIGNAL_LABELS: dict[str, str] = {
    "page":       "페이지 직접 매칭",
    "structural": "조문 직접 매칭",
    "phrase":     "키워드 결합 매칭",
}

# Confidence 색상 구간
def _conf_color(pct: float) -> str:
    if pct >= 80:
        return "#22c55e"   # 녹색
    if pct >= 60:
        return "#f59e0b"   # 노랑
    return "#6b7280"       # 회색


def _render_confidence_header(
    label: str,
    color: str,
    confidence: float,
    signals: list[str],
    *,
    confidence_is_na: bool,
) -> None:
    """verdict 배지 + confidence 점수 + 부스트 신호 라벨을 한 줄에 렌더링."""
    # Confidence 텍스트
    if confidence_is_na:
        conf_text = "신뢰도 N/A"
        conf_color = "#6b7280"
    else:
        conf_text = f"Confidence {confidence:.1f}%"
        conf_color = _conf_color(confidence)

    # 신호 라벨 HTML
    signal_html = ""
    for sig in signals:
        sig_label = _SIGNAL_LABELS.get(sig, sig)
        signal_html += (
            f'<span class="badge" style="background:#374151;color:#d1d5db;'
            f'font-size:11px;margin-left:6px;">{sig_label}</span>'
        )

    st.markdown(
        f'<div class="answer-card">'
        f'<span class="badge" style="background-color:{color};">{label}</span>'
        f'<span class="conf-score" style="color:{conf_color};">{conf_text}</span>'
        f'{signal_html}'
        f'</div>',
        unsafe_allow_html=True,
    )


def render_answer_card(
    result: dict,
    confidence: float = 0.0,
    *,
    ctx_stats: dict | None = None,
) -> None:
    """AnswerPayload dict를 답변 카드로 렌더링한다.

    Args:
        result: AnswerPayload dict
        confidence: 벡터 유사도 평균 (0~95). ctx_stats["signals"] 에 부스트 전용
                    신호만 있고 confidence==0.0 이면 N/A로 표시.
        ctx_stats: run_pipeline 에서 산출한 컨텍스트 사용량 통계
                   {"n_chunks", "n_chars", "n_tokens", "limit_tokens", "signals"}
    """
    verdict = result.get("verdict", "판단불가")
    label, color = _VERDICT_MAP.get(verdict, ("❓ 판단불가", "#6b7280"))
    summary = result.get("summary", "")
    citations: list[dict] = result.get("citations", [])
    risk_notes: list[str] = result.get("risk_notes", [])
    follow_up_needed: bool = result.get("follow_up_needed", False)
    follow_up_questions: list[str] = result.get("follow_up_questions", [])

    signals: list[str] = (ctx_stats or {}).get("signals", [])
    # confidence == 0.0 이고 signals 에 부스트 전용 신호가 있으면 N/A 표시
    confidence_is_na = (
        confidence == 0.0
        and bool(signals)
        and all(s in ("page", "structural") for s in signals)
    )

    # ── 헤더: 배지 + Confidence + 신호 라벨 ──
    _render_confidence_header(label, color, confidence, signals,
                              confidence_is_na=confidence_is_na)

    # ── 컨텍스트 사용량 캡션 ──
    if ctx_stats and ctx_stats.get("n_chunks", 0) > 0:
        n_chunks = ctx_stats["n_chunks"]
        n_chars = ctx_stats["n_chars"]
        n_tokens = ctx_stats["n_tokens"]
        limit = ctx_stats["limit_tokens"]
        pct = round(n_tokens / limit * 100, 1) if limit > 0 else 0.0
        model_label = "Sonnet 4.6"
        limit_k = limit // 1000
        st.caption(
            f"컨텍스트: {n_chunks}청크 / {n_chars:,}자 / "
            f"~{n_tokens // 1000}K 토큰 "
            f"({model_label}: {limit_k}K 한도의 {pct}%)"
        )

    # ── 요약 ──
    st.markdown(f"**{summary}**")

    # ── 근거 출처 ──
    if citations:
        st.markdown("---")
        st.markdown("**📚 GROUNDS & SOURCES**")
        cols = st.columns(min(len(citations), 3))
        for col, cit in zip(cols, citations):
            with col:
                doc = cit.get("document_name", "")
                art = cit.get("article_no", "")
                is_official_api = art.startswith("소관:")
                if is_official_api:
                    st.markdown(
                        f'<div class="web-badge">📋 <b>{doc}</b><br>'
                        f'<span style="color:#15803d;font-size:11px;">{art}</span></div>',
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(
                        f'<div class="source-badge">📄 <b>{doc}</b><br>'
                        f'<span style="color:#3b82f6;">{art}</span></div>',
                        unsafe_allow_html=True,
                    )

    # ── 주의사항 ──
    if risk_notes:
        st.markdown("---")
        st.markdown("**⚠️ 주의사항**")
        for note in risk_notes:
            st.markdown(f'<div class="risk-item">{note}</div>', unsafe_allow_html=True)

    # ── Critical Caution ──
    if follow_up_needed:
        items_html = "".join(f"• {q}<br>" for q in follow_up_questions)
        st.markdown(
            f'<div class="caution-block">'
            f'<b>❗ CRITICAL CAUTION</b><br>'
            f'전담기관 또는 담당 PM의 최종 확인이 필요합니다.<br>'
            f'{items_html}'
            f'</div>',
            unsafe_allow_html=True,
        )

    # ── 원문 보기 토글 ──
    if citations:
        with st.expander("📖 VIEW ORIGINAL TEXT"):
            for cit in citations:
                doc = cit.get("document_name", "")
                art = cit.get("article_no", "")
                quote = cit.get("quote", "")
                st.markdown(
                    f'<div class="original-text-box">'
                    f'<b>[{doc}  {art}]</b>\n\n{quote}'
                    f'</div>',
                    unsafe_allow_html=True,
                )


def render_quick_prompts() -> str | None:
    """Quick Prompt 칩을 렌더링하고 클릭된 텍스트를 반환한다."""
    cols = st.columns(len(QUICK_PROMPTS))
    for col, prompt in zip(cols, QUICK_PROMPTS):
        if col.button(prompt, key=f"qp_{prompt}"):
            return prompt
    return None
