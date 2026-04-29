"""pipeline.local_doc_mcp 단위 테스트.

Phase H — 로컬 PDF/HWP 직접 접근 도구. 실제 매뉴얼 PDF (`[본권] 25년도
국가연구개발혁신법 매뉴얼_배포용.pdf`) 가 프로젝트 루트에 존재한다는
가정 하에 동작 확인.

PDF 가 존재하지 않는 환경(CI 등) 에서는 해당 케이스를 자동 skip 한다.
"""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path

import pytest

from pipeline import local_doc_mcp


_PDF_GLOB_HINT = "국가연구개발혁신법 매뉴얼"


def _find_manual_pdf() -> Path | None:
    """프로젝트 루트에서 매뉴얼 PDF 를 찾는다.

    macOS APFS 는 한글 파일명을 NFD(자모 분해) 로 보존하므로 비교 전 NFC
    정규화 필수.
    """
    root = Path(__file__).resolve().parents[1]
    target = unicodedata.normalize("NFC", _PDF_GLOB_HINT)
    for p in root.glob("*.pdf"):
        nm_nfc = unicodedata.normalize("NFC", p.name)
        if target in nm_nfc:
            return p
    return None


@pytest.fixture(autouse=True)
def _clear_cache():
    """매 테스트마다 모듈 전역 캐시 초기화 — 다른 테스트 간섭 방지."""
    local_doc_mcp._doc_cache.clear()
    yield
    local_doc_mcp._doc_cache.clear()


@pytest.fixture(scope="session")
def manual_pdf() -> Path:
    """매뉴얼 PDF 경로 fixture. 없으면 모든 PDF 테스트 skip."""
    p = _find_manual_pdf()
    if p is None:
        pytest.skip(f"매뉴얼 PDF (`{_PDF_GLOB_HINT}` 포함) 가 프로젝트 루트에 없습니다.")
    return p


# ──────────────────────────────────────────────────────────────────
# list_documents
# ──────────────────────────────────────────────────────────────────


def test_list_documents_returns_list_of_dicts(manual_pdf):
    """config 의 pdf_dir / 프로젝트 루트를 스캔해 dict 리스트를 반환."""
    docs = local_doc_mcp.list_documents()
    assert isinstance(docs, list)
    assert len(docs) >= 1
    for d in docs:
        assert {"name", "path", "type", "pages"} <= d.keys()
        assert d["type"] in ("pdf", "hwp")


def test_list_documents_includes_manual_pdf(manual_pdf):
    """매뉴얼 PDF 가 목록에 들어있고 페이지 수가 합리적이어야 함."""
    docs = local_doc_mcp.list_documents()
    names = [d["name"] for d in docs]
    assert manual_pdf.name in names
    manual_entry = next(d for d in docs if d["name"] == manual_pdf.name)
    assert manual_entry["type"] == "pdf"
    # 매뉴얼은 500+ 페이지
    assert manual_entry["pages"] > 100


# ──────────────────────────────────────────────────────────────────
# read_page
# ──────────────────────────────────────────────────────────────────


def test_read_page_p151_contains_research_note_faq(manual_pdf):
    """p.151 호출 시 연구노트 FAQ Q1~Q* 텍스트가 들어 있어야 한다."""
    out = local_doc_mcp.read_page(manual_pdf.name, 151)
    assert out["page"] == 151
    assert out["doc_name"] == manual_pdf.name
    assert out["char_count"] > 100, f"p.151 too short: {out}"
    assert "error" not in out
    text = out["text"]
    # FAQ 신호 — 연구노트 단원의 Q1 / Q2 / 위탁연구 / 소유 같은 키워드
    assert "Q1" in text
    assert "연구노트" in text


def test_read_page_partial_doc_name(manual_pdf):
    """파일명 일부 ('매뉴얼') 만 줘도 매뉴얼 PDF 를 찾아야 한다."""
    out = local_doc_mcp.read_page("매뉴얼", 151)
    # stem 부분 일치 매칭 — 본권 매뉴얼이 가장 짧은 stem 매치라 hit 해야 함
    assert "error" not in out, out
    assert out["page"] == 151
    assert "Q1" in out["text"]


