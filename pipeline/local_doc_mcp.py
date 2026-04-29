"""로컬 PDF/HWP 직접 접근 도구 (Claude Agent SDK in-process MCP).

Phase H — Hybrid Agent-style Document Access.

배경:
    chunker 의 페이지 태깅이 1.3.x 까지 4번 패치돼도 "151p 알려줘" 류 페이지
    직접 조회가 회귀를 반복했다. 청크 단위 인덱스 위에서 페이지 정밀 조회를
    버티려고 한 것이 근본 원인. Phase H 는 정밀 조회 경로만 *Agent 스타일* —
    Claude 가 도구로 PDF 를 직접 읽도록 — 로 분기한다.

설계:
    - Claude Agent SDK 의 in-process MCP 서버(`create_sdk_mcp_server`)를 사용.
      별도 stdio/spawn 프로세스가 없고, 본 모듈의 함수가 동일 파이썬 프로세스
      안에서 Claude 의 도구 호출을 직접 처리한다.
    - 상태(파일 목록 캐시, 페이지 텍스트 캐시) 는 모듈 전역 dict 로 보관하며
      file mtime 으로 무효화한다. PDF 파싱이 비싸므로 LRU 적 동작을 갖는다.
    - 실패는 항상 fail-soft — 파일이 없거나 인덱스 범위를 벗어나면 빈 결과
      또는 친절한 오류 메시지를 도구 응답에 담아 돌려준다 (예외 raise 하지
      않음). Claude 가 그 메시지를 보고 다음 행동을 결정할 수 있어야 한다.

공개 API (Python 호출용 + MCP 도구 핸들러용):
    - list_documents()
    - read_page(doc_name, page_num)
    - search_text(doc_name, query, max_results=5)
    - get_article(doc_name, article_no)
    - list_articles(doc_name)
    - build_local_doc_server() → McpSdkServerConfig
"""
from __future__ import annotations

import os
import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

# pdfplumber 는 import 비용이 있으므로 함수 안에서 lazy import.

__all__ = [
    "list_documents",
    "read_page",
    "search_text",
    "get_article",
    "list_articles",
    "build_local_doc_server",
    "TOOL_NAMES",
]

# Claude 가 호출할 수 있는 MCP 도구 이름 모음.
# answerer.py 의 `allowed_tools` 빌드에 사용된다.
# 주의: SDK MCP 서버 도구는 외부에서 호출 시 `mcp__<server>__<tool>` 형태로
# allowed_tools 에 등록해야 한다. _SERVER_NAME 과 함께 빌드한다.
_SERVER_NAME = "local_doc"
_TOOL_BARE_NAMES = ["read_page", "get_article", "search_text", "list_articles", "list_documents"]
TOOL_NAMES = [f"mcp__{_SERVER_NAME}__{n}" for n in _TOOL_BARE_NAMES]


# ──────────────────────────────────────────────────────────────────
# 캐시
# ──────────────────────────────────────────────────────────────────
#
# 한 번 파싱된 PDF 의 (페이지별 텍스트, mtime) 을 보관한다. mtime 이
# 바뀌면 자동 무효화. HWP 도 동일 형식으로 저장하지만 항상 한 페이지로
# 압축된다.

@dataclass
class _DocCache:
    path: Path
    doc_type: str  # "pdf" | "hwp"
    mtime: float
    pages: list[str]  # 1-indexed when used externally; 0-indexed in list


_doc_cache: dict[str, _DocCache] = {}  # key: resolved abs path str


def _normalize_name(s: str) -> str:
    """파일명 비교용 정규화.

    macOS APFS 는 한글을 NFD(자모 분해) 로 보존해 PosixPath.name 도 NFD 가
    되곤 한다. 사용자가 입력한 doc_name 은 통상 NFC. 비교 전 NFC 정규화 +
    소문자 + 양끝 공백 제거.
    """
    return unicodedata.normalize("NFC", (s or "").strip()).lower()


def _stem_no_ext(name: str) -> str:
    return Path(_normalize_name(name)).stem


# ──────────────────────────────────────────────────────────────────
# 디렉토리 스캔 및 인덱스
# ──────────────────────────────────────────────────────────────────


