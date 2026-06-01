"""file_hashes.json을 현재 Qdrant + 디스크 상태로 재구성.

동기화 후 file_hashes.json이 비어 있을 때 실행.
- Qdrant의 청크를 source_file별로 그룹핑
- 실제 파일을 law/ 디렉토리에서 찾아 mtime/sha256/size 기록
- chunker_version/embedder_version은 현재 코드 상수값 사용
→ 다음 sync에서 unchanged로 인식해 재인덱싱하지 않음
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

ROOT = Path(__file__).resolve().parent.parent

from pipeline.chunker import CHUNKER_VERSION
from pipeline.embedder import EMBEDDER_VERSION
from qdrant_client import QdrantClient

def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s) if s else ""

def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while buf := f.read(1 << 20):
            h.update(buf)
    return h.hexdigest()

client = QdrantClient(path=str(ROOT / "qdrant_storage"))
points = []
offset = None
while True:
    batch, next_offset = client.scroll(
        "rnd_law_chunks", limit=1000, offset=offset,
        with_payload=True, with_vectors=False
    )
    points.extend(batch)
    if next_offset is None:
        break
    offset = next_offset
client.close()

print(f"Qdrant 청크 수: {len(points)}")

# source_file별 그룹핑
by_src: dict[str, list] = {}
for p in points:
    src = nfc(p.payload.get("source_file", ""))
    if src:
        by_src.setdefault(src, []).append(p)

print(f"source_file 종류: {len(by_src)}")

# law/ 디렉토리의 실제 PDF 파일 맵 (basename → Path)
law_dir = ROOT / "law"
pdf_map: dict[str, Path] = {}
for f in law_dir.rglob("*.pdf"):
    pdf_map[nfc(f.name)] = f

print(f"law/ 내 PDF: {len(pdf_map)}개")

meta: dict = {}
now = datetime.now().isoformat(timespec="seconds")

for src, recs in by_src.items():
    src_name = nfc(Path(src).name)
    path = pdf_map.get(src_name)

    if path is None or not path.exists():
        print(f"  [경고] 파일 미발견: {src_name}")
        meta[src] = {
            "mtime": 0.0, "sha256": "", "size_bytes": 0,
            "chunker_version": CHUNKER_VERSION,
            "embedder_version": EMBEDDER_VERSION,
            "chunk_ids": [str(r.id) for r in recs],
            "indexed_at": now,
            "doc_type": nfc(recs[0].payload.get("doc_type", "")),
            "page_count": 0,
            "indexed": False, "missing_on_disk": True,
        }
        continue

    stat = path.stat()
    sha = sha256_of(path)
    pages = [r.payload.get("page", 1) for r in recs if r.payload.get("page") is not None]
    page_count = max(pages) if pages else 0
    key = nfc(str(path.resolve()))

    meta[key] = {
        "mtime": stat.st_mtime,
        "sha256": sha,
        "size_bytes": stat.st_size,
        "chunker_version": CHUNKER_VERSION,
        "embedder_version": EMBEDDER_VERSION,
        "chunk_ids": [str(r.id) for r in recs],
        "indexed_at": now,
        "doc_type": nfc(recs[0].payload.get("doc_type", "")),
        "page_count": page_count,
        "indexed": True,
    }
    print(f"  {len(recs):4d}청크  {page_count:4d}p  {path.name}")

out_path = ROOT / "data" / "metadata" / "file_hashes.json"
out_path.write_text(
    json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True),
    encoding="utf-8"
)
print(f"\nfile_hashes.json 저장 완료: {len(meta)}개 항목")
