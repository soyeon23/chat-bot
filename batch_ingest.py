"""
전체 PDF 일괄 인덱싱 스크립트

파일명/폴더명에서 doc_name, doc_type, 날짜를 자동 추출해
모든 PDF를 Qdrant에 순차 인덱싱한다.
"""

import re
import sys
from dataclasses import asdict
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).parent

# ── 이미 인덱싱 완료된 문서 추적 (파일 기반) ─────────────────────────────────
DONE_LOG = BASE_DIR / "data" / "ingest_done.txt"


def load_done() -> set[str]:
    if DONE_LOG.exists():
        return set(DONE_LOG.read_text(encoding="utf-8").splitlines())
    return set()


def mark_done(file_path: Path) -> None:
    DONE_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(DONE_LOG, "a", encoding="utf-8") as f:
        f.write(str(file_path) + "\n")


# ── 메타데이터 자동 추출 ─────────────────────────────────────────────────────

def _extract_date(text: str) -> str:
    """파일명에서 YYYYMMDD 추출 → YYYY-MM-DD 변환"""
    m = re.search(r"\((\d{8})\)", text)
    if m:
        d = m.group(1)
        return f"{d[:4]}-{d[4:6]}-{d[6:]}"
    return ""


_HWP_EXTS = {".hwp", ".hwpx"}


def _is_hwp(path: Path) -> bool:
    return path.suffix.lower() in _HWP_EXTS


def _infer_doc_type(name: str) -> str:
    n = name
    if re.search(r"법률|법\(", n):
        return "법률"
    if "시행령" in n or "대통령령" in n:
        return "시행령"
    if "시행규칙" in n:
        return "시행규칙"
    if re.search(r"운영요령|관리요령|특별요령|보안관리요령", n):
        return "운영요령"
    if re.search(r"지침|예규", n):
        return "운영요령"
    if re.search(r"매뉴얼|manual|가이드", n, re.I):
        return "가이드"
    if re.search(r"공고|고시", n):
        return "운영요령"
    return "운영요령"


def _clean_doc_name(stem: str) -> str:
    """파일 stem에서 날짜·기관코드 괄호 제거 → 핵심 문서명 추출"""
    # 끝의 (날짜), (기관코드번호) 등 제거
    name = re.sub(r"\s*\([^)]*\d{6,}[^)]*\)\s*$", "", stem).strip()
    name = re.sub(r"\s*\([^)]*\d{4}\)\s*$", "", name).strip()
    # 별지/별표 서식인 경우 괄호 안 상위 문서명 추출
    m = re.search(r"\(([^)]{5,})\)\s*$", name)
    if m and not re.search(r"\d", m.group(1)):
        return m.group(1).strip()
    # 남은 날짜 괄호 추가 정리
    name = re.sub(r"\s*\([^)]*\)\s*$", "", name).strip()
    return name or stem


def get_metadata(pdf_path: Path) -> dict:
    """파일명에서 doc_name / doc_type / 날짜 추출 (PDF·HWP 공용)."""
    stem = pdf_path.stem
    date = _extract_date(stem)

    # 별지/별표/서식: 상위 문서명을 doc_name으로
    is_sub = bool(re.match(r"\[(별지|별표|서식|부록)", stem))

    if is_sub:
        # 괄호 안 마지막 항목이 상위 문서명
        m = re.findall(r"\(([^)]+)\)", stem)
        parent_candidates = [x for x in m if not re.search(r"\d{6,}", x) and len(x) > 4]
        doc_name = parent_candidates[-1] if parent_candidates else _clean_doc_name(stem)
    else:
        doc_name = _clean_doc_name(stem)

    doc_type = _infer_doc_type(stem)

    return {
        "doc_name": doc_name,
        "doc_type": doc_type,
        "effective_date": date,
        "revised_date": date,
        "is_current": True,
    }


# ── 단일 PDF 인덱싱 ──────────────────────────────────────────────────────────

