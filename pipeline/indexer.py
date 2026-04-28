import os
import uuid

from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct

from pipeline.embedder import EmbeddedChunk, EMBED_DIM

load_dotenv()

_QDRANT_PATH = os.getenv("QDRANT_PATH", "./qdrant_storage")
_COLLECTION = os.getenv("QDRANT_COLLECTION", "rnd_law_chunks")
_UPSERT_BATCH = 100


def _get_client() -> QdrantClient:
    return QdrantClient(path=_QDRANT_PATH)


def ensure_collection(recreate: bool = False) -> None:
    """컬렉션이 없으면 생성. recreate=True면 삭제 후 재생성."""
    client = _get_client()
    existing = [c.name for c in client.get_collections().collections]

    if recreate and _COLLECTION in existing:
        client.delete_collection(_COLLECTION)
        print(f"  기존 컬렉션 '{_COLLECTION}' 삭제됨")
        existing = []

    if _COLLECTION not in existing:
        client.create_collection(
            collection_name=_COLLECTION,
            vectors_config=VectorParams(size=EMBED_DIM, distance=Distance.COSINE),
        )
        print(f"  컬렉션 '{_COLLECTION}' 생성됨 (dim={EMBED_DIM}, distance=Cosine)")
    else:
        info = client.get_collection(_COLLECTION)
        print(f"  컬렉션 '{_COLLECTION}' 이미 존재 - 포인트 수: {info.points_count}")


def upsert_chunks(chunks_meta: list[dict], embedded: list[EmbeddedChunk]) -> int:
    """청크 메타데이터 + 임베딩을 Qdrant에 upsert. 적재 포인트 수 반환."""
    ensure_collection()
    client = _get_client()

    embed_map = {e.chunk_id: e.embedding for e in embedded}

    points = []
    for meta in chunks_meta:
        cid = meta["chunk_id"]
        if cid not in embed_map:
            print(f"  [경고] 임베딩 없는 청크 건너뜀: {cid}")
            continue
        points.append(PointStruct(
            id=str(uuid.UUID(cid)),
            vector=embed_map[cid],
            payload=meta,
        ))

    total = len(points)
    upserted = 0
    for i in range(0, total, _UPSERT_BATCH):
        batch = points[i:i + _UPSERT_BATCH]
        client.upsert(collection_name=_COLLECTION, points=batch)
        upserted += len(batch)
        print(f"  upsert {upserted}/{total}", end="\r", flush=True)

    print(f"\n  Qdrant 적재 완료 - {upserted}개 포인트")
    return upserted


def get_collection_count() -> int:
    client = _get_client()
    return client.get_collection(_COLLECTION).points_count
