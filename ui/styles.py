GLOBAL_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;700&display=swap');

html, body, [class*="css"] {
    font-family: 'Noto Sans KR', sans-serif !important;
}

/* 사이드바 */
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
    background-color: #0f172a;
    color: #e2e8f0 !important;
    border: 1px solid #334155;
    border-radius: 8px;
    font-size: 13px;
    font-weight: 500;
    width: 100%;
    padding: 8px 14px;
    transition: background 0.15s;
}
[data-testid="stSidebar"] .stButton > button:hover {
    background-color: #2563eb;
    border-color: #2563eb;
    color: #ffffff !important;
}
[data-testid="stSidebar"] .stSelectbox > div > div {
    background-color: #1e293b;
    color: #e2e8f0;
    border-color: #475569;
}

/* 앱 헤더 */
.app-header { font-size: 22px; font-weight: 700; margin-bottom: 2px; }
.app-subheader { font-size: 13px; color: #64748b; margin-bottom: 16px; }

/* 답변 카드 */
.answer-card {
    border: 1px solid rgba(148,163,184,0.2);
    border-radius: 14px;
    padding: 18px 22px;
    margin: 4px 0 12px 0;
    box-shadow: 0 1px 4px rgba(0,0,0,0.06);
}

/* verdict 배지 */
.badge {
    display: inline-block;
    padding: 4px 12px;
    border-radius: 6px;
    font-size: 12px;
    font-weight: 600;
    color: white;
    letter-spacing: 0.02em;
}
.conf-score {
    font-size: 12px;
    color: #64748b;
    margin-left: 10px;
}

/* 출처 배지 */
.source-badge {
    display: inline-block;
    border: 1px solid rgba(148,163,184,0.3);
    color: #1d4ed8;
    border-radius: 8px;
    padding: 5px 11px;
    font-size: 12px;
    margin: 3px;
    line-height: 1.5;
}
.web-badge {
    display: inline-block;
    border: 1px solid rgba(34,197,94,0.3);
    color: #15803d;
    border-radius: 8px;
    padding: 5px 11px;
    font-size: 12px;
    margin: 3px;
    line-height: 1.5;
}

/* 주의/경고 */
.caution-block {
    border-left: 3px solid #ef4444;
    background: rgba(239,68,68,0.06);
    padding: 10px 14px;
    border-radius: 0 8px 8px 0;
    margin: 10px 0;
    font-size: 13px;
    line-height: 1.7;
}
.risk-item {
    border-left: 2px solid #f97316;
    padding: 7px 12px;
    border-radius: 0 6px 6px 0;
    margin: 4px 0;
    font-size: 13px;
    line-height: 1.6;
    background: rgba(249,115,22,0.05);
}

/* 원문 박스 */
.original-text-box {
    background: rgba(148,163,184,0.08);
    border: 1px solid rgba(148,163,184,0.2);
    padding: 14px 16px;
    border-radius: 10px;
    font-family: 'Noto Sans KR', monospace;
    font-size: 13px;
    line-height: 1.8;
    margin: 8px 0;
    white-space: pre-wrap;
}

/* 퀵 프롬프트 */
div.quick-btn-wrap button {
    border: 1px solid rgba(148,163,184,0.3) !important;
    border-radius: 20px !important;
    font-size: 13px !important;
    padding: 6px 16px !important;
    transition: all 0.15s !important;
}
div.quick-btn-wrap button:hover {
    border-color: #2563eb !important;
    color: #2563eb !important;
}

/* 구분선 */
hr { border-color: rgba(148,163,184,0.2) !important; }
</style>
"""
