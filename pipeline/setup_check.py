"""환경 검사 — 위저드/온보딩에서 사용.

각 체크 함수는 CheckResult를 반환한다. 자동 수정 가능한 항목은 fix() 호출 가능.
"""
from __future__ import annotations

import importlib.metadata as md
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional


@dataclass
class CheckResult:
    name: str
    status: str             # "ok" | "missing" | "warn"
    detail: str
    fix_label: str = ""     # 버튼 라벨 (있으면 자동/수동 수정 가능)
    fix_fn: Optional[Callable[[], tuple[bool, str]]] = field(default=None, repr=False)
    fix_hint: str = ""      # 수동 명령어 (자동 못 할 때)
    blocking: bool = True   # 미충족 시 다음 단계 진행 차단 여부


# ── 시스템 ────────────────────────────────────────────

def check_python() -> CheckResult:
    v = sys.version_info
    detail = f"{v.major}.{v.minor}.{v.micro}"
    if (v.major, v.minor) >= (3, 10):
        return CheckResult("Python", "ok", detail)
    return CheckResult(
        "Python", "missing", f"{detail} (3.10+ 필요)",
        fix_hint="brew install python@3.12",
    )


_TESS_DEFAULT_DIRS = [
    r"C:\Program Files\Tesseract-OCR",
    r"C:\Program Files (x86)\Tesseract-OCR",
]


def _find_tesseract() -> str | None:
    """PATH 검색 후 Windows 기본 설치 경로도 확인."""
    binary = shutil.which("tesseract")
    if binary:
        return binary
    if platform.system() == "Windows":
        for d in _TESS_DEFAULT_DIRS:
            exe = Path(d) / "tesseract.exe"
            if exe.exists():
                os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
                return str(exe)
    return None


def check_tesseract() -> CheckResult:
    binary = _find_tesseract()
    if not binary:
        def _fix_tesseract() -> tuple[bool, str]:
            import tempfile
            import urllib.request

            system = platform.system()
            if system == "Windows":
                _TESS_URL = (
                    "https://github.com/UB-Mannheim/tesseract/releases/download/"
                    "v5.4.0.20240606/tesseract-ocr-w64-setup-5.4.0.20240606.exe"
                )
                _TESS_DIRS = [
                    r"C:\Program Files\Tesseract-OCR",
                    r"C:\Program Files (x86)\Tesseract-OCR",
                ]

                def _patch_path() -> None:
                    for d in _TESS_DIRS:
                        if Path(d).exists():
                            os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")
                            break

                # GitHub에서 설치 파일 다운로드 후 UAC 권한 요청하여 자동 설치
                try:
                    tmp = Path(tempfile.mktemp(suffix=".exe"))
                    urllib.request.urlretrieve(_TESS_URL, str(tmp))
                    # Start-Process -Verb RunAs: UAC 창 한 번 승인하면 관리자 권한으로 silent 설치
                    ps_cmd = (
                        f'Start-Process -FilePath "{tmp}" '
                        f'-ArgumentList "/VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP-" '
                        f'-Verb RunAs -Wait'
                    )
                    r = subprocess.run(
                        ["powershell", "-NoProfile", "-Command", ps_cmd],
                        capture_output=True, timeout=300,
                    )
                    tmp.unlink(missing_ok=True)
                    if r.returncode == 0:
                        _patch_path()
                        return True, "Tesseract 설치 완료 — 앱을 재시작하세요."
                    err = (r.stderr or b"").decode("utf-8", errors="replace")[-400:]
                    return False, f"설치 실패 (code={r.returncode}):\n{err}"
                except Exception as e:
                    return False, (
                        f"다운로드/설치 실패: {e}\n"
                        "수동 설치: https://github.com/UB-Mannheim/tesseract/wiki"
                    )
            else:
                if shutil.which("brew"):
                    r = subprocess.run(
                        ["brew", "install", "tesseract", "tesseract-lang"],
                        capture_output=True, timeout=600,
                    )
                    out = (r.stdout or b"").decode("utf-8", errors="replace")
                    err = (r.stderr or b"").decode("utf-8", errors="replace")
                    if r.returncode == 0:
                        return True, "Tesseract 설치 완료"
                    return False, f"brew 실패:\n{(err or out)[-400:]}"
                return False, "brew 없음. 수동 설치가 필요합니다."

        hint = "winget install UB-Mannheim.TesseractOCR" if platform.system() == "Windows" else "brew install tesseract tesseract-lang"
        return CheckResult(
            "tesseract OCR", "missing", "설치되지 않음",
            fix_label="자동 설치",
            fix_fn=_fix_tesseract,
            fix_hint=hint,
            blocking=False,
        )
    try:
        out = subprocess.run([binary, "--version"], capture_output=True, text=True, timeout=3)
        ver_line = out.stdout.splitlines()[0] if out.stdout else "unknown"
    except Exception:
        ver_line = "unknown"

    # 한국어 언어팩 확인
    try:
        langs = subprocess.run(
            [binary, "--list-langs"], capture_output=True, text=True, timeout=3,
        ).stdout.splitlines()
    except Exception:
        langs = []
    if "kor" not in langs:
        return CheckResult(
            "tesseract OCR", "warn",
            f"{ver_line} (한국어팩 없음)",
            fix_label="설치 명령 복사",
            fix_hint="brew install tesseract-lang",
        )
    return CheckResult("tesseract OCR", "ok", f"{ver_line} + kor")


