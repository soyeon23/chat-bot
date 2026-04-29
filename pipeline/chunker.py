import re
import json
import unicodedata
import uuid
from pathlib import Path
from dataclasses import dataclass, asdict, field
from typing import Optional

import pandas as pd

from pipeline.pdf_parser import ParseResult, ParsedPage


# 청킹 로직 버전. sync(증분 동기화) 가 코드 업그레이드를 감지해
# 영향받는 파일을 stale 로 마크하고 다음 sync 에서 재인덱싱하는 데 쓴다.
#
# 이력:
#   1.0.0 — 초기 버전 (G1 이전)
#   1.1.0 — Phase G1: ARTICLE_PATTERNS 줄 시작 강제 + 후행 컨텍스트 제약
#   1.2.0 — Phase G2: 별표 파일 라우팅(옵션 A) 추가, _BYEOLPYO_ITEM_SPLIT_RE
#   1.3.0 — Phase G2.1: 위치무관 `[별표 N]` / `[별표]` 정규식 제거.
#           매뉴얼 PDF 본문 안 인용 라벨(`[별표1]을 따름`,
#           `[별표 1]에 따라 ...`) 이 별표 헤더로 오분류되던 문제 수정.
#           시행령 별표 HWP 파일은 chunker 진입부의 `_split_byeolpyo_file`
#           라우팅으로 처리되므로 `_split_by_articles` 안에 위치무관 패턴이
#           필요하지 않다.
#
# 패치 시 반드시 함께 올려야 한다 — sync 가 이 값을 비교한다.
# 1.3.1 = forced-split subpart 의 페이지를 부모 페이지가 아닌 실제 offset 기반으로 산출
# 1.3.2 = 1.3.1 의 find() 폴백이 layout=True PDF 의 들여쓰기 공백 때문에 매칭 실패해
#         결국 부모 페이지로 회귀하던 버그 수정. _split_by_articles / _split_byeolpyo_file
#         / _split_faq 가 dict 에 start/end 절대 offset 을 직접 담아 반환.
# 1.3.3 = 1.3.2 의 parent_raw.find(sub) 매칭이 _greedy_pack(joiner="\n") 의 sub 재구성
#         때문에 또 실패하는 회귀 수정. find() 자체를 버리고 누적 글자 수 비율로
#         parent offset 범위에 균등 매핑. layout 공백·줄바꿈 차이 흡수.
# 1.3.4 = 매뉴얼 PDF 회귀 수정 (coverage_report 진단으로 발견).
#         pdfplumber `extract_text(layout=True)` 가 매뉴얼의 *법령 인용 표 셀* 을
#         줄별로 출력할 때 깊은 들여쓰기(10~30칸)와 함께 `제N조(...)` 셀 시작이
#         줄 시작으로 등장 — 이전 `^\s*` 가 흡수해 chunker 가 매뉴얼 본문을
#         잘못된 article_no 로 split 했다. 결과: "제2조 (part 23/66)" 같은 말도
#         안 되는 라벨 + 페이지 매핑 부정확 + 매뉴얼 PDF 페이지 커버리지 32%.
#         fix: ARTICLE_PATTERNS[0] (제N조) 의 줄시작 들여쓰기 허용을 ≤3칸으로
#         제한 (`^[ \t]{0,3}`). 진짜 시행령/매뉴얼 본문 헤더는 0~2칸 들여쓰기로
#         시작하므로 무영향. 매뉴얼 표 셀 인용 (≥10칸 들여쓰기)은 차단.
#         별표/부칙 패턴은 영향 받지 않음 (그대로 `^[\s■◎●]*`).
# 1.4.0 = 매뉴얼/가이드 doc_type 페이지 기반 청킹 라우팅(_split_by_pages) 추가.
#         매뉴얼 PDF 의 article-aware split false positive (표 셀, 인라인 인용) 회귀 종결.
#         article_no 를 'p.{N}' 형식으로 부여. 페이지 boundary 정확 보장 → 페이지
#         직접 조회·페이지 분포 정상화. 시행령 별표·시행규칙 별지·법령 본체는 영향 없음.
CHUNKER_VERSION = "1.4.0"