def test_read_page_out_of_range(manual_pdf):
    """페이지 범위를 벗어나면 error 필드가 채워진 응답."""
    out = local_doc_mcp.read_page(manual_pdf.name, 99999)
    assert out["text"] == ""
    assert "error" in out
    assert "범위" in out["error"]


def test_read_page_doc_not_found():
    """없는 문서 이름 → fail-soft 빈 응답 + error 필드."""
    out = local_doc_mcp.read_page("존재하지않는파일이름.pdf", 1)
    assert out["text"] == ""
    assert "error" in out


# ──────────────────────────────────────────────────────────────────
# search_text
# ──────────────────────────────────────────────────────────────────


def test_search_text_finds_research_note_keyword(manual_pdf):
    """'연구노트' 검색 시 매뉴얼 안에서 매칭 페이지가 여러 개 잡혀야 한다."""
    results = local_doc_mcp.search_text(manual_pdf.name, "연구노트", max_results=10)
    assert isinstance(results, list)
    assert len(results) >= 3, f"연구노트 검색 결과 너무 적음: {results}"
    for r in results:
        assert {"doc_name", "page", "match", "excerpt"} <= r.keys()
        assert "연구노트" in r["excerpt"] or "연구노트" in r["match"]


def test_search_text_invalid_regex_falls_back_to_literal(manual_pdf):
    """깨진 정규식이 들어와도 리터럴로 escape 후 동작."""
    # ')' 단독은 unbalanced — escape 폴백 진입
    results = local_doc_mcp.search_text(manual_pdf.name, ")", max_results=2)
    assert isinstance(results, list)


def test_search_text_no_match_returns_empty(manual_pdf):
    """매칭 0건이면 빈 리스트."""
    results = local_doc_mcp.search_text(manual_pdf.name, "ZZZ존재불가키워드ZZZ", max_results=5)
    assert results == []


# ──────────────────────────────────────────────────────────────────
# get_article
# ──────────────────────────────────────────────────────────────────


def test_get_article_finds_article_15(manual_pdf):
    """매뉴얼 본문에서 '제15조' 본문 + 시작/끝 페이지를 잡아야 한다.

    매뉴얼은 인용 형태 ("법 제15조 ...") 가 많지만, 줄 시작 강제 패턴이라
    실제 조문 헤더만 잡힌다. 매뉴얼 PDF 에 줄 시작 형태의 `제15조` 헤더가
    없으면 error 가 채워진 응답이 와야 한다 — 그것도 정상 동작이다.
    """
    out = local_doc_mcp.get_article(manual_pdf.name, "제15조")
    # 매뉴얼은 해설서라 제N조 헤더가 본문 줄 시작에 직접 등장하는 경우가 적다.
    # 이 테스트는 *함수가 깨지지 않고 합리적 응답* 하는지 검증.
    assert out["doc_name"] == manual_pdf.name
    assert out["article_no"] == "제15조"
    if "error" not in out:
        # 매칭됐다면 페이지 + 본문이 채워져야 함
        assert out["start_page"] >= 1
        assert out["end_page"] >= out["start_page"]
        assert out["char_count"] > 0
    else:
        # 매칭 실패도 fail-soft — 빈 텍스트 + error 메시지
        assert out["text"] == ""


def test_get_article_invalid_doc():
    out = local_doc_mcp.get_article("없는문서.pdf", "제1조")
    assert out["text"] == ""
    assert "error" in out


# ──────────────────────────────────────────────────────────────────
# Phase H+1: search_text 폴백 (매뉴얼 PDF article_no="p.N" 후속 조치)
# ──────────────────────────────────────────────────────────────────


