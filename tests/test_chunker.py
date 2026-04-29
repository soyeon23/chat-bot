"""
chunker 정규식 회귀 테스트 (Phase G1).

- PM 브리프 phase-g1-backend-brief.md §3 의 3 케이스 + 별표 헤더 케이스 1 추가.
- pytest 가 환경에 없어도 실행되도록 unittest 만 사용.

실행:
    cd /Users/maro/dev/company/chatbot
    source .venv/bin/activate
    python -m unittest tests.test_chunker -v
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

# 프로젝트 루트를 path에 추가
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.chunker import (
    ARTICLE_PATTERNS,
    MAX_CHUNK_LEN,
    _BYEOLPYO_FILE_RE,
    _BYEOLPYO_ITEM_SPLIT_RE,
    _ITEM_SPLIT_RE,
    _split_by_articles,
    _split_byeolpyo_file,
    _split_long_text,
    chunk_document,
)
from pipeline.pdf_parser import ParsedPage, ParseResult


def _split_points(text: str) -> list[tuple[str, int]]:
    """text 에서 ARTICLE_PATTERNS 가 매칭하는 (헤더, 시작오프셋) 리스트."""
    combined = "|".join(ARTICLE_PATTERNS)
    out: list[tuple[str, int]] = []
    for m in re.finditer(combined, text, re.MULTILINE):
        captured = next((g for g in m.groups() if g), None) or m.group(0)
        out.append((captured.strip(), m.start()))
    return out


def _fake_boundaries(text: str) -> list[tuple[int, int]]:
    """단일 페이지 가정의 boundary map (오프셋 0 → 페이지 1)."""
    return [(0, 1)]


class TestArticlePatternRegression(unittest.TestCase):
    """PM 정규식 정신: 줄 시작 강제 + 후행 컨텍스트 제약 → 인라인 참조 split 차단."""

    # ────────────────────────────────────────────────────────────
    # Case 1 — 별표2 synthetic 본문 + 인라인 (제20조제1항 관련) / 법 제32조
    # G2.1 이후: `[별표 2]` 토큰은 위치무관 정규식 제거로 split 트리거가
    # 아니다. 인라인 `(제20조제1항 관련)`, `법 제32조` 도 줄 시작이 아니므로
    # 매칭 안 됨. 결국 split point 0개 → 단일 청크로 본문 보존.
    # 시행령 별표 HWP 파일은 chunk_document 진입부의 라우팅 분기로
    # 처리되므로 본 경로는 영향 없음 (G2-1, G2-3 테스트 참조).
    # ────────────────────────────────────────────────────────────
    def test_case1_byeolpyo2_with_inline_references(self):
        text = """[별표 2] (제20조제1항 관련)

연구개발비 사용용도

1. 인건비
가. 인건비는 법 제32조에 따라 지급한다.
나. 학생인건비는 별도 기준에 따른다.

2. 연구활동비
가. 클라우드컴퓨팅서비스 이용료
나. 회의비
"""
        points = _split_points(text)

        # split point 0개. 인라인 인용 라벨, 인라인 조문 참조 모두 무시.
        self.assertEqual(
            len(points), 0,
            f"split point 0 이어야 한다. 실제: {points}",
        )

        # _split_by_articles 결과: 단일 청크에 본문 키워드 전부 보존
        chunks = _split_by_articles(text, _fake_boundaries(text))
        self.assertEqual(len(chunks), 1)
        joined = chunks[0]["text"]
        self.assertIn("1. 인건비", joined)
        self.assertIn("2. 연구활동비", joined)
        self.assertIn("클라우드컴퓨팅서비스", joined)

    # ────────────────────────────────────────────────────────────
    # Case 2 — 시행령 본체 회귀: 정상 조문 헤더만 split
    # ────────────────────────────────────────────────────────────
    def test_case2_main_text_inline_law_reference(self):
        text = """제13조(연구개발비의 사용)
① 연구개발기관의 장은 법 제32조제1항에 따라 ...
② 다음 각 호의 어느 하나에 해당하는 경우 ...