# ── Python 패키지 ────────────────────────────────────

REQUIRED_PACKAGES = [
    "streamlit", "pdfplumber", "qdrant_client", "claude_agent_sdk",
    "pydantic", "sentence_transformers", "langchain_huggingface",
    "torch", "ddgs", "pytesseract", "PIL", "pandas", "tqdm",
    "dotenv",  # python-dotenv
    "rank_bm25",  # BM25 sparse retrieval (Phase A 4B)
]

# pip install 시 사용할 정식 패키지명 매핑 (import name → pip name)
PIP_NAME = {
    "qdrant_client": "qdrant-client",
    "sentence_transformers": "sentence-transformers",
    "langchain_huggingface": "langchain-huggingface",
    "claude_agent_sdk": "claude-agent-sdk",
    "dotenv": "python-dotenv",
    "PIL": "Pillow",
    "rank_bm25": "rank-bm25",
}


def _check_package(import_name: str) -> bool:
    try:
        __import__(import_name)
        return True
    except Exception:
        return False


def check_packages() -> CheckResult:
    missing = [p for p in REQUIRED_PACKAGES if not _check_package(p)]
    if not missing:
        return CheckResult("Python 패키지", "ok", f"{len(REQUIRED_PACKAGES)}개 설치됨")

    pip_targets = " ".join(PIP_NAME.get(p, p) for p in missing)

    def fix() -> tuple[bool, str]:
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install"] + pip_targets.split(),
                capture_output=True, text=True, timeout=600,
            )
            if result.returncode == 0:
                return True, f"{len(missing)}개 패키지 설치 완료"
            return False, f"pip 실패:\n{result.stderr[-500:]}"
        except Exception as e:
            return False, f"오류: {e}"

    return CheckResult(
        "Python 패키지", "missing",
        f"누락 {len(missing)}개: {', '.join(missing)}",
        fix_label=f"자동 설치 ({len(missing)}개)",
        fix_fn=fix,
        fix_hint=f"pip install {pip_targets}",
    )


# ── 인증 ──────────────────────────────────────────────

