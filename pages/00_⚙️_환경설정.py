"""환경 검사 + 프로젝트 설정 위저드.

최초 실행 시 자동 라우팅되며, 사이드바 메뉴로 언제든 재진입 가능.
모든 필수 항목이 ✅이면 "시작하기" 버튼 → 챗봇으로 진입.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

load_dotenv()

st.set_page_config(
    page_title="환경 설정 — 연구행정 AI",
    page_icon="⚙️",
    layout="centered",
    initial_sidebar_state="collapsed",
)

from pipeline.config_store import ProjectConfig, load_config, save_config, update_config
from pipeline.setup_check import (
    CheckResult,
    all_blocking_ok,
    run_all_checks,
    system_summary,
)
from ui.styles import GLOBAL_CSS

st.markdown(GLOBAL_CSS, unsafe_allow_html=True)

# ── 위저드 전용 CSS ──────────────────────────────────────────
st.markdown(
    """
    <style>
    /* 메인 블록 폭 제한 + 가운데 정렬 (centered layout 위에서 한 번 더 안정화) */
    .block-container {
        max-width: 720px !important;
        padding-top: 3rem !important;
        padding-bottom: 4rem !important;
    }

    .wiz-header {
        text-align: center;
        margin-bottom: 32px;
    }
    .wiz-title {
        font-size: 30px;
        font-weight: 700;
        color: #e2e8f0;
        letter-spacing: -0.5px;
        margin-bottom: 6px;
    }
    .wiz-subtitle {
        color: #94a3b8;
        font-size: 13px;
    }
    .wiz-meta {
        text-align: center;
        color: #475569;
        font-size: 11px;
        margin-bottom: 28px;
        font-family: 'SF Mono', Menlo, monospace;
    }

    .check-card {
        display: flex;
        align-items: center;
        justify-content: space-between;
        padding: 14px 18px;
        margin: 0 0 8px 0;
        border: 1px solid rgba(148, 163, 184, 0.15);
        border-radius: 10px;
        background: rgba(30, 41, 59, 0.35);
    }
    .check-name {
        font-size: 14px;
        font-weight: 600;
        color: #e2e8f0;
    }
    .check-right {
        display: flex;
        align-items: center;
        gap: 12px;
    }
    .check-detail {
        font-family: 'SF Mono', Menlo, monospace;
        font-size: 12px;
        color: #94a3b8;
    }
    .status-dot {
        display: inline-block;
        width: 9px;
        height: 9px;
        border-radius: 50%;
        flex-shrink: 0;
    }
    .dot-ok    { background: #22c55e; box-shadow: 0 0 6px rgba(34,197,94,0.5); }
    .dot-warn  { background: #f59e0b; box-shadow: 0 0 6px rgba(245,158,11,0.5); }
    .dot-miss  { background: #ef4444; box-shadow: 0 0 6px rgba(239,68,68,0.5); }

    .fix-row {
        margin: -4px 0 12px 18px;
        display: flex;
        align-items: center;
        gap: 10px;
    }
    .fix-cmd {
        font-family: 'SF Mono', Menlo, monospace;
        font-size: 11px;
        color: #fbbf24;
        padding: 4px 8px;
        background: rgba(251, 191, 36, 0.08);
        border-left: 2px solid #f59e0b;
        border-radius: 4px;
        flex: 1;
    }

    .section-title {
        font-size: 13px;
        font-weight: 700;
        color: #e2e8f0;
        text-transform: uppercase;
        letter-spacing: 0.06em;
        margin: 36px 0 12px;
    }
    .section-caption {
        color: #94a3b8;
        font-size: 12px;
        margin-bottom: 12px;
    }

    /* 진입 버튼 */
    div[data-testid="stButton"] button[kind="primary"] {
        font-size: 15px;
        padding: 14px 0;
        font-weight: 700;
        border-radius: 10px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# ── 데이터 로드 ─────────────────────────────────────────────
cfg = load_config()
is_first_run = not cfg.onboarding_completed
checks: list[CheckResult] = run_all_checks(include_optional=True)
sys_info = system_summary()

# 최초 실행 시 사이드바 완전 숨김 — 페이지 네비게이션·열기 토글까지 차단해
# 사용자가 환경 검사 → "시작하기" 외 다른 페이지로 새지 않게.
# 온보딩 완료 후엔 환경설정 재방문 시 사이드바 정상 노출.
if is_first_run:
    st.markdown(
        """
        <style>
        [data-testid="stSidebar"] { display: none !important; }
        [data-testid="stSidebarCollapsedControl"] { display: none !important; }
        [data-testid="collapsedControl"] { display: none !important; }
        button[kind="header"] { display: none !important; }
        </style>
        """,
        unsafe_allow_html=True,
    )

# ── 헤더 ────────────────────────────────────────────────────
st.markdown(
    f"""
    <div class="wiz-header">
        <div class="wiz-title">{'환경 검사' if is_first_run else '환경 설정'}</div>
        <div class="wiz-subtitle">연구행정 AI 챗봇 실행에 필요한 항목을 확인합니다.</div>
    </div>
    <div class="wiz-meta">{sys_info["OS"]} · Python {sys_info["Python"]} · venv {sys_info["venv"]}</div>
    """,
    unsafe_allow_html=True,
)

# ── 체크 카드 ────────────────────────────────────────────────
DOT = {"ok": "dot-ok", "warn": "dot-warn", "missing": "dot-miss"}

for r in checks:
    st.markdown(
        f"""
        <div class="check-card">
            <span class="check-name">{r.name}</span>
            <span class="check-right">
                <span class="check-detail">{r.detail}</span>
                <span class="status-dot {DOT[r.status]}"></span>
            </span>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # 필요 시 수정 버튼/명령 한 줄
    if r.status != "ok":
        if r.fix_fn is not None:
            if st.button(
                f"⚡ {r.fix_label or '자동 수정'} — {r.name}",
                key=f"fix_{r.name}",
                use_container_width=True,
            ):
                with st.spinner(f"{r.name} 수정 중..."):
                    ok, msg = r.fix_fn()
                if ok:
                    st.success(msg)
                else:
                    st.error(msg)
                st.rerun()
        elif r.fix_hint:
            st.markdown(
                f'<div class="fix-row">'
                f'<span class="fix-cmd">$ {r.fix_hint}</span>'
                "</div>",
                unsafe_allow_html=True,
            )

# ── 일괄 자동 업데이트 ──────────────────────────────────────
auto_fixable = [r for r in checks if r.status != "ok" and r.fix_fn is not None]
ready = all_blocking_ok(checks)

if auto_fixable:
    st.markdown(
        '<div style="margin: 20px 0 8px;color:#fbbf24;font-size:12px;">'
        f'자동 수정 가능한 항목 {len(auto_fixable)}개'
        "</div>",
        unsafe_allow_html=True,
    )
    if st.button(
        f"⚡ 자동 업데이트 ({len(auto_fixable)}개 한꺼번에)",
        use_container_width=True,
        type="primary",
    ):
        progress = st.progress(0, "시작...")
        for i, r in enumerate(auto_fixable, 1):
            progress.progress(i / len(auto_fixable), f"{r.name} 수정 중...")
            ok, msg = r.fix_fn()
            if ok:
                st.success(f"✅ {r.name}: {msg}")
            else:
                st.error(f"❌ {r.name}: {msg}")
        progress.empty()
        st.rerun()

# ── 프로젝트 경로 ───────────────────────────────────────────
st.markdown('<div class="section-title">📂 프로젝트 경로</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="section-caption">PDF·HWP 파일이 있는 폴더. 저장 시 변경사항이 자동으로 인덱싱됩니다.</div>',
    unsafe_allow_html=True,
)


def _pick_folder_native(prompt: str = "폴더 선택") -> str | None:
    """플랫폼별 네이티브 폴더 선택 다이얼로그. 취소·실패 시 None.

    - macOS: osascript (AppleScript) 를 통해 Finder 다이얼로그 호출
    - Windows / Linux: tkinter filedialog 폴백 (Python 표준 라이브러리)
      tkinter 가 설치되지 않았거나 헤드리스 환경이면 None 반환
    """
    import platform as _pf
    system = _pf.system()

    if system == "Darwin":
        # macOS: osascript
        try:
            result = subprocess.run(
                ["osascript", "-e", f'POSIX path of (choose folder with prompt "{prompt}")'],
                capture_output=True,
                text=True,
                timeout=300,
            )
            if result.returncode != 0:
                return None
            path = result.stdout.strip().rstrip("/")
            return path or None
        except Exception:
            return None
    else:
        # Windows / Linux: tkinter filedialog 폴백
        try:
            import tkinter as tk
            from tkinter import filedialog

            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            path = filedialog.askdirectory(title=prompt)
            root.destroy()
            return path or None
        except Exception:
            # tkinter 없거나 헤드리스 환경 (예: Docker, WSL headless)
            return None


def _path_status(p: str, ext: str = "pdf") -> str:
    if not p:
        return ""
    pp = Path(p).expanduser()
    if not pp.exists():
        return "❌ 존재하지 않음"
    if not pp.is_dir():
        return "❌ 폴더가 아님"
    if ext == "pdf":
        files = list(pp.rglob("*.pdf"))
    else:
        files = list(pp.rglob("*.hwp")) + list(pp.rglob("*.hwpx"))
    if not files:
        return f"⚠️ {ext.upper()} 파일 없음"
    sample = ", ".join(f.name for f in files[:3])
    if len(files) > 3:
        sample += f", … 외 {len(files) - 3}개"
    return f"✅ {len(files):,}개 발견 — {sample}"


def _quick_picks() -> dict[str, str]:
    home = Path.home()
    return {
        "프로젝트 루트": ".",
        "Desktop": str(home / "Desktop"),
        "Documents": str(home / "Documents"),
        "Downloads": str(home / "Downloads"),
    }


# 세션 스테이트 초기화 (외부 변경 시 동기화)
if "cfg_pdf_dir" not in st.session_state:
    st.session_state.cfg_pdf_dir = cfg.pdf_dir
if "cfg_hwp_dir" not in st.session_state:
    st.session_state.cfg_hwp_dir = cfg.hwp_dir

# PDF 폴더 — 입력 + 폴더 선택 + 미리보기
st.markdown(
    '<div style="font-size:13px;font-weight:600;color:#cbd5e1;margin-bottom:6px;">PDF 폴더</div>',
    unsafe_allow_html=True,
)
pdf_col1, pdf_col2 = st.columns([5, 1])
with pdf_col1:
    st.text_input(
        "PDF 폴더",
        key="cfg_pdf_dir",
        label_visibility="collapsed",
    )
with pdf_col2:
    if st.button("📁 선택…", key="pick_pdf", use_container_width=True):
        picked = _pick_folder_native("PDF 폴더 선택")
        if picked:
            st.session_state.cfg_pdf_dir = picked
            st.rerun()

# 빠른 위치 칩
qp = _quick_picks()
chip_cols = st.columns(len(qp))
for (label, path), col in zip(qp.items(), chip_cols):
    with col:
        if st.button(label, key=f"qp_pdf_{label}", use_container_width=True):
            st.session_state.cfg_pdf_dir = path
            st.rerun()
st.caption(_path_status(st.session_state.cfg_pdf_dir, "pdf"))

st.markdown('<div style="height:14px;"></div>', unsafe_allow_html=True)

# HWP 폴더
st.markdown(
    '<div style="font-size:13px;font-weight:600;color:#cbd5e1;margin-bottom:6px;">한글(HWP) 폴더 (옵션)</div>',
    unsafe_allow_html=True,
)
hwp_col1, hwp_col2 = st.columns([5, 1])
with hwp_col1:
    st.text_input(
        "HWP 폴더",
        key="cfg_hwp_dir",
        placeholder="HWP MCP 활성화 시 사용",
        label_visibility="collapsed",
    )
with hwp_col2:
    if st.button("📁 선택…", key="pick_hwp", use_container_width=True):
        picked = _pick_folder_native("HWP 폴더 선택")
        if picked:
            st.session_state.cfg_hwp_dir = picked
            st.rerun()

if st.session_state.cfg_hwp_dir:
    st.caption(_path_status(st.session_state.cfg_hwp_dir, "hwp"))

new_pdf = st.session_state.cfg_pdf_dir
new_hwp = st.session_state.cfg_hwp_dir

# ── 인덱스 동기화 (Phase G4) ────────────────────────────────
st.markdown('<div class="section-title">📂 인덱스 동기화</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="section-caption">'
    '폴더의 PDF·HWP 파일을 스캔해 변경된 파일만 자동 재인덱싱합니다 (Git처럼 동작). '
    '청킹·임베딩 코드가 업데이트되면 영향받는 파일이 자동으로 stale 처리됩니다.'
    '</div>',
    unsafe_allow_html=True,
)


def _sync_roots_for_ui() -> list[Path]:
    """현재 설정에서 sync 루트 결정. config_store + 세션 상태."""
    roots: list[Path] = [Path(".").resolve()]
    if cfg.hwp_dir:
        extra = Path(cfg.hwp_dir).expanduser()
        if extra.exists() and extra.resolve() != roots[0]:
            roots.append(extra)
    return roots


# 자동 동기화 토글
new_auto_sync = st.checkbox(
    "앱 시작 시 자동 동기화",
    value=cfg.auto_sync_on_start,
    key="cfg_auto_sync",
    help=(
        "ON 시 챗봇 페이지 진입 직후 변경된 파일만 한 번 자동 재인덱싱합니다. "
        "변경 없으면 0초로 끝나므로 부담은 거의 없습니다."
    ),
)

# scan 미리보기 + apply 버튼
sync_col1, sync_col2 = st.columns([1, 1])

# scan: 변경 사항 확인 (메타파일 없으면 init)
with sync_col1:
    if st.button("🔍 변경 확인", use_container_width=True, key="sync_scan"):
        try:
            from pipeline.sync import (
                METADATA_PATH,
                init_metadata_from_qdrant,
                scan_changes,
            )

            roots = _sync_roots_for_ui()
            if not METADATA_PATH.exists():
                with st.spinner("초기 메타 생성 중 (Qdrant 스캔)..."):
                    init_metadata_from_qdrant(roots=roots)
            with st.spinner("폴더 스캔 중..."):
                changes = scan_changes(roots=roots)
            st.session_state["sync_last_scan"] = {
                "added": [str(p) for p in changes["added"]],
                "modified": [str(p) for p in changes["modified"]],
                "deleted": list(changes["deleted"]),
                "stale_code": [str(p) for p in changes["stale_code"]],
                "unchanged_count": len(changes["unchanged"]),
                "skipped_count": len(changes["skipped"]),
            }
        except Exception as e:
            st.error(f"스캔 실패: {type(e).__name__}: {e}")

# apply: 미리보기에서 변경분만 처리
last_scan = st.session_state.get("sync_last_scan")
have_work = bool(
    last_scan and (
        last_scan.get("added")
        or last_scan.get("modified")
        or last_scan.get("deleted")
        or last_scan.get("stale_code")
    )
)
with sync_col2:
    apply_clicked = st.button(
        "📂 동기화 실행",
        use_container_width=True,
        type="primary",
        disabled=not have_work,
        key="sync_apply",
    )

# 미리보기 결과 표시
if last_scan:
    n_add = len(last_scan["added"])
    n_mod = len(last_scan["modified"])
    n_del = len(last_scan["deleted"])
    n_stale = len(last_scan["stale_code"])
    n_unc = last_scan["unchanged_count"]
    n_skip = last_scan["skipped_count"]
    if n_add or n_mod or n_del or n_stale:
        st.info(
            f"신규 {n_add} · 변경 {n_mod} · 삭제 {n_del} · 코드업데이트 stale {n_stale} "
            f"· 변경없음 {n_unc} · 미지원 {n_skip}"
        )
        with st.expander("재인덱싱 대상 목록"):
            for kind, key in [("신규", "added"), ("변경", "modified"), ("stale", "stale_code")]:
                items = last_scan.get(key, [])
                if items:
                    st.markdown(f"**{kind}** ({len(items)}개)")
                    for p in items:
                        st.markdown(f"  - `{Path(p).name}`")
            if last_scan["deleted"]:
                st.markdown(f"**삭제** ({len(last_scan['deleted'])}개)")
                for p in last_scan["deleted"]:
                    st.markdown(f"  - `{Path(p).name}`")
    else:
        st.success(f"변경 없음 — 모든 파일 최신 상태 ({n_unc} unchanged, {n_skip} skipped)")

# 실행
if apply_clicked and last_scan:
    from pipeline.sync import apply_changes, scan_changes as _rescan

    # 최신 스냅샷으로 다시 스캔 (사용자가 미리보기 후 파일을 더 만졌을 수도)
    roots = _sync_roots_for_ui()
    with st.spinner("재스캔 중..."):
        changes = _rescan(roots=roots)

    total = (
        len(changes["added"]) + len(changes["modified"])
        + len(changes["deleted"]) + len(changes["stale_code"])
    )
    if total == 0:
        st.success("이미 동기화 상태입니다.")
        st.session_state.pop("sync_last_scan", None)
    else:
        progress_bar = st.progress(0, "시작...")
        status_text = st.empty()

        def _ui_progress(i: int, total_count: int, msg: str) -> None:
            progress_bar.progress(min(i / max(total_count, 1), 1.0), msg)
            status_text.caption(f"[{i}/{total_count}] {msg}")

        try:
            with st.spinner(f"동기화 진행 중 ({total}개 파일)..."):
                result = apply_changes(changes, progress_callback=_ui_progress)
            progress_bar.progress(1.0, "완료")
        except Exception as e:
            st.error(f"동기화 실패: {type(e).__name__}: {e}")
            result = None

        if result is not None:
            err_count = len(result["errors"])
            elapsed = result["elapsed_sec"]
            summary = (
                f"인덱싱 {result['indexed']} · 삭제 {result['deleted']} · "
                f"건너뜀 {result['skipped']} · 오류 {err_count} · "
                f"소요 {elapsed:.1f}초"
            )
            if err_count == 0:
                st.success(f"✅ 동기화 완료 — {summary}")
            else:
                st.warning(f"⚠️ 일부 오류와 함께 완료 — {summary}")
                with st.expander("오류 상세"):
                    for e in result["errors"]:
                        st.markdown(f"- `{Path(e['file']).name}` → {e['error']}")
            # scan 캐시 무효화 — 다음 미리보기 새로
            st.session_state.pop("sync_last_scan", None)


# ── 답변 모델 ───────────────────────────────────────────────
st.markdown('<div class="section-title">🤖 답변 모델</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="section-caption">'
    '연구행정 답변 생성에 사용할 Claude 모델. 기본은 Sonnet 4.6 이고, 여러 문서/조문을 '
    '비교하는 질의(예: "혁신법으로 달라진 점", "운영요령 vs 시행령 차이")는 자동으로 '
    'Opus 4.6 으로 승격됩니다. '
    'Opus 4.7 은 한국 법령 RAG 회귀가 보고돼 의도적으로 제외했습니다.'
    '</div>',
    unsafe_allow_html=True,
)

# 사용자 표시용 라벨 ↔ config.claude_model 값 매핑.
# "(자동)" 옵션은 config.claude_model = "" (빈 문자열) 로 저장 → answerer 가 kind 에 따라 자동 라우팅.
_MODEL_OPTIONS: list[tuple[str, str]] = [
    ("(자동) Sonnet 4.6 기본 + 비교형은 Opus 4.6", ""),
    ("Sonnet 4.6 (기본 — 빠르고 균형)", "claude-sonnet-4-6"),
    ("Opus 4.6 (고품질 — 비교형/복잡한 질의에 강함)", "claude-opus-4-6"),
]
_MODEL_LABELS = [label for label, _ in _MODEL_OPTIONS]
_MODEL_VALUES = [value for _, value in _MODEL_OPTIONS]

try:
    _current_model_idx = _MODEL_VALUES.index(cfg.claude_model)
except ValueError:
    # 과거에 저장된 sonnet-4-5 / opus-4-7 등 미지원 값 — "(자동)" 으로 폴백 표시.
    _current_model_idx = 0

new_model_label = st.selectbox(
    "Claude 답변 모델",
    _MODEL_LABELS,
    index=_current_model_idx,
    key="cfg_claude_model",
    help=(
        "(자동) 모드는 질의 종류를 분석해 비교형은 Opus, 그 외는 Sonnet 으로 라우팅합니다. "
        "특정 모델을 강제하면 모든 질의에 그 모델만 사용됩니다."
    ),
)
new_claude_model = _MODEL_VALUES[_MODEL_LABELS.index(new_model_label)]

# (자동) 모드일 때만 의미 있는 escalate 토글
new_escalate = st.checkbox(
    "비교형 질의 자동 Opus escalate",
    value=cfg.enable_comparison_escalate,
    key="cfg_enable_comparison_escalate",
    disabled=bool(new_claude_model),
    help=(
        "ON 이면 query_analyzer 가 kind='comparison' 으로 판단한 질의에서 자동으로 Opus 4.6 을 "
        "사용합니다. (특정 모델을 강제 선택했을 땐 효과 없음 — 사용자 선택이 항상 우선)"
    ),
)

# ── 외부 서비스 ─────────────────────────────────────────────
st.markdown('<div class="section-title">🔌 외부 서비스</div>', unsafe_allow_html=True)

new_mcp_url = st.text_input("법제처 MCP URL", value=cfg.korean_law_mcp_url, key="cfg_mcp_url")
new_oc = st.text_input(
    "법제처 OC (옵션)",
    value=cfg.korean_law_oc,
    placeholder="법제처 발급 OC 키",
    key="cfg_oc",
)
new_hwp_enabled = st.checkbox(
    "HWP MCP 활성화 (별도 설치 필요)",
    value=cfg.hwp_mcp_enabled,
    key="cfg_hwp_mcp",
    help=(
        "ON 시 동기화가 .hwp / .hwpx 파일도 PDF와 함께 인덱싱합니다. "
        "OLE2 기반 HWP v5.x / HWPX 는 treesoop/hwp-mcp 로, "
        "HWPML(XML, 법제처 포털 배포본) 은 stdlib XML 파서로 직접 처리합니다."
    ),
)
if new_hwp_enabled and not cfg.hwp_mcp_enabled:
    # 토글을 방금 ON 한 순간 — 설치 안내를 한 줄 띄움.
    try:
        import importlib
        importlib.import_module("hangul_mcp")
        st.caption("✅ hwp-mcp 가 이미 설치되어 있습니다. 저장 시 .hwp 파일이 자동 인덱싱됩니다.")
    except ImportError:
        st.caption(
            "ℹ️ hwp-mcp 미설치 — 위 환경 검사 카드의 'HWP MCP' 항목에서 자동 설치 버튼을 누르거나 "
            "터미널에서 `pip install hwp-mcp` 를 실행하세요."
        )

# 변경 감지
changes = (
    new_pdf != cfg.pdf_dir
    or new_hwp != cfg.hwp_dir
    or new_mcp_url != cfg.korean_law_mcp_url
    or new_oc != cfg.korean_law_oc
    or new_hwp_enabled != cfg.hwp_mcp_enabled
    or new_auto_sync != cfg.auto_sync_on_start
    or new_claude_model != cfg.claude_model
    or new_escalate != cfg.enable_comparison_escalate
)
if changes:
    if st.button("💾 설정 저장", use_container_width=True):
        # 저장 후 자동 sync 가 필요한 변경 — 폴더 경로·HWP 토글만 해당.
        sync_relevant = (
            new_pdf != cfg.pdf_dir
            or new_hwp != cfg.hwp_dir
            or new_hwp_enabled != cfg.hwp_mcp_enabled
        )
        update_config(
            pdf_dir=new_pdf,
            hwp_dir=new_hwp,
            korean_law_mcp_url=new_mcp_url,
            korean_law_oc=new_oc,
            hwp_mcp_enabled=new_hwp_enabled,
            auto_sync_on_start=new_auto_sync,
            claude_model=new_claude_model,
            enable_comparison_escalate=new_escalate,
        )
        st.success("저장 완료")

        if sync_relevant:
            with st.spinner("📂 변경사항 자동 인덱싱 중..."):
                try:
                    from pipeline.sync import (
                        apply_changes as _auto_apply,
                        scan_changes as _auto_scan,
                    )
                    _auto_roots = _sync_roots_for_ui()
                    _auto_changes = _auto_scan(roots=_auto_roots)
                    if (
                        _auto_changes["added"]
                        or _auto_changes["modified"]
                        or _auto_changes["deleted"]
                        or _auto_changes["stale_code"]
                    ):
                        _auto_result = _auto_apply(_auto_changes)
                        st.success(
                            f"인덱싱 완료 — "
                            f"신규/변경 {_auto_result['indexed']}건, "
                            f"삭제 {_auto_result['deleted']}건, "
                            f"오류 {len(_auto_result['errors'])}건 "
                            f"({_auto_result['elapsed_sec']:.1f}s)"
                        )
                    else:
                        st.info("인덱스가 이미 최신 상태입니다.")
                except Exception as _auto_e:
                    st.warning(
                        f"자동 인덱싱 실패: {type(_auto_e).__name__}: {_auto_e}. "
                        "아래 '동기화 실행' 버튼으로 수동 시도하세요."
                    )

        st.rerun()

# ── MCP 상태 (Phase G5) ─────────────────────────────────────
st.markdown('<div class="section-title">🔄 MCP 상태</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="section-caption">'
    '연동 MCP 의 스키마/버전 상태와 연속 실패 시 자동 비활성 정보를 보여줍니다. '
    '5회 연속 실패 시 1시간 비활성, "재활성" 버튼으로 즉시 복구.'
    '</div>',
    unsafe_allow_html=True,
)

from pipeline.mcp_sync import (
    MAX_CONSECUTIVE_FAILURES,
    STATUS_PATH as MCP_STATUS_PATH,
    check_hwp_mcp_version,
    is_channel_disabled,
    load_status,
    probe_korean_law_mcp,
    reset_channel,
    upgrade_hwp_mcp_background,
)

mcp_status = load_status(MCP_STATUS_PATH)
korean_block = mcp_status.get("korean-law-mcp", {})
hwp_block = mcp_status.get("hwp-mcp", {})

# 두 채널 모두 비활성 시 빨간 배너
both_disabled = (
    is_channel_disabled("korean-law-mcp", MCP_STATUS_PATH)
    and is_channel_disabled("hwp-mcp", MCP_STATUS_PATH)
)
if both_disabled:
    st.error(
        "⚠️ 모든 MCP 채널이 비활성 상태입니다. "
        "법령 검색 / HWP 파싱이 동작하지 않습니다. 아래 '재활성' 버튼으로 복구하세요."
    )

# ── korean-law-mcp ─────────────────────────────────────────
st.markdown(
    '<div style="font-size:13px;font-weight:600;color:#cbd5e1;margin:14px 0 6px;">'
    '📜 korean-law-mcp (법제처 원격)'
    '</div>',
    unsafe_allow_html=True,
)
schema_match = korean_block.get("schema_match")
last_probe = korean_block.get("last_probe_at", "—")
fails_kl = int(korean_block.get("consecutive_failures", 0) or 0)
disabled_kl = is_channel_disabled("korean-law-mcp", MCP_STATUS_PATH)

if schema_match is True:
    schema_label = "✅ 스키마 매치"
elif schema_match is False:
    missing = korean_block.get("missing_tools") or []
    if missing:
        schema_label = f"❌ 누락 tool: {', '.join(missing)}"
    else:
        schema_label = "❌ probe 실패"
else:
    schema_label = "❔ 미검사"

probe_err = korean_block.get("probe_error")
disable_label = (
    f" · 🛑 비활성 (until {korean_block.get('disabled_until')})" if disabled_kl else ""
)
fail_label = f" · 실패 {fails_kl}/{MAX_CONSECUTIVE_FAILURES}" if fails_kl else ""

st.markdown(
    f'<div style="font-family: SF Mono, monospace; font-size:12px; color:#94a3b8;">'
    f'{schema_label} · 마지막 probe: {last_probe}{fail_label}{disable_label}'
    f'</div>',
    unsafe_allow_html=True,
)
if probe_err:
    st.caption(f"오류: `{probe_err}`")

kl_col1, kl_col2 = st.columns([1, 1])
with kl_col1:
    if st.button("🔄 다시 probe", key="probe_korean_law", use_container_width=True):
        with st.spinner("MCP 스키마 확인 중..."):
            url = cfg.korean_law_mcp_url or "https://korean-law-mcp.fly.dev/mcp"
            if cfg.korean_law_oc:
                url = f"{url}?oc={cfg.korean_law_oc}"
            result = probe_korean_law_mcp(url)
        if result.ok and result.schema_match:
            st.success(f"스키마 일치 — actual {len(result.actual_tools)}개 tool")
        elif result.ok:
            st.warning(f"스키마 mismatch — 누락: {', '.join(result.missing_tools)}")
        else:
            st.error(f"probe 실패: {result.error}")
        st.rerun()
with kl_col2:
    if st.button(
        "♻️ 재활성",
        key="reset_korean_law",
        use_container_width=True,
        disabled=not (disabled_kl or fails_kl),
    ):
        reset_channel("korean-law-mcp")
        st.success("실패 카운터/비활성 상태 초기화")
        st.rerun()

# ── hwp-mcp ─────────────────────────────────────────────────
st.markdown(
    '<div style="font-size:13px;font-weight:600;color:#cbd5e1;margin:18px 0 6px;">'
    '📄 hwp-mcp (로컬 pip 패키지)'
    '</div>',
    unsafe_allow_html=True,
)
installed = hwp_block.get("installed_version") or "(미설치)"
latest = hwp_block.get("pypi_latest") or "—"
update_avail = bool(hwp_block.get("update_available"))
last_check = hwp_block.get("last_check_at", "—")
fails_hwp = int(hwp_block.get("consecutive_failures", 0) or 0)
disabled_hwp = is_channel_disabled("hwp-mcp", MCP_STATUS_PATH)

if update_avail:
    ver_label = f"🆙 {installed} → {latest} (업데이트 가능)"
elif installed and latest and installed == latest:
    ver_label = f"✅ 최신 ({installed})"
else:
    ver_label = f"설치: {installed} · PyPI: {latest}"
hwp_disable_label = (
    f" · 🛑 비활성 (until {hwp_block.get('disabled_until')})" if disabled_hwp else ""
)
hwp_fail_label = f" · 실패 {fails_hwp}/{MAX_CONSECUTIVE_FAILURES}" if fails_hwp else ""

st.markdown(
    f'<div style="font-family: SF Mono, monospace; font-size:12px; color:#94a3b8;">'
    f'{ver_label} · 마지막 확인: {last_check}{hwp_fail_label}{hwp_disable_label}'
    f'</div>',
    unsafe_allow_html=True,
)
check_err = hwp_block.get("check_error")
if check_err:
    st.caption(f"오류: `{check_err}`")

hwp_col1, hwp_col2, hwp_col3 = st.columns([1, 1, 1])
with hwp_col1:
    if st.button("🔄 버전 확인", key="check_hwp_version", use_container_width=True):
        with st.spinner("PyPI 조회 중..."):
            result = check_hwp_mcp_version()
        if result.ok:
            if result.update_available:
                st.warning(f"새 버전 발견: {result.installed_version} → {result.pypi_latest}")
            else:
                st.success(f"최신 버전 사용 중: {result.installed_version}")
        else:
            st.error(f"확인 실패: {result.error}")
        st.rerun()
with hwp_col2:
    if st.button(
        "🆙 업데이트",
        key="upgrade_hwp",
        use_container_width=True,
        disabled=not update_avail,
    ):
        proc = upgrade_hwp_mcp_background()
        st.info(f"백그라운드 설치 시작 (pid {proc.pid}). 완료 후 앱을 재시작하세요.")
with hwp_col3:
    if st.button(
        "♻️ 재활성",
        key="reset_hwp",
        use_container_width=True,
        disabled=not (disabled_hwp or fails_hwp),
    ):
        reset_channel("hwp-mcp")
        st.success("실패 카운터/비활성 상태 초기화")
        st.rerun()

# ── 진입 버튼 ───────────────────────────────────────────────
st.markdown('<div style="height:24px;"></div>', unsafe_allow_html=True)

if ready:
    label = "🚀 시작하기" if is_first_run else "💬 챗봇으로"
    if st.button(label, use_container_width=True, type="primary"):
        # 저장 안 된 경로/설정 변경도 함께 영구 저장
        update_config(
            pdf_dir=new_pdf,
            hwp_dir=new_hwp,
            korean_law_mcp_url=new_mcp_url,
            korean_law_oc=new_oc,
            hwp_mcp_enabled=new_hwp_enabled,
            auto_sync_on_start=new_auto_sync,
            claude_model=new_claude_model,
            enable_comparison_escalate=new_escalate,
            onboarding_completed=True,
        )
        st.switch_page("app.py")
else:
    blockers = [r.name for r in checks if r.blocking and r.status != "ok"]
    st.error(
        f"필수 항목 {len(blockers)}개 미충족: {', '.join(blockers)}. "
        "위 자동 수정 버튼을 누르거나, 표시된 명령어를 별도 터미널에서 실행하세요."
    )