제15조(연구개발비의 정산)
① 연구개발기관의 장은 ...
"""
        points = _split_points(text)

        # split point 2군데 (제13조, 제15조) 정확히 잡혀야 함
        self.assertEqual(
            len(points), 2,
            f"조문 헤더 2개만 매칭되어야 한다. 실제: {points}",
        )
        headers = [h for h, _ in points]
        self.assertEqual(headers[0], "제13조(연구개발비의 사용)")
        self.assertEqual(headers[1], "제15조(연구개발비의 정산)")

        # 인라인 "법 제32조제1항" 은 split 안 됨
        full_match = re.findall(r"제32조", " | ".join(headers))
        self.assertEqual(full_match, [])

    # ────────────────────────────────────────────────────────────
    # Case 3 — 별표 + 부칙 + 조문 혼합, 우선순위·경계 정확
    # ────────────────────────────────────────────────────────────
    def test_case3_mixed_articles_buchik_byeolpyo(self):
        text = """제32조 (시행일)
이 영은 공포한 날부터 시행한다.

부칙

별표 1
정부지원기준
"""
        points = _split_points(text)

        self.assertEqual(
            len(points), 3,
            f"제32조/부칙/별표 1 헤더 3개. 실제: {points}",
        )
        headers = [h for h, _ in points]
        self.assertEqual(headers[0], "제32조 (시행일)")
        self.assertEqual(headers[1], "부칙")
        self.assertEqual(headers[2], "별표 1")

    # ────────────────────────────────────────────────────────────
    # Case 4 — 실제 시행령 별표2 HWP 첫 줄: `_split_by_articles` 경로에서는
    # split 트리거되지 않음 (G2.1 부터 위치무관 정규식 제거).
    # 시행령 별표 HWP 파일의 실제 처리는 `chunk_document` 진입부의
    # `_split_byeolpyo_file` 라우팅이 담당하므로 (G2 옵션 A), 본 단위 테스트는
    # `_split_by_articles` 가 라우팅을 우회하는 경로 (= 일반 PDF/매뉴얼) 에서
    # 위치무관 `[별표 N]` 토큰이 무시되는지를 검증한다. 본문 보존(키워드 회수)
    # 만 보장하면 충분.
    # ────────────────────────────────────────────────────────────
    def test_case4_real_byeolpyo2_hwp_header(self):
        # hwp-mcp 가 추출하는 실제 첫 줄 형태:
        # "■ 국가연구개발혁신법 시행령 [별표 2] <개정 2026. 3. 10.>"
        text = """■ 국가연구개발혁신법 시행령 [별표 2] <개정 2026. 3. 10.>

연구개발비 사용용도(제20조제1항 관련)

1. 인건비
2. 연구활동비
가. 클라우드컴퓨팅서비스 이용료
나. 지식재산 창출 활동비
다. 학생인건비
라. 연구수당
마. 연구실 운영비
"""
        points = _split_points(text)

        # 위치무관 정규식 제거 후: 본 텍스트에는 줄 시작 별표/조문 헤더가 없으므로
        # split point 0개. (HWP 별표 파일은 라우팅 분기에서 처리되며, 본
        # `_split_by_articles` 경로는 매뉴얼/PDF 같은 일반 문서용.)
        self.assertEqual(
            len(points), 0,
            f"`_split_by_articles` 경로에서 별표 헤더가 split 트리거되지 않아야 한다. 실제: {points}",
        )

        # 본문 키워드 5종이 결과 청크에 포함되어야 한다 — split 안 됐으므로
        # 단일 청크에 전부 포함.
        chunks = _split_by_articles(text, _fake_boundaries(text))
        self.assertEqual(
            len(chunks), 1,
            f"split point 0 → 단일 청크. 실제: {len(chunks)}",
        )
        joined = chunks[0]["text"]
        for kw in [
            "지식재산 창출 활동비",
            "클라우드컴퓨팅서비스",
            "연구실 운영비",
            "연구수당",
            "학생인건비",
        ]:
            self.assertIn(kw, joined, f"별표2 본문에 '{kw}' 가 보존되어야 함")

    # ────────────────────────────────────────────────────────────
    # 회귀 보강: 인라인 "별표 N" 참조 (대괄호 없음 + 줄 중간) 는 split 안 됨
    # ────────────────────────────────────────────────────────────
    def test_inline_byeolpyo_reference_no_split(self):
        text = """제13조(별표)
