"""
BM25 sparse-index — 한국어 청크 코퍼스에 대한 보조 검색 신호.

Phase A 4B 하이브리드 검색의 sparse 신호.
정책: Qdrant 페이로드를 진실의 원천으로 사용 (인덱스와 항상 동기화).
- `Bm25Corpus.get()` — 프로세스 단위 싱글턴, 첫 호출 시 코퍼스 빌드.
- `search(query, top_n)` — 토큰 단위 BM25 점수 정렬.

토크나이저는 의도적으로 단순:
- 한글/영문/숫자 토큰만 (정규식 split).
- 영문 lowercase.
- 길이 1 토큰 제거.
- 짧은 한국어 조사·어미 화이트리스트(20개 미만)로 흔한 josa 제거.
- konlpy / mecab 같은 무거운 의존성 일체 사용하지 않음.

성능 목표:
- 코퍼스 빌드: 2,640 청크 < 2s.
- 쿼리당 점수: < 50ms.
"""
from __future__ import annotations

import os
import re
import threading
import time
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

from rank_bm25 import BM25Okapi


# ──────────────────────────────────────────────────────────────────
# 토크나이저
# ──────────────────────────────────────────────────────────────────

# 한글/영문/숫자 외 모두 split. (·, [, ], 등 한국 문서 특수기호 제거)
_TOKEN_SPLIT_RE = re.compile(r"[^가-힣A-Za-z0-9]+")

# 한국어 조사·어미 화이트리스트 (긴 것 먼저).
# 다중 글자 조사 — 일반 명사 끝과 충돌 확률이 낮아 길이 제약 없이 제거.
_MULTI_JOSA: Tuple[str, ...] = (
    # 격조사 결합형
    "에서는", "으로부터", "에게서", "로부터",
    "에서", "에게", "으로",
    # 보조사 (최소 2자)
    "까지", "부터", "마저", "조차",
    # 종결사 (질의 문장 어미)
    "이다", "입니다",
    # 의문문 어미 패턴
    "한가요", "인가요", "할까요", "되나요",
    "는지", "한지", "인지",
)

# 단일 글자 조사는 긴 토큰(≥5글자)에서만 제거 — 짧은 명사("사용용도", "비고")의
# 마지막 글자가 우연히 josa-shape인 경우를 보호한다. 6+ 글자 합성명사가 우연히
# 단일 josa 글자로 끝날 확률은 매우 낮으므로 의미 있는 보정.
_SINGLE_JOSA: Tuple[str, ...] = (
    "는", "은", "이", "가", "을", "를", "의", "에", "와", "과", "도", "만",
)
_SINGLE_JOSA_MIN_LEN = 5  # 이 길이 이상 토큰에서만 단일 josa 제거


def _strip_josa(tok: str) -> str:
    """단어 끝의 한국어 조사를 제거. 토큰이 너무 짧아지면 원본 유지.

    Two-tier 전략:
    1) 다중 글자 조사 — 길이 무관 제거 (충돌 확률 매우 낮음).
    2) 단일 글자 조사 — 토큰 길이 ≥5인 경우에만 제거 (예: "연구활동비를"
       → "연구활동비"는 OK, "사용용도"의 끝 "도"는 보존).
    """
    for suf in _MULTI_JOSA:
        if tok.endswith(suf) and len(tok) - len(suf) >= 2:
            return tok[: -len(suf)]
    if len(tok) >= _SINGLE_JOSA_MIN_LEN:
        for suf in _SINGLE_JOSA:
            if tok.endswith(suf):
                return tok[: -len(suf)]
    return tok


def tokenize_korean(text: str) -> List[str]:
    """
    한국어 BM25 토크나이저.

    1) 정규식으로 한글/영문/숫자 토큰만 추출.
    2) 영문 lowercase.
    3) 길이 < 2 토큰 제거.
    4) 흔한 조사·어미 1회 제거.

    실패 안전: text가 None/빈 문자열이면 [].
    """
    if not text:
        return []
    raw = _TOKEN_SPLIT_RE.split(text)
    out: List[str] = []
    for tok in raw:
        if not tok:
            continue
        # ASCII는 lowercase 정규화
        if tok.isascii():
            tok = tok.lower()
        tok = _strip_josa(tok)
        if len(tok) >= 2:
            out.append(tok)
    return out


# ──────────────────────────────────────────────────────────────────
# 코퍼스 빌드
# ──────────────────────────────────────────────────────────────────

