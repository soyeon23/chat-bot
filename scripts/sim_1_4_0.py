"""1.4.0 매뉴얼/별표/별지 시뮬레이션 — 캐시 기반 (.planning/audit-cache/)."""
from __future__ import annotations

import json
import sys
import unicodedata
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.chunker import chunk_document, CHUNKER_VERSION
from pipeline.pdf_parser import ParsedPage, ParseResult


CACHE_DIR = Path("/Users/maro/dev/company/chatbot/.planning/audit-cache")


def load_cache(stem: str) -> ParseResult:
    """캐시 JSON 을 ParseResult 로 복원."""
    path = CACHE_DIR / f"{stem}.json"
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    pages = [
        ParsedPage(page_num=p["page_num"], text=p["text"], needs_ocr=False)
        for p in d.get("pages", [])
    ]
    # source_file 이 None 이면 stem.pdf/.hwp 가정 — 매뉴얼은 .pdf
    source_file = d.get("source_file") or f"{stem}.pdf"
    return ParseResult(source_file=source_file, pages=pages)


def analyze(label: str, chunks):
    """청크 리스트 분석 출력."""
    total = len(chunks)
    article_counts = Counter(c.article_no for c in chunks)
    pages = sorted({c.page for c in chunks})
    page_min = min(pages) if pages else 0
    page_max = max(pages) if pages else 0
    page_count = len(pages)
    forced = sum(1 for c in chunks if "(part " in c.article_no)
    unique_articles = len(article_counts)

    # part 1/Y 만 카운트해서 원본 article 수 계산
    parent_articles = Counter()
    for c in chunks:
        a = c.article_no.split(" (part")[0]
        parent_articles[a] += 1

    print(f"\n--- {label} ---")
    print(f"  총 청크 수: {total}")
    print(f"  unique article_no: {unique_articles}")
    print(f"  unique parent (part 제거): {len(parent_articles)}")
    print(f"  forced_split 청크 수: {forced}")
    print(f"  forced_split 비율: {forced/total*100:.1f}%" if total else "  forced_split: -")
    print(f"  page 분포: {page_min} ~ {page_max} ({page_count} unique)")
    if total:
        # top-5 parent
        print(f"  top-5 parent article_no:")
        for art, cnt in parent_articles.most_common(5):
            print(f"    {art!r}: {cnt}")
    return {
        "total": total,
        "unique_articles": unique_articles,
        "unique_parents": len(parent_articles),
        "forced_split": forced,
        "forced_ratio": forced/total*100 if total else 0,
        "page_min": page_min,
        "page_max": page_max,
        "page_count": page_count,
    }


def main():
    print(f"chunker_version = {CHUNKER_VERSION}")
    print("=" * 70)

    # 1. 매뉴얼 PDF (1.4.0 라우팅 진입 검증)
    manual_stem = "[본권] 25년도 국가연구개발혁신법 매뉴얼_배포용"
    pr = load_cache(manual_stem)
    # source_file 을 매뉴얼 PDF 명으로 강제 (캐시는 None)
    pr.source_file = f"{manual_stem}.pdf"
    print(f"\n매뉴얼 PDF: {pr.source_file}")
    print(f"  페이지 수 (raw): {len(pr.pages)}")
    print(f"  글자 수 합계: {sum(len(p.text) for p in pr.pages)}")
    print(f"  빈 페이지: {sum(1 for p in pr.pages if not p.text.strip())}")

    chunks = chunk_document(
        parse_result=pr,
        doc_name="25년도 국가연구개발혁신법 매뉴얼",
        doc_type="매뉴얼",
        is_current=True,
    )
    metrics_manual = analyze("매뉴얼 1.4.0 시뮬", chunks)

    # 매뉴얼 article_no 형식 검증
    p_format = sum(1 for c in chunks if c.article_no.startswith("p."))
    print(f"  article_no 가 'p.' 로 시작: {p_format}/{metrics_manual['total']}")
    part_format = sum(1 for c in chunks if "(part " in c.article_no)
    print(f"  article_no 에 '(part ' 포함: {part_format}/{metrics_manual['total']}")

    # 2. 별표 6 (회귀 0건 확인)
    print("\n" + "=" * 70)
    byeolpyo_stem = "[별표 6] 참여제한 처분기준(제59조제1항 관련)(국가연구개발혁신법 시행령)"
    pr_b = load_cache(byeolpyo_stem)
    pr_b.source_file = f"{byeolpyo_stem}.hwp"
    chunks_b = chunk_document(
        parse_result=pr_b,
        doc_name="국가연구개발혁신법 시행령 [별표 6]",
        doc_type="시행령",
        is_current=True,
    )
    metrics_byeolpyo = analyze("별표 6 1.4.0 시뮬", chunks_b)
    article_nos = [c.article_no for c in chunks_b]
    starts_byeolpyo = all(a.startswith("별표 6") for a in article_nos)
    print(f"  모든 청크 article_no 가 '별표 6' 로 시작: {starts_byeolpyo}")

    # 3. 별지 제2호서식 (article-aware split — 회귀 0건 확인)
    print("\n" + "=" * 70)
    byeolji_stem = "[별지 제2호서식] 국가연구개발사업 협약서(국가연구개발혁신법 시행규칙)"
    pr_j = load_cache(byeolji_stem)
    pr_j.source_file = f"{byeolji_stem}.hwp"
    chunks_j = chunk_document(
        parse_result=pr_j,
        doc_name="별지 제2호서식 협약서",
        doc_type="시행규칙",
        is_current=True,
    )
    metrics_byeolji = analyze("별지 제2호서식 1.4.0 시뮬", chunks_j)

    # 4. 별표 2 (회귀 0건)
    print("\n" + "=" * 70)
    byeolpyo2_stem = "[별표 2] 연구개발비 사용용도(제20조제1항 관련)(국가연구개발혁신법 시행령)"
    pr_b2 = load_cache(byeolpyo2_stem)
    pr_b2.source_file = f"{byeolpyo2_stem}.hwp"
    chunks_b2 = chunk_document(
        parse_result=pr_b2,
        doc_name="국가연구개발혁신법 시행령 [별표 2]",
        doc_type="시행령",
        is_current=True,
    )
    metrics_byeolpyo2 = analyze("별표 2 1.4.0 시뮬", chunks_b2)
    starts_byeolpyo2 = all(c.article_no.startswith("별표 2") for c in chunks_b2)
    print(f"  모든 청크 article_no 가 '별표 2' 로 시작: {starts_byeolpyo2}")


if __name__ == "__main__":
    main()