위탁연구개발비는 별표 5 를 따른다.
별표 3 은 적용하지 않는다.
법 제32조에 따른다.
"""
        points = _split_points(text)
        # 제13조만 매칭. 인라인 "별표 5", "별표 3", "법 제32조" 는 split 안 됨.
        # 단, "별표 3 은 적용하지 않는다." 의 "별표 3" 은 줄 시작이지만 후행이 ` 은 ...`
        # 으로 lookahead `(?=\s*$|\s*\n|\s*\()` 미충족 → 매칭 안 됨.
        self.assertEqual(
            len(points), 1,
            f"제13조 헤더만 매칭되어야 한다. 실제: {points}",
        )
        self.assertEqual(points[0][0], "제13조(별표)")

    # ────────────────────────────────────────────────────────────
    # G2.1 회귀 — 매뉴얼 PDF 본문 안 [별표 N] / [별표] 인용 라벨은 split 안 됨
    #
    # 사전 동작: 위치무관 `\[\s*(별표\s*\d+)\s*\]` 정규식이 매뉴얼 본문에서
    # 자주 나오는 인용 라벨 (`[별표1]을 따름`, `자세한 내용은 [별표 5]에 따라 …`,
    # `[별표 1]에 따라 부담하는 …`) 을 잘못 매칭해 article_no='별표 N' 로
    # 라벨링했음 (~150건 misclassified). 본 테스트는 정규식 제거 후 매뉴얼
    # PDF body 형태에서 split point 가 발생하지 않음을 보장한다.
    # ────────────────────────────────────────────────────────────
    def test_g2_1_manual_pdf_inline_byeolpyo_citations(self):
        # 실제 매뉴얼 PDF 청크에서 관찰된 5종 인용 패턴 + 줄 시작 변형
        text = """           [별표1]을 따름

          마. 혁신법으로    달라진   점

자세한 내용은 [별표 5]에 따라 처리한다.
[별표 1]에 따라 연구개발기관이 부담하는 연구개발비를 현금과 현물로 구분하여 기재합니다.
[별표 3]에 따르되, 인건비 현금 인정 분야로 신청된 경우라 하더라도 평가위원회에서
[별표] 연구개발기관 보안대책에 포함되어야 하는 사항(국가연구개발사업 보안대책 제4조 관련)
"""
        points = _split_points(text)
        # 기대: 0개. 매뉴얼 본문 인용 라벨은 모두 줄 중간 또는 줄 시작 직후에
        # 한국어 텍스트가 따라붙어(`[별표1]을 따름`) 별표 패턴(줄시작+lookahead)
        # 미충족.
        self.assertEqual(
            len(points), 0,
            f"매뉴얼 본문 인용 라벨은 split 트리거가 아니어야 한다. 실제: {points}",
        )

        # 본문 보존 — 단일 청크
        chunks = _split_by_articles(text, _fake_boundaries(text))
        self.assertEqual(len(chunks), 1)
        joined = chunks[0]["text"]
        self.assertIn("혁신법으로", joined)
        self.assertIn("자세한 내용은", joined)
        self.assertIn("연구개발비를 현금과 현물로", joined)

    # ────────────────────────────────────────────────────────────
    # G2.1 보강 — 별표 헤더 본인은 줄시작 정규식 #2 로 정상 매칭
    #
    # 위치무관 정규식 제거 후에도, 글머리 기호 prefix 만 있는 줄시작 별표 헤더
    # (예: 일반 PDF 의 `별표 1` / `■ 별표 2` / `   별표 3`) 는 그대로 매칭된다.
    # ────────────────────────────────────────────────────────────
    def test_g2_1_byeolpyo_line_start_still_matches(self):
        text = """제32조 (시행일)
이 영은 공포한 날부터 시행한다.

별표 1
정부지원기준

  별표 2