def test_get_article_fallback_to_search_text_for_manual_pdf(manual_pdf):
    """매뉴얼 PDF 에서 '제10조' 호출 시 search_text 폴백 작동 + 본문 회수.

    chunker 1.4.0 매뉴얼 청킹은 article_no="p.N" 형식이라 줄 시작 헤더는
    *목차 페이지* 에만 잡혀 30자 이하 짧은 본문만 회수된다. _ARTICLE_BODY_MIN_CHARS
    임계 미만이면 search_text 폴백이 발동해 본문이 등장하는 페이지의 텍스트를
    합쳐 돌려줘야 한다.
    """
    out = local_doc_mcp.get_article(manual_pdf.name, "제10조")
    # 폴백 발동 — matched_via 플래그 + 풍부한 본문
    assert out.get("matched_via") == "search_text_fallback", (
        f"search_text 폴백이 발동하지 않음: {out}"
    )
    assert out["doc_name"] == manual_pdf.name
    assert out["article_no"] == "제10조"
    assert "error" not in out, out
    assert out["start_page"] >= 1
    assert out["end_page"] >= out["start_page"]
    # 폴백은 페이지 본문을 합치므로 char_count >= 100
    assert out["char_count"] >= 100, f"폴백 본문이 너무 짧음: {out['char_count']}자"
    # 디버그 필드 — fallback_reason / fallback_results
    assert out.get("fallback_reason") in {"header_no_match", "body_too_short"}
    assert isinstance(out.get("fallback_results"), list)
    assert len(out["fallback_results"]) >= 1


def test_get_article_returns_search_match_for_law_article_in_manual(manual_pdf):
    """매뉴얼 본문에 '제10조(연구개발과제 ...)' 텍스트가 회수돼야 한다.

    폴백이 발동하면 search_text 가 매칭한 페이지들의 *본문* 이 합쳐서
    반환되므로, '제10조(' 패턴이 본문 어딘가에 등장해야 한다.
    """
    out = local_doc_mcp.get_article(manual_pdf.name, "제10조")
    assert out.get("matched_via") == "search_text_fallback"
    text = out["text"]
    # 매뉴얼 본문 안 인용/해설 형태 — '제10조' 가 어떤 형식으로든 등장
    assert "제10조" in text, f"폴백 본문에 '제10조' 가 없음: text[:200]={text[:200]!r}"


def test_get_article_fallback_when_no_header_match_no_inline():
    """헤더 매칭 0건 + search_text 도 0건 → fallback_reason=header_no_match
    + 빈 본문 + 합리적 error 메시지.
    """
    # 매뉴얼 PDF 가 없을 때만 발동 — 별도 fixture 안 씀.
    # 매뉴얼 PDF 가 있는 환경에선 어떤 article_no 가 0건이 될지 보장 어려움 →
    # 의도적으로 절대 매칭되지 않을 토큰 사용.
    p = _find_manual_pdf()
    if p is None:
        pytest.skip("매뉴얼 PDF 없음 — skip")
    out = local_doc_mcp.get_article(p.name, "제99999조")
    assert out.get("matched_via") == "search_text_fallback"
    assert out.get("fallback_reason") == "header_no_match"
    assert out["text"] == ""
    assert "error" in out
    assert out["start_page"] == 0
    assert out["end_page"] == 0


def test_get_article_no_fallback_when_body_is_substantial(manual_pdf, monkeypatch):
    """헤더 매칭 + 본문이 임계치 이상 → 폴백 안 발동, 정상 경로.

    `_ARTICLE_BODY_MIN_CHARS` 를 1 로 임시 낮춰 매뉴얼의 짧은 매칭도 통과시킴.
    matched_via 키가 없어야 정상 경로.
    """
    monkeypatch.setattr(local_doc_mcp, "_ARTICLE_BODY_MIN_CHARS", 1)
    out = local_doc_mcp.get_article(manual_pdf.name, "제10조")
    # 임계 1자 → 매뉴얼 목차에서 잡힌 30자도 통과
    assert "matched_via" not in out, f"임계 1자에서도 폴백 발동: {out}"
    assert out["text"]  # 짧지만 비어있진 않음


# ──────────────────────────────────────────────────────────────────
# list_articles
# ──────────────────────────────────────────────────────────────────


