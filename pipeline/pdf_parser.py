import pdfplumber
import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ParsedPage:
    page_num: int        # 1-indexed 실제 페이지 번호
    text: str
    needs_ocr: bool = False


@dataclass
class ParseResult:
    source_file: str
    pages: list[ParsedPage] = field(default_factory=list)
    ocr_flagged_pages: list[int] = field(default_factory=list)

    def full_text(self) -> str:
        return "\n".join(p.text for p in self.pages if p.text)


OCR_THRESHOLD = 50  # 이 글자 수 미만이면 스캔 PDF 의심


def _extract_tables_as_text(page) -> str:
    """
    pdfplumber 페이지에서 표를 추출해 마크다운 테이블 문자열로 변환.
    extract_text()가 표 셀 내용을 뭉개는 문제를 보완한다.
    """
    tables = page.extract_tables() or []
    parts = []
    for table in tables:
        rows = []
        for row in table:
            cells = [str(cell).strip() if cell else "" for cell in row]
            rows.append(" | ".join(cells))
        if rows:
            parts.append("\n".join(rows))
    return "\n\n".join(parts)


def _is_header_or_footer(line: str) -> bool:
    """
    반복 출현하는 짧은 머리말/꼬리말 패턴 감지.
    - 숫자만 있는 줄 (페이지 번호)
    - '- N -' 형식
    - 5자 이하 단독 줄
    """
    stripped = line.strip()
    if not stripped:
        return True
    if re.fullmatch(r"-?\s*\d+\s*-?", stripped):
        return True
    if len(stripped) <= 5 and re.search(r"\d", stripped):
        return True
    return False


def _clean_page_text(lines: list[str]) -> str:
    """첫 줄·마지막 줄이 머리말/꼬리말이면 제거."""
    if not lines:
        return ""
    # 앞쪽 최대 2줄 검사
    start = 0
    for i in range(min(2, len(lines))):
        if _is_header_or_footer(lines[i]):
            start = i + 1
        else:
            break
    # 뒤쪽 최대 2줄 검사
    end = len(lines)
    for i in range(1, min(3, len(lines)) + 1):
        if _is_header_or_footer(lines[-i]):
            end = len(lines) - i
        else:
            break
    return "\n".join(lines[start:end]).strip()


def parse_pdf(pdf_path: str | Path, save_raw: bool = True) -> ParseResult:
    """
    PDF를 페이지 단위로 파싱한다.

    Args:
        pdf_path: PDF 파일 경로
        save_raw: True면 data/raw/<stem>_raw.txt 에 원문 저장

    Returns:
        ParseResult (pages 리스트, OCR 필요 페이지 목록 포함)
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF 파일을 찾을 수 없습니다: {pdf_path}")

    result = ParseResult(source_file=pdf_path.name)

    with pdfplumber.open(pdf_path) as pdf:
        total = len(pdf.pages)
        print(f"  총 {total}페이지 감지됨")

        for i, page in enumerate(pdf.pages, start=1):
            raw = page.extract_text(layout=True) or ""
            lines = raw.splitlines()
            cleaned = _clean_page_text(lines)

            # 표(table) 별도 추출 후 텍스트에 병합
            table_text = _extract_tables_as_text(page)
            if table_text:
                cleaned = cleaned + "\n" + table_text if cleaned.strip() else table_text

            needs_ocr = len(cleaned.strip()) < OCR_THRESHOLD
            if needs_ocr:
                result.ocr_flagged_pages.append(i)
                print(f"  [경고] {i}페이지 텍스트 부족 ({len(cleaned.strip())}자) - OCR 필요 가능성")

            result.pages.append(ParsedPage(
                page_num=i,
                text=cleaned,
                needs_ocr=needs_ocr,
            ))

    if save_raw:
        raw_dir = pdf_path.parent.parent / "data" / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        out_path = raw_dir / f"{pdf_path.stem}_raw.txt"
        with open(out_path, "w", encoding="utf-8") as f:
            for p in result.pages:
                f.write(f"=== PAGE {p.page_num} ===\n")
                f.write(p.text + "\n\n")
        print(f"  원문 저장: {out_path}")

    return result


def validate_parse_result(result: ParseResult) -> None:
    """파싱 결과 기본 검증 - 조문번호 존재 여부 확인."""
    full = result.full_text()
    article_hits = re.findall(r"제\d+조", full)
    appendix_hits = re.findall(r"별표\s*\d*", full)
    addendum_hits = re.findall(r"부칙", full)

    print(f"  검증 - 조 발견: {len(article_hits)}개 | 별표: {len(appendix_hits)}개 | 부칙: {len(addendum_hits)}개")
    if not article_hits:
        print("  [경고] 조문번호(제N조)가 하나도 감지되지 않았습니다. 파싱 결과를 육안 확인하세요.")
