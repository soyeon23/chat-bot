"""감사 로그 페이지."""
from __future__ import annotations

import io
import json
from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

st.set_page_config(page_title="감사 로그", page_icon="📋", layout="wide")

from ui.styles import GLOBAL_CSS
st.markdown(GLOBAL_CSS, unsafe_allow_html=True)

st.markdown("## 📋 감사 로그")
st.markdown("모든 질의 이력을 시간 역순으로 표시합니다.")
st.divider()

_AUDIT_LOG = Path("data/audit_log.jsonl")

if not _AUDIT_LOG.exists():
    st.info("질의 기록이 없습니다.")
    st.stop()

records = []
with open(_AUDIT_LOG, encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                pass

if not records:
    st.info("기록된 질의가 없습니다.")
    st.stop()

df = pd.DataFrame(records)

# 컬럼 정리
col_rename = {
    "timestamp":      "시각",
    "question":       "질문",
    "verdict":        "판단",
    "confidence":     "Confidence(%)",
    "citations_count": "근거 수",
    "follow_up":      "추가확인필요",
    "mcp_used":       "MCP사용",
}
df = df.rename(columns={k: v for k, v in col_rename.items() if k in df.columns})

# 시간 역순
if "시각" in df.columns:
    df = df.sort_values("시각", ascending=False).reset_index(drop=True)

# ── 필터 ──
col1, col2 = st.columns([3, 1])
with col1:
    search = st.text_input("🔍 질문 검색", placeholder="검색어...")
with col2:
    if "판단" in df.columns:
        verdict_filter = st.multiselect("판단 결과 필터", df["판단"].unique().tolist(), default=[])
    else:
        verdict_filter = []

filtered = df.copy()
if search and "질문" in filtered.columns:
    filtered = filtered[filtered["질문"].str.contains(search, case=False, na=False)]
if verdict_filter and "판단" in filtered.columns:
    filtered = filtered[filtered["판단"].isin(verdict_filter)]

# ── 배지 색 렌더링용 ──
_VERDICT_COLOR = {
    "가능": "#22c55e",
    "불가": "#ef4444",
    "조건부 가능": "#f59e0b",
    "판단불가": "#6b7280",
}

# ── 테이블 표시 ──
st.markdown(f"**{len(filtered):,}건** 표시 중")
st.dataframe(
    filtered,
    use_container_width=True,
    hide_index=True,
    column_config={
        "시각": st.column_config.TextColumn("시각", width=180),
        "질문": st.column_config.TextColumn("질문", width=380),
        "판단": st.column_config.TextColumn("판단", width=100),
        "Confidence(%)": st.column_config.NumberColumn("Confidence(%)", format="%.1f", width=120),
        "근거 수": st.column_config.NumberColumn("근거 수", width=80),
        "추가확인필요": st.column_config.CheckboxColumn("추가확인", width=90),
        "MCP사용": st.column_config.CheckboxColumn("MCP", width=70),
    },
)

st.divider()

# ── CSV 내보내기 ──
csv_bytes = filtered.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
st.download_button(
    label="📥 CSV 내보내기",
    data=csv_bytes,
    file_name="audit_log.csv",
    mime="text/csv",
)