사용용도

■ 별표 3
삭제
"""
        points = _split_points(text)
        headers = [h for h, _ in points]
        # 제32조 + 별표 1 + 별표 2 + 별표 3 — 4 매치
        self.assertEqual(
            len(points), 4,
            f"제32조/별표1/별표2/별표3 4개. 실제: {points}",
        )
        self.assertEqual(headers[0], "제32조 (시행일)")
        self.assertEqual(headers[1], "별표 1")
        self.assertEqual(headers[2], "별표 2")
        self.assertEqual(headers[3], "별표 3")

    # ────────────────────────────────────────────────────────────
    # 1.3.4 회귀 — 매뉴얼 PDF 의 법령 인용 표 셀이 article header 로 오인식되던 버그
    #
    # 배경: `coverage_report.py` 진단에서 매뉴얼 PDF 페이지 커버리지가 32.8% 로
    # 떨어지고 강제분할 비율 71.6% 인 회귀를 발견. 원인은 매뉴얼이 `extract_text(
    # layout=True)` 로 추출될 때, 본문 안 *법령 인용 표* (예: "혁신법 / 시행령 /
    # 시행규칙 조문 매핑 표") 의 셀이 줄별로 분리되며 깊은 들여쓰기(10~30칸)
    # + `제N조(...)` 형태로 출력된다. 이전 `^\s*` 패턴은 모든 공백을 흡수해
    # 표 셀 인용을 article 헤더로 오인식 → 매뉴얼 doc 안에 "제2조 (part 23/66)"
    # 같은 말도 안 되는 라벨 다수 생성.
    #
    # 1.3.4 fix: `^\s*` → `^[ \t]{0,3}` 로 제한. 진짜 법령 본문 / 매뉴얼의 진짜
    # article header 는 들여쓰기 0~2칸으로 시작하므로 무영향. 깊은 들여쓰기
    # (≥4칸) 셀 인용은 차단.
    # ────────────────────────────────────────────────────────────
    def test_1_3_4_manual_pdf_indented_article_citation_no_split(self):
        # 실제 매뉴얼 PDF p.16 / p.19 에서 layout=True 추출 시 관찰된 패턴.
        # 들여쓰기 10칸 / 30칸 / 더 깊은 것까지 다양하게.
        text = (
            "          제13조(연구개발비의 지급 및 사용 등) 제19조(연구개발비의 지원과 부담)            \n"
            "                              제20조(연구개발비의 사용용도 등)              \n"
            "          제14조(연구개발과제의 평가 등)  제13조(연구개발과제협약의 체결)\n"
            "          제15조(특별평가를 통한       제27조(연구개발과제평가단의 구성)\n"
            "                              제28조(심의위원회의 구성 및 운영)\n"
        )
        points = _split_points(text)
        self.assertEqual(
            len(points), 0,
            f"매뉴얼 표 셀 인용 (들여쓰기 ≥4칸) 은 split 트리거가 아니어야 한다. 실제: {points}",
        )

    def test_1_3_4_real_article_header_zero_indent_still_matches(self):
        """진짜 법령 본문 헤더 (들여쓰기 0칸) 는 그대로 매칭되어야 한다."""
        text = """제13조(연구개발비의 사용)
① 연구개발기관의 장은 ...
② 다음 각 호의 ...

