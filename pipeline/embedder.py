from __future__ import annotations

from dataclasses import dataclass

from langchain_huggingface import HuggingFaceEmbeddings

MODEL_NAME = "jhgan/ko-sroberta-multitask"
EMBED_DIM = 768

# 임베딩 모델 식별자. sync(증분 동기화) 가 모델 교체를 감지해 모든
# 파일을 stale 로 마크하는 데 쓴다. 모델명을 그대로 사용한다.
EMBEDDER_VERSION = MODEL_NAME

# 모듈 로드 시 한 번만 초기화 (첫 호출 때 모델 다운로드)
_embeddings: HuggingFaceEmbeddings | None = None


def _get_embeddings() -> HuggingFaceEmbeddings:
    global _embeddings
    if _embeddings is None:
        print(f"  임베딩 모델 로드 중: {MODEL_NAME} (최초 실행 시 다운로드 발생)")
        _embeddings = HuggingFaceEmbeddings(model_name=MODEL_NAME)
        print("  모델 로드 완료")
    return _embeddings


@dataclass
class EmbeddedChunk:
    chunk_id: str
    embedding: list[float]


def embed_query(text: str) -> list[float]:
    """질문 한 건을 임베딩한다."""
    return _get_embeddings().embed_query(text)


def embed_chunks(chunks: list[dict]) -> list[EmbeddedChunk]:
    """
    청크 리스트를 일괄 임베딩한다.

    Args:
        chunks: chunk_id, text 필드를 포함하는 dict 리스트

    Returns:
        EmbeddedChunk 리스트
    """
    if not chunks:
        return []

    texts = [c["text"] for c in chunks]
    print(f"  임베딩 생성 중 - {len(texts)}개")
    vectors = _get_embeddings().embed_documents(texts)
    print(f"  임베딩 완료 - {len(vectors)}개")

    return [
        EmbeddedChunk(chunk_id=c["chunk_id"], embedding=v)
        for c, v in zip(chunks, vectors)
    ]


def validate_embeddings(embedded: list[EmbeddedChunk]) -> None:
    """0-벡터 및 차원 검증."""
    zero_count = sum(1 for e in embedded if all(v == 0.0 for v in e.embedding))
    wrong_dim = [e for e in embedded if len(e.embedding) != EMBED_DIM]

    if wrong_dim:
        raise ValueError(
            f"차원 오류 {len(wrong_dim)}건: 예상 {EMBED_DIM}, "
            f"실제 {len(wrong_dim[0].embedding)} (chunk_id={wrong_dim[0].chunk_id})"
        )
    if zero_count:
        print(f"  [경고] 0-벡터 청크 {zero_count}개 감지됨")
    else:
        print(f"  검증 통과 - 모든 임베딩 차원 {EMBED_DIM}")