def _scan_dirs() -> list[Path]:
    """config 의 pdf_dir / hwp_dir + 프로젝트 루트(.) 에서 PDF/HWP 후보 수집.

    중복은 resolved abs path 기준으로 제거한다.
    """
    from pipeline.config_store import load_config

    try:
        cfg = load_config()
    except Exception:
        cfg = None

    seen: set[Path] = set()
    out: list[Path] = []

    candidate_dirs: list[Path] = [Path.cwd()]
    if cfg is not None:
        for raw in (cfg.pdf_dir, cfg.hwp_dir):
            if not raw:
                continue
            try:
                p = Path(raw).expanduser().resolve()
            except OSError:
                continue
            if p.exists() and p.is_dir():
                candidate_dirs.append(p)

    for d in candidate_dirs:
        try:
            d = d.resolve()
        except OSError:
            continue
        if not d.exists():
            continue
        # 너무 큰 트리 (홈 전체 등) 를 피하려고 1단계 + 1단계 하위(.../국가연구개발혁신법 ...)만
        # 훑는다. 사용자 데이터 디렉토리는 구조가 단순하다고 가정.
        try:
            for entry in d.iterdir():
                if entry.is_file():
                    if entry.suffix.lower() in (".pdf", ".hwp", ".hwpx"):
                        rp = entry.resolve()
                        if rp not in seen:
                            seen.add(rp)
                            out.append(rp)
                elif entry.is_dir() and not entry.name.startswith("."):
                    # 1 depth 하위 폴더 안의 .pdf/.hwp 도 잡는다 — 시행령/시행규칙
                    # 폴더가 프로젝트 루트에 형제로 풀려 있는 경우 대응.
                    try:
                        for sub in entry.iterdir():
                            if sub.is_file() and sub.suffix.lower() in (".pdf", ".hwp", ".hwpx"):
                                rp = sub.resolve()
                                if rp not in seen:
                                    seen.add(rp)
                                    out.append(rp)
                    except (OSError, PermissionError):
                        continue
        except (OSError, PermissionError):
            continue

    return out


def _find_doc_path(doc_name: str) -> Optional[Path]:
    """`doc_name` 으로 PDF/HWP 파일을 찾는다.

    매칭 우선순위:
        1) 정확한 파일명 (확장자 포함)
        2) 정확한 stem
        3) stem 부분 일치 (substring)
        4) NFC 정규화 후 부분 일치

    None 이면 매칭 실패.
    """
    if not doc_name or not doc_name.strip():
        return None

    target_full = _normalize_name(doc_name)
    target_stem = _stem_no_ext(doc_name)

    candidates = _scan_dirs()
    if not candidates:
        return None

    # 1) 정확한 파일명
    for p in candidates:
        if _normalize_name(p.name) == target_full:
            return p
    # 2) 정확한 stem
    for p in candidates:
        if _normalize_name(p.stem) == target_stem:
            return p
    # 3) stem 부분 일치 (사용자가 짧게 부른 경우 — "본권 매뉴얼", "혁신법 매뉴얼")
    matches = [p for p in candidates if target_stem and target_stem in _normalize_name(p.stem)]
    if len(matches) == 1:
        return matches[0]
    if matches:
        # 가장 짧은 stem 우선 (가장 정확한 매칭)
        matches.sort(key=lambda p: len(p.stem))
        return matches[0]
    # 4) 파일명 안에 사용자가 준 키워드 어떤 것이라도 들어있는지
    keywords = [w for w in re.split(r"\s+", target_full) if len(w) >= 2]
    for p in candidates:
        nm = _normalize_name(p.name)
        if all(k in nm for k in keywords) and keywords:
            return p
    return None


# ──────────────────────────────────────────────────────────────────
# 파싱 (페이지별 텍스트 캐시)
# ──────────────────────────────────────────────────────────────────