# 조문 구조 분할 패턴 (우선순위 순)
# 1) 조문 / 부칙: 줄 시작 강제 + 후행 컨텍스트 제약으로 인라인 참조 제외
#    예) "(제20조제1항 관련)", "법 제32조" 같은 본문 안 참조는 split 트리거 안 됨.
# 2) 별표(번호형) / 별표(번호 없음): 줄 시작 + 글머리·공백 prefix 강제.
#    매뉴얼 PDF 본문에 자주 나타나는 인라인 인용
#       "[별표1]을 따름", "자세한 내용은 [별표 5]에 따라"
#    는 줄 시작이 아니거나 prefix 가 한글 텍스트라 매칭 안 됨.
#    실제 시행령 별표 HWP 첫 줄
#       "■ 국가연구개발혁신법 시행령 [별표 2] <개정 …>"
#    역시 본 정규식으로는 매칭되지 않지만, 별표 HWP 파일은 `chunk_document`
#    진입부에서 `_split_byeolpyo_file` 로 라우팅되므로 본 정규식 경로에 들어오지
#    않는다. (G2 라우팅 옵션 A)
# 컴파일 시 re.MULTILINE 플래그 전달 — 인라인 (?m) 플래그는 alternation 안에서
# Python 3.12+ 가 거부하므로 사용하지 않는다.
#
# 1.3.4 — `^\s*` (제N조) → `^[ \t]{0,3}` 로 제한. 매뉴얼 PDF 의 법령 인용 표 셀이
# `pdfplumber.extract_text(layout=True)` 에서 깊은 들여쓰기(≥10칸)로 출력되어 article
# header 로 오인식되던 회귀 수정. 진짜 법령 본문 / 매뉴얼 진짜 article header 는
# 들여쓰기 0~2칸이므로 무영향.
ARTICLE_PATTERNS = [
    r"^[ \t]{0,3}(제\d+조(?:의\d+)?(?:\s*\([^)]*\))?)(?=\s*\(|\s*$|\s*\n)",      # 1: 제N조 (≤3칸 들여쓰기)
    r"^[\s■◎●]*(별표\s*\d+)(?=\s*$|\s*\n|\s*\()",                                # 2: 별표N (줄시작)
    r"^[\s■◎●]*(별표(?!\s*\d))(?=\s*$|\s*\n|\s*\()",                             # 3: 별표 (줄시작)
    r"^\s*(부\s*칙)(?=\s*$|\s*\n|\s*\()",                                        # 4: 부칙
]

# FAQ 분할 패턴
FAQ_PATTERNS = [
    r"((?:Q|질문|문)\s*[\.\)：:]?\s*\d*\s*)",
    r"(○\s*질의|○\s*답변)",
]

# 최소/최대 청크 글자 수
MIN_CHUNK_LEN = 20
MAX_CHUNK_LEN = 2000

# 긴 청크를 강제 분할할 때 사용하는 한국 법령/공문서 항목 마커
# (?:^|\n)\s* 뒤에 붙어 분할 지점을 찾는다.
_ITEM_SPLIT_RE = re.compile(
    r"(?=\n\s*(?:[①-⑳㉑-㉟㊱-㊿]"  # ① ② … ㉟
    r"|\d{1,2}\.\s"                                            # 1. 2.
    r"|[가-하]\.\s"                                             # 가. 나. 다.
    r"|제\d+항|제\d+호))"
)