제15조(연구개발비의 정산)
① ...
"""
        points = _split_points(text)
        self.assertEqual(len(points), 2, f"실제: {points}")
        self.assertEqual(points[0][0], "제13조(연구개발비의 사용)")
        self.assertEqual(points[1][0], "제15조(연구개발비의 정산)")

    def test_1_3_4_three_space_indent_still_matches(self):
        """들여쓰기 3칸까지는 article header 로 인식 (시행령 들여쓰기 케이스 보호).

        시행령 본문에서도 일부 조문은 단락 들여쓰기로 시작할 수 있다. 안전 마진
        으로 3칸까지 허용.
        """
        text = "   제13조(연구개발비의 사용)\n① ...\n"
        points = _split_points(text)
        self.assertEqual(len(points), 1, f"3칸 들여쓰기는 매칭되어야 한다. 실제: {points}")

    def test_1_3_4_four_space_indent_no_match(self):
        """들여쓰기 ≥4칸은 article header 로 인식하지 않는다 (인용 셀로 간주)."""
        text = "    제13조(연구개발비의 사용)\n① ...\n"
        points = _split_points(text)
        self.assertEqual(
            len(points), 0,
            f"4칸 이상 들여쓰기는 매칭되지 않아야 한다 (인용 셀). 실제: {points}",
        )

    def test_1_3_4_byeolpyo_pattern_unaffected(self):
        """별표 패턴 (#2, #3) 은 1.3.4 영향 받지 않음 — 깊은 들여쓰기 허용 유지.

        별표는 G2.1 에서 이미 `^[\\s■◎●]*` 로 정의돼 있고, HWP 별표 파일은
        라우팅으로 처리되므로 매뉴얼 본문 회귀 영역과 무관. 본 테스트는 별표
        헤더 매칭이 1.3.4 패치 후에도 동일하게 동작함을 보장.
        """
        text = """제32조 (시행일)
이 영은 ...

별표 1
정부지원기준

  별표 2
사용용도

■ 별표 3
삭제
"""
        points = _split_points(text)
        # 제32조 + 별표 1 + 별표 2 + 별표 3 — 4 매치 (1.3.3 과 동일)
        self.assertEqual(len(points), 4, f"별표 패턴은 영향 받지 않아야 함. 실제: {points}")


class TestByeolpyoRouting(unittest.TestCase):
    """Phase G2 — 별표 파일 라우팅 검증.

    source_file 명에 `[별표 N]` 패턴이 있으면 chunker 진입부에서 별도 분기.
    조문 헤더(`제N조`) split 우회, 항목 마커(`1./가./1)/가)/①`) 로만 sub-split.
    """

    @staticmethod
    def _make_parse_result(source_file: str, full_text: str) -> ParseResult:
        return ParseResult(
            source_file=source_file,
            pages=[ParsedPage(page_num=1, text=full_text, needs_ocr=False)],
        )

    # ────────────────────────────────────────────────────────────
    # G2-1 — synthetic 별표2 본문: 직접비/간접비 표 + 인라인 (제20조제1항 관련)
    # ────────────────────────────────────────────────────────────
    def test_g2_1_byeolpyo2_routes_and_inline_ref_ignored(self):
        source_file = "[별표 2] 연구개발비 사용용도(제20조제1항 관련)(국가연구개발혁신법 시행령).hwp"
        # MAX_CHUNK_LEN(2000) 을 살짝 넘는 길이로 만들어 sub-split 강제 — 의미 단위로
        # 잘리는지 검증. 1./가./1) 마커가 본문에 모두 등장.
        body_filler_a = ("학생인건비는 연구책임자가 정한 기준에 따라 지급한다. " * 30)
        body_filler_b = ("연구활동비 중 회의비·출장비는 사업비 집행 기준에 따른다. " * 30)
        text = f"""■ 국가연구개발혁신법 시행령 [별표 2] <개정 2026. 3. 10.>

연구개발비 사용용도(제20조제1항 관련)

1. 직접비
가. 인건비
1) 내부인건비는 법 제32조에 따라 지급한다.
2) 외부인건비는 별도 기준에 따른다.
{body_filler_a}

나. 학생인건비
1) 학생인건비 통합관리제는 연구책임자가 운영한다.
가) 지급률은 100% 한도.
나) 학생연구자 대상.

다. 연구활동비
1) 회의비
2) 출장비
{body_filler_b}

라. 지식재산 창출 활동비
1) 특허출원비
2) 기술이전 활동비