def _load_pages(path: Path) -> _DocCache:
    """파일 → _DocCache 로딩 (캐시 적용)."""
    key = str(path)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0

    cached = _doc_cache.get(key)
    if cached and cached.mtime == mtime:
        return cached

    suffix = path.suffix.lower()
    pages: list[str] = []
    doc_type = "pdf"

    if suffix == ".pdf":
        # pdfplumber 로 페이지별 raw text 추출. chunker 가 사용하는 layout=True
        # 옵션은 들여쓰기 보존이 목적이지만 페이지 본문 직접 표시에는 불필요해
        # 기본 추출을 사용 — 표 셀이 더 깔끔하다.
        import pdfplumber  # lazy
        try:
            with pdfplumber.open(path) as pdf:
                for page in pdf.pages:
                    txt = page.extract_text() or ""
                    pages.append(txt)
        except Exception as e:
            print(f"[local_doc_mcp] PDF 파싱 실패 {path.name}: {type(e).__name__}: {e}",
                  file=sys.stderr)
    elif suffix in (".hwp", ".hwpx"):
        doc_type = "hwp"
        # HWP 는 페이지 경계가 없으므로 한 덩어리. 비싼 호출이라 캐시 적중률이
        # 곧 비용 절감.
        try:
            from pipeline.hwp_parser import parse_hwp
            result = parse_hwp(str(path), save_raw=False)
            pages = [p.text for p in result.pages]
        except Exception as e:
            print(f"[local_doc_mcp] HWP 파싱 실패 {path.name}: {type(e).__name__}: {e}",
                  file=sys.stderr)

    cache = _DocCache(path=path, doc_type=doc_type, mtime=mtime, pages=pages)
    _doc_cache[key] = cache
    return cache


# ──────────────────────────────────────────────────────────────────
# 공개 함수 — Python 직접 호출 + MCP 도구 핸들러에서 공용으로 사용
# ──────────────────────────────────────────────────────────────────


def list_documents() -> list[dict[str, Any]]:
    """현재 환경에서 접근 가능한 PDF/HWP 문서 목록.

    pages 는 PDF 만 정확하다 — HWP 는 hwp-mcp 가 페이지 경계를 주지 않으므로
    1 또는 0 으로 표시된다.
    """
    out: list[dict[str, Any]] = []
    for p in _scan_dirs():
        suffix = p.suffix.lower()
        if suffix == ".pdf":
            try:
                # 페이지 수만 보려고 전체 파싱하지 않는다 — pdfplumber 가
                # `len(pdf.pages)` 만으로 빠르게 답한다.
                import pdfplumber  # lazy
                with pdfplumber.open(p) as pdf:
                    n_pages = len(pdf.pages)
            except Exception:
                n_pages = 0
            out.append({
                "name": p.name,
                "path": str(p),
                "type": "pdf",
                "pages": n_pages,
            })
        elif suffix in (".hwp", ".hwpx"):
            out.append({
                "name": p.name,
                "path": str(p),
                "type": "hwp",
                "pages": 1,  # 명목상 1페이지 — HWP 페이지 경계 미지원
            })
    return out


def read_page(doc_name: str, page_num: int) -> dict[str, Any]:
    """`doc_name` 의 1-indexed `page_num` 페이지 텍스트.

    HWP 는 page_num 무시하고 전체 텍스트를 반환 (페이지 경계 미지원).
    """
    print(f"[local_doc_mcp] read_page doc={doc_name!r} page={page_num}", file=sys.stderr)

    p = _find_doc_path(doc_name)
    if p is None:
        return {
            "doc_name": doc_name,
            "page": page_num,
            "text": "",
            "char_count": 0,
            "error": f"문서를 찾을 수 없습니다: {doc_name!r}",
        }
    cache = _load_pages(p)
    if not cache.pages:
        return {
            "doc_name": p.name,
            "page": page_num,
            "text": "",
            "char_count": 0,
            "error": "파일 파싱 실패 또는 빈 문서",
        }

    if cache.doc_type == "hwp":
        # HWP — 한 덩어리 그대로
        text = cache.pages[0] if cache.pages else ""
        return {
            "doc_name": p.name,
            "page": 1,
            "text": text,
            "char_count": len(text),
            "note": "HWP 는 페이지 경계가 없어 전체 본문을 반환합니다.",
        }

    # PDF — 1-indexed
    try:
        page_num = int(page_num)
    except (TypeError, ValueError):
        return {
            "doc_name": p.name, "page": 0, "text": "", "char_count": 0,
            "error": f"page_num 이 정수가 아닙니다: {page_num!r}",
        }

    if page_num < 1 or page_num > len(cache.pages):
        return {
            "doc_name": p.name,
            "page": page_num,
            "text": "",
            "char_count": 0,
            "error": f"페이지 범위를 벗어났습니다 (1..{len(cache.pages)})",
        }

    text = cache.pages[page_num - 1]
    return {
        "doc_name": p.name,
        "page": page_num,
        "text": text,
        "char_count": len(text),
    }


