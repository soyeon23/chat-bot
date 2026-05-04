"""HWP/HWPX → ParseResult 어댑터 (treesoop/hwp-mcp 기반).

`pipeline/pdf_parser.py`의 공개 인터페이스(`parse_pdf`, `ParseResult`,
`ParsedPage`)를 그대로 흉내 낸다. 호출 측(batch_ingest, 청크 파이프라인)은
파일 형식에 따라 분기만 하면 된다.

내부 동작:
- `python -m hangul_mcp` 을 stdio MCP 서버로 띄우고 `read_hwp_text` 도구를
  호출해 텍스트를 받는다. (hwp-mcp 0.1.x 가 노출하는 도구 이름은
  `read_hwp_text`, 모듈명은 `hangul_mcp` — 패키지명과 다르다.)
- HWP 는 PDF 와 달리 견고한 페이지 경계가 없으므로 한 파일을 단일
  ParsedPage(page_num=1) 로 감싼다. chunker.py 는 본문 안의 `제N조`
  패턴으로 분할하므로 페이지 단위 정밀도는 출처 표시용으로만 쓰인다.
- hwp-mcp 0.1.1 은 OLE2 기반 HWP v5.x 와 HWPX 만 지원한다. 법제처
  포털이 배포하는 일부 .hwp 파일은 HWPML(XML 텍스트) 포맷이라 OLE2
  파서가 거부한다. 그 경우 ParseResult.pages 가 비어 있고 호출자는
  `if not result.pages` 로 건너뛸 수 있다 — 조용히 PDF 등으로 우회시키지 않는다.
"""
from __future__ import annotations

import asyncio
import re
import sys
import threading
from concurrent.futures import Future
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

# pdf_parser.py 의 데이터 클래스를 그대로 재사용 — 다운스트림이 동일하게 처리.
from pipeline.pdf_parser import ParsedPage, ParseResult

_HWPML_PREFIX = b"<?xml"  # 법제처 포털 .hwp 가 종종 이 형태(HWPML)로 배포됨


# ── MCP 클라이언트 (한 세션 내에서 여러 파일 처리) ─────────────────────

@asynccontextmanager
async def _open_session() -> AsyncIterator["object"]:
    """hwp-mcp 서버를 stdio 로 띄우고 ClientSession 을 yield 한다.

    venv 의 python 인터프리터를 그대로 사용하므로 별도 PATH 설정 없이
    macOS / Windows 모두 동일하게 동작한다.
    """
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "hangul_mcp"],
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            yield session


async def _call_read_hwp_text(session, file_path: str) -> str:
    res = await session.call_tool("read_hwp_text", {"file_path": file_path})
    parts = []
    for c in res.content:
        text = getattr(c, "text", None)
        if text:
            parts.append(text)
    return "".join(parts)


# ── 동기 래퍼: 백그라운드 스레드에서 새 이벤트 루프를 돌린다 ───────────
#
# stdio_client 는 anyio TaskGroup 을 쓰며 cancel scope 가 동일 task 내에서
# enter/exit 되어야 한다. asyncio.run() 을 같은 호출에서 두 번 쓰면
# "different task" 에러가 나므로, 매 호출마다 전용 스레드 + 새 이벤트 루프로
# 격리한다. 호출 빈도가 낮은 인덱싱 경로에서는 충분하다.


def _run_async(coro_factory) -> object:
    """asyncio coroutine factory 를 별도 스레드의 fresh loop 에서 실행."""
    fut: Future = Future()

    def _worker():
        # hangul_mcp 0.1.x 의 OLE2 파서가 재귀 깊이 1000 을 쉽게 초과 →
        # RecursionError 로 *모든* OLE2 HWP 파싱이 실패. recursion limit 은
        # thread-local 이라 worker 진입 시점에 늘린다 (다른 thread 영향 없음).
        sys.setrecursionlimit(max(sys.getrecursionlimit(), 20000))
        try:
            result = asyncio.run(coro_factory())
            fut.set_result(result)
        except BaseException as e:  # noqa: BLE001
            fut.set_exception(e)

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join()
    return fut.result()


# ── 사전 검증 ─────────────────────────────────────────────────────────


def _is_hwpml(path: Path) -> bool:
    """확장자가 .hwp 지만 실제로는 HWPML(XML) 인지 빠르게 판단."""
    try:
        with open(path, "rb") as f:
            head = f.read(8)
        return head.startswith(_HWPML_PREFIX)
    except OSError:
        return False


def _is_supported_extension(path: Path) -> bool:
    return path.suffix.lower() in (".hwp", ".hwpx")


# ── 공개 API ──────────────────────────────────────────────────────────