# 별표 라우팅 전용 sub-split 마커.
# 별표 본문은 조문 구조가 아니라 항목·예시 표 구조이며, 닫는 괄호 형태
# `1)`, `2)`, `가)`, `나)` 가 빈번하다 (예: 별표6 가중기준 1) 2) 3) 4) /
# 별표6 개별기준 가) 나) 다)). 본 패턴은 `_ITEM_SPLIT_RE` 와 동일한
# 줄 시작 lookahead 형태를 유지하되 닫는 괄호 변형을 추가로 허용한다.
# 들여쓰기(공백 2~6칸 또는 탭) 가 있어도 매칭하도록 `\s*` 로 흡수.
_BYEOLPYO_ITEM_SPLIT_RE = re.compile(
    r"(?=\n\s*(?:[①-⑳㉑-㉟㊱-㊿]"  # ① ② … ㉟
    r"|\d{1,2}\.\s"                                            # 1. 2.
    r"|[가-하]\.\s"                                             # 가. 나. 다.
    r"|\d{1,2}\)\s"                                            # 1) 2) ...
    r"|[가-하]\)\s"                                             # 가) 나) 다) ...
    r"|제\d+항|제\d+호))"
)

# source_file 명에서 별표 N 추출용 정규식. macOS 가 NFD(자모 분해) 로
# 보존하더라도 ASCII 영역의 `[별표 N]` 토큰은 동일하게 매칭된다.
_BYEOLPYO_FILE_RE = re.compile(r"\[\s*별표\s*(\d+)\s*\]")


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
    # MULTILINE: 패턴 안의 ^ 가 줄 시작에서 동작하도록. (?m) 인라인 플래그는
    # alternation 중간에 두면 Python 3.12+ 가 거부하므로 명시적으로 전달.
    splits = list(re.finditer(combined_pattern, full_text, re.MULTILINE))

    if not splits:
        # 조문 패턴 없으면 전체를 하나의 청크로
        return [{
            "text": full_text.strip(),
            "article_no": "",
            "article_title": "",
            "page": boundaries[0][1] if boundaries else 1,
            "start": 0,
            "end": len(full_text),
        }]

    chunks = []
    for idx, match in enumerate(splits):
        start = match.start()
        end = splits[idx + 1].start() if idx + 1 < len(splits) else len(full_text)
        chunk_text = full_text[start:end].strip()

        # ARTICLE_PATTERNS 가 alternation 안에 여러 capture group 을 갖는다
        # (별표는 괄호형/줄시작형 OR). 매칭된 group 중 None 이 아닌 첫 값을 사용.
        captured = next((g for g in match.groups() if g), None)
        article_no = (captured or match.group(0) or "").strip()
        article_title = _extract_article_title(full_text[match.end():match.end() + 100])
        page = _offset_to_page(start, boundaries)

        # `start` / `end` 는 full_text 안에서의 절대 offset.
        # 강제 분할 시 각 sub-part 의 페이지를 정확히 계산하기 위해 보존한다.
        # find() 폴백은 layout=True PDF 의 들여쓰기 공백 이슈로 매칭 실패함.
        chunks.append({
            "text": chunk_text,
            "article_no": article_no,
            "article_title": article_title,
            "page": page,
            "start": start,
            "end": end,
        })

    # 첫 번째 분할 이전 서문 처리
    preamble = full_text[:splits[0].start()].strip()
    if len(preamble) >= MIN_CHUNK_LEN:
        chunks.insert(0, {
            "text": preamble,
            "article_no": "서문",
            "article_title": "",
            "page": boundaries[0][1] if boundaries else 1,
            "start": 0,
            "end": splits[0].start(),
        })

    return chunks


