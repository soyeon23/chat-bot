"""이용 통계 페이지."""
from __future__ import annotations

import json
from pathlib import Path
from collections import Counter

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

st.set_page_config(page_title="이용 통계", page_icon="📊", layout="wide")

from ui.styles import GLOBAL_CSS
st.markdown(GLOBAL_CSS, unsafe_allow_html=True)

st.markdown("## 📊 이용 통계")
st.divider()

_AUDIT_LOG = Path("data/audit_log.jsonl")

if not _AUDIT_LOG.exists():
    st.info("아직 질의 기록이 없습니다. 메인 화면에서 질문을 입력하면 통계가 쌓입니다.")
    st.stop()

# ── 로그 로드 ──
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
df["timestamp"] = pd.to_datetime(df["timestamp"])
df["date"] = df["timestamp"].dt.date

# ── 요약 KPI ──
col1, col2, col3, col4 = st.columns(4)
col1.metric("총 질문 수", f"{len(df):,}")
col2.metric("오늘 질문 수", str((df["date"] == pd.Timestamp.now().date()).sum()))
col3.metric("MCP 활용률", f'{df["mcp_used"].mean()*100:.0f}%' if "mcp_used" in df else "-")
col4.metric("추가 확인 필요율", f'{df["follow_up"].mean()*100:.0f}%' if "follow_up" in df else "-")

st.divider()

# ── 일별 질문 수 ──
col_a, col_b = st.columns(2)

with col_a:
    st.markdown("#### 일별 질문 수")
    daily = df.groupby("date").size().reset_index(name="count")
    daily["date"] = daily["date"].astype(str)
    st.line_chart(daily.set_index("date")["count"])

with col_b:
    st.markdown("#### 판단 결과 분포")
    if "verdict" in df.columns:
        verdict_counts = df["verdict"].value_counts().reset_index()
        verdict_counts.columns = ["verdict", "count"]
        st.bar_chart(verdict_counts.set_index("verdict"))
    else:
        st.info("데이터 없음")

st.divider()

# ── Confidence Score 분포 ──
st.markdown("#### Confidence Score 분포")
if "confidence" in df.columns:
    bins = [0, 50, 60, 70, 80, 90, 100]
    labels = ["~50%", "50-60%", "60-70%", "70-80%", "80-90%", "90-100%"]
    df["conf_bin"] = pd.cut(df["confidence"], bins=bins, labels=labels, right=True)
    conf_dist = df["conf_bin"].value_counts().sort_index().reset_index()
    conf_dist.columns = ["구간", "count"]
    st.bar_chart(conf_dist.set_index("구간"))
