"""
PDF → Markdown 변환 스크립트
pdfplumber로 텍스트 + 표를 추출해 마크다운으로 저장
"""
import re
import sys
from pathlib import Path
import pdfplumber


def table_to_md(table: list[list]) -> str:
    """pdfplumber 표 → 마크다운 테이블 문자열"""
    rows = []
    for row in table:
        cells = [str(c).replace("\n", " ").strip() if c else "" for c in row]
        rows.append("| " + " | ".join(cells) + " |")
    if not rows:
        return ""
    # 헤더 구분선 삽입
    col_count = len(table[0]) if table else 0
    separator = "| " + " | ".join(["---"] * col_count) + " |"
    rows.insert(1, separator)
    return "\n".join(rows)


def get_table_bboxes(page) -> list:
    """표가 차지하는 영역(bbox) 목록 반환"""
    try:
        return [t.bbox for t in page.find_tables()]
    except Exception:
        return []


def text_outside_tables(page, table_bboxes: list) -> str:
    """표 영역을 제외한 텍스트만 추출"""
    if not table_bboxes:
        return page.extract_text() or ""

    # 표 bbox를 제외하고 텍스트 추출
    filtered = page
    for bbox in table_bboxes:
        try:
            filtered = filtered.filter(
                lambda obj, b=bbox: not (
                    obj.get("x0", 0) >= b[0] - 2 and
                    obj.get("x1", 0) <= b[2] + 2 and
                    obj.get("top", 0) >= b[1] - 2 and
                    obj.get("bottom", 0) <= b[3] + 2
                )
            )
        except Exception:
            pass
    return filtered.extract_text() or ""


def is_header_footer(line: str) -> bool:
    s = line.strip()
    if not s:
        return True
    if re.fullmatch(r"-?\s*\d+\s*-?", s):
        return True
    if len(s) <= 5 and re.search(r"\d", s):
        return True
    return False


def clean_text(text: str) -> str:
    lines = text.splitlines()
    # 앞뒤 2줄 머리말/꼬리말 제거
    start = 0
    for i in range(min(2, len(lines))):
        if is_header_footer(lines[i]):
            start = i + 1
        else:
            break
    end = len(lines)
    for i in range(1, min(3, len(lines)) + 1):
        if is_header_footer(lines[-i]):
            end = len(lines) - i
        else:
            break
    return "\n".join(lines[start:end]).strip()


def add_md_headings(text: str) -> str:
    """조문 번호에 마크다운 헤딩 적용"""
    lines = []
    for line in text.splitlines():
        s = line.strip()
        # 제N조 또는 제N조(제목) → ## 헤딩
        if re.match(r"^제\d+조(?:의\d+)?(?:\s*\([^)]*\))?$", s):
            lines.append(f"\n## {s}")
        elif re.match(r"^별표\s*\d*\s*", s) and len(s) < 30:
            lines.append(f"\n### {s}")
        elif re.match(r"^부\s*칙", s) and len(s) < 20:
            lines.append(f"\n### {s}")
        else:
            lines.append(line)
    return "\n".join(lines)


def convert_pdf_to_md(pdf_path: str, output_path: str) -> None:
    pdf_path = Path(pdf_path)
    output_path = Path(output_path)

    print(f"변환 시작: {pdf_path.name}")

    parts = []
    parts.append(f"# {pdf_path.stem}\n")
    parts.append(f"> 출처: {pdf_path.name}\n")
    parts.append("---\n")

    with pdfplumber.open(pdf_path) as pdf:
        total = len(pdf.pages)
        print(f"총 {total}페이지")

        for i, page in enumerate(pdf.pages, start=1):
            if i % 50 == 0:
                print(f"  {i}/{total}페이지 처리 중...")

            page_parts = [f"\n<!-- PAGE {i} -->\n"]

            # 표 영역 감지
            table_bboxes = get_table_bboxes(page)

            # 표 외 텍스트
            raw_text = text_outside_tables(page, table_bboxes)
            if raw_text:
                cleaned = clean_text(raw_text)
                if cleaned:
                    headed = add_md_headings(cleaned)
                    page_parts.append(headed)

            # 표 추출 → 마크다운 테이블
            tables = page.extract_tables() or []
            for tbl in tables:
                if tbl and any(any(cell for cell in row) for row in tbl):
                    md_tbl = table_to_md(tbl)
                    if md_tbl:
                        page_parts.append("\n" + md_tbl + "\n")

            parts.append("\n".join(page_parts))

    md_content = "\n".join(parts)

    # 연속 빈 줄 3개 이상 → 2개로 압축
    md_content = re.sub(r"\n{4,}", "\n\n\n", md_content)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(md_content, encoding="utf-8")

    size_kb = output_path.stat().st_size // 1024
    print(f"완료: {output_path}")
    print(f"파일 크기: {size_kb:,} KB / 총 문자: {len(md_content):,}자")


if __name__ == "__main__":
    pdf = r"C:\Users\ssy49\Desktop\Chat-bot\data\uploads\[본권] 25년도 국가연구개발혁신법 매뉴얼_배포용.pdf"
    out = r"C:\Users\ssy49\Desktop\Chat-bot\data\md\[본권] 25년도 국가연구개발혁신법 매뉴얼_배포용.md"
    convert_pdf_to_md(pdf, out)