def search_text(doc_name: str, query: str, max_results: int = 5) -> list[dict[str, Any]]:
    """문서 안에서 정규식/리터럴 검색. 매칭 페이지 + 짧은 발췌 반환.

    `query` 가 정규식으로 컴파일되지 않으면 리터럴 escape 후 재시도.
    """
    print(f"[local_doc_mcp] search_text doc={doc_name!r} query={query!r} max={max_results}",
          file=sys.stderr)

    p = _find_doc_path(doc_name)
    if p is None:
        return []
    cache = _load_pages(p)
    if not cache.pages or not (query or "").strip():
        return []

    try:
        pat = re.compile(query, re.IGNORECASE | re.MULTILINE)
    except re.error:
        pat = re.compile(re.escape(query), re.IGNORECASE | re.MULTILINE)

    out: list[dict[str, Any]] = []
    for idx, page_text in enumerate(cache.pages, start=1):
        if not page_text:
            continue
        m = pat.search(page_text)
        if not m:
            continue
        # 매칭 주변 80자 발췌 (앞 30 + 매칭 + 뒤 30)
        start = max(0, m.start() - 30)
        end = min(len(page_text), m.end() + 30)
        excerpt = page_text[start:end].replace("\n", " ")
        out.append({
            "doc_name": p.name,
            "page": idx if cache.doc_type == "pdf" else 1,
            "match": m.group(0),
            "excerpt": excerpt,
        })
        if len(out) >= max_results:
            break
    return out


# 조문 매칭용 정규식 — chunker.ARTICLE_PATTERNS 와 동일한 톤이지만 단일 article_no
# 매칭 + 다음 조문 시작점 탐색에 맞게 단순화. chunker 는 split 트리거였고 여기는
# 사용자가 준 article_no 한 개로 본문을 잘라야 한다.
def _article_regex(article_no: str) -> re.Pattern[str]:
    """`제15조`, `제15조의2`, `별표 2`, `부칙` 등의 헤더 매칭 정규식.

    줄 시작 강제 + 후행 컨텍스트(공백·괄호·줄끝) — chunker 와 일치.
    """
    norm = re.sub(r"\s+", "", article_no or "")  # "제 15 조" → "제15조"
    if re.match(r"^제\d+조(?:의\d+)?$", norm):
        return re.compile(rf"^\s*{re.escape(norm)}(?=\s*\(|\s*$|\s*\n)", re.MULTILINE)
    # `별표2`, `별표 2`
    m = re.match(r"^별표\s*(\d+)$", norm)
    if m:
        return re.compile(rf"^[\s■◎●]*별표\s*{m.group(1)}(?=\s*$|\s*\n|\s*\()", re.MULTILINE)
    if norm == "별표":
        return re.compile(r"^[\s■◎●]*별표(?!\s*\d)(?=\s*$|\s*\n|\s*\()", re.MULTILINE)
    if norm in ("부칙", "부 칙"):
        return re.compile(r"^\s*부\s*칙(?=\s*$|\s*\n|\s*\()", re.MULTILINE)
    # 그 외는 사용자 입력을 그대로 escape — 실패해도 fail-soft
    return re.compile(re.escape(article_no or ""), re.MULTILINE)


# 모든 조문 헤더 매칭용 (다음 조문 시작점 찾기)
_ANY_ARTICLE_HEADER_RE = re.compile(
    r"^\s*(제\d+조(?:의\d+)?)(?=\s*\(|\s*$|\s*\n)"
    r"|^[\s■◎●]*(별표\s*\d+)(?=\s*$|\s*\n|\s*\()"
    r"|^[\s■◎●]*(별표)(?!\s*\d)(?=\s*$|\s*\n|\s*\()"
    r"|^\s*(부\s*칙)(?=\s*$|\s*\n|\s*\()",
    re.MULTILINE,
)


