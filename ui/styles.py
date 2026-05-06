GLOBAL_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@300;400;500;700&display=swap');

html, body, [class*="css"] {
    font-family: 'Noto Sans KR', sans-serif !important;
    background-color: #fafafa !important;
}

/* 사이드바 */
[data-testid="stSidebar"] {
    background-color: #111111 !important;
    border-right: none !important;
}
[data-testid="stSidebar"] * {
    color: #e0e0e0 !important;
}
[data-testid="stSidebar"] .stButton > button {
    background-color: #1a1a1a;
    color: #e0e0e0 !important;
    border: 1px solid #333333;
    border-radius: 8px;
    font-size: 13px;
    font-weight: 400;
    width: 100%;
    padding: 8px 14px;
    transition: all 0.15s;
}
[data-testid="stSidebar"] .stButton > button:hover {
    background-color: #e60023;
    border-color: #e60023;
    color: #ffffff !important;
}

/* 메인 버튼 */
.stButton > button {
    border-radius: 24px !important;
    font-weight: 500 !important;
    font-size: 14px !important;
    border: 1px solid #efefef !important;
    background: #ffffff !important;
    color: #1a1a1a !important;
    padding: 8px 20px !important;
    transition: all 0.15s !important;
}
.stButton > button:hover {
    border-color: #e60023 !important;
    color: #e60023 !important;
    background: #fff5f5 !important;
}
button[kind="primary"] {
    background: #e60023 !important;
    color: #ffffff !important;
    border-color: #e60023 !important;
}
button[kind="primary"]:hover {
    background: #c0001e !important;
    border-color: #c0001e !important;
    color: #ffffff !important;
}

/* 답변 카드 */
.answer-card {
    background: #ffffff;
    border: 1px solid #efefef;
    border-radius: 16px;
    padding: 16px 20px;
    margin: 0 0 12px 0;
    box-shadow: 0 2px 8px rgba(0,0,0,0.06);
}

/* verdict 배지 */
.badge {
    display: inline-block;
    padding: 4px 12px;
    border-radius: 20px;
    font-size: 12px;
    font-weight: 500;
    color: white;
    letter-spacing: 0.02em;
}
.conf-score {
    font-size: 12px;
    color: #767676;
    margin-left: 10px;
}

/* 출처 배지 */
.source-badge {
    display: inline-block;
    background: #ffffff;
    border: 1px solid #efefef;
    color: #1a1a1a;
    border-radius: 12px;
    padding: 6px 12px;
    font-size: 12px;
    margin: 3px;
    line-height: 1.5;
}
.web-badge {
    display: inline-block;
    background: #fff5f5;
    border: 1px solid #ffd0d7;
    color: #e60023;
    border-radius: 12px;
    padding: 6px 12px;
    font-size: 12px;
    margin: 3px;
    line-height: 1.5;
}

/* 주의/경고 */
.caution-block {
    border-left: 3px solid #e60023;
    background: #fff5f5;
    padding: 10px 14px;
    border-radius: 0 8px 8px 0;
    margin: 10px 0;
    color: #c0001e;
    font-size: 13px;
    line-height: 1.7;
}
.risk-item {
    background: #fafafa;
    border-left: 2px solid #767676;
    padding: 7px 12px;
    border-radius: 0 6px 6px 0;
    margin: 4px 0;
    font-size: 13px;
    color: #1a1a1a;
    line-height: 1.6;
}

/* 원문 박스 */
.original-text-box {
    background: #f5f5f5;
    color: #1a1a1a;
    padding: 14px 16px;
    border-radius: 12px;
    font-family: 'Noto Sans KR', monospace;
    font-size: 13px;
    line-height: 1.8;
    margin: 8px 0;
    white-space: pre-wrap;
    border: 1px solid #efefef;
}

/* 퀵 프롬프트 */
div.quick-btn-wrap button,
div[data-testid="stHorizontalBlock"] .stButton > button.quick-btn {
    background: #ffffff !important;
    border: 1px solid #efefef !important;
    color: #1a1a1a !important;
    border-radius: 20px !important;
    font-size: 13px !important;
    padding: 6px 16px !important;
    transition: all 0.15s !important;
}
div.quick-btn-wrap button:hover,
div[data-testid="stHorizontalBlock"] .stButton > button.quick-btn:hover {
    border-color: #e60023 !important;
    color: #e60023 !important;
    background: #fff5f5 !important;
}

/* 구분선 */
hr { border-color: #efefef !important; }

/* 앱 헤더 */
.app-header { font-size: 22px; font-weight: 700; color: #1a1a1a; margin-bottom: 2px; }
.app-subheader { font-size: 13px; color: #767676; margin-bottom: 16px; }
</style>
"""
