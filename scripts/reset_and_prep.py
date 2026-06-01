"""HWP 인덱스 제거 + PDF 재인덱싱 준비 스크립트.

실행:
    .venv\Scripts\python.exe scripts\reset_and_prep.py

작업 내용:
1. Qdrant 컬렉션 rnd_law_chunks 삭제
2. data/metadata/file_hashes.json → {} 초기화
3. data/chunks/ 의 HWP 유래 _chunks.json 삭제
4. data/metadata/ 의 HWP 유래 _metadata.csv 삭제
5. config.json pdf_dir → "law" 로 업데이트
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# ── 1. Qdrant 컬렉션 삭제 ──────────────────────────────────────────
print("[1/5] Qdrant 컬렉션 삭제...")
from qdrant_client import QdrantClient
client = QdrantClient(path=str(ROOT / "qdrant_storage"))
cols = {c.name for c in client.get_collections().collections}
if "rnd_law_chunks" in cols:
    client.delete_collection("rnd_law_chunks")
    print("  → rnd_law_chunks 삭제 완료")
else:
    print("  → 컬렉션 없음 (스킵)")
client.close()

# ── 2. file_hashes.json 초기화 ────────────────────────────────────
print("[2/5] file_hashes.json 초기화...")
hashes_path = ROOT / "data" / "metadata" / "file_hashes.json"
hashes_path.write_text("{}", encoding="utf-8")
print("  → 완료")

# ── 3. HWP 유래 chunk JSON 삭제 ───────────────────────────────────
HWP_KEYWORDS = [
    "별지",          # 시행규칙 별지 서식 10종
    "별표",          # 시행령 별표 7종
    "시행규칙",       # 시행규칙 본체
    "시행령",         # 시행령 본체
    "혁신법",         # 혁신법 본체 (있을 경우)
]
chunks_dir = ROOT / "data" / "chunks"
removed_chunks = []
for f in chunks_dir.glob("*_chunks.json"):
    if any(kw in f.name for kw in HWP_KEYWORDS):
        f.unlink()
        removed_chunks.append(f.name)

print(f"[3/5] HWP 유래 chunk JSON 삭제: {len(removed_chunks)}개")
for n in removed_chunks:
    print(f"  - {n}")

# ── 4. HWP 유래 metadata CSV 삭제 ────────────────────────────────
meta_dir = ROOT / "data" / "metadata"
removed_meta = []
for f in meta_dir.glob("*_metadata.csv"):
    if any(kw in f.name for kw in HWP_KEYWORDS):
        f.unlink()
        removed_meta.append(f.name)

print(f"[4/5] HWP 유래 metadata CSV 삭제: {len(removed_meta)}개")
for n in removed_meta:
    print(f"  - {n}")

# ── 5. config.json pdf_dir 업데이트 ──────────────────────────────
print("[5/5] config.json pdf_dir → 'law' 업데이트...")
config_path = ROOT / "data" / "config.json"
config = json.loads(config_path.read_text(encoding="utf-8"))
config["pdf_dir"] = "law"
config["hwp_dir"] = ""
config_path.write_text(
    json.dumps(config, ensure_ascii=False, indent=2),
    encoding="utf-8",
)
print("  → 완료")

print("\n완료! 이제 앱 환경설정 페이지에서 '인덱스 동기화'를 실행하세요.")
print(f"PDF 폴더: {ROOT / 'law'}")
pdfs = list((ROOT / "law").glob("*.pdf"))
print(f"인덱싱 대상 PDF: {len(pdfs)}개")
for p in pdfs:
    print(f"  - {p.name}")