# 매뉴얼/가이드 PDF 에서 article 헤더가 본문이 아닌 *목차/요약 표* 로만
# 잡힐 때 사용할 최소 본문 길이 임계치. 이보다 짧으면 search_text 폴백.
# 30~50자 짜리 목차 제목 한 줄만 잡히는 케이스를 거른다.
_ARTICLE_BODY_MIN_CHARS = 100


def _article_search_text_fallback(
    p: Path,
    article_no: str,
    cache: _DocCache,
    *,
    reason: str,
) -> dict[str, Any]:
    """`get_article` 실패/얕은 매칭 시 search_text 로 폴백.

    매뉴얼 PDF (chunker 1.4.0 부터 article_no="p.N" 으로 청킹) 같은 경우,
    본문 안에 `제10조(연구개발과제 ...)` 형태로만 등장하고 줄 시작 article
    헤더가 *목차 페이지* 에서만 잡힐 때 본 함수가 호출된다.

    반환 형식은 `get_article` 와 동일한 키셋을 유지해 호출 측 코드를 깨뜨리
    지 않는다. 추가 디버그 필드 `matched_via="search_text_fallback"` 와
    `fallback_reason`, `fallback_results` (검색된 페이지 목록) 를 채운다.
    """
    # 검색어 정규화 — "제10조" 같은 단순 형태로 본문 매칭. 줄시작 강제 X.
    norm = re.sub(r"\s+", "", article_no or "")
    # 조문 형태면 본문에 `제10조(` 형태로 자주 시작하므로 그대로 사용.
    # 별표/부칙은 정규식 escape 후 사용.
    if re.match(r"^제\d+조(?:의\d+)?$", norm):
        query = norm  # "제10조"
    else:
        query = article_no or ""

    results = search_text(p.name, query, max_results=5)
    if not results:
        return {
            "doc_name": p.name,
            "article_no": article_no,
            "text": "",
            "start_page": 0,
            "end_page": 0,
            "error": (
                f"조문 헤더 매칭 실패 + search_text 폴백도 0건. "
                f"이 문서에 '{article_no}' 가 등장하지 않습니다."
            ),
            "matched_via": "search_text_fallback",
            "fallback_reason": reason,
        }

    # 검색된 페이지들의 본문을 합쳐서 반환. 첫 매칭 페이지를 start_page,
    # 마지막 매칭 페이지를 end_page 로.
    pages_seen = sorted({r["page"] for r in results})
    start_page = pages_seen[0]
    end_page = pages_seen[-1]

    # 본문 — 매칭된 페이지들의 *발췌* 가 아니라 페이지 본문 그대로 합쳐야
    # Claude 가 조문 본문을 풀어 쓸 수 있다. 단, 페이지가 너무 많으면 token
    # 폭주 위험이 있어 최대 3페이지까지로 제한.
    text_parts: list[str] = []
    for pg in pages_seen[:3]:
        if cache.doc_type == "pdf" and 1 <= pg <= len(cache.pages):
            text_parts.append(f"=== p.{pg} ===\n{cache.pages[pg - 1] or ''}")
        elif cache.doc_type == "hwp" and cache.pages:
            text_parts.append(cache.pages[0] or "")
            break

    body = "\n\n".join(text_parts).strip()

    return {
        "doc_name": p.name,
        "article_no": article_no,
        "text": body,
        "start_page": start_page,
        "end_page": end_page,
        "char_count": len(body),
        "matched_via": "search_text_fallback",
        "fallback_reason": reason,
        "fallback_results": results,
    }