@dataclass
class _Doc:
    point_id: str
    payload: dict


def _scroll_all_chunks(client, collection: str) -> List[_Doc]:
    """Qdrant 전체 페이로드를 메모리에 끌어올린다. 페이지당 1000.

    Qdrant local file mode는 동시에 1개 클라이언트만 허용하므로 외부에서 받은
    `client`를 재사용한다 (직접 `QdrantClient(path=...)`을 새로 만들지 않음).
    """
    docs: List[_Doc] = []
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=collection,
            limit=1000,
            with_payload=True,
            offset=offset,
        )
        for p in points:
            docs.append(_Doc(point_id=str(p.id), payload=p.payload or {}))
        if offset is None:
            break
    return docs


class Bm25Corpus:
    """
    BM25Okapi 인덱스 + 청크 ID ↔ payload 매핑.

    프로세스 단위 싱글턴. `Bm25Corpus.get()` 으로 접근.
    """

    _instance: Optional["Bm25Corpus"] = None
    _lock = threading.Lock()

    def __init__(
        self,
        chunks: Sequence[_Doc],
        *,
        verbose: bool = False,
    ) -> None:
        self._docs: List[_Doc] = list(chunks)
        # 청크별 토큰 — BM25Okapi가 보관하는 본체. 메모리는 코퍼스 크기 비례.
        t0 = time.time()
        tokenized: List[List[str]] = []
        for d in self._docs:
            text = d.payload.get("text", "") or ""
            # article_no/article_title도 포함 — 조문번호 BM25 매칭 가능하게.
            extra = " ".join(
                str(d.payload.get(k, "") or "")
                for k in ("article_no", "article_title")
            )
            tokenized.append(tokenize_korean(text + " " + extra))
        self._bm25 = BM25Okapi(tokenized)
        self._build_seconds = time.time() - t0
        if verbose:
            n = len(self._docs)
            print(
                f"[bm25] built corpus: {n} docs in {self._build_seconds:.2f}s "
                f"(avg tokens/doc={sum(len(t) for t in tokenized)/max(n,1):.1f})"
            )

    @classmethod
    def get(
        cls,
        *,
        client=None,
        force_rebuild: bool = False,
        verbose: bool = False,
    ) -> "Bm25Corpus":
        """
        프로세스 단위 싱글턴 접근. 첫 호출 시 코퍼스 빌드.

        Args:
            client: QdrantClient 인스턴스 (Qdrant local file mode는 동시
                다중 client를 허용하지 않으므로 retriever와 같은 client를
                재사용해야 함). None이면 임시 client를 만들어 빌드.
            force_rebuild: True면 캐시된 인덱스 무시하고 재빌드.
            verbose: True면 빌드 시간/통계를 stdout에 출력.
        """
        with cls._lock:
            if cls._instance is None or force_rebuild:
                collection = os.getenv("QDRANT_COLLECTION", "rnd_law_chunks")
                if client is None:
                    # 호출자가 client를 안 넘기면 임시 생성 (스크립트/테스트용).
                    from qdrant_client import QdrantClient
                    qdrant_path = os.getenv("QDRANT_PATH", "./qdrant_storage")
                    tmp = QdrantClient(path=qdrant_path)
                    try:
                        docs = _scroll_all_chunks(tmp, collection)
                    finally:
                        tmp.close()
                else:
                    docs = _scroll_all_chunks(client, collection)
                cls._instance = cls(docs, verbose=verbose)
            return cls._instance

    @property
    def size(self) -> int:
        return len(self._docs)

    @property
    def build_seconds(self) -> float:
        return self._build_seconds

    def search(self, query: str, top_n: int = 30) -> List[Tuple[str, float, dict]]:
        """
        BM25 점수 상위 top_n을 반환. 빈 query면 [].

        Returns:
            (point_id, score, payload) 리스트. 점수 내림차순.
        """
        tokens = tokenize_korean(query)
        if not tokens:
            return []
        scores = self._bm25.get_scores(tokens)
        # argsort top_n
        # 점수 0인 항목은 매칭 토큰이 하나도 없는 청크 — 컷오프
        scored_idx = sorted(
            (i for i, s in enumerate(scores) if s > 0.0),
            key=lambda i: -scores[i],
        )[:top_n]
        out: List[Tuple[str, float, dict]] = []
        for i in scored_idx:
            d = self._docs[i]
            out.append((d.point_id, float(scores[i]), d.payload))
        return out
