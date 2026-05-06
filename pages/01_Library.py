"""문서 라이브러리 페이지."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

st.set_page_config(page_title="문서 라이브러리", layout="wide")

from ui.styles import GLOBAL_CSS
st.markdown(GLOBAL_CSS, unsafe_allow_html=True)

st.markdown("## 문서 라이브러리")
st.markdown("인덱싱된 문서 목록입니다. `data/metadata/` 폴더의 CSV를 기반으로 표시합니다.")
st.divider()

# ── 메타데이터 CSV 로드 ──
meta_dir = Path("data/metadata")
if not meta_dir.exists() or not list(meta_dir.glob("*.csv")):
    st.info("인덱싱된 문서가 없습니다. 메인 화면에서 PDF를 업로드하세요.")
    st.stop()

dfs = []
for csv_path in sorted(meta_dir.glob("*.csv")):
    try:
        dfs.append(pd.read_csv(csv_path, encoding="utf-8-sig"))
    except Exception:
        pass

if not dfs:
    st.warning("메타데이터 파일을 읽을 수 없습니다.")
    st.stop()

df_all = pd.concat(dfs, ignore_index=True)

# 문서별 집계
if "doc_name" not in df_all.columns:
    st.error("메타데이터 형식 오류: doc_name 컬럼이 없습니다.")
    st.stop()

summary = (
    df_all.groupby(["doc_name", "doc_type"])
    .agg(
        청크수=("chunk_id", "count"),
        페이지수=("page", "max"),
        시행일=("effective_date", "first"),
        개정일=("revised_date", "first"),
        현행여부=("is_current", "first"),
        파일명=("source_file", "first"),
    )
    .reset_index()
    .sort_values("doc_name")
)

# ── 필터 ──
col1, col2 = st.columns([2, 1])
with col1:
    search = st.text_input("문서명 검색", placeholder="검색어를 입력하세요...")
with col2:
    type_filter = st.multiselect(
        "문서 유형 필터",
        options=sorted(summary["doc_type"].unique()),
        default=[],
    )

filtered = summary.copy()
if search:
    filtered = filtered[filtered["doc_name"].str.contains(search, case=False, na=False)]
if type_filter:
    filtered = filtered[filtered["doc_type"].isin(type_filter)]

# ── 테이블 렌더링 ──
st.markdown(f"**총 {len(filtered)}개 문서** (전체 청크: {df_all.shape[0]:,}개)")

for _, row in filtered.iterrows():
    is_current = row.get("현행여부", True)
    badge_html = (
        '<span style="background:#22c55e;color:white;border-radius:4px;'
        'padding:2px 8px;font-size:11px;font-weight:700;">현행</span>'
        if is_current else
        '<span style="background:#94a3b8;color:white;border-radius:4px;'
        'padding:2px 8px;font-size:11px;font-weight:700;">구버전</span>'
    )
    type_color = {
        "법률": "#2563eb", "시행령": "#7c3aed", "시행규칙": "#0891b2",
        "운영요령": "#059669", "공고문": "#d97706", "FAQ": "#db2777",
        "가이드": "#64748b",
    }.get(str(row.get("doc_type", "")), "#64748b")

    with st.container():
        c1, c2, c3, c4, c5 = st.columns([4, 1, 1, 1, 1])
        with c1:
            st.markdown(
                f'**{row["doc_name"]}** &nbsp; {badge_html}',
                unsafe_allow_html=True,
            )
            st.markdown(
                f'<span style="color:{type_color};font-size:12px;font-weight:600;">'
                f'{row.get("doc_type","")}</span>'
                f'<span style="color:#94a3b8;font-size:12px;"> | {row.get("파일명","")}</span>',
                unsafe_allow_html=True,
            )
        with c2:
            st.metric("청크 수", f'{int(row.get("청크수", 0)):,}')
        with c3:
            st.metric("최대 페이지", row.get("페이지수", "-"))
        with c4:
            st.metric("시행일", str(row.get("시행일", "-"))[:10] or "-")
        with c5:
            st.metric("개정일", str(row.get("개정일", "-"))[:10] or "-")
        st.divider()