2. 간접비
가. 일반관리비 등
"""
        pr = self._make_parse_result(source_file, text)
        chunks = chunk_document(
            parse_result=pr,
            doc_name="국가연구개발혁신법 시행령 [별표 2] 연구개발비 사용용도",
            doc_type="시행령",
            effective_date="2026-03-10",
            revised_date="2026-03-10",
            is_current=True,
        )

        self.assertGreater(len(chunks), 0, "청크가 1개 이상 생성되어야 한다.")

        # (a) 모든 청크의 article_no 가 '별표 2' 로 시작 (part 표기 포함)
        for c in chunks:
            self.assertTrue(
                c.article_no.startswith("별표 2"),
                f"article_no 가 '별표 2' 로 시작해야 함. 실제: {c.article_no!r}",
            )

        # (b) 단일 청크면 정확히 '별표 2', 다중 청크면 '별표 2 (part X/Y)'
        if len(chunks) == 1:
            self.assertEqual(chunks[0].article_no, "별표 2")
        else:
            for idx, c in enumerate(chunks, start=1):
                self.assertRegex(
                    c.article_no,
                    rf"^별표 2 \(part {idx}/{len(chunks)}\)$",
                    f"part 표기가 일관되지 않음: {c.article_no!r}",
                )

        # (c) article_title 추출 (인라인 (제20조제1항 관련) 제거됨)
        self.assertEqual(chunks[0].article_title, "연구개발비 사용용도")
        self.assertNotIn("관련", chunks[0].article_title)

        # (d) 인라인 "(제20조제1항 관련)" 안의 "제20조" 가 별도 article_no 로
        #     떨어지지 않아야 함 — 라우팅 분기에서 _split_by_articles 우회 검증
        for c in chunks:
            self.assertFalse(
                c.article_no.startswith("제20조"),
                f"인라인 참조가 article_no 로 잘못 잡혔다: {c.article_no!r}",
            )

        # (e) 본문 키워드 보존 (의미 단위 분할 검증)
        joined = "\n".join(c.text for c in chunks)
        for kw in ["1. 직접비", "가. 인건비", "지식재산 창출 활동비", "학생인건비"]:
            self.assertIn(kw, joined, f"본문 키워드 '{kw}' 가 보존되어야 함")

    # ────────────────────────────────────────────────────────────
    # G2-2 — 회귀: 시행령 본체(별표 아님) 는 G1 정규식 그대로 동작
    # ────────────────────────────────────────────────────────────
    def test_g2_2_main_text_not_routed(self):
        source_file = "국가연구개발혁신법 시행령(대통령령)(제36163호)(20260310).hwp"
        text = """제13조(연구개발비의 사용)
① 연구개발기관의 장은 법 제32조제1항에 따라 ...
② 다음 각 호의 어느 하나에 해당하는 경우 ...

제15조(연구개발비의 정산)
① 연구개발기관의 장은 ...
"""
        # source_file 매칭부터 검증
        self.assertIsNone(
            _BYEOLPYO_FILE_RE.search(source_file),
            "시행령 본체 source_file 은 별표 라우팅 분기를 타지 않아야 함",
        )

        pr = self._make_parse_result(source_file, text)
        chunks = chunk_document(
            parse_result=pr,
            doc_name="국가연구개발혁신법 시행령",
            doc_type="시행령",
            effective_date="2026-03-10",
            revised_date="2026-03-10",
            is_current=True,
        )

        # G1 정규식 그대로: 제13조, 제15조 청크 2개 (각각 article_no 시작)
        self.assertEqual(len(chunks), 2, f"제13조/제15조 2개 청크. 실제: {len(chunks)}")
        article_nos = [c.article_no for c in chunks]
        self.assertTrue(article_nos[0].startswith("제13조"), article_nos)
        self.assertTrue(article_nos[1].startswith("제15조"), article_nos)
        # 별표 라우팅이 안 탔으므로 article_no 가 '별표' 로 시작하지 않아야 함
        for c in chunks:
            self.assertFalse(
                c.article_no.startswith("별표"),
                f"별표 라우팅 분기가 잘못 탔다: {c.article_no!r}",
            )

    # ────────────────────────────────────────────────────────────
    # G2-3 — 별표6 가중·감경 사유: 라우팅 + 항목 마커 sub-split
    # ────────────────────────────────────────────────────────────
    def test_g2_3_byeolpyo6_chunking_aggravation_mitigation(self):
        # 실제 별표6 본문 형태(2,774자) 기반 축약 — `1.`, `가.`, `1)`, `가)`
        # 4종 마커가 모두 등장한다.
        source_file = "[별표 6] 참여제한 처분기준(제59조제1항 관련)(국가연구개발혁신법 시행령).hwp"
        text = """■ 국가연구개발혁신법 시행령 [별표 6] <개정 2021. 12. 31.>