def get_article(doc_name: str, article_no: str) -> dict[str, Any]:
    """`제N조` (또는 `별표 N`) 본문 + 시작/끝 페이지.

    chunker 가 split 시 사용하는 같은 `^` + 후행 컨텍스트 규칙을 단일
    article_no 매칭 형태로 재사용한다. 인라인 참조 (`[별표1]을 따름`,
    `법 제32조 ...`) 는 줄 시작이 아니므로 매칭되지 않는다.

    Phase H+1 — 매뉴얼/가이드 PDF (chunker 1.4.0 article_no="p.N") 의 경우
    줄 시작 헤더가 *목차 페이지* 에만 등장해 30자짜리 제목만 잡히거나
    아예 매칭 실패하는 일이 잦다. 이런 케이스를 위해 두 단계 폴백:
        1) 헤더 정규식 매칭이 0건 → search_text 로 본문 등장 페이지 회수
        2) 매칭은 됐지만 본문이 < `_ARTICLE_BODY_MIN_CHARS` 자
           → search_text 폴백 (목차에서만 잡힌 사례 회피)
    폴백 응답은 동일 키셋 + `matched_via="search_text_fallback"` 디버그 플래그.
    """
    print(f"[local_doc_mcp] get_article doc={doc_name!r} article={article_no!r}", file=sys.stderr)

    p = _find_doc_path(doc_name)
    if p is None:
        return {
            "doc_name": doc_name, "article_no": article_no,
            "text": "", "start_page": 0, "end_page": 0,
            "error": f"문서를 찾을 수 없습니다: {doc_name!r}",
        }
    cache = _load_pages(p)
    if not cache.pages:
        return {
            "doc_name": p.name, "article_no": article_no,
            "text": "", "start_page": 0, "end_page": 0,
            "error": "파일 파싱 실패 또는 빈 문서",
        }

    # 페이지를 이어붙이며 페이지별 시작 offset 도 기록 → offset → page 변환
    full_text_parts: list[str] = []
    boundaries: list[tuple[int, int]] = []  # (offset, page_num)
    offset = 0
    for idx, page_text in enumerate(cache.pages, start=1):
        boundaries.append((offset, idx))
        full_text_parts.append(page_text or "")
        offset += len(page_text or "") + 1  # +1 for joiner '\n'
    full_text = "\n".join(full_text_parts)

    pat = _article_regex(article_no)
    m = pat.search(full_text)
    if m is None:
        # Phase H+1 폴백 단계 1: 헤더 매칭 0건 → search_text 로 본문 회수.
        print(
            f"[local_doc_mcp] get_article: 헤더 매칭 0건, search_text 폴백 진입 "
            f"(doc={p.name!r}, article={article_no!r})",
            file=sys.stderr,
        )
        return _article_search_text_fallback(
            p, article_no, cache, reason="header_no_match",
        )

    start_off = m.start()
    # 다음 조문 헤더 시작 offset
    next_match = _ANY_ARTICLE_HEADER_RE.search(full_text, m.end())
    end_off = next_match.start() if next_match else len(full_text)

    body = full_text[start_off:end_off].strip()

    def _off_to_page(off: int) -> int:
        page = 1
        for s, pn in boundaries:
            if off >= s:
                page = pn
            else:
                break
        return page

    start_page = _off_to_page(start_off)
    end_page = _off_to_page(max(start_off, end_off - 1))

    if cache.doc_type == "hwp":
        # HWP 는 페이지 경계가 없으므로 1 로 통일
        start_page = 1
        end_page = 1

    # Phase H+1 폴백 단계 2: 헤더는 잡혔지만 본문이 임계치 미만 →
    # 매뉴얼 PDF 의 *목차 페이지* 에서만 잡힌 사례. search_text 폴백.
    if len(body) < _ARTICLE_BODY_MIN_CHARS:
        print(
            f"[local_doc_mcp] get_article: 매칭 본문 {len(body)}자 < {_ARTICLE_BODY_MIN_CHARS}자, "
            f"search_text 폴백 진입 (doc={p.name!r}, article={article_no!r}, "
            f"shallow_page={start_page})",
            file=sys.stderr,
        )
        return _article_search_text_fallback(
            p, article_no, cache, reason="body_too_short",
        )

    return {
        "doc_name": p.name,
        "article_no": article_no,
        "text": body,
        "start_page": start_page,
        "end_page": end_page,
        "char_count": len(body),
    }


