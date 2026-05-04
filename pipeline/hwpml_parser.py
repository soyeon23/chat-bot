"""HWPML(XML 텍스트) → ParseResult — stdlib 직접 파싱.

법제처 국가법령정보센터가 배포하는 일부 .hwp 는 OLE2 가 아니라 XML 평문
(`<?xml ... ?><!DOCTYPE HWPML ...><HWPML>...`). hwp-mcp(0.1.x) 는 OLE2
파서만 갖고 있어 이런 파일을 거부 (또는 RecursionError 로 실패) 한다.
이 모듈은 그 갭을 메우기 위해 Python stdlib `xml.etree.ElementTree` 로
본문 텍스트만 직접 추출한다.

설계:
- 파싱 결과는 `pdf_parser.ParseResult` / `ParsedPage` 와 호환 (단일 page=1).
  HWPML 에는 견고한 페이지 경계가 없어 chunker 가 `제N조` 패턴으로 분할.
- BODY → SECTION → P 구조를 walk 하며 모든 노드의 .text + .tail 합산.
  P 단위로 줄바꿈 삽입 (단락 보존).
- 본 파일은 hwp-mcp 의존성을 *전혀* 쓰지 않는다. RecursionError 등 hwp-mcp
  부작용을 우회하는 것이 본 모듈의 또 다른 목적.
"""
from __future__ import annotations

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

from pipeline.pdf_parser import ParsedPage, ParseResult


_HWPML_PREFIX = b"<?xml"


def is_hwpml_file(path: Path) -> bool:
    """확장자가 .hwp/.hwpx 든 .xml 든, 첫 5바이트가 `<?xml` 이면 HWPML 후보."""
    try:
        with open(path, "rb") as f:
            return f.read(8).startswith(_HWPML_PREFIX)
    except OSError:
        return False


def _extract_text_from_section(section: ET.Element) -> str:
    """SECTION 한 개의 모든 P 단락 텍스트를 줄바꿈으로 이어 반환."""
    paras: list[str] = []
    for p in section.iter("P"):
        chars: list[str] = []
        for el in p.iter():
            if el.text:
                chars.append(el.text)
            if el.tail:
                chars.append(el.tail)
        para = "".join(chars).strip()
        if para:
            paras.append(para)
    return "\n".join(paras)


def parse_hwpml(hwp_path: str | Path, save_raw: bool = True) -> ParseResult:
    """HWPML 단일 파일 → ParseResult(단일 페이지).

    OLE2 HWP 가 아니므로 hwp-mcp 를 거치지 않는다. 실패 시 빈 ParseResult.
    """
    hwp_path = Path(hwp_path)
    if not hwp_path.exists():
        raise FileNotFoundError(f"HWPML 파일을 찾을 수 없습니다: {hwp_path}")

    print(f"  HWPML 파싱 (stdlib): {hwp_path.name}")

    try:
        tree = ET.parse(hwp_path)
    except ET.ParseError as e:
        print(f"  [HWPML 파싱 실패] {hwp_path.name}: {type(e).__name__}: {e}")
        return ParseResult(source_file=hwp_path.name, pages=[])

    root = tree.getroot()
    if root.tag != "HWPML":
        print(f"  [HWPML 형식 아님] root={root.tag} ({hwp_path.name})")
        return ParseResult(source_file=hwp_path.name, pages=[])

    body = root.find("BODY")
    if body is None:
        print(f"  [HWPML BODY 없음] {hwp_path.name}")
        return ParseResult(source_file=hwp_path.name, pages=[])

    sections: list[str] = []
    for sec in body.findall("SECTION"):
        sec_text = _extract_text_from_section(sec)
        if sec_text:
            sections.append(sec_text)

    text = "\n\n".join(sections).strip()
    if not text:
        print(f"  [HWPML 본문 비어있음] {hwp_path.name}")
        return ParseResult(source_file=hwp_path.name, pages=[])

    page = ParsedPage(page_num=1, text=text, needs_ocr=False)
    result = ParseResult(source_file=hwp_path.name, pages=[page])

    if save_raw:
        raw_dir = hwp_path.parent.parent / "data" / "raw"
        try:
            raw_dir.mkdir(parents=True, exist_ok=True)
            out_path = raw_dir / f"{hwp_path.stem}_raw.txt"
            with open(out_path, "w", encoding="utf-8") as f:
                f.write("=== PAGE 1 ===\n")
                f.write(text + "\n")
            print(f"  원문 저장: {out_path}")
        except OSError as e:
            print(f"  [경고] 원문 저장 실패: {e}", file=sys.stderr)

    article_hits = re.findall(r"제\d+조", text)
    table_hits = re.findall(r"별표\s*\d*", text)
    print(
        f"  검증 - 길이 {len(text)}자 | 제N조 {len(article_hits)}개 | 별표 {len(table_hits)}개"
    )
    return result


__all__ = ["parse_hwpml", "is_hwpml_file"]
