"""증분 동기화 (Incremental Sync) — Phase G4.

폴더에서 PDF/HWP 파일을 스캔해 *변경된 파일만* 재인덱싱한다.
Git처럼 mtime + sha256 + 코드 버전을 기록해 두고 비교한다.

핵심 함수:
    scan_changes(roots) -> dict
        파일 시스템 + 메타파일 + Qdrant 스냅샷을 비교해 added/modified/
        deleted/stale_code/unchanged 로 분류한다.

    apply_changes(changes, progress_callback) -> dict
        분류 결과를 받아 Qdrant 에서 청크를 삭제하거나 새로 인덱싱한다.

    init_metadata_from_qdrant() -> dict
        file_hashes.json 이 없을 때 Qdrant 의 source_file 별 청크들로부터
        초기 메타파일을 작성한다. 별표 6개 파일은 G2 산출물이므로
        chunker_version='1.2.0' 으로 마크하고, 그 외 파일은 'pre-G1'
        으로 마크해 다음 sync 에서 stale 처리되도록 한다.

데이터 스키마: data/metadata/file_hashes.json
    {
      "<source_file_absolute_path_NFC>": {
        "mtime": float,
        "sha256": str (hex),
        "size_bytes": int,
        "chunker_version": str,
        "embedder_version": str,
        "chunk_ids": [str, ...],
        "indexed_at": iso8601 str,
        "doc_type": str,
        "page_count": int,
        "indexed": bool        # False 면 sync 가 시도하지 않음 (HWPML 등)
      },
      ...
    }

설계 메모:
- 파일 식별자는 *절대경로 NFC 정규화*. macOS APFS 가 한글 파일명을
  NFD 로 보존하지만 우리 코드는 NFC 로 통일.
- HWPML 본체 3종 (혁신법.hwp / 시행령.hwp / 시행규칙.hwp) 은 hwp-mcp 가
  거부하므로 init 시 indexed=False 로 마크하고 다음 sync 에서 건너뛴다.
- 코드 버전 변경 감지: chunker_version 또는 embedder_version 이 현재
  코드의 상수와 다르면 stale_code 로 분류한다.
- Qdrant 청크 삭제: PointIdsList 사용. file_hashes.json 의 chunk_ids 를
  배치(100건) 로 끊어 삭제한다.
- 인덱싱 핵심 로직은 batch_ingest.ingest_one 의 단계를 재사용 — 거기서
  파싱→청킹→임베딩→업서트를 모두 하므로, sync 는 그 함수를 직접 부르는 게
  아니라 동등한 단계를 mtime/sha256/chunk_ids 기록과 함께 수행한다.
"""
from __future__ import annotations

import hashlib
import json
import time
import unicodedata
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

# 모듈 import 는 가벼운 것만 top-level. 실제 인덱싱 시점에 무거운 모듈을 lazy import.
from pipeline.chunker import CHUNKER_VERSION
from pipeline.embedder import EMBEDDER_VERSION


# ──────────────────────────────────────────────────────────────────
# 경로/상수
# ──────────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parent.parent
METADATA_PATH = PROJECT_ROOT / "data" / "metadata" / "file_hashes.json"
QDRANT_PATH = "./qdrant_storage"
COLLECTION = "rnd_law_chunks"

# 인덱싱 대상 확장자
PDF_EXTS = {".pdf"}
HWP_EXTS = {".hwp", ".hwpx"}
SUPPORTED_EXTS = PDF_EXTS | HWP_EXTS

# 폴더 스캔 시 제외할 경로 조각 (data/, .git, .venv 등)
EXCLUDE_PARTS = {
    "versions", "chunks", "raw", "metadata",
    ".git", ".venv", "__pycache__", "qdrant_storage",
    ".planning",
}

# 1KB 미만 파일은 빈 파일로 간주
MIN_FILE_SIZE = 1000


# ──────────────────────────────────────────────────────────────────
# 헬퍼
# ──────────────────────────────────────────────────────────────────

