GLOBAL_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700&display=swap');

html, body, [class*="css"] {
    font-family: 'Noto Sans KR', sans-serif !important;
}

/* ── 사이드바 ── */
[data-testid="stSidebar"] {
    background-color: #1e293b !important;
}
[data-testid="stSidebar"] label,
[data-testid="stSidebar"] p,
[data-testid="stSidebar"] div[data-testid="stWidgetLabel"] p,
[data-testid="stSidebar"] .stMarkdown p,
[data-testid="stSidebar"] .stMarkdown div,
[data-testid="stSidebar"] span {
    color: #e2e8f0 !important;
}
[data-testid="stSidebar"] .stButton > button {
    background-color: #2563eb;
    color: white !important;
    border: none;
    border-radius: 6px;
    font-weight: 600;
    width: 100%;
    transition: background 0.2s;
}
[data-testid="stSidebar"] .stButton > button:hover {
    background-color: #1d4ed8;
}
[data-testid="stSidebar"] .stSelectbox > div > div {
    background-color: #334155;
    color: #e2e8f0;
    border-color: #475569;
}

/* ── 앱 헤더 ── */
.app-header {
    font-size: 22px;
    font-weight: 700;
    color: #111827;
    margin-bottom: 2px;
}
.app-subheader {
    font-size: 13px;
    color: #6b7280;
    margin-bottom: 16px;
}

/* ── 답변 카드 ── */
.answer-card {
    background: #f9fafb;
    border: 1px solid #e5e7eb;
    border-radius: 10px;
    padding: 16px 20px;
    margin: 4px 0 10px 0;
    box-shadow: 0 1px 3px rgba(0,0,0,0.06);
}

/* ── verdict 배지 ── */
.badge {
    display: inline-block;
    padding: 3px 10px;
    border-radius: 4px;
    font-size: 12px;
    font-weight: 600;
    color: white;
    letter-spacing: 0.02em;
    vertical-align: middle;
}
.conf-score {
    font-size: 12px;
    color: #6b7280;
    margin-left: 8px;
    vertical-align: middle;
}

/* ── 출처 배지 ── */
.source-badge {
    display: inline-block;
    background: transparent;
    border: 1px solid #d1d5db;
    color: #2563eb;
    border-radius: 6px;
    padding: 5px 10px;
    font-size: 12px;
    margin: 3px;
    line-height: 1.6;
}

/* ── 웹 검색 출처 배지 ── */
.web-badge {
    display: inline-block;
    background: transparent;
    border: 1px solid #d1d5db;
    color: #059669;
    border-radius: 6px;
    padding: 5px 10px;
    font-size: 12px;
    margin: 3px;
    line-height: 1.6;
}

/* ── Critical Caution ── */
.caution-block {
    border-left: 3px solid #ef4444;
    background: #fef2f2;
    padding: 10px 14px;
    border-radius: 0 6px 6px 0;
    margin: 10px 0;
    color: #991b1b;
    font-size: 13px;
    line-height: 1.7;
}

/* ── 주의사항 ── */
.risk-item {
    background: #fff7ed;
    border-left: 3px solid #f97316;
    padding: 6px 10px;
    border-radius: 0 4px 4px 0;
    margin: 3px 0;
    font-size: 13px;
    color: #7c2d12;
    line-height: 1.6;
}

/* ── 원문 텍스트 ── */
.original-text-box {
    background: #f3f4f6;
    color: #374151;
    padding: 12px 16px;
    border-radius: 6px;
    font-family: 'Noto Sans KR', 'D2Coding', monospace;
    font-size: 13px;
    line-height: 1.7;
    margin: 8px 0;
    white-space: pre-wrap;
    border: 1px solid #e5e7eb;
}

/* ── Quick Prompt 칩 ── */
div[data-testid="stHorizontalBlock"] .stButton > button.quick-btn,
div.quick-btn-wrap button {
    background: #eff6ff;
    border: 1px solid #bfdbfe;
    color: #2563eb;
    border-radius: 20px;
    font-size: 13px;
    padding: 4px 14px;
    transition: all 0.2s;
}
div[data-testid="stHorizontalBlock"] .stButton > button.quick-btn:hover,
div.quick-btn-wrap button:hover {
    background: #2563eb;
    color: white;
}

/* ── 구분선 ── */
hr { border-color: #e5e7eb !important; }
</style>
"""