def parse_hwp(hwp_path: str | Path, save_raw: bool = True) -> ParseResult:
    """HWP/HWPX 단일 파일을 ParseResult 로 변환한다.

    호출당 hwp-mcp stdio 세션이 1회 열린다. 다수 파일을 처리할 때는
    `parse_hwp_batch` 를 써서 spawn 비용을 줄여라.
    """
    hwp_path = Path(hwp_path)
    if not hwp_path.exists():
        raise FileNotFoundError(f"HWP 파일을 찾을 수 없습니다: {hwp_path}")
    if not _is_supported_extension(hwp_path):
        raise ValueError(f"HWP/HWPX 파일이 아닙니다: {hwp_path.name}")

    print(f"  HWP 파싱 (hwp-mcp): {hwp_path.name}")

    if _is_hwpml(hwp_path):
        # HWPML(XML) — hwp-mcp(OLE2) 우회, stdlib 파서로 위임.
        from pipeline.hwpml_parser import parse_hwpml
        return parse_hwpml(hwp_path, save_raw=save_raw)

    async def _go():
        async with _open_session() as session:
            text = await _call_read_hwp_text(session, str(hwp_path.resolve()))
            return text

    from pipeline.mcp_sync import call_with_fallback, MCPDisabledError
    try:
        text = call_with_fallback("hwp-mcp", _run_async, _go)
    except MCPDisabledError as e:
        print(f"  [HWP 채널 비활성] {e}")
        return ParseResult(source_file=hwp_path.name, pages=[])
    except Exception as e:
        print(f"  [HWP 파싱 실패] {type(e).__name__}: {e}")
        return ParseResult(source_file=hwp_path.name, pages=[])

    return _wrap_text_as_result(hwp_path, text, save_raw=save_raw)


def parse_hwp_batch(
    hwp_paths: list[Path],
    save_raw: bool = True,
) -> dict[Path, ParseResult]:
    """여러 HWP 파일을 단일 hwp-mcp 세션 안에서 처리한다.

    인덱싱 파이프라인이 한 번에 N개의 hwp 를 돌릴 때 stdio spawn 비용을
    한 번으로 줄이기 위함이다. 파일 별 결과 dict 를 반환하며, 실패 파일은
    빈 ParseResult 가 들어간다.
    """
    if not hwp_paths:
        return {}

    # OLE2 미지원 파일은 사전에 분리 — 세션을 띄우지도 않는다.
    # HWPML 은 stdlib 파서로 직접 처리 (hwp-mcp 우회).
    from pipeline.hwpml_parser import parse_hwpml
    results: dict[Path, ParseResult] = {}
    real_targets: list[Path] = []
    for p in hwp_paths:
        p = Path(p)
        if not _is_supported_extension(p):
            print(f"  [건너뜀] HWP/HWPX 가 아님: {p.name}")
            results[p] = ParseResult(source_file=p.name, pages=[])
            continue
        if _is_hwpml(p):
            results[p] = parse_hwpml(p, save_raw=save_raw)
            continue
        real_targets.append(p)

    if not real_targets:
        return results

    async def _go():
        out: dict[Path, str] = {}
        async with _open_session() as session:
            for p in real_targets:
                try:
                    out[p] = await _call_read_hwp_text(session, str(p.resolve()))
                except Exception as e:  # noqa: BLE001 — fail-soft per file
                    print(f"  [HWP 파싱 실패] {p.name}: {type(e).__name__}: {e}")
                    out[p] = ""
        return out

    from pipeline.mcp_sync import call_with_fallback, MCPDisabledError
    try:
        texts = call_with_fallback("hwp-mcp", _run_async, _go)
    except MCPDisabledError as e:
        print(f"  [HWP 채널 비활성] {e}")
        for p in real_targets:
            results[p] = ParseResult(source_file=p.name, pages=[])
        return results
    except Exception as e:
        print(f"  [HWP 배치 세션 실패] {type(e).__name__}: {e}")
        for p in real_targets:
            results[p] = ParseResult(source_file=p.name, pages=[])
        return results

    for p in real_targets:
        results[p] = _wrap_text_as_result(p, texts.get(p, ""), save_raw=save_raw)
    return results


# ── 내부: 텍스트 → ParseResult ────────────────────────────────────────


# hwp-mcp 가 도구 호출 실패 시 텍스트로 돌려주는 마커들
_FAILURE_PREFIXES = (
    "텍스트 추출 오류",
    "파일을 찾을 수 없습니다",
    "(텍스트가 비어있습니다)",
)


def _looks_like_failure(text: str) -> bool:
    if not text or not text.strip():
        return True
    head = text.lstrip()[:50]
    return any(head.startswith(p) for p in _FAILURE_PREFIXES)


def _wrap_text_as_result(
    hwp_path: Path,
    text: str,
    *,
    save_raw: bool,
) -> ParseResult:
    """텍스트 1 덩어리 → ParseResult(단일 페이지) 변환 + 원문 저장."""
    if _looks_like_failure(text):
        print(f"  [HWP 비어있음/오류] {hwp_path.name}: {text[:80]!r}")
        return ParseResult(source_file=hwp_path.name, pages=[])

    cleaned = text.strip()
    page = ParsedPage(page_num=1, text=cleaned, needs_ocr=False)
    result = ParseResult(source_file=hwp_path.name, pages=[page])

    if save_raw:
        raw_dir = hwp_path.parent.parent / "data" / "raw"
        try:
            raw_dir.mkdir(parents=True, exist_ok=True)
            out_path = raw_dir / f"{hwp_path.stem}_raw.txt"
            with open(out_path, "w", encoding="utf-8") as f:
                f.write("=== PAGE 1 ===\n")
                f.write(cleaned + "\n")
            print(f"  원문 저장: {out_path}")
        except OSError as e:
            print(f"  [경고] 원문 저장 실패: {e}")

    # 진단용 — pdf_parser 의 validate 와 같은 로그 톤으로.
    article_hits = re.findall(r"제\d+조", cleaned)
    table_hits = re.findall(r"별표\s*\d*", cleaned)
    print(
        f"  검증 - 길이 {len(cleaned)}자 | 제N조 {len(article_hits)}개 | 별표 {len(table_hits)}개"
    )
    return result


__all__ = ["parse_hwp", "parse_hwp_batch", "ParsedPage", "ParseResult"]