def _extract_byeolpyo_title(full_text: str) -> str:
    """별표 본문 첫 두 줄에서 article_title 후보를 뽑는다.

    실제 시행령 별표 HWP 의 첫 줄은
        "■ 국가연구개발혁신법 시행령 [별표 N] <개정 ...>"
    형태로 헤더 메타이고, 두 번째 줄(또는 세 번째 줄)에 본문 제목이 온다.
        "참여제한 처분기준(제59조제1항 관련)"
    인라인 `(제N조 관련)` 은 제목 부분만 떼어 반환한다.
    제목 후보를 찾지 못하면 빈 문자열.
    """
    lines = [ln.strip() for ln in full_text.splitlines() if ln.strip()]
    for line in lines[:5]:
        # 헤더 메타 라인은 건너뛴다 — 별표 토큰 이 줄 안에 등장
        if _BYEOLPYO_FILE_RE.search(line) or "[별표" in line:
            continue
        # 인라인 "(제N조제M항 관련)" 은 제거하고 앞부분만 제목으로
        m = re.match(r"^([^()]{2,80})\s*\(", line)
        title = m.group(1).strip() if m else line
        # 너무 길면 잘라낸다
        if len(title) > 80:
            title = title[:80]
        if title:
            return title
    return ""


def _split_byeolpyo_file(
    full_text: str,
    boundaries: list[tuple[int, int]],
    byeolpyo_n: str,
) -> list[dict]:
    """별표 파일 라우팅 (옵션 A).

    파일 전체를 `별표 N` 단일 트리로 wrap 한 후, 항목 마커
    (`1./가./1)/가)/①` + `제N항/제N호`) 만 split 트리거로 사용한다.
    조문(`제N조`) 이나 `[별표]` 헤더는 split 트리거에서 제외 — 별표 본문 안에
    인라인 참조 `(제20조제1항 관련)` 가 자주 등장하기 때문이다.

    실제 sub-chunk 분할은 `chunk_document` 의 길이 제한 단계에서 수행하므로
    여기서는 단일 dict 1개만 반환한다.
    """
    article_no = f"별표 {byeolpyo_n}"
    article_title = _extract_byeolpyo_title(full_text)
    page = boundaries[0][1] if boundaries else 1
    return [{
        "text": full_text.strip(),
        "article_no": article_no,
        "article_title": article_title,
        "page": page,
        "start": 0,
        "end": len(full_text),
    }]


def _split_by_pages(
    pages: list[ParsedPage],
    boundaries: list[tuple[int, int]],
    max_len: int = MAX_CHUNK_LEN,
) -> list[dict]:
    """페이지 기반 청킹 — 매뉴얼·가이드 doc_type 전용 (1.4.0).

    각 페이지를 단일 청크로 만들되, max_len 초과 시 항목 마커 (_ITEM_SPLIT_RE) 로
    sub-split. article_no 는 'p.{N}' 또는 'p.{N} (part X/Y)'.

    페이지 boundary 가 정확히 보장됨 — chunker 의 page 태깅 버그 회피.
    article-aware split 의 false positive (표 셀, 인라인 인용) 회귀 종결.

    Args:
        pages: 빈 페이지가 제거된 ParsedPage 리스트 (chunk_document 진입부에서 필터링).
        boundaries: pages 와 1:1 대응되는 (page_start_offset, page_num) 리스트.
        max_len: 페이지 텍스트 길이 임계 — 초과 시 sub-split.

    Returns:
        _split_by_articles 와 동일한 dict 스키마 — text/article_no/article_title/page/start/end.
        chunk_document 의 후속 길이 제한 단계가 part X/Y 라벨을 자동 부여하므로
        본 함수는 *페이지 단위* dict 만 반환한다 (sub-split 은 _split_long_text 가 담당).
    """
    if not pages:
        return []

    # boundaries 가 비거나 pages 와 길이 다른 경우 대비
    boundary_map = {pnum: start for start, pnum in boundaries}

    chunks: list[dict] = []
    for p in pages:
        text = p.text.strip()
        if len(text) < MIN_CHUNK_LEN:
            # 빈/너무 짧은 페이지는 스킵 (의미 단위 미달)
            continue

        # full_text 안 절대 offset — chunk_document 의 forced-split offset 매핑이
        # 누적 문자 비율로 페이지 boundary 안에서만 움직이도록 page_start ~ page_end
        # 범위로 제한한다. 페이지 안 분할이라 어차피 page boundary 를 넘지 않는다.
        page_start = boundary_map.get(p.page_num, 0)
        page_end = page_start + len(p.text)

        chunks.append({
            "text": text,
            "article_no": f"p.{p.page_num}",
            "article_title": "",
            "page": p.page_num,
            "start": page_start,
            "end": page_end,
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
            "start": start,
            "end": end,
        })
    return chunks


