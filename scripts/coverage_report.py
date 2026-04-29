"""인덱스 품질 진단 — 페이지 / 조문 / doc_name 다양성 / Phase H 도구 검증.

용도:
    chunker / sync 패치 직후 사용자가 실행해 *현재 인덱스 품질* 한 번에 본다.
    아래 4개 시그널을 수집해 종합 진단을 출력한다.

    1. 페이지 커버리지 — 매뉴얼 PDF 의 청크-있는 페이지 / 청크-없는 페이지.
       특히 pdfplumber 가 텍스트는 추출했는데 청크가 0개인 페이지(=chunker 누락)
       를 의심 케이스로 분리.
    2. 조문 커버리지 — 고유 article_no 분포 + 강제분할 비율 + 매뉴얼 PDF 의
       별표 인용 misclassification 회귀 감지.
    3. doc_name 다양성 — 한쪽 문서 편향 + 토픽 검색 sample 5건 의 top-K 다양성.
    4. Phase H 도구 검증 — read_page / get_article / search_text 가 현재 환경에서
       정확히 동작하는지 + 응답 시간.

사용:
    cd /Users/maro/dev/company/chatbot
    source .venv/bin/activate
    python scripts/coverage_report.py
    python scripts/coverage_report.py --json   # 결과 JSON 으로

종료 코드:
    0 — 모든 시그널 정상
    1 — 회귀 의심 (의심 페이지 ≥ 임계값 또는 별표 misclass ≥ 1)

⚠ 인덱스가 stale 한 chunker_version 으로 인덱싱돼 있으면 결과 상단에 경고
   배너를 출력하고, 사용자는 streamlit "📂 동기화" 버튼으로 1.3.x 적용 후
   재실행해야 한다.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional

# 프로젝트 루트
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline.chunker import CHUNKER_VERSION
from pipeline.sync import load_metadata, METADATA_PATH


# ──────────────────────────────────────────────────────────────────
# 임계값 / 상수
# ──────────────────────────────────────────────────────────────────

# pdfplumber 가 페이지에서 추출한 텍스트가 이 이상이면 "본문 있음" 으로 간주.
# 이미지 전용 페이지 / 빈 페이지 는 보통 < 100자.
_PAGE_TEXT_NONEMPTY_THRESH = 100

# 의심 페이지 (pdfplumber 텍스트 충분 + 청크 0) 가 이 이상이면 회귀 경고
_SUSPECT_PAGE_THRESH = 30

# 매뉴얼 PDF 본문 안 별표 인용 misclassification 회귀 임계값
# (G3 chunker 1.3.x 패치 후엔 0 또는 매우 적어야 정상)
_MANUAL_BYEOLPYO_MAX = 5

# 토픽 검색 sample 다양성 측정 — top-K 안에 등장한 doc_name 종류
_DIVERSITY_SAMPLE_K = 8

# Phase H 도구 검증용 sample.
# 각 케이스의 expect_keywords 는 도구 응답의 text(또는 search_text 발췌 모음)
# 안에 *모두* 등장해야 PASS. 검증 키워드는 진단 도구 수동 확인 후 매뉴얼 PDF
# 실제 본문에 존재하는 정확 token 만 사용.
_PHASE_H_SAMPLES = [
    # p.151 — FAQ Q1~Q7. 매뉴얼 본문은 영어 "FAQ" 가 아니라 "Q&A" / "Q1." 로 표기.
    {"kind": "read_page",    "doc_name": "매뉴얼", "page_num": 151,
     "expect_keywords": ["Q&A", "Q1", "Q7", "연구노트"]},
    {"kind": "read_page",    "doc_name": "매뉴얼", "page_num": 78,
     "expect_keywords": []},  # 키워드 검증 없음 — 본문 존재 자체 확인
    {"kind": "read_page",    "doc_name": "매뉴얼", "page_num": 230,
     "expect_keywords": []},
    # 매뉴얼은 진짜 제N조 본문이 없고 인용 표만 있음 — 매칭 자체 (≥1자 본문) 만 확인.
    {"kind": "get_article",  "doc_name": "매뉴얼", "article_no": "제15조",
     "expect_keywords": []},
    {"kind": "get_article",  "doc_name": "매뉴얼", "article_no": "제32조",
     "expect_keywords": []},
    # search_text 는 정확 phrase. 매뉴얼 안 "연구노트" + 페이지 안 "보존기간 30년"
    # 가 가까이 있으면 OK. 정규식으로 거리 허용.
    {"kind": "search_text",  "doc_name": "매뉴얼", "query": r"연구노트.{0,40}보존기간",
     "expect_keywords": []},
    {"kind": "read_page",    "doc_name": "매뉴얼", "page_num": 999,
     "expect_keywords": [], "expect_error": True},  # 범위 초과 negative
]

# 토픽 검색 sample (다양성 측정)
_TOPIC_QUERIES = [
    "학생인건비 지급기준",
    "연구노트 보존기간 30년",
    "간접비 비율 한도",
    "연구활동비 클라우드",
    "별표 6 가중기준",
]


# ──────────────────────────────────────────────────────────────────
# 데이터 클래스
# ──────────────────────────────────────────────────────────────────

@dataclass
class PageCoverage:
    pdf_name: str
    total_pages: int
    pages_with_chunks: int
    empty_pages: list[int]                # 청크 0 인 페이지
    truly_blank_pages: list[int]          # pdfplumber text < threshold
    suspect_pages: list[int]              # 텍스트 있는데 청크 0 (= 누락)
    error: Optional[str] = None


@dataclass
class ArticleCoverage:
    total_chunks: int
    unique_article_nos: int
    forced_split_count: int                # "(part X/Y)" 가 들어간 청크
    forced_split_ratio: float
    manual_byeolpyo_in_manual: int         # 매뉴얼 doc 안의 별표 article_no 청크
    article_no_to_docs: dict               # article_no -> {doc_name set}


@dataclass
class DocDiversity:
    doc_name_counts: dict
    topic_samples: list[dict]              # 각 query 의 top-K 분포


@dataclass
class PhaseHResult:
    kind: str
    args: dict
    ok: bool
    elapsed_ms: float
    char_count: int
    error: Optional[str]
    keyword_hits: dict


@dataclass
class Report:
    timestamp: str
    chunker_version: str
    metadata_chunker_versions: dict        # 인덱스 안 chunker_version 분포
    sync_warning: Optional[str]
    pages: list[PageCoverage]
    articles: ArticleCoverage
    diversity: DocDiversity
    phase_h: list[PhaseHResult]
    overall_ok: bool
    diagnostics: list[str] = field(default_factory=list)


# ──────────────────────────────────────────────────────────────────
# 유틸
# ──────────────────────────────────────────────────────────────────

def _nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s or "")


def _scroll_all_chunks(client) -> list[dict]:
    """Qdrant 컬렉션 전체 scroll → payload list."""
    out: list[dict] = []
    nxt = None
    while True:
        batch, nxt = client.scroll(
            collection_name=os.getenv("QDRANT_COLLECTION", "rnd_law_chunks"),
            limit=500,
            offset=nxt,
            with_payload=True,
            with_vectors=False,
        )
        for p in batch:
            out.append(p.payload or {})
        if nxt is None:
            break
    return out


def _find_manual_pdf() -> Optional[Path]:
    """프로젝트 안 매뉴얼 PDF 경로 탐색 — 1단계 깊이."""
    for p in sorted(ROOT.iterdir()):
        if p.is_file() and p.suffix.lower() == ".pdf" and "매뉴얼" in _nfc(p.name):
            return p
    # config 의 pdf_dir 도 시도
    try:
        from pipeline.config_store import load_config
        cfg = load_config()
        if cfg and cfg.pdf_dir:
            d = Path(cfg.pdf_dir).expanduser()
            if d.exists():
                for p in sorted(d.iterdir()):
                    if p.is_file() and p.suffix.lower() == ".pdf" and "매뉴얼" in _nfc(p.name):
                        return p
    except Exception:
        pass
    return None


# ──────────────────────────────────────────────────────────────────
# 1. 페이지 커버리지
# ──────────────────────────────────────────────────────────────────

def diagnose_page_coverage(payloads: list[dict]) -> list[PageCoverage]:
    """매뉴얼 PDF 의 페이지 커버리지 분석.

    HWP / 별표 / 별지서식은 페이지 경계가 없거나 1페이지 짜리라 의미 없음.
    매뉴얼 PDF 한 종만 분석.
    """
    manual_pdf = _find_manual_pdf()
    if manual_pdf is None:
        return [PageCoverage(
            pdf_name="(매뉴얼 PDF 없음)",
            total_pages=0,
            pages_with_chunks=0,
            empty_pages=[],
            truly_blank_pages=[],
            suspect_pages=[],
            error="매뉴얼 PDF 파일을 프로젝트 루트에서 찾지 못했습니다.",
        )]

    target_doc_name_token = "매뉴얼"
    chunked_pages: set[int] = set()
    for pl in payloads:
        if target_doc_name_token in _nfc(pl.get("doc_name", "")):
            page = pl.get("page", 0)
            if isinstance(page, int) and page > 0:
                chunked_pages.add(page)

    # pdfplumber 로 매뉴얼 PDF 의 각 페이지 텍스트 길이 측정.
    # 주의: 전체 516 페이지 추출은 ~30~60초. 단, layout=False 라 chunker 보다 빠름.
    print(f"  [page] pdfplumber 직접 추출 중 ({manual_pdf.name})...", flush=True)
    import pdfplumber
    page_text_lens: dict[int, int] = {}
    try:
        with pdfplumber.open(manual_pdf) as pdf:
            total_pages = len(pdf.pages)
            for i, page in enumerate(pdf.pages, start=1):
                try:
                    txt = page.extract_text() or ""
                except Exception:
                    txt = ""
                page_text_lens[i] = len(txt.strip())
                if i % 50 == 0:
                    print(f"  [page] {i}/{total_pages} ...", flush=True)
    except Exception as e:
        return [PageCoverage(
            pdf_name=manual_pdf.name,
            total_pages=0,
            pages_with_chunks=len(chunked_pages),
            empty_pages=[],
            truly_blank_pages=[],
            suspect_pages=[],
            error=f"pdfplumber 실패: {type(e).__name__}: {e}",
        )]

    empty_pages = sorted(set(range(1, total_pages + 1)) - chunked_pages)
    truly_blank: list[int] = []
    suspect: list[int] = []
    for p in empty_pages:
        if page_text_lens.get(p, 0) < _PAGE_TEXT_NONEMPTY_THRESH:
            truly_blank.append(p)
        else:
            suspect.append(p)

    return [PageCoverage(
        pdf_name=manual_pdf.name,
        total_pages=total_pages,
        pages_with_chunks=len(chunked_pages),
        empty_pages=empty_pages,
        truly_blank_pages=truly_blank,
        suspect_pages=suspect,
    )]


# ──────────────────────────────────────────────────────────────────
# 2. 조문 커버리지
# ──────────────────────────────────────────────────────────────────

def diagnose_articles(payloads: list[dict]) -> ArticleCoverage:
    article_to_docs: dict[str, set[str]] = defaultdict(set)
    forced = 0
    manual_byeolpyo = 0  # 매뉴얼 doc 안에 article_no='별표 N' 인 청크 (회귀 시그널)

    for pl in payloads:
        art = _nfc(pl.get("article_no", ""))
        doc = _nfc(pl.get("doc_name", ""))
        article_to_docs[art].add(doc)
        if "(part " in art:
            forced += 1
        # 매뉴얼 doc 인데 별표 헤더 article_no 가 붙은 청크 (G2.1 패치 회귀 감지)
        # — chunker 가 매뉴얼 본문 안 인라인 인용을 별표 헤더로 잘못 분류했을 때 발생.
        if "매뉴얼" in doc and art.startswith("별표"):
            manual_byeolpyo += 1

    total = len(payloads)
    return ArticleCoverage(
        total_chunks=total,
        unique_article_nos=len(article_to_docs),
        forced_split_count=forced,
        forced_split_ratio=forced / total if total else 0.0,
        manual_byeolpyo_in_manual=manual_byeolpyo,
        article_no_to_docs={k: sorted(v) for k, v in article_to_docs.items()},
    )


# ──────────────────────────────────────────────────────────────────
# 3. doc_name 다양성
# ──────────────────────────────────────────────────────────────────

def diagnose_doc_diversity(payloads: list[dict]) -> DocDiversity:
    counts: dict[str, int] = defaultdict(int)
    for pl in payloads:
        counts[_nfc(pl.get("doc_name", ""))] += 1

    # 토픽 검색 sample
    samples: list[dict] = []
    try:
        from pipeline.embedder import embed_query
        from pipeline.retriever import search_chunks_smart

        for q in _TOPIC_QUERIES:
            try:
                t0 = time.time()
                hits = search_chunks_smart(q, embed_query(q), top_k=_DIVERSITY_SAMPLE_K)
                elapsed = (time.time() - t0) * 1000
                docs = [_nfc(h.get("document_name", "") or "") for h in hits]
                doc_counts: dict[str, int] = defaultdict(int)
                for d in docs:
                    doc_counts[d] += 1
                samples.append({
                    "query": q,
                    "elapsed_ms": round(elapsed, 1),
                    "top_k": len(hits),
                    "unique_docs": len(doc_counts),
                    "doc_counts": dict(doc_counts),
                    "top1_doc": docs[0] if docs else "",
                    "top1_article": _nfc(hits[0].get("article_no", "")) if hits else "",
                    "top1_page": hits[0].get("page", 0) if hits else 0,
                })
            except Exception as e:
                samples.append({
                    "query": q,
                    "error": f"{type(e).__name__}: {e}",
                })
    except Exception as e:
        samples.append({"query": "<embedder load failed>", "error": str(e)})

    return DocDiversity(
        doc_name_counts=dict(sorted(counts.items(), key=lambda kv: -kv[1])),
        topic_samples=samples,
    )


# ──────────────────────────────────────────────────────────────────
# 4. Phase H 도구 검증
# ──────────────────────────────────────────────────────────────────

def diagnose_phase_h() -> list[PhaseHResult]:
    out: list[PhaseHResult] = []
    try:
        from pipeline.local_doc_mcp import read_page, get_article, search_text
    except Exception as e:
        return [PhaseHResult(
            kind="<import>", args={}, ok=False, elapsed_ms=0.0,
            char_count=0, error=f"local_doc_mcp import 실패: {e}",
            keyword_hits={},
        )]

    for spec in _PHASE_H_SAMPLES:
        kind = spec["kind"]
        keywords = spec.get("expect_keywords", [])
        expect_error = spec.get("expect_error", False)
        args = {k: v for k, v in spec.items() if k not in ("kind", "expect_keywords", "expect_error")}

        t0 = time.time()
        try:
            if kind == "read_page":
                resp = read_page(spec["doc_name"], spec["page_num"])
                text = resp.get("text", "")
                err = resp.get("error")
            elif kind == "get_article":
                resp = get_article(spec["doc_name"], spec["article_no"])
                text = resp.get("text", "")
                err = resp.get("error")
            elif kind == "search_text":
                resp = search_text(spec["doc_name"], spec["query"], 5)
                # search_text 는 list — 첫 매칭 발췌 모음
                if isinstance(resp, list):
                    text = "\n".join((m.get("excerpt", "") + " | " + m.get("match", "")) for m in resp[:5])
                    err = None if resp else "no matches"
                else:
                    text = ""
                    err = str(resp)
            else:
                text = ""
                err = f"unknown kind: {kind}"
        except Exception as e:
            text = ""
            err = f"{type(e).__name__}: {e}"
        elapsed = (time.time() - t0) * 1000

        keyword_hits = {kw: (kw in text) for kw in keywords}

        if expect_error:
            ok = err is not None and len(text) == 0
        else:
            # text 가 있고, keyword 가 있다면 모두 매칭 (없으면 본문 존재만 검사)
            ok = (
                len(text) > 0
                and (not keywords or all(keyword_hits.values()))
                and not err
            )

        out.append(PhaseHResult(
            kind=kind,
            args=args,
            ok=ok,
            elapsed_ms=round(elapsed, 1),
            char_count=len(text),
            error=err,
            keyword_hits=keyword_hits,
        ))
    return out


# ──────────────────────────────────────────────────────────────────
# 메인
# ──────────────────────────────────────────────────────────────────

def _check_sync_state() -> tuple[dict, Optional[str]]:
    """file_hashes.json 의 chunker_version 분포 + 경고 메시지."""
    meta = load_metadata(METADATA_PATH)
    versions: dict[str, int] = defaultdict(int)
    stale_files: list[str] = []
    for k, v in meta.items():
        if not v.get("indexed", False):
            continue
        ver = v.get("chunker_version", "?")
        versions[ver] += 1
        if ver != CHUNKER_VERSION:
            stale_files.append(Path(k).name)

    warning: Optional[str] = None
    if stale_files:
        warning = (
            f"인덱스가 stale 입니다: 코드 chunker_version={CHUNKER_VERSION} 인데 "
            f"인덱스 안 일부 파일은 {dict(versions)} 입니다. "
            f"streamlit '📂 동기화' 버튼으로 최신 chunker 적용 후 재실행해야 정확한 진단이 됩니다. "
            f"({len(stale_files)}개 파일 stale)"
        )
    return dict(versions), warning


def _format_page_ranges(pages: list[int], max_show: int = 30) -> tuple[str, list[tuple[int, int]]]:
    """연속 페이지 구간 묶음 + 첫 max_show 개 페이지."""
    if not pages:
        return "(없음)", []
    pages = sorted(pages)
    ranges: list[tuple[int, int]] = []
    s = e = pages[0]
    for p in pages[1:]:
        if p == e + 1:
            e = p
        else:
            ranges.append((s, e))
            s = e = p
    ranges.append((s, e))
    range_strs = []
    for s, e in ranges[:20]:
        range_strs.append(f"{s}" if s == e else f"{s}-{e}")
    suffix = "" if len(ranges) <= 20 else f" ... ({len(ranges)} 구간)"
    return ", ".join(range_strs) + suffix, ranges


def main() -> int:
    parser = argparse.ArgumentParser(description="인덱스 품질 진단")
    parser.add_argument("--json", action="store_true", help="JSON 형식으로 출력")
    parser.add_argument("--skip-page", action="store_true",
                        help="페이지 커버리지 분석 생략 (pdfplumber 30~60초 소요)")
    parser.add_argument("--skip-phase-h", action="store_true",
                        help="Phase H 도구 검증 생략")
    args = parser.parse_args()

    started = time.time()
    print("=" * 78)
    print("인덱스 품질 진단 (coverage_report)")
    print("=" * 78)

    # 0. sync 상태
    meta_versions, sync_warning = _check_sync_state()
    if sync_warning:
        print(f"\n⚠ {sync_warning}")
    print(f"\n  [meta] chunker_version 분포: {meta_versions}")
    print(f"  [code] 현재 CHUNKER_VERSION = {CHUNKER_VERSION}")

    # 1. Qdrant 청크 전체 로드
    print("\n[1/4] Qdrant 청크 전체 로드...")
    from qdrant_client import QdrantClient
    client = QdrantClient(path=os.getenv("QDRANT_PATH", "./qdrant_storage"))
    try:
        payloads = _scroll_all_chunks(client)
    finally:
        client.close()
    print(f"  총 {len(payloads)} 청크")

    # 2. 페이지 커버리지
    page_results: list[PageCoverage] = []
    if not args.skip_page:
        print("\n[2/4] 페이지 커버리지 분석...")
        page_results = diagnose_page_coverage(payloads)
    else:
        print("\n[2/4] 페이지 커버리지 — SKIP")

    # 3. 조문 커버리지
    print("\n[3/4] 조문 커버리지 분석...")
    art_result = diagnose_articles(payloads)

    # 4. 문서 다양성 + 토픽 검색 sample
    print("\n[4/4] doc_name 다양성 + 토픽 검색 sample...")
    div_result = diagnose_doc_diversity(payloads)

    # 5. Phase H 도구 검증
    phase_h: list[PhaseHResult] = []
    if not args.skip_phase_h:
        print("\n[+] Phase H 도구 검증...")
        phase_h = diagnose_phase_h()

    # ── 출력 ──────────────────────────────────────────────
    diagnostics: list[str] = []
    overall_ok = True

    print("\n" + "=" * 78)
    print("=== 페이지 커버리지 ===")
    print("=" * 78)
    for pr in page_results:
        if pr.error:
            print(f"  ✗ {pr.pdf_name}: ERROR — {pr.error}")
            diagnostics.append(f"page-coverage error: {pr.error}")
            overall_ok = False
            continue
        coverage_pct = (pr.pages_with_chunks / pr.total_pages * 100) if pr.total_pages else 0
        print(f"  {pr.pdf_name}")
        print(f"    전체 페이지       : {pr.total_pages}")
        print(f"    청크 있는 페이지  : {pr.pages_with_chunks} ({coverage_pct:.1f}%)")
        print(f"    빈 페이지         : {len(pr.empty_pages)}")
        print(f"      ㄴ 진짜 빈 페이지 (text<{_PAGE_TEXT_NONEMPTY_THRESH}자): {len(pr.truly_blank_pages)}")
        print(f"      ㄴ 의심 (텍스트 있는데 누락): {len(pr.suspect_pages)} ← chunker 결함 가능성")
        if pr.suspect_pages:
            ranges_str, _ = _format_page_ranges(pr.suspect_pages, max_show=30)
            print(f"        의심 페이지 구간: {ranges_str}")
            first_30 = pr.suspect_pages[:30]
            print(f"        첫 30개: {first_30}")
        if len(pr.suspect_pages) >= _SUSPECT_PAGE_THRESH:
            diagnostics.append(
                f"⚠ 의심 페이지 {len(pr.suspect_pages)}개 ≥ {_SUSPECT_PAGE_THRESH} — "
                "chunker 회귀 가능 (강제분할 페이지 매핑 점검 필요)"
            )
            overall_ok = False
        elif pr.suspect_pages:
            diagnostics.append(
                f"의심 페이지 {len(pr.suspect_pages)}개 (임계 {_SUSPECT_PAGE_THRESH} 미만 — 무시)"
            )

    print("\n" + "=" * 78)
    print("=== 조문 커버리지 ===")
    print("=" * 78)
    a = art_result
    print(f"  총 청크                   : {a.total_chunks}")
    print(f"  고유 article_no           : {a.unique_article_nos}")
    print(f"  강제분할 비율             : {a.forced_split_ratio*100:.1f}% ({a.forced_split_count}/{a.total_chunks})")
    print(f"  매뉴얼 doc 의 별표 청크   : {a.manual_byeolpyo_in_manual}개"
          f"  {'(정상)' if a.manual_byeolpyo_in_manual <= _MANUAL_BYEOLPYO_MAX else '⚠ 회귀 의심'}")
    if a.manual_byeolpyo_in_manual > _MANUAL_BYEOLPYO_MAX:
        diagnostics.append(
            f"⚠ 매뉴얼 doc 안에 별표 article_no 청크 {a.manual_byeolpyo_in_manual}개 "
            f"(임계 {_MANUAL_BYEOLPYO_MAX}) — G2.1 패치 회귀 가능"
        )
        overall_ok = False

    # 같은 article_no 가 여러 doc 에 있는지 (정보용)
    multi_doc_articles = [
        (art, docs) for art, docs in a.article_no_to_docs.items()
        if len(docs) > 1 and art and not art.startswith("(part") and "FAQ" not in art
    ]
    if multi_doc_articles:
        print(f"  중복 article_no (≥2 docs): {len(multi_doc_articles)}개")
        for art, docs in multi_doc_articles[:5]:
            doc_str = ", ".join(d[:25] for d in docs)
            print(f"    {art[:30]:<30s}  → {doc_str}")

    print("\n" + "=" * 78)
    print("=== doc_name 분포 ===")
    print("=" * 78)
    total_chunks = sum(div_result.doc_name_counts.values()) or 1
    for doc, n in list(div_result.doc_name_counts.items())[:10]:
        pct = n / total_chunks * 100
        print(f"  {doc[:60]:<60s} {n:>5d}  ({pct:.1f}%)")

    # 단일 doc 90% 이상 편향 시 경고
    top_doc, top_n = next(iter(div_result.doc_name_counts.items())) if div_result.doc_name_counts else ("", 0)
    top_pct = top_n / total_chunks * 100 if total_chunks else 0
    if top_pct >= 95:
        diagnostics.append(
            f"doc_name 편향 — 상위 1개 문서가 {top_pct:.0f}% 차지 (코퍼스 다양성 부족)"
        )

    print("\n" + "=" * 78)
    print("=== 토픽 검색 sample ===")
    print("=" * 78)
    for s in div_result.topic_samples:
        if "error" in s:
            print(f"  ✗ {s['query']}: {s['error']}")
            continue
        print(f"  q: {s['query']!r}")
        print(f"     top1: {s['top1_doc'][:35]} / {s['top1_article'][:25]} / p.{s['top1_page']}")
        print(f"     top-{s['top_k']} 안 unique docs: {s['unique_docs']}  분포: {s['doc_counts']}")
        print(f"     elapsed: {s['elapsed_ms']}ms")

    print("\n" + "=" * 78)
    print("=== Phase H 도구 검증 ===")
    print("=" * 78)
    for r in phase_h:
        marker = "✓" if r.ok else "✗"
        args_short = ", ".join(f"{k}={v!r}" for k, v in r.args.items())
        print(f"  {marker} {r.kind}({args_short})  {r.elapsed_ms:.0f}ms  chars={r.char_count}")
        if r.error:
            print(f"      error: {r.error}")
        if r.keyword_hits:
            hit_str = ", ".join(f"{k}={'O' if v else 'X'}" for k, v in r.keyword_hits.items())
            print(f"      keywords: {hit_str}")
        if not r.ok:
            overall_ok = False
            diagnostics.append(f"Phase H {r.kind} 실패 — args={r.args}")

    # ── 종합 ──
    print("\n" + "=" * 78)
    print("=== 종합 진단 ===")
    print("=" * 78)
    if not diagnostics:
        print("  ✓ 모든 시그널 정상")
    else:
        for d in diagnostics:
            print(f"  - {d}")

    elapsed_sec = time.time() - started
    print(f"\n  소요 시간: {elapsed_sec:.1f}s")
    print(f"  결과: {'OK' if overall_ok else 'NEEDS-ATTENTION'}")

    if args.json:
        report = Report(
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            chunker_version=CHUNKER_VERSION,
            metadata_chunker_versions=meta_versions,
            sync_warning=sync_warning,
            pages=page_results,
            articles=art_result,
            diversity=div_result,
            phase_h=phase_h,
            overall_ok=overall_ok,
            diagnostics=diagnostics,
        )
        print("\n" + json.dumps(asdict(report), ensure_ascii=False, indent=2, default=str))

    return 0 if overall_ok else 1


if __name__ == "__main__":
    sys.exit(main())