def list_articles(doc_name: str) -> list[dict[str, Any]]:
    """문서 안 모든 조문 헤더 목록 (article_no, page).

    chunker 가 사용하는 동일 패턴으로 매칭. 매뉴얼처럼 본문 안에 인라인
    참조가 많은 문서도 줄 시작 강제 덕에 노이즈 적게 잡힌다.
    """
    print(f"[local_doc_mcp] list_articles doc={doc_name!r}", file=sys.stderr)

    p = _find_doc_path(doc_name)
    if p is None:
        return []
    cache = _load_pages(p)
    if not cache.pages:
        return []

    out: list[dict[str, Any]] = []
    for idx, page_text in enumerate(cache.pages, start=1):
        if not page_text:
            continue
        for m in _ANY_ARTICLE_HEADER_RE.finditer(page_text):
            article = next((g for g in m.groups() if g), "").strip()
            if not article:
                continue
            out.append({
                "doc_name": p.name,
                "article_no": article,
                "page": idx if cache.doc_type == "pdf" else 1,
            })
    return out


# ──────────────────────────────────────────────────────────────────
# Claude Agent SDK MCP 서버 빌드
# ──────────────────────────────────────────────────────────────────


def build_local_doc_server():
    """in-process MCP 서버 인스턴스를 만들어 반환.

    `ClaudeAgentOptions(mcp_servers={"local_doc": <this>}, allowed_tools=TOOL_NAMES)`
    형태로 사용한다. SDK 가 매 turn 마다 도구 핸들러를 동일 프로세스에서 직접
    호출하므로 IPC 가 없다.
    """
    from claude_agent_sdk import create_sdk_mcp_server, tool

    # 각 도구 핸들러는 SDK 규약에 따라
    #   async def handler(args: dict) -> {"content": [{"type":"text","text": ...}], ...}
    # 형태여야 한다. JSON 직렬화는 SDK 가 텍스트로 처리하면 되므로 간단히
    # `repr`/`json.dumps` 로 응답 안에 담는다.
    import json

    def _ok(payload: Any) -> dict[str, Any]:
        return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]}

    @tool(
        "list_documents",
        "프로젝트가 인식하는 PDF/HWP 문서 목록을 반환한다. doc_name 후보를 알아낼 때 먼저 호출.",
        {},
    )
    async def _list_documents(args: dict) -> dict:
        return _ok(list_documents())

    @tool(
        "read_page",
        "지정한 PDF 문서의 1-indexed page_num 페이지 텍스트를 반환한다. "
        "사용자가 'N페이지 / Np / N쪽' 의 *본문* 을 요구하면 호출하라. "
        "HWP 는 페이지 경계가 없어 전체 본문을 돌려준다.",
        {"doc_name": str, "page_num": int},
    )
    async def _read_page(args: dict) -> dict:
        return _ok(read_page(args.get("doc_name", ""), int(args.get("page_num", 0))))

    @tool(
        "search_text",
        "문서 안에서 키워드/정규식으로 검색해 매칭 페이지·발췌를 최대 max_results 개 반환. "
        "특정 페이지를 모를 때 후보 페이지를 좁히는 용도.",
        {"doc_name": str, "query": str, "max_results": int},
    )
    async def _search_text(args: dict) -> dict:
        return _ok(search_text(
            args.get("doc_name", ""),
            args.get("query", ""),
            int(args.get("max_results", 5)),
        ))

    @tool(
        "get_article",
        "지정한 조문(예: '제15조', '제15조의2', '별표 2', '부칙') 의 본문을 시작/끝 페이지와 함께 반환. "
        "사용자가 조문 본문을 요청했을 때 호출.",
        {"doc_name": str, "article_no": str},
    )
    async def _get_article(args: dict) -> dict:
        return _ok(get_article(args.get("doc_name", ""), args.get("article_no", "")))

    @tool(
        "list_articles",
        "문서 안 모든 조문/별표/부칙 헤더 목록 (article_no, page).",
        {"doc_name": str},
    )
    async def _list_articles(args: dict) -> dict:
        return _ok(list_articles(args.get("doc_name", "")))

    return create_sdk_mcp_server(
        name=_SERVER_NAME,
        version="1.0.0",
        tools=[_list_documents, _read_page, _search_text, _get_article, _list_articles],
    )