def _nfc(s: str) -> str:
    """한글 파일명/경로 NFC 정규화. macOS APFS NFD ↔ NFC 차이 흡수."""
    return unicodedata.normalize("NFC", s) if s else ""


def _abs_nfc(p: Path) -> str:
    """파일 식별자 — 절대경로 + NFC."""
    return _nfc(str(p.resolve()))


def _sha256_of(path: Path, chunk_size: int = 1 << 20) -> str:
    """파일 sha256 (1MB chunk streaming)."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            buf = f.read(chunk_size)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()


def _is_supported(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_EXTS


def _excluded_by_parts(path: Path) -> bool:
    return bool(EXCLUDE_PARTS.intersection(path.parts))


def _iter_files(roots: Iterable[Path]) -> list[Path]:
    """루트 폴더들을 재귀 스캔 — 지원 확장자 + 사이즈 OK 인 파일 반환.

    중복 절대경로는 한 번만 포함. 정렬 — sync 출력의 결정성 위해.
    """
    seen: set[str] = set()
    out: list[Path] = []
    for root in roots:
        root = Path(root).expanduser()
        if not root.exists():
            continue
        for p in root.rglob("*"):
            if not p.is_file():
                continue
            if not _is_supported(p):
                continue
            if _excluded_by_parts(p):
                continue
            try:
                if p.stat().st_size < MIN_FILE_SIZE:
                    continue
            except OSError:
                continue
            key = _abs_nfc(p)
            if key in seen:
                continue
            seen.add(key)
            out.append(p)
    return sorted(out, key=_abs_nfc)


# ──────────────────────────────────────────────────────────────────
# 메타파일 I/O
# ──────────────────────────────────────────────────────────────────

def load_metadata(path: Path = METADATA_PATH) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_metadata(meta: dict, path: Path = METADATA_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


# ──────────────────────────────────────────────────────────────────
# init: Qdrant → file_hashes.json
# ──────────────────────────────────────────────────────────────────

def _scroll_all(client) -> list:
    """Qdrant 컬렉션 전체 scroll."""
    out = []
    nxt = None
    while True:
        batch, nxt = client.scroll(
            collection_name=COLLECTION,
            limit=500,
            offset=nxt,
            with_payload=True,
            with_vectors=False,
        )
        out.extend(batch)
        if nxt is None:
            break
    return out


def _resolve_source_path(source_file: str, roots: list[Path]) -> Path | None:
    """payload.source_file 값(파일명만)을 실제 절대경로로 해석.

    인덱싱 시 chunker 가 source_file 에 *파일명*만 저장하는 경우와
    *상대경로* 또는 *절대경로* 를 저장하는 경우가 섞여 있다 (HWP 는 .name,
    PDF 는 path 통째). 둘 다 처리하기 위해 roots 안에서 basename 매칭으로
    실제 파일을 찾아낸다.

    macOS APFS 는 한글 파일명을 NFD 로 보존한다. `Path.rglob(name_nfc)` 는
    NFD 디스크 엔트리와 직접 매칭이 안 되므로, 확장자 글로브로 후보를 모은
    뒤 NFC 비교한다.

    찾지 못하면 None — 호출자가 이를 indexed=False/missing 으로 처리.
    """
    if not source_file:
        return None
    src_nfc = _nfc(source_file)
    # 절대경로면 그대로
    p = Path(src_nfc)
    if p.is_absolute() and p.exists():
        return p

    target_name_nfc = _nfc(p.name)
    target_ext = p.suffix.lower()
    if not target_ext:
        return None

    glob_pattern = f"*{target_ext}"
    for root in roots:
        root = Path(root).expanduser()
        if not root.exists():
            continue
        for cand in root.rglob(glob_pattern):
            if not cand.is_file():
                continue
            if _excluded_by_parts(cand):
                continue
            if _nfc(cand.name) == target_name_nfc:
                return cand
    return None


# 별표 6개 파일은 Phase G2 에서 새 chunker(1.2.0) 로 재인덱싱됨
_BYEOLPYO_GROUP_PREFIXES = ("[별표 1]", "[별표 2]", "[별표 4]", "[별표 5]", "[별표 6]", "[별표 7]")


def _initial_chunker_version_for(source_file: str) -> str:
    """init 시 source_file 이름으로 chunker_version 추정.

    별표 6개(시행령) 는 G2 산출물 → 1.2.0
    그 외(매뉴얼 PDF, 시행규칙 별지) 는 G1 이전 → 'pre-G1'
    'pre-G1' 은 현재 CHUNKER_VERSION 과 다르므로 다음 sync 에서 stale 처리됨.
    """
    name = _nfc(Path(source_file).name)
    for prefix in _BYEOLPYO_GROUP_PREFIXES:
        if name.startswith(prefix):
            return CHUNKER_VERSION  # 1.2.0
    return "pre-G1"


def _is_hwpml_file(path: Path) -> bool:
    """HWPML(XML) 포맷 사전 감지 — hwp-mcp(OLE2) 가 거부하므로 인덱싱 시도 회피용.

    실제 검사는 `pipeline.hwp_parser._is_hwpml` 와 동일하게 첫 8바이트가
    `<?xml` 로 시작하는지 본다. import 순환 회피를 위해 인라인 구현.
    """
    if path.suffix.lower() not in HWP_EXTS:
        return False
    try:
        with open(path, "rb") as f:
            return f.read(8).startswith(b"<?xml")
    except OSError:
        return False


def init_metadata_from_qdrant(roots: list[Path] | None = None) -> dict:
    """기존 Qdrant 인덱스에서 file_hashes.json 초기 데이터 추출.

    이미 인덱싱된 청크들을 source_file 별로 그룹핑해 메타파일을 작성한다.
    파일이 디스크에 존재하면 mtime/sha256/size 를 기록하고, 못 찾으면
    indexed=False 로 마크해 다음 sync 에서 시도하지 않게 한다.

    추가로, 디스크에 *있지만* 아직 인덱싱되지 않은 HWPML 본체(혁신법/시행령/
    시행규칙) 도 메타에 indexed=False 로 등록한다. F5 에서 LibreOffice
    headless 같은 우회로가 추가되기 전까지 sync 가 매번 무용한 시도를 안
    하도록 nudge.

    별표 6개(G2 산출물): chunker_version='1.2.0'
    그 외 인덱싱된 파일: chunker_version='pre-G1' → 다음 sync 에서 stale → 재인덱싱

    Args:
        roots: 파일 검색 경로 목록. None 이면 PROJECT_ROOT.

    Returns:
        새로 작성된 메타파일 dict (저장 후 반환).
    """
    from qdrant_client import QdrantClient

    if roots is None:
        roots = [PROJECT_ROOT]

    client = QdrantClient(path=QDRANT_PATH)
    try:
        records = _scroll_all(client)
    finally:
        client.close()

    # source_file 별로 그룹핑
    by_source: dict[str, list] = {}
    for r in records:
        src = _nfc(r.payload.get("source_file", ""))
        if not src:
            continue
        by_source.setdefault(src, []).append(r)

    meta: dict = {}
    now_iso = datetime.now().isoformat(timespec="seconds")
    for src, recs in by_source.items():
        path = _resolve_source_path(src, roots)
        if path is None or not path.exists():
            # 인덱싱은 되어 있지만 디스크에 파일이 없음 → indexed=False (HWPML 등)
            meta_key = src  # 절대경로 못 찾았으면 source_file 자체를 key 로
            meta[meta_key] = {
                "mtime": 0.0,
                "sha256": "",
                "size_bytes": 0,
                "chunker_version": _initial_chunker_version_for(src),
                "embedder_version": EMBEDDER_VERSION,
                "chunk_ids": [str(r.id) for r in recs],
                "indexed_at": now_iso,
                "doc_type": _nfc(recs[0].payload.get("doc_type", "")),
                "page_count": 0,
                "indexed": False,
                "missing_on_disk": True,
            }
            continue

        try:
            stat = path.stat()
            sha = _sha256_of(path)
        except OSError:
            stat = None
            sha = ""
        # page_count: payload 에 page 가 있으면 max
        pages = [r.payload.get("page", 1) for r in recs if r.payload.get("page") is not None]
        page_count = max(pages) if pages else 0

        key = _abs_nfc(path)
        meta[key] = {
            "mtime": stat.st_mtime if stat else 0.0,
            "sha256": sha,
            "size_bytes": stat.st_size if stat else 0,
            "chunker_version": _initial_chunker_version_for(src),
            "embedder_version": EMBEDDER_VERSION,
            "chunk_ids": [str(r.id) for r in recs],
            "indexed_at": now_iso,
            "doc_type": _nfc(recs[0].payload.get("doc_type", "")),
            "page_count": page_count,
            "indexed": True,
        }

    # 디스크에 있는데 인덱싱은 안 된 HWPML 본체(혁신법/시행령/시행규칙)는
    # indexed=False 로 사전 등록 — sync 매번 시도 회피.
    for path in _iter_files(roots):
        key = _abs_nfc(path)
        if key in meta:
            continue
        if not _is_hwpml_file(path):
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        meta[key] = {
            "mtime": stat.st_mtime,
            "sha256": _sha256_of(path),
            "size_bytes": stat.st_size,
            "chunker_version": "n/a",
            "embedder_version": EMBEDDER_VERSION,
            "chunk_ids": [],
            "indexed_at": now_iso,
            "doc_type": "",
            "page_count": 0,
            "indexed": False,
            "skip_reason": "hwpml_unsupported",
        }

    save_metadata(meta)
    return meta


# ──────────────────────────────────────────────────────────────────
# scan
# ──────────────────────────────────────────────────────────────────

def scan_changes(
    roots: list[Path] | None = None,
    metadata_path: Path = METADATA_PATH,
) -> dict:
    """폴더 스캔 + 메타파일 비교 → 변경 분류.

    Returns:
        {
            "added":       [Path, ...],   # 메타파일에 없는 파일
            "modified":    [Path, ...],   # mtime 또는 sha256 변경됨
            "deleted":     [str, ...],   # 메타파일에 있지만 디스크에 없음 (key)
            "stale_code":  [Path, ...],   # 코드 버전 변경 (chunker/embedder)
            "unchanged":   [Path, ...],   # 변경 없음
            "skipped":     [str, ...],   # indexed=False (HWPML 등) — sync 무시
        }
    """
    if roots is None:
        roots = [PROJECT_ROOT]

    meta = load_metadata(metadata_path)
    on_disk = _iter_files(roots)
    on_disk_keys = {_abs_nfc(p): p for p in on_disk}

    added: list[Path] = []
    modified: list[Path] = []
    stale_code: list[Path] = []
    unchanged: list[Path] = []
    skipped: list[str] = []

    for key, path in on_disk_keys.items():
        rec = meta.get(key)
        if rec is None:
            added.append(path)
            continue
        if not rec.get("indexed", True):
            skipped.append(key)
            continue
        # mtime 1차 비교 (저렴) — 다르면 sha256 검증
        try:
            cur_mtime = path.stat().st_mtime
            cur_size = path.stat().st_size
        except OSError:
            added.append(path)
            continue

        mtime_changed = abs(cur_mtime - rec.get("mtime", 0.0)) > 1e-3
        size_changed = cur_size != rec.get("size_bytes", -1)

        if mtime_changed or size_changed:
            cur_sha = _sha256_of(path)
            if cur_sha != rec.get("sha256", ""):
                modified.append(path)
                continue
            # 내용 동일 (touch 만 했음) — 내용 같으면 mtime 갱신만 후 unchanged
            # 그러나 코드 버전 비교는 별도로
        # 내용 동일 — 코드 버전 비교
        if (
            rec.get("chunker_version") != CHUNKER_VERSION
            or rec.get("embedder_version") != EMBEDDER_VERSION
        ):
            stale_code.append(path)
        else:
            unchanged.append(path)

    # deleted: 메타파일에 있지만 디스크에 없음, indexed=True 였던 것
    deleted: list[str] = []
    for key, rec in meta.items():
        if not rec.get("indexed", True):
            continue
        if rec.get("missing_on_disk"):
            continue
        # key 는 절대경로(NFC) — on_disk_keys 와 비교
        if key not in on_disk_keys:
            # key 가 절대경로가 아닐 수도 (init 시 missing_on_disk True 였던 경우 등) 보호
            if Path(key).is_absolute():
                deleted.append(key)

    return {
        "added": added,
        "modified": modified,
        "deleted": deleted,
        "stale_code": stale_code,
        "unchanged": unchanged,
        "skipped": skipped,
    }


# ──────────────────────────────────────────────────────────────────
# apply
# ──────────────────────────────────────────────────────────────────

def _delete_chunks(chunk_ids: list[str]) -> int:
    """Qdrant 에서 chunk_id 리스트로 청크 삭제. 삭제된 개수 반환."""
    if not chunk_ids:
        return 0

    from qdrant_client import QdrantClient
    from qdrant_client.http import models as qmodels

    client = QdrantClient(path=QDRANT_PATH)
    try:
        batch = 100
        for i in range(0, len(chunk_ids), batch):
            client.delete(
                collection_name=COLLECTION,
                points_selector=qmodels.PointIdsList(points=chunk_ids[i:i + batch]),
            )
    finally:
        client.close()
    return len(chunk_ids)


def _index_one(path: Path) -> dict | None:
    """단일 파일 인덱싱. file_hashes.json 에 저장할 메타 dict 반환.

    실패 시 None.
    """
    # batch_ingest 의 메타데이터 추출 함수 재사용 — 인덱싱 핵심 로직 변경 금지 정신.
    from batch_ingest import get_metadata, _is_hwp

    file_meta = get_metadata(path)

    if _is_hwp(path):
        from pipeline.hwp_parser import parse_hwp
        result = parse_hwp(path, save_raw=False)
    else:
        from pipeline.pdf_parser import parse_pdf, validate_parse_result
        result = parse_pdf(path, save_raw=False)
        validate_parse_result(result)

    if not result.pages or not result.full_text().strip():
        # HWPML 등 파싱 불가 — indexed=False 로 기록
        return {
            "_status": "parse_empty",
            "doc_type": file_meta["doc_type"],
            "page_count": 0,
        }

    from pipeline.chunker import chunk_document, save_chunks

    chunks = chunk_document(
        parse_result=result,
        doc_name=file_meta["doc_name"],
        doc_type=file_meta["doc_type"],
        effective_date=file_meta["effective_date"],
        revised_date=file_meta["revised_date"],
        is_current=file_meta["is_current"],
    )
    if not chunks:
        return {
            "_status": "chunk_empty",
            "doc_type": file_meta["doc_type"],
            "page_count": len(result.pages),
        }

    save_chunks(chunks, path.stem, PROJECT_ROOT)
    chunks_meta = [asdict(c) for c in chunks]

    from pipeline.embedder import embed_chunks, validate_embeddings
    embedded = embed_chunks(chunks_meta)
    validate_embeddings(embedded)

    from pipeline.indexer import upsert_chunks
    upsert_chunks(chunks_meta, embedded)

    return {
        "_status": "ok",
        "doc_type": file_meta["doc_type"],
        "page_count": len(result.pages),
        "chunk_ids": [c.chunk_id for c in chunks],
    }


def apply_changes(
    changes: dict,
    progress_callback: Callable[[int, int, str], None] | None = None,
    metadata_path: Path = METADATA_PATH,
) -> dict:
    """scan 결과에 따라 인덱스/메타파일 업데이트.

    동작 순서:
      1) deleted + modified + stale_code: Qdrant 에서 기존 chunk 삭제 → 메타에서 키 제거
      2) added + modified + stale_code: 파싱→청킹→임베딩→업서트 → 메타 갱신
      3) unchanged: 변경 없음 (mtime 만 갱신 — touch 케이스)

    Args:
        changes: scan_changes() 결과
        progress_callback: (i, total, message) 호출 — UI 진행률 표시용

    Returns:
        {
            "indexed":    int,  # 신규/재인덱싱된 파일 수
            "deleted":    int,  # 삭제된 파일 수 (Qdrant 청크 단위 아님)
            "errors":     [{"file": str, "error": str}, ...],
            "elapsed_sec": float,
            "skipped":    int,  # parse_empty, chunk_empty
        }
    """
    started = time.monotonic()
    meta = load_metadata(metadata_path)

    to_reindex: list[Path] = []
    to_reindex.extend(changes.get("added", []))
    to_reindex.extend(changes.get("modified", []))
    to_reindex.extend(changes.get("stale_code", []))
    to_delete: list[str] = list(changes.get("deleted", []))

    # 중복 제거 (added/modified/stale_code 사이 경로 중복 없을 거지만 보호)
    seen: set[str] = set()
    deduped: list[Path] = []
    for p in to_reindex:
        k = _abs_nfc(p)
        if k in seen:
            continue
        seen.add(k)
        deduped.append(p)
    to_reindex = deduped

    indexed = 0
    deleted = 0
    skipped = 0
    errors: list[dict] = []

    total = len(to_reindex) + len(to_delete)
    step = 0

    # 1) deleted: Qdrant 에서 청크 삭제 + 메타 제거
    for key in to_delete:
        step += 1
        rec = meta.get(key, {})
        chunk_ids = rec.get("chunk_ids", [])
        name = Path(key).name if Path(key).is_absolute() else key
        if progress_callback:
            progress_callback(step, total, f"삭제 중: {name}")
        try:
            _delete_chunks(chunk_ids)
            meta.pop(key, None)
            deleted += 1
        except Exception as e:  # noqa: BLE001
            errors.append({"file": key, "error": f"{type(e).__name__}: {e}"})

    # 2) added/modified/stale_code: 기존 청크 삭제 후 새로 인덱싱
    for path in to_reindex:
        step += 1
        key = _abs_nfc(path)
        if progress_callback:
            progress_callback(step, total, f"인덱싱 중: {path.name}")
        try:
            # 기존 청크가 있다면 먼저 삭제
            old_rec = meta.get(key)
            if old_rec and old_rec.get("chunk_ids"):
                _delete_chunks(old_rec["chunk_ids"])

            result = _index_one(path)
            if result is None:
                errors.append({"file": str(path), "error": "indexing returned None"})
                continue

            if result.get("_status") != "ok":
                # parse_empty / chunk_empty — indexed=False 로 메타 마크
                stat = path.stat()
                meta[key] = {
                    "mtime": stat.st_mtime,
                    "sha256": _sha256_of(path),
                    "size_bytes": stat.st_size,
                    "chunker_version": CHUNKER_VERSION,
                    "embedder_version": EMBEDDER_VERSION,
                    "chunk_ids": [],
                    "indexed_at": datetime.now().isoformat(timespec="seconds"),
                    "doc_type": result.get("doc_type", ""),
                    "page_count": result.get("page_count", 0),
                    "indexed": False,
                    "skip_reason": result.get("_status"),
                }
                skipped += 1
                continue

            # 정상 인덱싱
            stat = path.stat()
            meta[key] = {
                "mtime": stat.st_mtime,
                "sha256": _sha256_of(path),
                "size_bytes": stat.st_size,
                "chunker_version": CHUNKER_VERSION,
                "embedder_version": EMBEDDER_VERSION,
                "chunk_ids": result["chunk_ids"],
                "indexed_at": datetime.now().isoformat(timespec="seconds"),
                "doc_type": result.get("doc_type", ""),
                "page_count": result.get("page_count", 0),
                "indexed": True,
            }
            indexed += 1
        except Exception as e:  # noqa: BLE001
            import traceback
            errors.append({
                "file": str(path),
                "error": f"{type(e).__name__}: {e}",
                "traceback": traceback.format_exc(limit=3),
            })

    # 메타파일 저장
    save_metadata(meta, metadata_path)

    return {
        "indexed": indexed,
        "deleted": deleted,
        "skipped": skipped,
        "errors": errors,
        "elapsed_sec": time.monotonic() - started,
    }


# ──────────────────────────────────────────────────────────────────
# 진입점 (CLI)
# ──────────────────────────────────────────────────────────────────

def _summarize_scan(changes: dict) -> str:
    return (
        f"added={len(changes['added'])}, "
        f"modified={len(changes['modified'])}, "
        f"deleted={len(changes['deleted'])}, "
        f"stale_code={len(changes['stale_code'])}, "
        f"unchanged={len(changes['unchanged'])}, "
        f"skipped={len(changes['skipped'])}"
    )


def _cli_progress(i: int, total: int, msg: str) -> None:
    print(f"  [{i}/{total}] {msg}")


def _resolve_default_roots() -> list[Path]:
    """프로젝트 설정에서 기본 sync 루트를 결정한다."""
    roots: list[Path] = [PROJECT_ROOT]
    try:
        from pipeline.config_store import load_config
        cfg = load_config()
        if cfg.hwp_dir:
            extra = Path(cfg.hwp_dir).expanduser()
            if extra.exists() and extra.resolve() != PROJECT_ROOT.resolve():
                roots.append(extra)
    except Exception as e:  # noqa: BLE001
        print(f"  [경고] config 로드 실패 (기본 루트만 사용): {e}")
    return roots


# ──────────────────────────────────────────────────────────────────
# Streamlit pause/resume — sync CLI 가 실 인덱싱하는 동안 streamlit 이
# Qdrant 파일 모드를 점유하고 있으면 충돌난다. G2 패턴 그대로 SIGSTOP/SIGCONT.
# ──────────────────────────────────────────────────────────────────

def _pgrep_streamlit() -> list[int]:
    import subprocess
    try:
        out = subprocess.run(
            ["pgrep", "-f", "streamlit"],
            capture_output=True, text=True, check=False,
        )
        return [int(p) for p in out.stdout.strip().splitlines() if p.strip().isdigit()]
    except Exception:
        return []


def _stop_streamlit() -> list[int]:
    import os
    import signal
    pids = _pgrep_streamlit()
    if not pids:
        return []
    print(f"  [streamlit] 일시정지: {pids}")
    for pid in pids:
        try:
            os.kill(pid, signal.SIGSTOP)
        except (ProcessLookupError, PermissionError):
            pass
    return pids


def _cont_streamlit(pids: list[int]) -> None:
    import os
    import signal
    if not pids:
        return
    print(f"  [streamlit] 재개: {pids}")
    for pid in pids:
        try:
            os.kill(pid, signal.SIGCONT)
        except (ProcessLookupError, PermissionError):
            pass


def main() -> int:
    """CLI 모드: scan + apply 한 번에 실행. 결과 출력."""
    print("=" * 70)
    print("증분 동기화 (Phase G4)")
    print("=" * 70)

    roots = _resolve_default_roots()

    if not METADATA_PATH.exists():
        print("\nfile_hashes.json 없음 — Qdrant 에서 초기화...")
        meta = init_metadata_from_qdrant(roots=roots)
        print(f"  초기 메타 작성: {len(meta)}개 파일")

    print("\n[1/2] scan...")
    changes = scan_changes(roots=roots)
    print(f"  {_summarize_scan(changes)}")

    if not (changes["added"] or changes["modified"] or changes["deleted"] or changes["stale_code"]):
        print("\n변경 없음 — sync 종료.")
        return 0

    print("\n[2/2] apply...")
    streamlit_pids = _stop_streamlit()
    try:
        result = apply_changes(changes, progress_callback=_cli_progress)
    finally:
        _cont_streamlit(streamlit_pids)

    print("\n" + "=" * 70)
    print(f"완료: indexed={result['indexed']}, deleted={result['deleted']}, "
          f"skipped={result['skipped']}, errors={len(result['errors'])}, "
          f"elapsed={result['elapsed_sec']:.1f}s")
    if result["errors"]:
        print("\n오류 목록:")
        for e in result["errors"]:
            print(f"  - {e['file']}: {e['error']}")
    print("=" * 70)
    return 0 if not result["errors"] else 1


if __name__ == "__main__":
    import sys
    sys.exit(main())
