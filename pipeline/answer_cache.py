"""답변 캐시 — (rewritten_query + 필터 + 모델 + 인덱스 버전) 키로 응답 보관.

목적: 같은 질문 재요청 시 retrieval + generate 를 모두 스킵해 0.1초 안에 응답.
범위: prior_turns 가 빈 단발 질의만. 멀티턴은 컨텍스트가 매번 달라 캐시 효과
미미하고 키 폭발 위험이 있어 제외.

저장:
    data/answer_cache/<sha>.json
    {
      "key_repr": "...",        # 디버그용 입력 요약
      "saved_at": "ISO",
      "result": {...},          # AnswerPayload dict (or chat payload)
      "confidence": float,
      "web_used": bool,
      "ctx_stats": {...},
    }
무효화:
    chunker.CHUNKER_VERSION 변경 시 디렉토리 통째로 비움 (자동, init 시 1회).
    - 인덱스 자체가 재구성됐을 때 stale 답변이 살아남는 것 방지.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

_CACHE_DIR = Path("data/answer_cache")
_VERSION_MARKER = _CACHE_DIR / ".chunker_version"


def _ensure_cache_dir() -> None:
    """캐시 디렉토리 생성 + chunker 버전 변경 시 통째로 무효화."""
    try:
        from pipeline.chunker import CHUNKER_VERSION
    except Exception:
        CHUNKER_VERSION = "unknown"

    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    saved = ""
    if _VERSION_MARKER.exists():
        try:
            saved = _VERSION_MARKER.read_text(encoding="utf-8").strip()
        except OSError:
            saved = ""
    if saved != CHUNKER_VERSION:
        # 버전 바뀜 → 기존 캐시 삭제
        for f in _CACHE_DIR.glob("*.json"):
            try:
                f.unlink()
            except OSError:
                pass
        try:
            _VERSION_MARKER.write_text(CHUNKER_VERSION, encoding="utf-8")
        except OSError:
            pass


@dataclass
class CacheEntry:
    result: dict
    confidence: float
    web_used: bool
    ctx_stats: dict


def _cache_key(
    *,
    query: str,
    doc_type_filter: Optional[str],
    use_mcp: bool,
    use_web: bool,
    claude_model: str,
    kind: str = "",
    doc_hint: str = "",
    prior_turns: Optional[list] = None,  # 호환용 — 무시.
) -> tuple[str, str]:
    """캐시 키. analyzer 변동(rewritten_query 미세 변화) 에 강하도록
    *사용자 원문* + *의도 구조* (kind + doc_hint) 를 사용.

    Returns:
        (sha_hex, key_repr) — 파일명용 해시 + 디버그용 원본 표현
    """
    parts = [
        f"q={query.strip()}",
        f"dt={doc_type_filter or ''}",
        f"mcp={int(bool(use_mcp))}",
        f"web={int(bool(use_web))}",
        f"model={claude_model or ''}",
        f"kind={kind or ''}",
        f"doc={(doc_hint or '').strip()}",
    ]
    repr_str = "|".join(parts)
    sha = hashlib.sha256(repr_str.encode("utf-8")).hexdigest()
    return sha, repr_str


def get(
    *,
    query: str,
    doc_type_filter: Optional[str],
    use_mcp: bool,
    use_web: bool,
    claude_model: str,
    kind: str = "",
    doc_hint: str = "",
    prior_turns: Optional[list] = None,
) -> Optional[CacheEntry]:
    """캐시 조회. 미스 시 None.

    멀티턴도 적중 가능 — 같은 질의 + 같은 직전 대화 해시 조합이면 hit.
    """
    if os.getenv("DISABLE_ANSWER_CACHE") == "1":
        return None

    _ensure_cache_dir()
    sha, _ = _cache_key(
        query=query,
        doc_type_filter=doc_type_filter,
        use_mcp=use_mcp,
        use_web=use_web,
        claude_model=claude_model,
        kind=kind,
        doc_hint=doc_hint,
        prior_turns=prior_turns,
    )
    path = _CACHE_DIR / f"{sha}.json"
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"[answer_cache] read 실패 {sha[:8]}: {exc}", file=sys.stderr)
        return None
    return CacheEntry(
        result=raw.get("result") or {},
        confidence=float(raw.get("confidence") or 0.0),
        web_used=bool(raw.get("web_used") or False),
        ctx_stats=raw.get("ctx_stats") or {},
    )


def put(
    *,
    query: str,
    doc_type_filter: Optional[str],
    use_mcp: bool,
    use_web: bool,
    claude_model: str,
    entry: CacheEntry,
    kind: str = "",
    doc_hint: str = "",
    prior_turns: Optional[list] = None,
) -> None:
    """캐시 저장. 실패는 조용히 (캐시는 best-effort)."""
    if os.getenv("DISABLE_ANSWER_CACHE") == "1":
        return

    _ensure_cache_dir()
    sha, key_repr = _cache_key(
        query=query,
        doc_type_filter=doc_type_filter,
        use_mcp=use_mcp,
        use_web=use_web,
        claude_model=claude_model,
        kind=kind,
        doc_hint=doc_hint,
        prior_turns=prior_turns,
    )
    path = _CACHE_DIR / f"{sha}.json"
    payload = {
        "key_repr": key_repr,
        "saved_at": datetime.now().isoformat(timespec="seconds"),
        "result": entry.result,
        "confidence": entry.confidence,
        "web_used": entry.web_used,
        "ctx_stats": entry.ctx_stats,
    }
    try:
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except OSError as exc:
        print(f"[answer_cache] write 실패 {sha[:8]}: {exc}", file=sys.stderr)


def clear() -> int:
    """캐시 디렉토리 비우기. 삭제 파일 수 반환."""
    if not _CACHE_DIR.exists():
        return 0
    n = 0
    for f in _CACHE_DIR.glob("*.json"):
        try:
            f.unlink()
            n += 1
        except OSError:
            pass
    return n
