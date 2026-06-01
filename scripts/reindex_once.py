"""Qdrant 초기화 후 law/ 폴더 PDF를 1회만 인덱싱.

중복 청크 문제 해결용. sync UI 우회 — 직접 _index_one + save_metadata.
"""
from __future__ import annotations

import hashlib
import json
import sys
import io
import unicodedata
from datetime import datetime
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

ROOT = Path(".").resolve()

from pipeline.chunker import CHUNKER_VERSION
from pipeline.embedder import EMBEDDER_VERSION
from pipeline.sync import _index_one, save_metadata, METADATA_PATH

def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s) if s else ""

# ── 1. qdrant_storage 디렉토리 확인 ──────────────────────────────
print("[1/3] qdrant_storage 확인...")
qs = ROOT / "qdrant_storage"
if qs.exists():
    print(f"  → 경고: {qs} 가 아직 있습니다. 앱 종료 후 직접 삭제하세요.")
    sys.exit(1)
print("  → 깨끗한 상태 확인")

# ── 2. file_hashes.json 초기화 ────────────────────────────────────
print("[2/3] file_hashes.json 초기화...")
METADATA_PATH.write_text("{}", encoding="utf-8")

# ── 3. PDF 1회 인덱싱 ────────────────────────────────────────────
print("[3/3] law/ 폴더 PDF 인덱싱 시작...")
law_dir = ROOT / "law"
pdfs = sorted(law_dir.glob("*.pdf"))
print(f"  대상: {len(pdfs)}개 PDF")

meta: dict = {}
now = datetime.now().isoformat(timespec="seconds")
total_chunks = 0

for i, pdf_path in enumerate(pdfs, 1):
    print(f"  [{i}/{len(pdfs)}] {pdf_path.name} ...")
    key = nfc(str(pdf_path.resolve()))
    try:
        result = _index_one(pdf_path)
        if result is None or result.get("_status") != "ok":
            status = result.get("_status") if result else "None"
            print(f"    → 건너뜀 ({status})")
            continue
        n = len(result["chunk_ids"])
        total_chunks += n
        stat = pdf_path.stat()
        meta[key] = {
            "mtime": stat.st_mtime,
            "sha256": "",  # 빠른 실행을 위해 sha256 생략 (sync가 mtime으로 먼저 비교)
            "size_bytes": stat.st_size,
            "chunker_version": CHUNKER_VERSION,
            "embedder_version": EMBEDDER_VERSION,
            "chunk_ids": result["chunk_ids"],
            "indexed_at": now,
            "doc_type": result.get("doc_type", ""),
            "page_count": result.get("page_count", 0),
            "indexed": True,
        }
        print(f"    → {n}청크  {result.get('page_count', 0)}p")
    except Exception as e:
        print(f"    → 오류: {e}")

save_metadata(meta)
print(f"\n완료: {len(meta)}개 파일, 총 {total_chunks}청크")
print(f"file_hashes.json: {METADATA_PATH}")
