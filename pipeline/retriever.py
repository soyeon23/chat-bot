"""
Qdrant 검색 — 두 진입점:

1) `search_chunks(query_vector, ...)` — 기존 순수 벡터 검색 (역호환).
2) `search_chunks_smart(question, query_vector, ...)` — 정규식 prefilter + 페이로드 부스트.

두 번째 함수가 신규 채팅 경로의 기본값. 질의에서 추출한 메타 힌트
(조문번호·별표·핵심 키워드)를 Qdrant 페이로드 매칭에 결합해 정확 키워드 질의의
누락을 보완한다.
"""
from __future__ import annotations

import os
from itertools import combinations
from typing import Dict, List, Optional

from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue, MatchText

from pipeline.query_parser import QueryHints, parse_query

load_dotenv()

_QDRANT_PATH = os.getenv("QDRANT_PATH", "./qdrant_storage")
_COLLECTION = os.getenv("QDRANT_COLLECTION", "rnd_law_chunks")

# 부스트 값 — 페이로드 매칭이 벡터 점수보다 우위를 점하도록.
# (벡터 점수는 일반적으로 0.55~0.85 범위)
_BOOST_STRUCTURAL = 0.50   # article_no/별표 정확 매칭 (질의에 명시적 조문번호)
_BOOST_PHRASE_PAIR = 0.35  # 키워드 2-gram 페이로드 매칭 (희소·고신호)
_BOOST_COMPARISON  = 0.30  # 종전 vs 혁신법 비교표 청크 (질의가 변경/차이 묻는 경우)
_BOOST_KEYWORD_AND = 0.25  # 모든 키워드 AND 매칭 (도메인 키워드 ≥2개)
_BOOST_PER_KEYWORD = 0.05  # 단일 키워드 매칭(추가 시그널)

# 후보 풀 상한 — 벡터 + 페이로드 보조 fetch 합계.
_VEC_TOPK_CANDIDATES = 30
_FILTERED_VEC_LIMIT = 15      # 페이로드 필터 + 벡터 정렬 fetch 한계
_PAYLOAD_SCROLL_LIMIT = 50    # 페이로드 단독 scroll 결과 한계


def get_qdrant_client() -> QdrantClient:
    return QdrantClient(path=_QDRANT_PATH)


def _payload_to_result(point_id, score: float, payload: dict) -> dict:
    return {
        "id": str(point_id),
        "score": score,
        "text": payload.get("text", ""),
        "document_name": payload.get("doc_name", ""),
        "document_type": payload.get("doc_type", ""),
        "article_no": payload.get("article_no", ""),
        "article_title": payload.get("article_title", ""),
        "page": payload.get("page", 0),
        "effective_date": payload.get("effective_date", ""),
        "file_name": payload.get("source_file", ""),
    }


# ──────────────────────────────────────────────────────────────────
# 기존 함수 (역호환)
# ──────────────────────────────────────────────────────────────────

