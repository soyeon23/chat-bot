"""인덱스 상태 확인 스크립트."""
import sys, io, json
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

from collections import Counter
from qdrant_client import QdrantClient

client = QdrantClient(path="./qdrant_storage")
info = client.get_collection("rnd_law_chunks")
print(f"총 청크 수: {info.points_count}")

doc_counts = Counter()
offset = None
while True:
    result, next_offset = client.scroll(
        "rnd_law_chunks", limit=1000, offset=offset,
        with_payload=True, with_vectors=False
    )
    for p in result:
        doc_counts[p.payload.get("doc_name", "?")] += 1
    if next_offset is None:
        break
    offset = next_offset

print()
print("문서별 청크 수:")
for k, v in sorted(doc_counts.items(), key=lambda x: -x[1]):
    print(f"  {v:4d}  {k}")

hashes = json.loads(open("data/metadata/file_hashes.json", encoding="utf-8").read())
print()
print(f"file_hashes.json 항목: {len(hashes)}개")
for path, rec in sorted(hashes.items(), key=lambda x: x[0]):
    fname = path.split("\\")[-1]
    pc = rec.get("page_count", 0)
    cc = len(rec.get("chunk_ids", []))
    print(f"  {pc:4d}p  {cc:4d}청크  {fname}")