참여제한 처분기준(제59조제1항 관련)
1. 일반기준
  가. 가중기준
    처분권자는 위반행위자가 다음의 어느 하나에 해당하는 경우에는 제2호의 개별기준에 따른 참여제한 기간의 2분의 1 범위에서 가중할 수 있다.
    1) 법 제32조제1항제3호에 따른 위반행위로서 법 제31조제1항제2호에 해당하는 부정행위 중 학생인건비 또는 학생연구자에게 지급하는 인건비·연구수당의 사용용도와 사용기준을 위반한 경우
    2) 참여제한 기간이 종료된 날부터 5년 이내에 같은 위반행위로 참여제한 처분을 받는 경우.
    3) 하나의 연구개발과제에서 발생한 위반행위가 둘 이상인 경우.
    4) 그 밖에 위반행위의 정도, 위반행위의 동기와 그 결과 등을 고려하여 가중할 필요가 있다고 인정되는 경우
  나. 감경기준
    처분권자는 위반행위자가 다음의 어느 하나에 해당하는 경우에는 제2호의 개별기준에 따른 참여제한 기간의 2분의 1 범위에서 감경할 수 있다.
    1) 연구개발기관의 장이 법 제32조제1항제3호에 따른 위반행위로서 법 제31조제1항 각 호의 어느 하나에 해당하는 부정행위를 같은 조 제2항에 따라 검증하여 필요한 조치를 한 경우.
    2) 법 제32조제1항제3호에 따른 위반행위로서 법 제31조제1항 각 호의 어느 하나에 해당하는 부정행위를 한 자가 같은 조 제3항에 따라 중앙행정기관의 장이 실시하는 조사에 성실하게 협조한 경우
    3) 위반행위가 사소한 부주의나 오류로 인한 것으로 인정되는 경우
    4) 그 밖에 위반행위의 정도, 위반행위의 동기와 그 결과 등을 고려하여 감경할 필요가 있다고 인정되는 경우
  다. 합산기준
    둘 이상의 위반행위가 서로 다른 연구개발과제에서 발생한 경우에는 연구개발과제별로 각각 발생한 위반행위에 대하여 가목 및 나목의 가중·감경기준을 적용한 후 산출된 참여제한 기간을 모두 합산한다.
2. 개별기준
  가. 법 제32조제1항 각 호의 위반행위에 대한 위반행위별 참여제한 기간은 다음과 같다.
    1) 법 제12조제2항에 따른 평가 결과 연구개발과제의 수행과정과 결과가 극히 불량한 경우
      가) 연구개발자료 또는 연구개발성과를 위조·변조·표절하거나 저자를 부당하게 표시하는 행위를 한 경우 3년 이내
      나) 법 제16조제1항부터 제3항까지의 규정을 위반하여 연구개발성과를 소유하거나 제3자에게 소유하게 한 행위를 한 경우 3년
