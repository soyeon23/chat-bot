"""retriever.search_chunks_smart 가 rewritten_query 와 호환되는지 검증.

핵심 회귀 보호:
  - hints.rewritten_query 와 question 인자가 다르더라도 search_chunks_smart 는
    hints 안의 구조 신호 (article_no/target_pages/keywords) 만 참조하고 question
    문자열은 페이로드 부스트 ("rewritten" 단어 검색 같은 것) 에 *직접* 사용하지 않는다.
  - 즉 caller 가 question=rewritten 으로 넘겨도 retriever 의 내부 동작은 안전하다.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from pipeline import retriever
from pipeline.query_parser import QueryHints, parse_query


class _FakeResponse:
    """qdrant query_points 응답 mock."""

    def __init__(self, points):
        self.points = points


class _FakePoint:
    def __init__(self, pid, score, payload):
        self.id = pid
        self.score = score
        self.payload = payload


class _FakeClient:
    """search_chunks_smart 가 호출하는 qdrant API 의 최소 mock."""

    def __init__(self):
        self.query_calls: list[dict] = []
        self.scroll_calls: list[dict] = []

    def query_points(self, *, collection_name, query, query_filter=None, limit=10, with_payload=True):
        self.query_calls.append({
            "filter": query_filter,
            "limit": limit,
        })
        # 단일 가짜 결과: 별표2 / 매뉴얼 / page=205
        return _FakeResponse([
            _FakePoint(
                "fake-1",
                0.78,
                {
                    "doc_name": "매뉴얼",
                    "doc_type": "매뉴얼",
                    "article_no": "별표2",
                    "article_title": "비목별",
                    "page": 205,
                    "text": "학생인건비 한도 ...",
                },
            )
        ])

    def scroll(self, *, collection_name, scroll_filter, limit, with_payload=True):
        self.scroll_calls.append({"filter": scroll_filter, "limit": limit})
        return ([], None)


def test_search_chunks_smart_accepts_rewritten_query_unchanged():
    """question 이 rewritten_query 든 원본이든 동일 결과 (hints 가 진짜 신호)."""
    fake = _FakeClient()
    with patch.object(retriever, "get_qdrant_client", return_value=fake):
        hints = parse_query("학생인건비 한도")
        hints.rewritten_query = "학생인건비 한도와 그 사례"  # caller 가 멀티턴 rewriting

        result = retriever.search_chunks_smart(
            "학생인건비 한도와 그 사례",  # rewritten 으로 넘김
            [0.1] * 768,
            top_k=5,
            hints=hints,
        )
    assert len(result) == 1
    assert result[0]["article_no"] == "별표2"
    assert result[0]["page"] == 205


def test_search_chunks_smart_hints_take_priority_over_question():
    """hints 가 명시되면 question 문자열은 page/article 추출에 안 쓰임 (부스트 신호 안전)."""
    fake = _FakeClient()
    with patch.object(retriever, "get_qdrant_client", return_value=fake):
        # hints 에는 별표2 + page=151 이 있는데 question 텍스트는 무관한 자연어
        hints = QueryHints(
            article_nos=[],
            appendices=["별표2"],
            target_pages=[151],
            keywords=["학생인건비"],
            rewritten_query="별표 2 학생인건비 151p",
        )
        retriever.search_chunks_smart(
            "전혀 다른 텍스트",  # 실제 검색은 hints 의 구조 신호만 사용해야 함
            [0.1] * 768,
            top_k=5,
            hints=hints,
        )

    # scroll 호출 중 page=151 으로 필터된 호출이 1번 이상 있어야 한다 (1b 단계)
    page_filter_seen = False
    for call in fake.scroll_calls:
        flt = call["filter"]
        if flt is None:
            continue
        for clause in flt.must or []:
            # FieldCondition.match.value 가 151 인지
            try:
                if getattr(clause, "key", "") == "page" and clause.match.value == 151:
                    page_filter_seen = True
            except AttributeError:
                continue
    assert page_filter_seen, "target_pages=151 에 대한 scroll 필터가 호출돼야 한다"


def test_search_chunks_smart_no_hints_fallback_uses_question():
    """hints=None 이면 question 으로 parse_query 폴백 — 회귀 보호."""
    fake = _FakeClient()
    with patch.object(retriever, "get_qdrant_client", return_value=fake):
        # hints 미지정 → 내부에서 parse_query("별표 2") 호출 → appendices=['별표2'] 추출
        retriever.search_chunks_smart(
            "별표 2 학생인건비",
            [0.1] * 768,
            top_k=5,
        )
    # 별표2 매칭에 의한 추가 query_points 호출이 1번 이상 있어야 함
    assert len(fake.query_calls) >= 2, (
        "hints 미지정 시 parse_query 가 별표를 잡아 구조 매칭이 실행돼야 한다"
    )