def search_chunks(
    query_vector: list[float],
    top_k: int = 8,
    doc_type: Optional[str] = None,
) -> list[dict]:
    """
    Qdrant 로컬 파일 모드에서 벡터 유사도 검색을 수행한다 (단순 벡터 검색).

    신규 코드는 `search_chunks_smart`를 권장. 이 함수는 역호환을 위해 유지.
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
        results.append(_payload_to_result(point.id, point.score, point.payload or {}))
    return results


# ──────────────────────────────────────────────────────────────────
# 신규: 정규식 prefilter + 페이로드 부스트
# ──────────────────────────────────────────────────────────────────

def _doc_type_filter(doc_type: Optional[str]) -> Optional[Filter]:
    if doc_type is None:
        return None
    return Filter(must=[FieldCondition(key="doc_type", match=MatchValue(value=doc_type))])


def _structural_match_strings(hints: QueryHints) -> List[str]:
    """
    조문/별표/별지/절 힌트를 article_no MatchText 인자로 사용할 수 있는 문자열 리스트로 변환.

    Qdrant MatchText는 토큰 단위 매칭이므로 '제15조'는 '제15조의2'와 매칭되지 않는다.
    별표 표기는 chunker가 '별표 2' 형태로 인덱싱하므로 공백 보강 형태도 함께 시도.
    """
    out: List[str] = []
    out.extend(hints.article_nos)  # "제15조", "제15조의2"
    # 별표: chunker는 "별표2", "별표 2" 두 형태가 모두 가능 — 둘 다 시도
    for app in hints.appendices:
        out.append(app)                        # "별표2"
        # "별표2" → "별표 2"로 공백 형태 추가
        if app.startswith("별표"):
            n = app[len("별표"):].strip()
            if n:
                out.append(f"별표 {n}")
    for f in hints.forms:
        out.append(f)
        if f.startswith("별지"):
            n = f[len("별지"):].strip()
            if n:
                out.append(f"별지 {n}")
    out.extend(hints.sections)
    # dedupe
    seen = set()
    deduped = []
    for x in out:
        if x not in seen:
            seen.add(x)
            deduped.append(x)
    return deduped


def _build_phrase_pairs(keywords: List[str]) -> List[str]:
    """
    키워드 리스트에서 2-gram 구(phrase)를 만들어 반환.
    MatchText에 공백 구분 phrase를 넘기면 토큰 AND 매칭이 된다.
    예: ['연구활동비', '사용', '비목'] → ['연구활동비 사용', '연구활동비 비목', '사용 비목']
    """
    if len(keywords) < 2:
        return []
    return [f"{a} {b}" for a, b in combinations(keywords, 2)]


def _scroll_match_text(
    client: QdrantClient,
    field: str,
    text: str,
    extra_filter: Optional[Filter] = None,
    limit: int = _PAYLOAD_SCROLL_LIMIT,
) -> List[dict]:
    """
    `field` payload에 `text`(공백 구분 시 토큰 AND)가 매칭되는 청크를 scroll로 가져온다.
    오류는 빈 리스트 반환으로 흡수.
    """
    must_clauses = [FieldCondition(key=field, match=MatchText(text=text))]
    if extra_filter and extra_filter.must:
        must_clauses.extend(extra_filter.must)
    try:
        points, _ = client.scroll(
            collection_name=_COLLECTION,
            scroll_filter=Filter(must=must_clauses),
            limit=limit,
            with_payload=True,
        )
    except Exception:
        return []
    return [(str(p.id), p.payload or {}) for p in points]


def _vector_with_filter(
    client: QdrantClient,
    query_vector: list[float],
    payload_filter: Filter,
    limit: int,
) -> List[dict]:
    """페이로드 필터 + 벡터 정렬. 결과 없으면 빈 리스트."""
    try:
        resp = client.query_points(
            collection_name=_COLLECTION,
            query=query_vector,
            query_filter=payload_filter,
            limit=limit,
            with_payload=True,
        )
    except Exception:
        return []
    return [(str(p.id), p.score, p.payload or {}) for p in resp.points]


_BOOST_PAGE_LOOKUP = 1.00  # 사용자가 페이지를 직접 지목 — 최우선 부스트
_BOOST_DOC_NAME = 0.20     # 문서명 일부 매칭 (예: "매뉴얼")


def search_chunks_smart(
    question: str,
    query_vector: list[float],
    top_k: int = 8,
    doc_type: Optional[str] = None,
    debug: bool = False,
    hints: Optional[QueryHints] = None,
) -> list[dict]:
    """
    의도 분석(정규식 또는 LLM) prefilter + 벡터 검색 + 페이로드 부스트 결합 검색.

    Args:
        question: 사용자 자연어 질의. 멀티턴 모드에서는 caller 가
            `hints.rewritten_query` (직전 컨텍스트가 흡수된 self-contained 질의) 를
            여기에 넘겨야 한다. `query_vector` 도 같은 텍스트로 임베딩돼 있어야 일관성 유지.
            `hints` 가 None 인 경우만 정규식 `parse_query` 폴백에 사용된다.
        query_vector: 임베딩된 질의 벡터.
        top_k: 최종 반환 개수.
        doc_type: 문서 종류 사전 필터.
        debug: True면 각 후보의 부스트 내역을 stderr에 출력.
        hints: 사전 분석된 `QueryHints`. None이면 정규식 `parse_query`로 fallback.
               LLM 분석 결과 (`query_analyzer.analyze_query`)를 넘기면 페이지 직접
               조회·문서명 한정 등이 추가로 활성화된다. `hints.rewritten_query` 가
               `question` 인자와 다른 경우는 caller 가 의도적으로 멀티턴 rewriting
               을 적용한 것이므로 그대로 따른다.

    Returns:
        기존 search_chunks와 동일한 dict 리스트 (score 키 포함).
    """
    client = get_qdrant_client()
    base_filter = _doc_type_filter(doc_type)
    if hints is None:
        hints = parse_query(question or "")
    # doc_name_hint 는 base_filter 에 직접 넣지 않고, 후순위 부스트 단계에서
    # 소프트하게 적용한다(아래 step 7). LLM 이 doc_hint 를 잘못 추론할 경우
    # 정답 청크가 통째로 잘려나가는 문제를 방지하기 위함.

    # candidate_id → (max_score, payload, sources)
    pool: Dict[str, dict] = {}

    def _add(point_id: str, score: float, payload: dict, source: str) -> None:
        if point_id in pool:
            entry = pool[point_id]
            entry["score"] = max(entry["score"], score)
            entry["sources"].append(source)
            # 동일 청크가 여러 경로에서 매칭되면 추가 부스트
            entry["score"] += 0.02
        else:
            pool[point_id] = {
                "score": score,
                "payload": payload,
                "sources": [source],
            }

    # 1) 벡터 단독 후보 (top_k_candidates)
    try:
        resp = client.query_points(
            collection_name=_COLLECTION,
            query=query_vector,
            query_filter=base_filter,
            limit=_VEC_TOPK_CANDIDATES,
            with_payload=True,
        )
        for p in resp.points:
            _add(str(p.id), float(p.score), p.payload or {}, "vec")
    except Exception:
        pass

    # 1b) 페이지 직접 지목 — Claude 분석기가 "151p / 151페이지 / 백오십일쪽" 등을
    #     모두 target_pages 로 정규화. 해당 페이지의 모든 청크를 최상위 후보로.
    for page_no in getattr(hints, "target_pages", None) or []:
        page_must = [FieldCondition(key="page", match=MatchValue(value=int(page_no)))]
        if base_filter and base_filter.must:
            page_must.extend(base_filter.must)
        try:
            points, _ = client.scroll(
                collection_name=_COLLECTION,
                scroll_filter=Filter(must=page_must),
                limit=20,
                with_payload=True,
            )
        except Exception:
            points = []
        for p in points:
            # 페이지 직접 지목은 사용자 명시적 의도이므로 매우 큰 부스트.
            _add(str(p.id), 0.5 + _BOOST_PAGE_LOOKUP, p.payload or {}, f"page:{page_no}")

    # 2) 구조적 힌트(조문/별표/별지/절) — article_no payload MatchText
    for s in _structural_match_strings(hints):
        # (a) 페이로드 필터 + 벡터 정렬
        results = _vector_with_filter(
            client,
            query_vector,
            Filter(must=[FieldCondition(key="article_no", match=MatchText(text=s))]
                   + ([base_filter.must[0]] if base_filter else [])),
            limit=_FILTERED_VEC_LIMIT,
        )
        for pid, sc, pl in results:
            _add(pid, float(sc) + _BOOST_STRUCTURAL, pl, f"struct:{s}")
        # (b) scroll fallback — 벡터 정렬에 누락된 article_no 매칭 청크도 후보 풀로
        for pid, pl in _scroll_match_text(
            client, "article_no", s, base_filter, limit=_PAYLOAD_SCROLL_LIMIT
        ):
            # scroll은 score가 없으므로 baseline 0.5 + boost
            _add(pid, 0.5 + _BOOST_STRUCTURAL, pl, f"struct_scroll:{s}")

    # 3) 키워드 phrase pair 매칭 — text payload MatchText (토큰 AND)
    pairs = _build_phrase_pairs(hints.keywords)
    for phrase in pairs:
        # (a) 페이로드 필터 + 벡터 정렬 — 검색 정밀도 우선
        results = _vector_with_filter(
            client,
            query_vector,
            Filter(must=[FieldCondition(key="text", match=MatchText(text=phrase))]
                   + ([base_filter.must[0]] if base_filter else [])),
            limit=_FILTERED_VEC_LIMIT,
        )
        for pid, sc, pl in results:
            _add(pid, float(sc) + _BOOST_PHRASE_PAIR, pl, f"phrase:{phrase}")
        # (b) scroll fallback — phrase가 매우 희소할 때 (≤10 hits) 모두 풀에 포함
        scroll_hits = _scroll_match_text(
            client, "text", phrase, base_filter, limit=20,
        )
        if 0 < len(scroll_hits) <= 10:
            for pid, pl in scroll_hits:
                _add(pid, 0.5 + _BOOST_PHRASE_PAIR, pl, f"phrase_scroll:{phrase}")

    # 4) 모든 키워드 AND 매칭 (≥2개 키워드인 경우)
    if len(hints.keywords) >= 2:
        and_clauses = [
            FieldCondition(key="text", match=MatchText(text=k))
            for k in hints.keywords
        ]
        if base_filter:
            and_clauses.extend(base_filter.must or [])
        try:
            resp_and = client.query_points(
                collection_name=_COLLECTION,
                query=query_vector,
                query_filter=Filter(must=and_clauses),
                limit=_FILTERED_VEC_LIMIT,
                with_payload=True,
            )
            for p in resp_and.points:
                _add(str(p.id), float(p.score) + _BOOST_KEYWORD_AND,
                     p.payload or {}, "kw_and")
        except Exception:
            pass

    # 5) 비교 의도(종전 vs 혁신법) — 매뉴얼의 비교표 청크에 부스트
    if hints.comparison_intent:
        # "종전" AND "혁신법" 둘 다 포함된 청크 (비교표 시그너처)
        compare_filter = Filter(must=[
            FieldCondition(key="text", match=MatchText(text="종전")),
            FieldCondition(key="text", match=MatchText(text="혁신법")),
        ] + ([base_filter.must[0]] if base_filter else []))
        results = _vector_with_filter(
            client, query_vector, compare_filter, limit=_FILTERED_VEC_LIMIT,
        )
        for pid, sc, pl in results:
            _add(pid, float(sc) + _BOOST_COMPARISON, pl, "compare:vector")
        try:
            for p in _scroll_with_filter(client, compare_filter, limit=30):
                pid = str(p.id)
                pl = p.payload or {}
                _add(pid, 0.5 + _BOOST_COMPARISON, pl, "compare:scroll")
        except Exception:
            pass

    # 6) 단일 키워드 보강 (희소 키워드만) — phrase pair가 모두 0건일 때 백업
    if hints.keywords and not pairs:
        for kw in hints.keywords:
            results = _vector_with_filter(
                client,
                query_vector,
                Filter(must=[FieldCondition(key="text", match=MatchText(text=kw))]
                       + ([base_filter.must[0]] if base_filter else [])),
                limit=_FILTERED_VEC_LIMIT,
            )
            for pid, sc, pl in results:
                _add(pid, float(sc) + _BOOST_PER_KEYWORD, pl, f"kw:{kw}")

    # 7) 문서명 힌트 소프트 부스트 — pool 내 청크 중 doc_name 에 힌트 토큰이
    #    포함된 것에만 가산점. 풀 외부 후보를 새로 끌어오진 않는다.
    doc_hint = (getattr(hints, "doc_name_hint", "") or "").strip()
    if doc_hint:
        for entry in pool.values():
            doc_name = (entry["payload"].get("doc_name", "") or "")
            if doc_hint in doc_name:
                entry["score"] += _BOOST_DOC_NAME
                entry["sources"].append(f"doc:{doc_hint}")

    # 정렬 & top_k
    ranked = sorted(pool.items(), key=lambda kv: -kv[1]["score"])

    if debug:
        import sys
        print(f"[smart_search] hints={hints.to_dict()}", file=sys.stderr)
        print(f"[smart_search] phrase_pairs={pairs}", file=sys.stderr)
        print(f"[smart_search] pool_size={len(pool)}", file=sys.stderr)
        for i, (pid, e) in enumerate(ranked[:top_k]):
            pl = e["payload"]
            print(
                f"  #{i+1} score={e['score']:.4f} "
                f"{pl.get('article_no','')} p.{pl.get('page',0)} "
                f"sources={','.join(e['sources'][:3])}",
                file=sys.stderr,
            )

    out: List[dict] = []
    for pid, entry in ranked[:top_k]:
        out.append(_payload_to_result(pid, entry["score"], entry["payload"]))
    return out


# ──────────────────────────────────────────────────────────────────
# 신규: Dense + Sparse(BM25) + Structural Reciprocal Rank Fusion
# ──────────────────────────────────────────────────────────────────

# RRF 표준 상수. k=60은 Cormack et al. (2009) 권고값.
_RRF_K = 60

# 각 신호당 가져올 후보 수 — 풀 사이즈를 ~50 안팎으로 유지.
_DENSE_RRF_TOPN = 30
_BM25_RRF_TOPN = 30
_STRUCT_RRF_TOPN = 30


def _dense_topn(
    client: QdrantClient,
    query_vector: list[float],
    top_n: int,
    base_filter: Optional[Filter],
) -> List[tuple]:
    """벡터 단독 top_n. (point_id, score, payload) 리스트 (score 내림차순)."""
    try:
        resp = client.query_points(
            collection_name=_COLLECTION,
            query=query_vector,
            query_filter=base_filter,
            limit=top_n,
            with_payload=True,
        )
    except Exception:
        return []
    return [(str(p.id), float(p.score), p.payload or {}) for p in resp.points]


def _structural_topn(
    client: QdrantClient,
    hints: QueryHints,
    query_vector: list[float],
    base_filter: Optional[Filter],
    top_n: int,
) -> List[tuple]:
    """
    조문/별표/별지 등 구조적 힌트로 article_no 매칭 청크를 모은다.
    - 페이로드 필터 + 벡터 정렬 (정확 매칭 + 의미 가까운 순).
    - scroll fallback 으로 벡터 정렬에 누락된 청크도 흡수.
    힌트가 없으면 빈 리스트.
    """
    s_strings = _structural_match_strings(hints)
    if not s_strings:
        return []

    seen: dict = {}  # pid -> (score, payload)

    for s in s_strings:
        # 페이로드 필터 + 벡터 정렬
        results = _vector_with_filter(
            client,
            query_vector,
            Filter(must=[FieldCondition(key="article_no", match=MatchText(text=s))]
                   + ([base_filter.must[0]] if base_filter else [])),
            limit=_FILTERED_VEC_LIMIT,
        )
        for pid, sc, pl in results:
            # 같은 pid가 여러 힌트로 매칭되면 최대 점수 보존.
            if pid not in seen or float(sc) > seen[pid][0]:
                seen[pid] = (float(sc), pl)
        # scroll fallback — 벡터 정렬이 놓친 매칭 추가
        for pid, pl in _scroll_match_text(
            client, "article_no", s, base_filter, limit=_PAYLOAD_SCROLL_LIMIT
        ):
            if pid not in seen:
                # scroll 결과는 정렬 점수가 없으므로 baseline 0.5
                seen[pid] = (0.5, pl)

    # 점수 내림차순으로 top_n
    ranked = sorted(seen.items(), key=lambda kv: -kv[1][0])[:top_n]
    return [(pid, sc, pl) for pid, (sc, pl) in ranked]


def search_chunks_hybrid(
    question: str,
    query_vector: list[float],
    top_k: int = 8,
    doc_type: Optional[str] = None,
    debug: bool = False,
) -> list[dict]:
    """
    Dense (벡터) + Sparse (BM25) + Structural (조문/별표 정규식) 하이브리드 검색.

    각 신호에서 top-N 후보를 뽑고, Reciprocal Rank Fusion (k=60)으로 통합.
    동점 시 raw 벡터 cosine 점수로 tie-break.

    `search_chunks_smart`와 호환되는 dict 리스트를 반환.

    ⚠️ **현재 비활성** — 채팅 진입점(`app.py`, `answer_cli.py`)은 여전히
    `search_chunks_smart`를 사용한다. Phase A 4B 평가 결과:
        - smart  : 9/10 PASS, avg 204.7ms
        - hybrid : 8/10 PASS, avg 139.6ms
    hybrid는 더 빠르지만 PM 버그 케이스("연구활동비 비목")에서 회귀 발생.
    실패 모드: 한국어 OCR 청크의 어휘 불일치로 BM25가 핵심 청크를 놓침
    (질의 토큰 `비목`/`사용`이 청크에 없고 `사용용도`/`활동비`로 표현됨 →
    BM25 매칭 0). smart 의 phrase substring scroll 경로가 이 갭을 메움.
    `scripts/eval_retrieval.py` 참조.

    재평가 트리거: 코퍼스 OCR 품질 개선, 또는 Qdrant native sparse vector
    인덱싱(future Phase B) 도입 시.

    Args:
        question: 사용자 자연어 질의.
        query_vector: 임베딩된 질의 벡터.
        top_k: 최종 반환 개수.
        doc_type: 문서 종류 사전 필터.
        debug: True면 각 신호별 랭크 + RRF 점수를 stderr에 출력.

    Returns:
        기존 search_chunks와 동일한 dict 리스트 (score 키는 RRF 점수).
    """
    from pipeline.bm25_index import Bm25Corpus  # 지연 import — 모듈 cold start 회피

    client = get_qdrant_client()
    base_filter = _doc_type_filter(doc_type)
    hints = parse_query(question or "")

    # ── 1) 세 신호 수집 ────────────────────────────
    dense_hits = _dense_topn(client, query_vector, _DENSE_RRF_TOPN, base_filter)

    try:
        # Qdrant local file mode는 동시 client 1개만 허용 → retriever client 재사용.
        bm25 = Bm25Corpus.get(client=client)
        bm25_hits = bm25.search(question, top_n=_BM25_RRF_TOPN)
        # doc_type 필터 사후 적용 (BM25는 페이로드 필터 무시).
        if doc_type is not None:
            bm25_hits = [h for h in bm25_hits if h[2].get("doc_type") == doc_type]
    except Exception as e:
        if debug:
            import sys
            print(f"[hybrid] bm25 failed: {e}", file=sys.stderr)
        bm25_hits = []

    struct_hits = _structural_topn(
        client, hints, query_vector, base_filter, _STRUCT_RRF_TOPN
    )

    # ── 2) RRF 점수 계산 ───────────────────────────
    # pid -> {"rrf": float, "vec_score": float, "payload": dict, "ranks": dict}
    pool: dict = {}

    def _accumulate(hits: list, signal: str) -> None:
        for rank, (pid, score, payload) in enumerate(hits, start=1):
            entry = pool.setdefault(pid, {
                "rrf": 0.0,
                "vec_score": 0.0,
                "payload": payload,
                "ranks": {},
            })
            entry["rrf"] += 1.0 / (_RRF_K + rank)
            entry["ranks"][signal] = rank
            # 페이로드는 가장 먼저 본 것을 유지 (모두 동일해야 정상)
            if not entry["payload"]:
                entry["payload"] = payload
            # 벡터 신호의 raw cosine 만 tie-break 용으로 보관
            if signal == "dense":
                entry["vec_score"] = max(entry["vec_score"], float(score))

    _accumulate(dense_hits, "dense")
    _accumulate(bm25_hits, "bm25")
    _accumulate(struct_hits, "struct")

    # ── 3) 정렬: RRF 점수 desc, tie-break vec_score desc ────────
    ranked = sorted(
        pool.items(),
        key=lambda kv: (-kv[1]["rrf"], -kv[1]["vec_score"]),
    )

    if debug:
        import sys
        print(f"[hybrid] hints={hints.to_dict()}", file=sys.stderr)
        print(
            f"[hybrid] candidates: dense={len(dense_hits)} "
            f"bm25={len(bm25_hits)} struct={len(struct_hits)} "
            f"pool={len(pool)}",
            file=sys.stderr,
        )
        for i, (pid, e) in enumerate(ranked[:top_k]):
            pl = e["payload"]
            print(
                f"  #{i+1} rrf={e['rrf']:.4f} vec={e['vec_score']:.4f} "
                f"{pl.get('article_no','')} p.{pl.get('page',0)} "
                f"ranks={e['ranks']}",
                file=sys.stderr,
            )

    out: List[dict] = []
    for pid, entry in ranked[:top_k]:
        out.append(_payload_to_result(pid, entry["rrf"], entry["payload"]))
    return out
