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


def render_answer_card(result: dict, confidence: float = 0.0) -> None:
    """AnswerPayload dict를 답변 카드로 렌더링한다."""
    verdict = result.get("verdict", "판단불가")
    label, color = _VERDICT_MAP.get(verdict, ("❓ 판단불가", "#6b7280"))
    summary = result.get("summary", "")
    citations: list[dict] = result.get("citations", [])
    risk_notes: list[str] = result.get("risk_notes", [])
    follow_up_needed: bool = result.get("follow_up_needed", False)
    follow_up_questions: list[str] = result.get("follow_up_questions", [])

    # ── 헤더: 배지 + Confidence ──
    st.markdown(
        f'<div class="answer-card">'
        f'<span class="badge" style="background-color:{color};">{label}</span>'
        f'<span class="conf-score">Confidence {confidence:.1f}%</span>'
        f'</div>',
        unsafe_allow_html=True,
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
                page = cit.get("page", 0)
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
                        f'<span style="color:#3b82f6;">{art}</span>'
                        f'{"  |  p." + str(page) if page else ""}</div>',
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