def test_list_articles_returns_entries_with_pages(manual_pdf):
    """매뉴얼 목록에서 article_no/page 키를 가진 dict 리스트를 반환."""
    arts = local_doc_mcp.list_articles(manual_pdf.name)
    assert isinstance(arts, list)
    # 매뉴얼은 부칙·별표 헤더가 한두 개라도 등장한다고 가정
    if arts:
        for a in arts:
            assert {"doc_name", "article_no", "page"} <= a.keys()
            assert a["page"] >= 1


def test_list_articles_doc_not_found():
    arts = local_doc_mcp.list_articles("없는문서.pdf")
    assert arts == []


# ──────────────────────────────────────────────────────────────────
# 정규식 헬퍼
# ──────────────────────────────────────────────────────────────────


def test_article_regex_matches_제N조():
    pat = local_doc_mcp._article_regex("제15조")
    text = "\n제15조(연구개발기관 선정)\n  ① 부처의 장은..."
    assert pat.search(text) is not None


def test_article_regex_does_not_match_inline_reference():
    """줄 중간 인라인 참조는 매칭하면 안 된다."""
    pat = local_doc_mcp._article_regex("제15조")
    text = "법 제15조에 따라 부처의 장은..."  # 줄 시작이 '법 ' — 매칭 NO
    assert pat.search(text) is None


def test_article_regex_byeolpyo():
    pat = local_doc_mcp._article_regex("별표 2")
    text = "\n별표 2\n참여제한 처분기준\n..."
    assert pat.search(text) is not None


def test_article_regex_buchik():
    pat = local_doc_mcp._article_regex("부칙")
    text = "\n부 칙\n이 법은..."
    assert pat.search(text) is not None


# ──────────────────────────────────────────────────────────────────
# MCP 서버 빌드
# ──────────────────────────────────────────────────────────────────


def test_build_local_doc_server_returns_config():
    """create_sdk_mcp_server 가 McpSdkServerConfig 를 돌려준다."""
    cfg = local_doc_mcp.build_local_doc_server()
    # 외부에서 dict 처럼 mcp_servers 에 그대로 들어가는 객체 — None 만 아니면 OK
    assert cfg is not None


def test_tool_names_are_namespaced():
    """allowed_tools 에 넣을 이름이 mcp__local_doc__<tool> 패턴인지 확인."""
    assert all(n.startswith("mcp__local_doc__") for n in local_doc_mcp.TOOL_NAMES)
    bare = {n.removeprefix("mcp__local_doc__") for n in local_doc_mcp.TOOL_NAMES}
    assert {"read_page", "get_article", "search_text", "list_articles", "list_documents"} <= bare


# ──────────────────────────────────────────────────────────────────
# 캐시 동작
# ──────────────────────────────────────────────────────────────────


def test_read_page_uses_cache(manual_pdf, monkeypatch):
    """두 번째 read_page 호출은 pdfplumber 를 다시 열지 않아야 한다."""
    open_count = {"n": 0}

    import pdfplumber as _pp
    real_open = _pp.open

    def _spy_open(*args, **kwargs):
        open_count["n"] += 1
        return real_open(*args, **kwargs)

    monkeypatch.setattr(_pp, "open", _spy_open)

    out1 = local_doc_mcp.read_page(manual_pdf.name, 151)
    out2 = local_doc_mcp.read_page(manual_pdf.name, 152)
    assert "error" not in out1
    assert "error" not in out2
    # list_documents 가 _scan_dirs 안에서 페이지 수만 보려고 pdfplumber 를
    # 한 번 열 수 있고, _load_pages 가 처음 한 번 더 연다. 두 번째 read_page
    # 는 캐시 적중이라 추가로 열지 않아야 한다 — 결과적으로 호출 횟수는
    # 매뉴얼 1개를 *처음 한 번만* 본문 파싱했음을 의미한다.
    # _scan_dirs 가 각 read_page 마다 list_documents 풍 호출을 하는 게 아니므로
    # 차이를 볼 수 있다: 첫 호출에서 1회 (full parse), 두 번째 호출에서 0회.
    # 단, _find_doc_path 안에서 _scan_dirs 를 매번 부르는데 거긴 pdfplumber 를
    # 열지 않으므로 안전.
    assert open_count["n"] == 1, f"PDF 가 캐시 무시하고 {open_count['n']}번 열림"