def check_claude_cli() -> CheckResult:
    """`claude` CLI 가 PATH 에 있고 즉시 실행 가능한지 확인.

    답변 생성 경로는 claude-agent-sdk → claude CLI 서브프로세스로 동작하므로
    이 체크가 실질적인 'ready to answer' 시그널이다.
    """
    binary = shutil.which("claude")
    if not binary:
        return CheckResult(
            "claude CLI", "missing",
            "PATH 에서 `claude` 실행파일을 찾지 못했습니다",
            fix_label="설치 안내 복사",
            fix_hint="curl -fsSL https://claude.ai/install.sh | bash  # 또는 brew install claude (배포 채널에 따라)",
        )
    try:
        out = subprocess.run(
            [binary, "--version"], capture_output=True, text=True, timeout=5,
        )
        ver = (out.stdout or out.stderr or "").strip().splitlines()[0] if (out.stdout or out.stderr) else "unknown"
    except Exception as e:
        return CheckResult(
            "claude CLI", "warn",
            f"실행은 되지만 --version 실패: {e}",
            fix_hint=f"수동 점검: {binary} --version",
            blocking=False,
        )
    if out.returncode != 0:
        return CheckResult(
            "claude CLI", "warn",
            f"비정상 종료 (rc={out.returncode}): {ver}",
            fix_hint="claude  # 별도 터미널에서 실행 후 로그인 상태 점검",
            blocking=False,
        )
    return CheckResult("claude CLI", "ok", ver)


def check_auth() -> CheckResult:
    """Claude Code 인증 상태.

    Agent SDK 경로는 claude CLI 자체가 인증을 처리하므로 우리는 토큰을 직접
    들고 있을 필요가 없다. 다만 사이드바/위저드의 사용자 친화 라벨용으로
    여전히 키체인/env 토큰을 조회해 결과를 표시한다.
    """
    try:
        from pipeline.auth import get_auth_source, auth_status_label
        get_auth_source()
        return CheckResult("Claude Code OAuth", "ok", auth_status_label())
    except RuntimeError:
        return CheckResult(
            "Claude Code OAuth", "missing",
            "로그인되지 않음",
            fix_label="터미널에서 `claude` 실행",
            fix_hint="claude  # 별도 터미널에서 실행 후 로그인",
        )


# ── 인덱스 ────────────────────────────────────────────

def check_index() -> CheckResult:
    qdrant_path = os.getenv("QDRANT_PATH", "./qdrant_storage")

    def _fix_ingest() -> tuple[bool, str]:
        # subprocess 대신 in-process 호출 — Qdrant 로컬 모드는 단일 프로세스만 허용
        try:
            import importlib.util
            base = Path(__file__).parent.parent
            spec = importlib.util.spec_from_file_location(
                "_batch_ingest_runtime", str(base / "batch_ingest.py")
            )
            mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
            mod.main()
            return True, "인덱싱 완료"
        except SystemExit:
            return True, "인덱싱 완료"
        except Exception as e:
            return False, f"오류: {e}"

    if not Path(qdrant_path).exists():
        return CheckResult(
            "Qdrant 인덱스", "missing",
            "qdrant_storage/ 폴더 없음 — 인덱싱 필요",
            fix_label="자동 인덱싱 실행",
            fix_fn=_fix_ingest,
            fix_hint="python batch_ingest.py",
            blocking=False,
        )
    try:
        from pipeline.indexer import get_collection_count
        n = get_collection_count()
    except Exception as e:
        return CheckResult(
            "Qdrant 인덱스", "missing",
            f"컬렉션 없음 — 인덱싱 필요 ({e})",
            fix_label="자동 인덱싱 실행",
            fix_fn=_fix_ingest,
            fix_hint="python batch_ingest.py",
            blocking=False,
        )
    if n == 0:
        return CheckResult(
            "Qdrant 인덱스", "warn",
            "컬렉션은 있으나 청크 0개",
            fix_label="자동 인덱싱 실행",
            fix_fn=_fix_ingest,
            fix_hint="python batch_ingest.py",
            blocking=False,
        )
    return CheckResult("Qdrant 인덱스", "ok", f"{n:,}개 청크")


# ── MCP 서버 ──────────────────────────────────────────

def check_korean_law_mcp() -> CheckResult:
    """기본 URL이 응답하는지만 가볍게 체크. 인증·OC 없이 ping."""
    try:
        from pipeline.config_store import load_config
        cfg = load_config()
        url = cfg.korean_law_mcp_url
    except Exception:
        url = "https://korean-law-mcp.fly.dev/mcp"

    # MCP 서버는 HEAD 미지원 — TCP 연결만 확인
    try:
        import socket
        from urllib.parse import urlparse
        parsed = urlparse(url)
        host = parsed.hostname
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if not host:
            raise ValueError("URL에서 호스트 추출 실패")
        with socket.create_connection((host, port), timeout=5):
            pass
    except Exception as e:
        return CheckResult(
            "법제처 MCP", "warn",
            f"연결 실패 ({type(e).__name__})",
            blocking=False,
            fix_hint=f"URL 변경: {url}",
        )
    return CheckResult("법제처 MCP", "ok", f"도달 가능 ({host})")