"""
        pr = self._make_parse_result(source_file, text)
        chunks = chunk_document(
            parse_result=pr,
            doc_name="국가연구개발혁신법 시행령 [별표 6] 참여제한 처분기준",
            doc_type="시행령",
            effective_date="2021-12-31",
            revised_date="2021-12-31",
            is_current=True,
        )

        self.assertGreater(len(chunks), 0)

        # (a) 모든 청크 article_no 가 '별표 6' 로 시작
        for c in chunks:
            self.assertTrue(
                c.article_no.startswith("별표 6"),
                f"article_no 시작 불일치: {c.article_no!r}",
            )

        # (b) 가중기준 + 감경기준 + 합산기준 의 키워드는 같은 청크 또는 인접 청크 안에서 회수 가능해야 함.
        #     요구사항: "가중·감경 사유가 같은 청크에 묶이거나 인접 part 에 분산"
        joined = "\n".join(c.text for c in chunks)
        for kw in ["가중기준", "감경기준", "합산기준", "학생인건비"]:
            self.assertIn(kw, joined, f"본문 키워드 '{kw}' 보존 실패")

        # (c) 닫는 괄호 마커 1) / 가) 도 sub-split 트리거가 됨을 패턴 단위로 확인.
        #     본문 안 "1)", "가)" 마커 앞에서 _BYEOLPYO_ITEM_SPLIT_RE 가 split point 를 발견.
        # 일부러 닫는 괄호 형태가 _ITEM_SPLIT_RE 에는 잡히지 않고 _BYEOLPYO_ITEM_SPLIT_RE 에만
        # 잡혀야 하는지 확인 (regression 가드).
        sample = "\n  나) 항목\n"
        self.assertEqual(_ITEM_SPLIT_RE.split(sample), [sample])
        self.assertEqual(len(_BYEOLPYO_ITEM_SPLIT_RE.split(sample)), 2)

        # (d) 가중기준(가.) 단락 keywords 와 감경기준(나.) 단락 keywords 가
        #     서로 다른 청크라도 인접해야 함 (1 청크 차이 이내).
        agg_idx = next(
            (i for i, c in enumerate(chunks) if "가중기준" in c.text and "1)" in c.text),
            None,
        )
        mit_idx = next(
            (i for i, c in enumerate(chunks) if "감경기준" in c.text and "1)" in c.text),
            None,
        )
        self.assertIsNotNone(agg_idx, "가중기준 청크 미발견")
        self.assertIsNotNone(mit_idx, "감경기준 청크 미발견")
        self.assertLessEqual(
            abs(agg_idx - mit_idx), 1,
            f"가중·감경 청크가 인접하지 않음 (agg={agg_idx}, mit={mit_idx}, total={len(chunks)})",
        )

    # ────────────────────────────────────────────────────────────
    # G2 보강 — 별표 라우팅 단위 함수 직접 검증
    # ────────────────────────────────────────────────────────────
    def test_g2_split_byeolpyo_file_unit(self):
        text = """■ 국가연구개발혁신법 시행령 [별표 5] <개정 2026. 3. 10.>

통합정보시스템을 통한 제공 요청 대상 정보나 자료(제42조제2항 관련)

1. 연구자 정보
2. 연구개발기관 정보
"""
        raw = _split_byeolpyo_file(text, [(0, 1)], byeolpyo_n="5")
        self.assertEqual(len(raw), 1)
        self.assertEqual(raw[0]["article_no"], "별표 5")
        self.assertEqual(
            raw[0]["article_title"],
            "통합정보시스템을 통한 제공 요청 대상 정보나 자료",
        )
        self.assertEqual(raw[0]["page"], 1)
        self.assertIn("연구자 정보", raw[0]["text"])

    # ────────────────────────────────────────────────────────────
    # G2 보강 — _BYEOLPYO_FILE_RE 정규식: macOS NFD 명도 매칭
    # ────────────────────────────────────────────────────────────
    def test_g2_byeolpyo_file_regex(self):
        cases_match = [
            "[별표 1] 정부지원기준.hwp",
            "[별표 2] 연구개발비 사용용도.hwp",
            "[별표 6] 참여제한 처분기준(제59조제1항 관련)(국가연구개발혁신법 시행령).hwp",
            "[별표  7] 제재부가금 처분기준.hwp",  # 공백 2개
        ]
        for name in cases_match:
            m = _BYEOLPYO_FILE_RE.search(name)
            self.assertIsNotNone(m, f"매칭되어야 함: {name!r}")

        cases_no_match = [
            "국가연구개발혁신법 시행령(대통령령)(제36163호)(20260310).hwp",
            "별표안내문.hwp",  # 대괄호 없음
            "[부록 1] 양식.hwp",
        ]
        for name in cases_no_match:
            self.assertIsNone(
                _BYEOLPYO_FILE_RE.search(name),
                f"매칭되면 안 됨: {name!r}",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
