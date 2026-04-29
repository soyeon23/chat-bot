"""
별표2 단일 파일 재인덱싱 스크립트 (Phase G1 한정).

목적:
    chunker 의 ARTICLE_PATTERNS 정규식 강화 후, 시행령 [별표 2] HWP 한 파일만
    재파싱·재청킹·재임베딩·재upsert. 다른 문서 청크는 건드리지 않는다.

흐름:
    1) 기존 인덱스에서 source_file 이 별표2 HWP 인 포인트만 삭제
    2) hwp-mcp 로 파싱 → 새 chunker 로 청킹 → 임베딩 → 업서트
    3) 검증 메트릭 출력 (PM 합격기준)

실행:
    cd /Users/maro/dev/company/chatbot
    source .venv/bin/activate
    python scripts/reindex_byeolpyo2.py

종료 코드:
    0 — 합격기준 모두 충족
    1 — 합격기준 미달
"""
from __future__ import annotations

import sys
import unicodedata
from dataclasses import asdict
from pathlib import Path

# 프로젝트 루트를 path 에 추가
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

from pipeline.chunker import chunk_document
from pipeline.embedder import embed_chunks, validate_embeddings
from pipeline.hwp_parser import parse_hwp
from pipeline.indexer import upsert_chunks


# ──────────────────────────────────────────────────────────────────
# 설정
# ──────────────────────────────────────────────────────────────────

BYEOLPYO2_PATH = Path(
    "/Users/maro/dev/company/chatbot/"
    "국가연구개발혁신법 시행령(대통령령)(제36163호)(20260310)/"
    "[별표 2] 연구개발비 사용용도(제20조제1항 관련)(국가연구개발혁신법 시행령).hwp"
)
QDRANT_PATH = "./qdrant_storage"
COLLECTION = "rnd_law_chunks"

DOC_NAME = "국가연구개발혁신법 시행령 [별표 2] 연구개발비 사용용도"
DOC_TYPE = "시행령"
EFFECTIVE_DATE = "2026-03-10"
REVISED_DATE = "2026-03-10"

KEYWORDS = [
    "지식재산 창출 활동비",
    "클라우드컴퓨팅서비스",
    "연구실 운영비",
    "연구수당",
    "학생인건비",
]

# 합격기준
MIN_BYEOLPYO2_CHUNKS = 5
MIN_TEXT_LEN_TOTAL = 10_000  # 원문 14,077 자의 70%+


def _nfc(s: str | None) -> str:
    return unicodedata.normalize("NFC", s) if s else ""


def _scroll_all(client: QdrantClient) -> list:
    """컬렉션 전체 scroll (소규모 인덱스 가정)."""
    out = []
    next_offset = None
    while True:
        batch, next_offset = client.scroll(
            collection_name=COLLECTION,
            limit=500,
            offset=next_offset,
            with_payload=True,
            with_vectors=False,
        )
        out.extend(batch)
        if next_offset is None:
            break
    return out


def _is_byeolpyo2_source(source_file: str | None, target_filename: str) -> bool:
    """source_file 이 별표2 HWP 인지 판정 (NFC 정규화 후 비교).

    macOS 가 HWP 파일명을 NFD 자모 분해로 저장해 그대로 payload 에 들어가는
    경우가 있어, 두 쪽 모두 NFC 변환 후 비교한다.
    """
    sf = _nfc(source_file)
    return sf == target_filename


def _delete_existing_byeolpyo2(client: QdrantClient, target_filename: str) -> int:
    """기존 인덱스에서 별표2 HWP 청크만 식별 후 id 기반 삭제."""
    all_records = _scroll_all(client)
    to_delete = [
        str(r.id)
        for r in all_records
        if _is_byeolpyo2_source(r.payload.get("source_file"), target_filename)
    ]

    print(f"  기존 별표2 HWP 청크: {len(to_delete)}개")
    if not to_delete:
        return 0

    # 안전: 100개씩 배치 삭제
    batch = 100
    for i in range(0, len(to_delete), batch):
        client.delete(
            collection_name=COLLECTION,
            points_selector=qmodels.PointIdsList(points=to_delete[i:i + batch]),
        )
    print(f"  기존 별표2 청크 {len(to_delete)}개 삭제 완료")
    return len(to_delete)


def _verify_after(client: QdrantClient, target_filename: str) -> dict:
    """재upsert 후 검증 메트릭 산출."""
    all_records = _scroll_all(client)
    hwp_records = [
        r for r in all_records
        if _is_byeolpyo2_source(r.payload.get("source_file"), target_filename)
    ]

    # article_no 가 '별표 2' 로 시작하는 청크 수
    by2_records = [
        r for r in hwp_records
        if _nfc(r.payload.get("article_no", "")).startswith("별표 2")
        or _nfc(r.payload.get("article_no", "")).startswith("별표2")
    ]

    total_text_len = sum(len(_nfc(r.payload.get("text", ""))) for r in hwp_records)

    keyword_hits = {}
    for kw in KEYWORDS:
        hits = sum(
            1 for r in hwp_records
            if kw in _nfc(r.payload.get("text", ""))
        )
        keyword_hits[kw] = hits

    return {
        "hwp_chunks_total": len(hwp_records),
        "byeolpyo2_chunks": len(by2_records),
        "text_len_total": total_text_len,
        "keyword_hits": keyword_hits,
    }