def check_hwp_mcp_optional() -> CheckResult:
    """HWP MCP는 옵션 — 사용자가 활성화한 경우만 실제 설치 상태를 검사한다.

    체크 항목 (활성 시):
      1. `hangul_mcp` 모듈 import 가능 여부 (pip install hwp-mcp 결과)
      2. `python -m hangul_mcp` 또는 `hwp-mcp` 콘솔 스크립트 실행 가능 여부
    실패 시 자동 수정 fix_fn 으로 `pip install hwp-mcp` 실행.
    """
    try:
        from pipeline.config_store import load_config
        cfg = load_config()
    except Exception:
        return CheckResult("HWP MCP", "ok", "비활성", blocking=False)

    if not cfg.hwp_mcp_enabled:
        return CheckResult("HWP MCP", "ok", "비활성 (옵션)", blocking=False)

    # 1) 모듈 import 검증 — 패키지명은 hwp-mcp, 모듈명은 hangul_mcp 로 다르다.
    has_module = _check_package("hangul_mcp")

    def _fix_install() -> tuple[bool, str]:
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pip", "install", "hwp-mcp"],
                capture_output=True, text=True, timeout=300,
            )
            if result.returncode == 0:
                return True, "hwp-mcp 설치 완료"
            return False, f"pip 실패:\n{result.stderr[-500:]}"
        except Exception as e:
            return False, f"오류: {e}"

    if not has_module:
        return CheckResult(
            "HWP MCP", "missing",
            "hwp-mcp 미설치 (활성화됨)",
            fix_label="자동 설치",
            fix_fn=_fix_install,
            fix_hint=f"{sys.executable} -m pip install hwp-mcp",
            blocking=False,  # HWP 가 없어도 PDF 만으로 챗봇은 동작 가능
        )

    # 2) 모듈 import 는 가능 — 콘솔 스크립트 또는 -m 실행 둘 중 하나만 되면 OK.
    has_console = shutil.which("hwp-mcp") is not None
    try:
        # `python -m hangul_mcp --help` 는 stdio 서버를 띄우므로 실제로는 hang 한다.
        # 그래서 -c "import hangul_mcp" 로 갈음 — 여기까지 왔으면 이미 통과한 것.
        version = ""
        try:
            from hangul_mcp import __version__ as _v  # type: ignore
            version = str(_v)
        except Exception:
            pass
    except Exception:
        version = ""

    detail_bits = ["설치됨"]
    if version:
        detail_bits.append(f"v{version}")
    if has_console:
        detail_bits.append("CLI ✓")
    else:
        detail_bits.append("python -m hangul_mcp")
    return CheckResult("HWP MCP", "ok", " · ".join(detail_bits), blocking=False)


# ── 통합 ──────────────────────────────────────────────

def run_all_checks(include_optional: bool = True) -> list[CheckResult]:
    checks = [
        check_python(),
        check_packages(),
        check_tesseract(),
        check_claude_cli(),
        check_auth(),
        check_index(),
        check_korean_law_mcp(),
    ]
    if include_optional:
        checks.append(check_hwp_mcp_optional())
    return checks


def all_blocking_ok(results: list[CheckResult]) -> bool:
    return all(r.status == "ok" for r in results if r.blocking)


def system_summary() -> dict[str, str]:
    return {
        "OS": f"{platform.system()} {platform.mac_ver()[0] or platform.release()}",
        "Python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "venv": "활성" if sys.prefix != sys.base_prefix else "미활성",
    }


if __name__ == "__main__":
    for r in run_all_checks():
        marker = {"ok": "✅", "warn": "⚠️", "missing": "❌"}[r.status]
        print(f"{marker} {r.name:20s}  {r.detail}")
        if r.fix_hint:
            print(f"     ↳ {r.fix_hint}")
