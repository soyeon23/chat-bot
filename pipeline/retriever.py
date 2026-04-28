import os

from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue

load_dotenv()

_QDRANT_PATH = os.getenv("QDRANT_PATH", "./qdrant_storage")
_COLLECTION = os.getenv("QDRANT_COLLECTION", "rnd_law_chunks")


def get_qdrant_client() -> QdrantClient:
    return QdrantClient(path=_QDRANT_PATH)


def search_chunks(
    query_vector: list[float],
    top_k: int = 8,
    doc_type: str | None = None,
) -> list[dict]:
    """
    Qdrant 로컬 파일 모드에서 벡터 유사도 검색을 수행한다.

    Args:
        query_vector: 질문 임베딩 벡터
        top_k: 반환할 최대 결과 수
        doc_type: 문서 유형 필터 (예: "법률", "운영요령"). None이면 전체 검색.

    Returns:
        검색 결과 dict 리스트. 결과 없으면 빈 리스트.
    """
    client = get_qdrant_client()

    query_filter = None
    if doc_type is not None:
        query_filter = Filter(
            must=[FieldCondition(key="doc_type", match=MatchValue(value=doc_type))]
        )

    response = client.query_points(
        collection_name=_COLLECTION,
        query=query_vector,
        limit=top_k,
        query_filter=query_filter,
        with_payload=True,
    )

    results = []
    for point in response.points:
        payload = point.payload or {}
        results.append({
            "score": point.score,
            "text": payload.get("text", ""),
            "document_name": payload.get("doc_name", ""),
            "document_type": payload.get("doc_type", ""),
            "article_no": payload.get("article_no", ""),
            "article_title": payload.get("article_title", ""),
            "page": payload.get("page", 0),
            "effective_date": payload.get("effective_date", ""),
            "file_name": payload.get("source_file", ""),
        })

    return results