def ingest_one(pdf_path: Path) -> bool:
    """PDF 또는 HWP/HWPX 파일 1개를 인덱싱한다 (확장자로 자동 분기)."""
    meta = get_metadata(pdf_path)
    stem = pdf_path.stem

    print(f"  doc_name : {meta['doc_name']}")
    print(f"  doc_type : {meta['doc_type']}  |  날짜: {meta['effective_date'] or '미상'}")

    try:
        if _is_hwp(pdf_path):
            from pipeline.hwp_parser import parse_hwp
            result = parse_hwp(pdf_path, save_raw=False)
        else:
            from pipeline.pdf_parser import parse_pdf, validate_parse_result
            result = parse_pdf(pdf_path, save_raw=False)
            validate_parse_result(result)

        if not result.pages or not result.full_text().strip():
            print("  [건너뜀] 텍스트 없음")
            return False

        from pipeline.chunker import chunk_document, save_chunks
        chunks = chunk_document(
            parse_result=result,
            doc_name=meta["doc_name"],
            doc_type=meta["doc_type"],
            effective_date=meta["effective_date"],
            revised_date=meta["revised_date"],
            is_current=meta["is_current"],
        )
        if not chunks:
            print("  [건너뜀] 청크 생성 실패")
            return False

        save_chunks(chunks, stem, BASE_DIR)
        chunks_meta = [asdict(c) for c in chunks]

        from pipeline.embedder import embed_chunks, validate_embeddings
        embedded = embed_chunks(chunks_meta)
        validate_embeddings(embedded)

        from pipeline.indexer import upsert_chunks
        upserted = upsert_chunks(chunks_meta, embedded)

        print(f"  → 완료: {len(chunks)}청크 / {upserted}포인트 적재")
        return True

    except Exception as e:
        print(f"  [오류] {type(e).__name__}: {e}")
        return False


# ── 메인 ────────────────────────────────────────────────────────────────────

def main() -> None:
    force = "--force" in sys.argv

    # 대상 파일: versions/ 제외, chunks·raw·metadata 하위 폴더 제외
    # data/uploads/ 는 포함 (앱 업로드 파일)
    _EXCLUDE_PARTS = {"versions", "chunks", "raw", "metadata"}

    def _filter(paths: list[Path]) -> list[Path]:
        return [
            p for p in paths
            if not _EXCLUDE_PARTS.intersection(p.parts)
            and p.exists()
            and p.stat().st_size > 1000  # 1KB 미만 빈 파일 제외
        ]

    pdf_files = _filter(sorted(BASE_DIR.rglob("*.pdf")))

    # HWP/HWPX — config 의 hwp_mcp_enabled 토글이 ON 일 때만 수집한다.
    # 검색 위치: BASE_DIR(프로젝트 루트) + cfg.hwp_dir(있다면).
    hwp_files: list[Path] = []
    try:
        from pipeline.config_store import load_config
        cfg = load_config()
        if cfg.hwp_mcp_enabled:
            roots: list[Path] = [BASE_DIR]
            if cfg.hwp_dir:
                extra = Path(cfg.hwp_dir).expanduser()
                if extra.exists() and extra.resolve() != BASE_DIR.resolve():
                    roots.append(extra)
            seen: set[Path] = set()
            for root in roots:
                for ext in _HWP_EXTS:
                    for p in sorted(root.rglob(f"*{ext}")):
                        rp = p.resolve()
                        if rp not in seen:
                            seen.add(rp)
                            hwp_files.append(p)
            hwp_files = _filter(hwp_files)
    except Exception as e:
        print(f"  [경고] HWP 수집 단계 실패 (무시하고 PDF만 진행): {e}")
        hwp_files = []

    all_files = pdf_files + hwp_files

    done = load_done()
    pending = [p for p in all_files if force or str(p) not in done]

    print(
        f"전체 PDF: {len(pdf_files)}개  |  HWP: {len(hwp_files)}개  |  "
        f"완료: {len(done)}개  |  대기: {len(pending)}개"
    )
    if force:
        print("  ※ --force 모드: 완료 파일 포함 전체 재인덱싱\n")
    else:
        print()

    if not pending:
        print("모든 파일이 이미 인덱싱되어 있습니다.")
        from pipeline.indexer import get_collection_count
        print(f"Qdrant 총 포인트 수: {get_collection_count()}")
        return

    ok = fail = skip = 0
    for i, fp in enumerate(pending, 1):
        try:
            rel = fp.relative_to(BASE_DIR)
        except ValueError:
            # cfg.hwp_dir 가 BASE_DIR 바깥인 경우 절대 경로 그대로 표시
            rel = fp
        print(f"\n[{i}/{len(pending)}] {rel}")
        print("-" * 60)

        success = ingest_one(fp)
        if success:
            ok += 1
            if str(fp) not in done:  # force 재실행 시 중복 기록 방지
                mark_done(fp)
        else:
            fail += 1

    print(f"\n{'='*60}")
    print(f"인덱싱 완료: 성공 {ok}개 / 실패 {fail}개")

    from pipeline.indexer import get_collection_count
    print(f"Qdrant 총 포인트 수: {get_collection_count()}")
    print("=" * 60)


if __name__ == "__main__":
    main()