def _greedy_pack(parts: list[str], max_len: int, joiner: str = "\n") -> list[str]:
    """parts를 max_len을 넘지 않게 그리디 방식으로 합친다."""
    out: list[str] = []
    buf = ""
    for p in parts:
        if not p.strip():
            continue
        if not buf:
            buf = p
        elif len(buf) + len(joiner) + len(p) <= max_len:
            buf = buf + joiner + p
        else:
            out.append(buf)
            buf = p
    if buf:
        out.append(buf)
    return out


def _split_by_lines(text: str, max_len: int) -> list[str]:
    """줄 단위로 분할 후 그리디 패킹. 한 줄이 max_len 초과면 하드 슬라이스."""
    lines = text.split("\n")
    expanded: list[str] = []
    for line in lines:
        if len(line) <= max_len:
            expanded.append(line)
        else:
            for i in range(0, len(line), max_len):
                expanded.append(line[i:i + max_len])
    return _greedy_pack(expanded, max_len, joiner="\n")


def _split_long_text(
    text: str,
    max_len: int,
    *,
    item_split_re: re.Pattern[str] | None = None,
) -> list[str]:
    """긴 텍스트를 의미 단위(항목 마커→줄→하드)로 분할.

    Args:
        text: 분할 대상 청크 본문.
        max_len: 단일 청크 최대 길이.
        item_split_re: 1차 split 에 사용할 마커 정규식. 기본은 일반 `_ITEM_SPLIT_RE`.
            별표 라우팅 시 `_BYEOLPYO_ITEM_SPLIT_RE` 를 넘기면 닫는 괄호 형태
            (`1)`, `가)`) 도 추가로 분할 트리거가 된다.
    """
    if len(text) <= max_len:
        return [text]

    pattern = item_split_re or _ITEM_SPLIT_RE

    # 1차: 항/호/번호 마커에서 분할
    parts = pattern.split(text)
    parts = [p for p in parts if p.strip()]

    out: list[str] = []
    for p in parts:
        if len(p) <= max_len:
            out.append(p)
        else:
            out.extend(_split_by_lines(p, max_len))

    # 너무 잘게 쪼개진 경우 다시 합치기
    return _greedy_pack(out, max_len, joiner="\n")


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

    # 별표 파일 라우팅 (Phase G2 옵션 A).
    # source_file 명에 `[별표 N]` 패턴이 있으면 본문 전체를 단일 `별표 N` 트리로
    # wrap 후 항목 마커(1./가./1)/가)/①) 로만 sub-split. 시행령 본체나 매뉴얼
    # 같은 일반 문서는 분기 안 탐.
    # macOS APFS 가 한글 파일명을 NFD(자모 분해) 로 보존해 PosixPath.name 도
    # NFD 인 경우가 있으므로, 매칭 전 NFC 정규화 후 검사한다.
    source_nfc = unicodedata.normalize("NFC", parse_result.source_file or "")
    byeolpyo_match = _BYEOLPYO_FILE_RE.search(source_nfc)
    is_byeolpyo_file = byeolpyo_match is not None
    item_split_re = _BYEOLPYO_ITEM_SPLIT_RE if is_byeolpyo_file else _ITEM_SPLIT_RE

    # 매뉴얼/가이드 doc_type 판별 (Phase A1, chunker 1.4.0).
    # 매뉴얼 PDF 는 운영 가이드이지 법령 본문이 아니므로 article-aware split 부적합.
    # `제N조` 토큰이 본문 안 인용/표 셀로 다수 등장해 false positive 폭증 — 회귀.
    # → 페이지 기반 청킹으로 라우팅. article_no='p.{N}', 페이지 boundary 정확 보장.
    #
    # 신호:
    #   a. doc_type 이 "매뉴얼" / "가이드" / "handbook"
    #   b. source_file 에 "매뉴얼" 또는 "본권" 포함 (현 매뉴얼 PDF 명: "[본권] 25년도 ...")
    #
    # 주의: "본체" (혁신법 본체 PDF 등) 는 신호로 쓰지 않는다. 본체 = 법령 본문이라
    # article-aware split 이 적합. "본권" 만 매뉴얼 신호로 사용.
    is_manual = (
        doc_type in {"매뉴얼", "가이드", "handbook"}
        or "매뉴얼" in source_nfc
        or "본권" in source_nfc
    )

    if is_byeolpyo_file:
        raw_chunks = _split_byeolpyo_file(
            full_text,
            boundaries,
            byeolpyo_n=byeolpyo_match.group(1),
        )
    elif is_manual:
        raw_chunks = _split_by_pages(pages, boundaries, MAX_CHUNK_LEN)
    elif doc_type.upper() == "FAQ":
        raw_chunks = _split_faq(full_text, boundaries)
    else:
        raw_chunks = _split_by_articles(full_text, boundaries)

    results = []
    skipped = 0
    split_count = 0  # 강제 분할된 원본 청크 수

    for rc in raw_chunks:
        text = rc["text"].strip()
        if len(text) < MIN_CHUNK_LEN:
            skipped += 1
            continue

        sub_texts = _split_long_text(text, MAX_CHUNK_LEN, item_split_re=item_split_re)
        if len(sub_texts) > 1:
            split_count += 1

        total_parts = len(sub_texts)
        # 부모 article 의 full_text 안 절대 offset 범위.
        # 1.3.3 — `_split_long_text` 가 `_greedy_pack(joiner="\n")` 으로 sub 를 *재구성*
        # 하므로 `parent_raw.find(sub)` 매칭은 layout=True PDF 의 들여쓰기 공백·줄바꿈
        # 차이 때문에 실패한다(1.3.2 회귀). find() 자체를 버리고 **누적 글자 수 비율**
        # 로 각 part 의 절대 offset 을 추정한다. 부모 글자 길이를 part 들의 길이 비율로
        # 나눠 매핑 — layout 공백 차이를 흡수하면서 페이지 단위 정밀도는 유지.
        parent_start = rc.get("start", -1)
        parent_end = rc.get("end", -1)
        parent_len = max(0, parent_end - parent_start)
        sub_lens = [len(s.strip()) for s in sub_texts]
        total_sub_chars = sum(sub_lens) or 1
        cum_chars = 0
        for idx, sub in enumerate(sub_texts):
            sub = sub.strip()
            if len(sub) < MIN_CHUNK_LEN:
                cum_chars += sub_lens[idx]
                continue
            article_no = rc["article_no"]
            if total_parts > 1:
                article_no = f"{article_no} (part {idx + 1}/{total_parts})"
                if parent_start >= 0 and parent_len > 0:
                    ratio = cum_chars / total_sub_chars
                    approx_offset = parent_start + int(ratio * parent_len)
                    sub_page = _offset_to_page(approx_offset, boundaries)
                else:
                    sub_page = rc["page"]
            else:
                sub_page = rc["page"]
            cum_chars += sub_lens[idx]
            results.append(ChunkMetadata(
                chunk_id=str(uuid.uuid4()),
                doc_name=doc_name,
                doc_type=doc_type,
                article_no=article_no,
                article_title=rc["article_title"],
                page=sub_page,
                effective_date=effective_date,
                revised_date=revised_date,
                is_current=is_current,
                source_file=parse_result.source_file,
                text=sub,
            ))

    if skipped:
        print(f"  너무 짧은 청크 {skipped}개 제외됨")
    if split_count:
        print(f"  긴 청크 {split_count}개를 부분 청크로 자동 분할")

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
