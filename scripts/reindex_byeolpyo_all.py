"""별표 6개 파일 재인덱싱 스크립트 (Phase G2).

목적:
    chunker 의 별표 라우팅 분기(옵션 A) 추가 후, 시행령 별표 1·2·4·5·6·7
    HWP 파일을 모두 재파싱·재청킹·재임베딩·재upsert. 다른 문서 청크는 건드리지 않는다.
    별표 3 은 본문이 "삭제" 만 있어 의미 단위 청크가 나오지 않으므로 제외.

흐름:
    파일별로
        1) 기존 인덱스에서 source_file 매칭 청크 삭제
        2) hwp-mcp 로 파싱 → 새 chunker 로 청킹 → 임베딩 → 업서트
        3) 파일 단위 메트릭 누적
    마지막에 메트릭 표 + 합격기준 평가 출력.

실행:
    cd /Users/maro/dev/company/chatbot
    source .venv/bin/activate
    python scripts/reindex_byeolpyo_all.py

종료 코드:
    0 — 합격기준 모두 충족
    1 — 합격기준 미달

주의:
    - 매뉴얼 PDF / 시행령 본체 / 시행규칙 본체 청크는 손대지 않는다.
    - Streamlit 이 떠 있으면 스크립트 시작 시 SIGSTOP, 종료 시 SIGCONT 로 재개한다.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import unicodedata
from collections import Counter
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

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SIRYEONG_DIR = PROJECT_ROOT / "국가연구개발혁신법 시행령(대통령령)(제36163호)(20260310)"

# 별표 3 은 "삭제" 만 있어 의미 청크가 안 나옴 → 제외 (Phase G2 범위)
BYEOLPYO_NUMBERS = [1, 2, 4, 5, 6, 7]

QDRANT_PATH = "./qdrant_storage"
COLLECTION = "rnd_law_chunks"

DOC_TYPE = "시행령"
EFFECTIVE_DATE = "2026-03-10"
REVISED_DATE = "2026-03-10"

# 별표 N → (파일 stem 검색용 prefix, 사람-친화적 doc_name 후미)
# 실제 파일명 prefix 로 매칭한다 ("[별표 N] " 시작).

# G1 baseline (별표 2): 5,741자 / 4 청크 → G2 후 의미 단위 개선 확인용 비교.
G1_BASELINE_BY2 = {"chunks": 4, "text_len": 5741}


# ──────────────────────────────────────────────────────────────────
# Streamlit pause/resume
# ──────────────────────────────────────────────────────────────────

def _pgrep_streamlit() -> list[int]:
    try:
        out = subprocess.run(
            ["pgrep", "-f", "streamlit"],
            capture_output=True,
            text=True,
            check=False,
        )
        return [int(p) for p in out.stdout.strip().splitlines() if p.strip().isdigit()]
    except Exception:
        return []


def _stop_streamlit() -> list[int]:
    """Streamlit 프로세스에 SIGSTOP. 종료된 PID 리스트 반환 (없으면 빈 리스트)."""
    pids = _pgrep_streamlit()
    if not pids:
        return []
    print(f"  [streamlit] 일시정지: {pids}")
    for pid in pids:
        try:
            os.kill(pid, signal.SIGSTOP)
        except ProcessLookupError:
            pass
        except PermissionError:
            print(f"  [streamlit] 권한 부족 — pid {pid} 일시정지 실패 (무시)")
    return pids


def _cont_streamlit(pids: list[int]) -> None:
    if not pids:
        return
    print(f"  [streamlit] 재개: {pids}")
    for pid in pids:
        try:
            os.kill(pid, signal.SIGCONT)
        except ProcessLookupError:
            pass
        except PermissionError:
            pass


# ──────────────────────────────────────────────────────────────────
# 헬퍼
# ──────────────────────────────────────────────────────────────────

def _nfc(s: str | None) -> str:
    return unicodedata.normalize("NFC", s) if s else ""


def _scroll_all(client: QdrantClient) -> list:
    """컬렉션 전체 scroll."""
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


def _find_byeolpyo_file(n: int) -> Path | None:
    """시행령 폴더에서 [별표 N] 으로 시작하는 hwp 파일 1개 반환.

    macOS APFS 는 한글 파일명을 NFD(자모 분해) 로 저장하므로 prefix 비교 시
    양쪽을 NFC 정규화해 매칭한다. PosixPath 는 디스크 표기 그대로 (NFD) 보존.
    """
    if not SIRYEONG_DIR.exists():
        return None
    prefix = unicodedata.normalize("NFC", f"[별표 {n}]")
    candidates = [
        p for p in SIRYEONG_DIR.iterdir()
        if p.suffix.lower() == ".hwp"
        and unicodedata.normalize("NFC", p.name).startswith(prefix)
    ]
    if not candidates:
        return None
    if len(candidates) > 1:
        print(f"  [경고] 별표 {n} 매칭 후보 다수: {[c.name for c in candidates]}")
    return candidates[0]


def _delete_existing_for_source(client: QdrantClient, target_filename: str) -> int:
    """source_file 매칭(NFC 비교) 청크 삭제. 삭제 개수 반환."""
    all_records = _scroll_all(client)
    target_nfc = _nfc(target_filename)
    to_delete = [
        str(r.id) for r in all_records
        if _nfc(r.payload.get("source_file")) == target_nfc
    ]
    if not to_delete:
        return 0
    batch = 100
    for i in range(0, len(to_delete), batch):
        client.delete(
            collection_name=COLLECTION,
            points_selector=qmodels.PointIdsList(points=to_delete[i:i + batch]),
        )
    return len(to_delete)


def _doc_name_for(n: int, fname: str) -> str:
    """별표 파일에서 doc_name 자동 생성.

    파일명: "[별표 N] 제목(...)(...)(국가연구개발혁신법 시행령).hwp"
    핵심 제목만 떼어 "국가연구개발혁신법 시행령 [별표 N] 제목" 형태로.
    macOS NFD 파일명도 받아 NFC 변환 후 정규식 매칭.
    """
    import re
    stem_nfc = unicodedata.normalize("NFC", Path(fname).stem)
    m = re.match(rf"\[\s*별표\s*{n}\s*\]\s*([^()]+)", stem_nfc)
    if m:
        title = m.group(1).strip()
        return f"국가연구개발혁신법 시행령 [별표 {n}] {title}"
    return f"국가연구개발혁신법 시행령 [별표 {n}]"


# ──────────────────────────────────────────────────────────────────
# 단일 파일 재인덱싱
# ──────────────────────────────────────────────────────────────────

def reindex_one(n: int) -> dict:
    """별표 N 단일 파일 재인덱싱. 파일 단위 메트릭 dict 반환."""
    print("\n" + "─" * 78)
    print(f"별표 {n}")
    print("─" * 78)

    src_path = _find_byeolpyo_file(n)
    if src_path is None:
        print(f"  [건너뜀] 별표 {n} 파일 없음")
        return {
            "n": n,
            "filename": "",
            "status": "missing",
            "chunk_count": 0,
            "text_len_total": 0,
            "article_no_dist": {},
        }

    target_filename = src_path.name
    doc_name = _doc_name_for(n, target_filename)
    print(f"  파일      : {target_filename}")
    print(f"  doc_name  : {doc_name}")

    # 1) 기존 청크 삭제
    client = QdrantClient(path=QDRANT_PATH)
    deleted = _delete_existing_for_source(client, target_filename)
    print(f"  기존 청크 삭제: {deleted}개")
    client.close()

    # 2) 파싱
    parse_result = parse_hwp(src_path, save_raw=False)
    if not parse_result.pages:
        print(f"  [건너뜀] 파싱 결과 빈 페이지 (HWPML 등)")
        return {
            "n": n,
            "filename": target_filename,
            "status": "parse_empty",
            "chunk_count": 0,
            "text_len_total": 0,
            "article_no_dist": {},
        }
    full_text_len = sum(len(p.text) for p in parse_result.pages)
    print(f"  파싱 길이 : {full_text_len}자")

    # 3) 청킹
    chunks = chunk_document(
        parse_result=parse_result,
        doc_name=doc_name,
        doc_type=DOC_TYPE,
        effective_date=EFFECTIVE_DATE,
        revised_date=REVISED_DATE,
        is_current=True,
    )
    if not chunks:
        print("  [건너뜀] 청킹 결과 0개")
        return {
            "n": n,
            "filename": target_filename,
            "status": "chunk_empty",
            "chunk_count": 0,
            "text_len_total": full_text_len,
            "article_no_dist": {},
        }

    article_no_counts = Counter(c.article_no for c in chunks)
    text_len_sum = sum(len(c.text) for c in chunks)
    print(f"  생성 청크 : {len(chunks)}개, 텍스트 합계 {text_len_sum}자")
    for art, cnt in article_no_counts.most_common():
        print(f"    {cnt:2d}  {art!r}")

    # 4) 임베딩
    chunks_dicts = [asdict(c) for c in chunks]
    embedded = embed_chunks(chunks_dicts)
    validate_embeddings(embedded)

    # 5) 업서트
    upsert_chunks(chunks_dicts, embedded)

    return {
        "n": n,
        "filename": target_filename,
        "status": "ok",
        "chunk_count": len(chunks),
        "text_len_total": text_len_sum,
        "article_no_dist": dict(article_no_counts),
        "doc_name": doc_name,
        "parse_len": full_text_len,
    }


# ──────────────────────────────────────────────────────────────────
# 검증
# ──────────────────────────────────────────────────────────────────

def _all_byeolpyo_records(client: QdrantClient, filenames: list[str]) -> list:
    """source_file 이 별표 6개 파일 중 하나인 records 반환."""
    targets_nfc = {_nfc(f) for f in filenames if f}
    return [
        r for r in _scroll_all(client)
        if _nfc(r.payload.get("source_file")) in targets_nfc
    ]


def verify(metrics: list[dict]) -> bool:
    """메트릭 + 합격기준 검증. PASS 면 True."""
    print("\n" + "=" * 78)
    print("Phase G2 합격기준 평가")
    print("=" * 78)

    # 합격기준 1: 모든 청크 article_no 가 '별표 N (part X/Y)' 또는 '별표 N' 형태
    article_no_uniform = True
    import re
    pattern = re.compile(r"^별표 \d+(?:\s+\(part \d+/\d+\))?$")
    for m in metrics:
        if m["status"] != "ok":
            continue
        for art in m["article_no_dist"]:
            if not pattern.match(art):
                article_no_uniform = False
                print(f"  [WARN] 별표 {m['n']} article_no 형식 이탈: {art!r}")

    # 합격기준 2: 별표 6 가중·감경 키워드가 같은 또는 인접 청크에 등장
    # 합격기준 3: 별표 2 의 핵심 7개 비목 키워드 중 5개+ 가 같은 또는 인접 청크에 분포

    # 인덱스에서 직접 확인 (Qdrant)
    filenames = [m["filename"] for m in metrics if m.get("filename")]
    client = QdrantClient(path=QDRANT_PATH)
    records = _all_byeolpyo_records(client, filenames)
    by_filename: dict[str, list] = {}
    for r in records:
        by_filename.setdefault(_nfc(r.payload.get("source_file", "")), []).append(r)

    # 별표 6 검증
    by6_filename = next(
        (m["filename"] for m in metrics if m["n"] == 6 and m.get("filename")),
        None,
    )
    by6_pass_adjacent = False
    by6_text_present = False
    if by6_filename:
        by6_records = by_filename.get(_nfc(by6_filename), [])
        # part X/Y 순서대로 정렬
        def _part_key(r) -> int:
            art = _nfc(r.payload.get("article_no", ""))
            m = re.search(r"part\s*(\d+)/", art)
            return int(m.group(1)) if m else 0
        by6_sorted = sorted(by6_records, key=_part_key)
        agg_idx = next(
            (i for i, r in enumerate(by6_sorted)
             if "가중" in _nfc(r.payload.get("text", ""))),
            None,
        )
        mit_idx = next(
            (i for i, r in enumerate(by6_sorted)
             if "감경" in _nfc(r.payload.get("text", ""))),
            None,
        )
        if agg_idx is not None and mit_idx is not None:
            by6_pass_adjacent = abs(agg_idx - mit_idx) <= 1
            by6_text_present = True
        print(f"\n  별표 6 가중·감경 인접성:")
        print(f"    가중 키워드 청크 인덱스: {agg_idx}")
        print(f"    감경 키워드 청크 인덱스: {mit_idx}")
        print(f"    [{'PASS' if by6_pass_adjacent else 'FAIL'}] 인접(|diff| ≤ 1)")

    # 별표 2 검증
    by2_filename = next(
        (m["filename"] for m in metrics if m["n"] == 2 and m.get("filename")),
        None,
    )
    by2_keyword_pass = False
    by2_text_total = 0
    if by2_filename:
        by2_records = by_filename.get(_nfc(by2_filename), [])
        joined = "\n".join(_nfc(r.payload.get("text", "")) for r in by2_records)
        by2_text_total = sum(len(_nfc(r.payload.get("text", ""))) for r in by2_records)
        keywords = [
            "인건비",
            "학생인건비",
            "연구활동비",
            "연구재료비",
            "위탁",  # 위탁연구개발비
            "국제공동",  # 국제공동연구개발비
            "연구수당",
        ]
        hits = {kw: kw in joined for kw in keywords}
        present = sum(hits.values())
        by2_keyword_pass = present >= 6  # 7개 중 6개 이상 (위탁/국제공동 표기 변동성 흡수)
        print(f"\n  별표 2 7개 비목 키워드 누락 검사:")
        for kw, ok in hits.items():
            print(f"    [{'OK' if ok else '..'}] {kw}")
        print(f"    [{'PASS' if by2_keyword_pass else 'FAIL'}] {present}/7 보존 (목표 ≥ 6)")
        print(f"  별표 2 G1→G2 비교: G1 {G1_BASELINE_BY2['chunks']}청크 / "
              f"{G1_BASELINE_BY2['text_len']}자 → G2 "
              f"{len(by2_records)}청크 / {by2_text_total}자")

    client.close()

    # 메트릭 표 출력
    print("\n  파일별 메트릭:")
    print(f"  {'별표':>4}  {'청크수':>6}  {'텍스트합':>8}  파일명")
    for m in metrics:
        print(
            f"  {m['n']:>4}  {m['chunk_count']:>6}  {m['text_len_total']:>8}  "
            f"{m['filename']}"
        )

    # 종합
    overall = (
        article_no_uniform
        and by6_pass_adjacent
        and by2_keyword_pass
        and all(m["status"] == "ok" for m in metrics)
    )
    print(f"\n  [{'PASS' if article_no_uniform else 'FAIL'}] 모든 청크 article_no 형식 통일")
    print(f"  [{'PASS' if by6_pass_adjacent else 'FAIL'}] 별표 6 가중·감경 인접")
    print(f"  [{'PASS' if by2_keyword_pass else 'FAIL'}] 별표 2 비목 키워드 ≥ 6/7 보존")
    print(f"\n  전체: {'PASS' if overall else 'FAIL'}")
    return overall


# ──────────────────────────────────────────────────────────────────
# 메인
# ──────────────────────────────────────────────────────────────────

def main() -> int:
    print("=" * 78)
    print("별표 6개 파일 재인덱싱 (Phase G2)")
    print("=" * 78)

    if not SIRYEONG_DIR.exists():
        print(f"  [ERROR] 시행령 폴더 없음: {SIRYEONG_DIR}")
        return 1

    streamlit_pids = _stop_streamlit()
    metrics: list[dict] = []
    try:
        for n in BYEOLPYO_NUMBERS:
            try:
                m = reindex_one(n)
            except Exception as e:
                import traceback
                print(f"  [ERROR] 별표 {n} 처리 실패: {type(e).__name__}: {e}")
                traceback.print_exc()
                m = {
                    "n": n, "filename": "", "status": "error",
                    "chunk_count": 0, "text_len_total": 0, "article_no_dist": {},
                }
            metrics.append(m)

        ok = verify(metrics)
    finally:
        _cont_streamlit(streamlit_pids)

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