def _print_metrics(label: str, m: dict) -> None:
    print(f"\n[{label}]")
    print(f"  HWP source_file 전체 청크: {m['hwp_chunks_total']}")
    print(f"  article_no='별표 2*' 청크: {m['byeolpyo2_chunks']}")
    print(f"  텍스트 길이 합계: {m['text_len_total']}")
    print("  키워드 hits:")
    for kw, c in m["keyword_hits"].items():
        print(f"    {kw}: {c}")


def main() -> int:
    print("=" * 80)
    print("별표2 단일 파일 재인덱싱 (Phase G1)")
    print("=" * 80)

    if not BYEOLPYO2_PATH.exists():
        print(f"  [ERROR] HWP 파일 없음: {BYEOLPYO2_PATH}")
        return 1

    target_filename = BYEOLPYO2_PATH.name  # NFC 가정

    # ── 0. Before 메트릭 ───────────────────────────────────────
    client = QdrantClient(path=QDRANT_PATH)
    before = _verify_after(client, target_filename)
    _print_metrics("Before", before)

    # ── 1. 기존 별표2 HWP 청크 삭제 ────────────────────────────
    print("\n[Step 1] 기존 별표2 HWP 청크 삭제")
    deleted = _delete_existing_byeolpyo2(client, target_filename)
    client.close()  # local Qdrant: 다음 단계가 client 다시 열기 전 닫음

    # ── 2. 파싱 → 청킹 ────────────────────────────────────────
    print("\n[Step 2] HWP 파싱 + 청킹")
    parse_result = parse_hwp(BYEOLPYO2_PATH, save_raw=False)
    if not parse_result.pages:
        print("  [ERROR] 파싱 결과 빈 페이지")
        return 1
    print(f"  파싱 페이지: {len(parse_result.pages)}, 총 텍스트 길이: "
          f"{sum(len(p.text) for p in parse_result.pages)}")

    chunks = chunk_document(
        parse_result,
        doc_name=DOC_NAME,
        doc_type=DOC_TYPE,
        effective_date=EFFECTIVE_DATE,
        revised_date=REVISED_DATE,
        is_current=True,
    )
    print(f"  생성 청크: {len(chunks)}")
    if not chunks:
        print("  [ERROR] 청킹 결과 0개")
        return 1

    # 미리보기
    print("\n  청크 article_no 분포:")
    from collections import Counter
    for art, c in Counter(_nfc(c.article_no) for c in chunks).most_common():
        print(f"    {c:3d}  {art!r}")

    # ── 3. 임베딩 ─────────────────────────────────────────────
    print("\n[Step 3] 임베딩")
    chunks_dicts = [asdict(c) for c in chunks]
    embedded = embed_chunks(chunks_dicts)
    validate_embeddings(embedded)

    # ── 4. 업서트 ─────────────────────────────────────────────
    print("\n[Step 4] Qdrant 업서트")
    upsert_chunks(chunks_dicts, embedded)

    # ── 5. After 메트릭 + 합격기준 검증 ───────────────────────
    client = QdrantClient(path=QDRANT_PATH)
    after = _verify_after(client, target_filename)
    _print_metrics("After", after)

    # 회귀 sample: 별표2 HWP 외 임의 5개
    all_records = _scroll_all(client)
    other_records = [
        r for r in all_records
        if not _is_byeolpyo2_source(r.payload.get("source_file"), target_filename)
    ]
    print("\n[회귀 sample] 다른 문서 청크 5개 (article_no/source_file 정합성):")
    for r in other_records[:5]:
        p = r.payload
        print(
            f"  art={_nfc(p.get('article_no',''))[:30]!r:32s} "
            f"sf={_nfc(p.get('source_file',''))[:50]!r}"
        )

    client.close()

    # ── 합격기준 평가 ────────────────────────────────────────
    print("\n" + "=" * 80)
    print("합격기준 평가")
    print("=" * 80)

    pass_chunks = after["byeolpyo2_chunks"] >= MIN_BYEOLPYO2_CHUNKS
    pass_textlen = after["text_len_total"] >= MIN_TEXT_LEN_TOTAL
    pass_keywords = all(c >= 1 for c in after["keyword_hits"].values())

    print(f"  [{'PASS' if pass_chunks else 'FAIL'}] article_no='별표 2*' 청크 ≥ "
          f"{MIN_BYEOLPYO2_CHUNKS}: {after['byeolpyo2_chunks']}")
    print(f"  [{'PASS' if pass_textlen else 'FAIL'}] 텍스트 길이 합계 ≥ "
          f"{MIN_TEXT_LEN_TOTAL}: {after['text_len_total']}")
    print(f"  [{'PASS' if pass_keywords else 'FAIL'}] 키워드 5종 모두 ≥ 1 hit: "
          f"{after['keyword_hits']}")

    overall = pass_chunks and pass_textlen and pass_keywords
    print(f"\n  전체: {'PASS' if overall else 'FAIL'}")
    print(f"  삭제 {deleted} → upsert {len(embedded)} → "
          f"net change {len(embedded) - deleted:+d}")

    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())
