import re
import json
import uuid
from pathlib import Path
from dataclasses import dataclass, asdict, field
from typing import Optional

import pandas as pd

from pipeline.pdf_parser import ParseResult, ParsedPage


# 조문 구조 분할 패턴 (우선순위 순)
ARTICLE_PATTERNS = [
    r"(제\d+조(?:의\d+)?(?:\s*\([^)]*\))?)",   # 제N조, 제N조의N, 제N조(제목)
    r"(별표\s*\d+)",                              # 별표N
    r"(별표(?!\s*\d))",                           # 별표 (번호 없는)
    r"(부\s*칙)",                                 # 부칙
]

# FAQ 분할 패턴
FAQ_PATTERNS = [
    r"((?:Q|질문|문)\s*[\.\)：:]?\s*\d*\s*)",
    r"(○\s*질의|○\s*답변)",
]

# 최소/최대 청크 글자 수
MIN_CHUNK_LEN = 20
MAX_CHUNK_LEN = 2000


@dataclass
class ChunkMetadata:
    chunk_id: str
    doc_name: str
    doc_type: str
    article_no: str
    article_title: str
    page: int
    effective_date: str
    revised_date: str
    is_current: bool
    source_file: str
    text: str


def _build_page_boundary_map(pages: list[ParsedPage]) -> list[tuple[int, int]]:
    """
    각 페이지의 (시작_char_offset, page_num) 매핑 반환.
    전체 텍스트에서 특정 offset이 몇 페이지인지 조회할 때 사용.
    """
    boundaries = []
    offset = 0
    for p in pages:
        boundaries.append((offset, p.page_num))
        offset += len(p.text) + 1  # +1 for '\n' separator
    return boundaries


def _offset_to_page(offset: int, boundaries: list[tuple[int, int]]) -> int:
    """char offset → page_num 변환."""
    page_num = 1
    for start, pnum in boundaries:
        if offset >= start:
            page_num = pnum
        else:
            break
    return page_num


def _extract_article_title(text_after_header: str) -> str:
    """조문 헤더 직후 줄에서 제목 추출 (괄호 안 제목 또는 첫 줄)."""
    lines = text_after_header.strip().splitlines()
    if not lines:
        return ""
    first = lines[0].strip()
    # 제목이 괄호 안에 있는 경우: 제13조(연구개발비의 사용)
    m = re.search(r"\(([^)]{2,30})\)", first)
    if m:
        return m.group(1)
    # 첫 줄이 짧으면 제목으로 간주
    if len(first) <= 30:
        return first
    return ""


def _split_by_articles(full_text: str, boundaries: list[tuple[int, int]]) -> list[dict]:
    """
    조문 단위 분할. 반환: [{text, article_no, article_title, page}, ...]
    """
    combined_pattern = "|".join(ARTICLE_PATTERNS)
    splits = list(re.finditer(combined_pattern, full_text))

    if not splits:
        # 조문 패턴 없으면 전체를 하나의 청크로
        return [{
            "text": full_text.strip(),
            "article_no": "",
            "article_title": "",
            "page": boundaries[0][1] if boundaries else 1,
        }]

    chunks = []
    for idx, match in enumerate(splits):
        start = match.start()
        end = splits[idx + 1].start() if idx + 1 < len(splits) else len(full_text)
        chunk_text = full_text[start:end].strip()

        article_no = match.group().strip()
        article_title = _extract_article_title(full_text[match.end():match.end() + 100])
        page = _offset_to_page(start, boundaries)

        chunks.append({
            "text": chunk_text,
            "article_no": article_no,
            "article_title": article_title,
            "page": page,
        })

    # 첫 번째 분할 이전 서문 처리
    preamble = full_text[:splits[0].start()].strip()
    if len(preamble) >= MIN_CHUNK_LEN:
        chunks.insert(0, {
            "text": preamble,
            "article_no": "서문",
            "article_title": "",
            "page": boundaries[0][1] if boundaries else 1,
        })

    return chunks


def _split_faq(full_text: str, boundaries: list[tuple[int, int]]) -> list[dict]:
    """FAQ 문서: 1문1답 단위로 분할."""
    combined = "|".join(FAQ_PATTERNS)
    splits = list(re.finditer(combined, full_text, re.IGNORECASE))

    if not splits:
        return _split_by_articles(full_text, boundaries)

    chunks = []
    for idx, match in enumerate(splits):
        start = match.start()
        end = splits[idx + 1].start() if idx + 1 < len(splits) else len(full_text)
        chunk_text = full_text[start:end].strip()
        page = _offset_to_page(start, boundaries)
        chunks.append({
            "text": chunk_text,
            "article_no": f"FAQ-{idx + 1}",
            "article_title": "",
            "page": page,
        })
    return chunks


def chunk_document(
    parse_result: ParseResult,
    doc_name: str,
    doc_type: str,
    effective_date: str = "",
    revised_date: str = "",
    is_current: bool = True,
) -> list[ChunkMetadata]:
    """
    ParseResult → ChunkMetadata 리스트 반환.
    doc_type이 'FAQ'면 FAQ 분할 전략 사용.
    """
    pages = [p for p in parse_result.pages if p.text.strip()]
    full_text = "\n".join(p.text for p in pages)
    boundaries = _build_page_boundary_map(pages)

    if doc_type.upper() == "FAQ":
        raw_chunks = _split_faq(full_text, boundaries)
    else:
        raw_chunks = _split_by_articles(full_text, boundaries)

    results = []
    skipped = 0
    warned_long = 0

    for rc in raw_chunks:
        text = rc["text"].strip()
        if len(text) < MIN_CHUNK_LEN:
            skipped += 1
            continue
        if len(text) > MAX_CHUNK_LEN:
            warned_long += 1
            try:
                print(f"  [경고] 청크 길이 초과 ({len(text)}자) - article_no: {rc['article_no']} page: {rc['page']}")
            except UnicodeEncodeError:
                print(f"  [경고] 청크 길이 초과 ({len(text)}자) - page: {rc['page']}")

        results.append(ChunkMetadata(
            chunk_id=str(uuid.uuid4()),
            doc_name=doc_name,
            doc_type=doc_type,
            article_no=rc["article_no"],
            article_title=rc["article_title"],
            page=rc["page"],
            effective_date=effective_date,
            revised_date=revised_date,
            is_current=is_current,
            source_file=parse_result.source_file,
            text=text,
        ))

    if skipped:
        print(f"  너무 짧은 청크 {skipped}개 제외됨")
    if warned_long:
        print(f"  [주의] 긴 청크 {warned_long}개 - 표/별표 수동 확인 권장")

    return results


def save_chunks(
    chunks: list[ChunkMetadata],
    stem: str,
    base_dir: str | Path = ".",
) -> tuple[Path, Path]:
    """
    chunks.json + metadata.csv 저장.
    Returns: (chunks_path, metadata_path)
    """
    base = Path(base_dir)
    chunks_dir = base / "data" / "chunks"
    meta_dir = base / "data" / "metadata"
    chunks_dir.mkdir(parents=True, exist_ok=True)
    meta_dir.mkdir(parents=True, exist_ok=True)

    chunks_path = chunks_dir / f"{stem}_chunks.json"
    meta_path = meta_dir / f"{stem}_metadata.csv"

    data = [asdict(c) for c in chunks]

    with open(chunks_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    df = pd.DataFrame(data).drop(columns=["text"])
    df.to_csv(meta_path, index=False, encoding="utf-8-sig")

    print(f"  청크 저장: {chunks_path} ({len(chunks)}개)")
    print(f"  메타데이터 저장: {meta_path}")

    return chunks_path, meta_path
