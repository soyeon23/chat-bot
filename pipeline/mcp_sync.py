"""MCP 동기화 — 스키마 probe + 업데이트 체커 + fallback wrapper (Phase G5).

지원 채널:
    - "korean-law-mcp": 원격 HTTP MCP 서버 (URL 은 config.korean_law_mcp_url).
      `list_tools` 로 노출된 도구 시그니처를 받아, 클라이언트 코드가 호출하는
      tool 들이 모두 존재하는지 비교한다.
    - "hwp-mcp": pip 패키지 (`hwp-mcp`). `pip show` 로 로컬 버전을 읽고
      PyPI 의 최신 버전과 비교한다. 업데이트 가능 여부만 결정한다 (자동
      설치는 UI 에서 사용자가 트리거).

상태는 `data/metadata/mcp_status.json` 에 영구 저장된다. UI(00_⚙️_환경설정.py)
와 호출부(`pipeline.korean_law_client`, `pipeline.hwp_parser`) 가 이 파일을
공유한다.

호출 fallback (`call_with_fallback`):
    - 1회 자동 retry. 두 번째 호출도 실패 시 consecutive_failures += 1.
    - 5회 연속 실패가 누적되면 1시간 동안 채널을 비활성 (`disabled_until`).
    - 비활성 상태에서는 함수 호출 없이 즉시 RuntimeError 를 던진다.
    - 한 번이라도 성공하면 카운터/disabled_until 초기화.

이 모듈은 streamlit 의존성 없이 단독 동작해야 하며, 모든 IO 는 atomic
write 로 처리한다.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

# 설정 / 상수 ────────────────────────────────────────────────────────────

STATUS_PATH = Path("data/metadata/mcp_status.json")

# korean-law-mcp 호출부(`pipeline/korean_law_client.py`)에서 실제로 사용하는 tool들.
# 이 목록은 코드에서 직접 추출해서 동기화 — `pipeline/korean_law_client.py` 를
# 수정해 새 tool 을 추가하면 여기도 추가해야 한다.
KOREAN_LAW_EXPECTED_TOOLS: tuple[str, ...] = (
    "search_law",
    "get_law_text",
    "get_annexes",
)

# fallback 정책
MAX_CONSECUTIVE_FAILURES = 5
DISABLE_DURATION_SEC = 3600  # 1시간
RETRY_DELAY_SEC = 0.5        # 1차 실패 후 retry 전 대기

# probe 타임아웃
PROBE_TIMEOUT_SEC = 15
PYPI_TIMEOUT_SEC = 8


# 데이터 클래스 ─────────────────────────────────────────────────────────


@dataclass
class ProbeResult:
    """korean-law-mcp probe 결과."""
    ok: bool
    url: str
    expected_tools: list[str]
    actual_tools: list[str]
    missing_tools: list[str]
    schema_hash: str
    schema_match: bool
    error: Optional[str] = None
    probed_at: str = ""


@dataclass
class VersionCheckResult:
    """hwp-mcp 버전 비교 결과."""
    ok: bool
    installed_version: Optional[str]
    pypi_latest: Optional[str]
    update_available: bool
    error: Optional[str] = None
    checked_at: str = ""


@dataclass
class ChannelStatus:
    """단일 MCP 채널의 상태 (json 영속 형식)."""
    consecutive_failures: int = 0
    disabled_until: Optional[str] = None  # ISO 시각, None 이면 활성

    # 채널별 추가 필드는 자유롭게 추가 (extras).
    extras: dict[str, Any] = field(default_factory=dict)


# JSON 영속 ─────────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _atomic_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_status(path: Path = STATUS_PATH) -> dict:
    """현재 mcp_status.json 을 dict 로 로드. 파일이 없으면 빈 dict."""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_status(status: dict, path: Path = STATUS_PATH) -> None:
    _atomic_write(path, status)


def _channel_block(status: dict, channel: str) -> dict:
    """채널 블록을 가져오거나 새로 만든다."""
    return status.setdefault(channel, {
        "consecutive_failures": 0,
        "disabled_until": None,
    })


# 비활성 판정 / 재활성 ─────────────────────────────────────────────────


def _is_disabled(block: dict) -> bool:
    until = block.get("disabled_until")
    if not until:
        return False
    try:
        deadline = datetime.fromisoformat(until)
    except ValueError:
        return False
    return datetime.now() < deadline


def reset_channel(channel: str, path: Path = STATUS_PATH) -> dict:
    """채널의 실패 카운터/비활성 상태를 모두 비운다 (UI '재활성' 버튼)."""
    status = load_status(path)
    block = _channel_block(status, channel)
    block["consecutive_failures"] = 0
    block["disabled_until"] = None
    block["last_reset_at"] = _now_iso()
    save_status(status, path)
    return block


def is_channel_disabled(channel: str, path: Path = STATUS_PATH) -> bool:
    status = load_status(path)
    block = status.get(channel)
    if not block:
        return False
    return _is_disabled(block)


# korean-law-mcp probe ──────────────────────────────────────────────────


async def _list_tools_async(url: str) -> list[Any]:
    """MCP HTTP endpoint 에 list_tools 호출. tool 객체 리스트 반환."""
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async with streamablehttp_client(url) as (read, write, _):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools_resp = await session.list_tools()
            return list(tools_resp.tools or [])


def _signature_of(tool: Any) -> dict:
    """tool 의 비교 가능한 시그니처 추출."""
    schema = getattr(tool, "inputSchema", None) or {}
    props = sorted((schema.get("properties") or {}).keys())
    required = sorted(schema.get("required") or [])
    return {"name": tool.name, "args": props, "required": required}


def _compute_schema_hash(signatures: list[dict]) -> str:
    blob = json.dumps(sorted(signatures, key=lambda s: s["name"]), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def probe_korean_law_mcp(
    url: str,
    expected_tools: Iterable[str] = KOREAN_LAW_EXPECTED_TOOLS,
    timeout_sec: float = PROBE_TIMEOUT_SEC,
    persist: bool = True,
    status_path: Path = STATUS_PATH,
) -> ProbeResult:
    """원격 MCP 서버에 list_tools 호출 → 기대 tool 와 비교.

    Args:
        url: streamable_http 엔드포인트 (이미 ?oc=... 가 붙어있어도 무방).
        expected_tools: 클라이언트가 호출할 도구 명. 누락 = mismatch.
        timeout_sec: 전체 probe 호출 타임아웃.
        persist: True 면 mcp_status.json 에 결과 반영.
    """
    expected_list = list(expected_tools)
    probed_at = _now_iso()

    try:
        tools = asyncio.run(asyncio.wait_for(_list_tools_async(url), timeout_sec))
    except Exception as exc:  # noqa: BLE001 — 네트워크 / 핸드쉐이크 등 광범위 실패
        result = ProbeResult(
            ok=False,
            url=url,
            expected_tools=expected_list,
            actual_tools=[],
            missing_tools=expected_list,
            schema_hash="",
            schema_match=False,
            error=f"{type(exc).__name__}: {exc}",
            probed_at=probed_at,
        )
        if persist:
            _persist_probe(result, status_path)
        return result

    sigs = [_signature_of(t) for t in tools]
    actual_names = sorted(s["name"] for s in sigs)
    missing = [t for t in expected_list if t not in actual_names]
    schema_hash = _compute_schema_hash(sigs)

    result = ProbeResult(
        ok=True,
        url=url,
        expected_tools=expected_list,
        actual_tools=actual_names,
        missing_tools=missing,
        schema_hash=schema_hash,
        schema_match=(len(missing) == 0),
        error=None,
        probed_at=probed_at,
    )
    if persist:
        _persist_probe(result, status_path)
    return result


def _persist_probe(result: ProbeResult, path: Path) -> None:
    status = load_status(path)
    block = _channel_block(status, "korean-law-mcp")
    block.update({
        "url": result.url,
        "last_probe_at": result.probed_at,
        "schema_hash": result.schema_hash,
        "expected_tools": result.expected_tools,
        "actual_tools": result.actual_tools,
        "missing_tools": result.missing_tools,
        "schema_match": result.schema_match,
        "probe_ok": result.ok,
        "probe_error": result.error,
    })
    save_status(status, path)


# hwp-mcp version check ────────────────────────────────────────────────


_PIP_VERSION_RE = re.compile(r"^Version:\s*(.+)$", re.MULTILINE)


def _read_installed_version(package: str = "hwp-mcp") -> Optional[str]:
    """`pip show <package>` 로 설치된 버전을 읽는다. 미설치/실패 시 None."""
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "show", package],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    if proc.returncode != 0:
        return None
    m = _PIP_VERSION_RE.search(proc.stdout)
    return m.group(1).strip() if m else None


def _read_pypi_latest(package: str = "hwp-mcp", timeout_sec: float = PYPI_TIMEOUT_SEC) -> Optional[str]:
    """PyPI JSON API 로 최신 버전을 읽는다. 실패 시 None."""
    url = f"https://pypi.org/pypi/{package}/json"
    try:
        with urllib.request.urlopen(url, timeout=timeout_sec) as resp:
            data = json.load(resp)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None
    info = data.get("info") or {}
    ver = info.get("version")
    return str(ver) if ver else None


def _version_tuple(v: str) -> tuple:
    """0.1.10 > 0.1.2 가 되도록 dot-segment 를 정수로 변환. 비숫자 segment 는
    0 으로 폴백 (단순한 SemVer 비교, RC/태그 미지원)."""
    parts = []
    for seg in v.split("."):
        try:
            parts.append((0, int(seg)))
        except ValueError:
            parts.append((1, seg))  # 비숫자는 숫자보다 큰 것으로 정렬 — 안전 측 추정
    return tuple(parts)


def check_hwp_mcp_version(
    package: str = "hwp-mcp",
    persist: bool = True,
    status_path: Path = STATUS_PATH,
    *,
    _pip_show: Callable[[str], Optional[str]] | None = None,
    _pypi_latest: Callable[[str], Optional[str]] | None = None,
) -> VersionCheckResult:
    """로컬 vs PyPI 버전 비교. 테스트에서는 hooks 인자로 mock 주입 가능."""
    pip_show = _pip_show or _read_installed_version
    pypi_latest = _pypi_latest or _read_pypi_latest

    checked_at = _now_iso()
    installed = pip_show(package)
    latest = pypi_latest(package)

    update_available = False
    error: Optional[str] = None

    if installed is None and latest is None:
        error = "pip / PyPI 둘 다 조회 실패"
    elif installed is None:
        error = "패키지 미설치"
    elif latest is None:
        error = "PyPI 조회 실패 (네트워크?)"
    else:
        try:
            update_available = _version_tuple(latest) > _version_tuple(installed)
        except Exception as exc:  # noqa: BLE001 — 비교 실패해도 데이터는 보존
            error = f"버전 비교 실패: {exc}"

    result = VersionCheckResult(
        ok=(error is None),
        installed_version=installed,
        pypi_latest=latest,
        update_available=update_available,
        error=error,
        checked_at=checked_at,
    )

    if persist:
        status = load_status(status_path)
        block = _channel_block(status, "hwp-mcp")
        block.update({
            "installed_version": installed,
            "pypi_latest": latest,
            "update_available": update_available,
            "last_check_at": checked_at,
            "check_ok": result.ok,
            "check_error": error,
        })
        save_status(status, status_path)

    return result


# call_with_fallback ─────────────────────────────────────────────────────


class MCPDisabledError(RuntimeError):
    """비활성 상태인 채널에 호출이 들어왔을 때 던진다."""


def _record_failure(channel: str, exc: BaseException, status_path: Path) -> dict:
    status = load_status(status_path)
    block = _channel_block(status, channel)
    block["consecutive_failures"] = int(block.get("consecutive_failures", 0)) + 1
    block["last_failure_at"] = _now_iso()
    block["last_error"] = f"{type(exc).__name__}: {exc}"[:300]
    if block["consecutive_failures"] >= MAX_CONSECUTIVE_FAILURES:
        deadline = datetime.now() + timedelta(seconds=DISABLE_DURATION_SEC)
        block["disabled_until"] = deadline.isoformat(timespec="seconds")
    save_status(status, status_path)
    return block


def _record_success(channel: str, status_path: Path) -> dict:
    status = load_status(status_path)
    block = _channel_block(status, channel)
    # 첫 성공이거나 누적 실패가 있으면 IO 한 번 — 그 외에는 last_success_at 만 기록.
    block["consecutive_failures"] = 0
    block["disabled_until"] = None
    block["last_success_at"] = _now_iso()
    save_status(status, status_path)
    return block


def call_with_fallback(
    channel: str,
    fn: Callable[..., Any],
    *args,
    status_path: Path = STATUS_PATH,
    retry_delay_sec: float = RETRY_DELAY_SEC,
    **kwargs,
) -> Any:
    """MCP 호출 wrapper.

    동작:
        1. 채널이 비활성이면 즉시 MCPDisabledError.
        2. fn(*args, **kwargs) 호출. 성공 시 카운터 리셋 후 결과 반환.
        3. 실패 시 retry_delay_sec 만큼 sleep 후 한 번 더 시도.
        4. 두 번째 시도도 실패 시 consecutive_failures += 1, 5회 누적 시 비활성.
           원래 예외를 그대로 raise.
    """
    status = load_status(status_path)
    block = status.get(channel) or {}
    if _is_disabled(block):
        raise MCPDisabledError(
            f"MCP 채널 '{channel}' 이 일시 비활성 상태입니다 (until={block.get('disabled_until')})"
        )

    last_exc: Optional[BaseException] = None
    for attempt in (1, 2):
        try:
            result = fn(*args, **kwargs)
            _record_success(channel, status_path)
            return result
        except MCPDisabledError:
            raise
        except Exception as exc:  # noqa: BLE001 — 어떤 예외라도 fallback 처리
            last_exc = exc
            if attempt == 1 and retry_delay_sec > 0:
                time.sleep(retry_delay_sec)

    # 두 번 다 실패
    assert last_exc is not None
    _record_failure(channel, last_exc, status_path)
    raise last_exc


# 편의 함수: UI / CLI 에서 한 번에 사용 ─────────────────────────────────


def refresh_all(status_path: Path = STATUS_PATH) -> dict:
    """양쪽 채널 상태를 한꺼번에 갱신해 status dict 를 반환."""
    # korean-law-mcp 는 config 에서 url 을 읽는다. 임포트는 함수 안에서 — config_store
    # 가 streamlit 등 무거운 의존성을 끌고 오지 않도록.
    try:
        from pipeline.config_store import load_config
        cfg = load_config()
        url = cfg.korean_law_mcp_url or "https://korean-law-mcp.fly.dev/mcp"
        if cfg.korean_law_oc:
            url = f"{url}?oc={cfg.korean_law_oc}"
    except Exception:
        url = "https://korean-law-mcp.fly.dev/mcp"

    probe_korean_law_mcp(url, persist=True, status_path=status_path)
    check_hwp_mcp_version(persist=True, status_path=status_path)
    return load_status(status_path)


def upgrade_hwp_mcp_background() -> subprocess.Popen:
    """`pip install -U hwp-mcp` 를 백그라운드 프로세스로 실행. Popen 객체 반환."""
    return subprocess.Popen(
        [sys.executable, "-m", "pip", "install", "-U", "hwp-mcp"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


__all__ = [
    "STATUS_PATH",
    "KOREAN_LAW_EXPECTED_TOOLS",
    "MAX_CONSECUTIVE_FAILURES",
    "DISABLE_DURATION_SEC",
    "ProbeResult",
    "VersionCheckResult",
    "MCPDisabledError",
    "load_status",
    "save_status",
    "is_channel_disabled",
    "reset_channel",
    "probe_korean_law_mcp",
    "check_hwp_mcp_version",
    "call_with_fallback",
    "refresh_all",
    "upgrade_hwp_mcp_background",
]
