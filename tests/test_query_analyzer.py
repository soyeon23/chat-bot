"""query_analyzer 의 멀티턴 / rewritten_query 처리 테스트.

전제:
- LLM 호출(`_run_query_sync`) 은 mock 으로 대체. 분석기 자체의 *결과 처리 로직*
  (rewritten_query 폴백, chat 분기, regex fallback) 만 검증한다.
- 실제 LLM 의 rewriting 품질은 prompt 설계의 책임이고 단위 테스트 범위 밖.
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from pipeline import query_analyzer
from pipeline.query_parser import QueryHints, parse_query


def _mock_llm_response(payload: dict) -> str:
    """LLM 이 돌려줄 raw 텍스트 (JSON 한 줄)."""
    return json.dumps(payload, ensure_ascii=False)


# ── 케이스 1: 후속 질문 — rewritten_query 가 standalone 질의로 들어옴 ─────────


def test_rewritten_query_followup_absorbs_prior_topic():
    """후속어("실제 사례 있어?") 가 직전 주제 흡수해 self-contained 질의로 변환."""
    raw = _mock_llm_response({
        "kind": "open",
        "chat_response": "",
        "target_pages": [],
        "target_articles": [],
        "target_paragraphs": [],
        "target_items": [],
        "target_appendices": [],
        "target_forms": [],
        "target_sections": [],
        "doc_name_hint": "",
        "topic_keywords": ["회의비", "세미나", "사례"],
        "comparison_intent": False,
        "rewritten_query": "회의비로 세미나 개최한 실제 사례",
    })
    prior = [
        {"role": "user", "content": "회의비로 세미나 사용 가능?"},
        {"role": "assistant", "content": "가능합니다. 회의비 사용 기준에 따라..."},
    ]
    with patch.object(query_analyzer, "_run_query_sync", return_value=raw):
        hints = query_analyzer.analyze_query("실제 사례 있어?", prior_turns=prior)
    assert hints.rewritten_query == "회의비로 세미나 개최한 실제 사례"
    assert hints.kind == "open"
    assert "회의비" in hints.keywords
    assert "사례" in hints.keywords


# ── 케이스 2: 주제 전환 — 새 주제 그대로, 직전 주제 끼어들지 않음 ─────────────


def test_rewritten_query_topic_shift():
    """사용자가 새 주제 명시 → rewritten_query 는 새 주제 그대로 (이전 컨텍스트 흡수 X)."""
    raw = _mock_llm_response({
        "kind": "open",
        "chat_response": "",
        "target_pages": [],
        "target_articles": [],
        "target_paragraphs": [],
        "target_items": [],
        "target_appendices": [],
        "target_forms": [],
        "target_sections": [],
        "doc_name_hint": "",
        "topic_keywords": ["학생인건비"],
        "comparison_intent": False,
        "rewritten_query": "학생인건비 한도와 지급 기준",
    })
    prior = [
        {"role": "user", "content": "회의비로 세미나 사용 가능?"},
        {"role": "assistant", "content": "가능합니다."},
    ]
    with patch.object(query_analyzer, "_run_query_sync", return_value=raw):
        hints = query_analyzer.analyze_query(
            "그럼 학생인건비는?", prior_turns=prior,
        )
    # 새 주제로 깨끗이 전환됐는지: rewritten 에 회의비 흔적 X
    assert "회의비" not in hints.rewritten_query
    assert "학생인건비" in hints.rewritten_query


# ── 케이스 3: 단발 질문 — rewritten_query 가 question 과 동일 또는 자연 변형 ─


def test_rewritten_query_first_turn_passthrough():
    """첫 질문 (prior_turns 없음). LLM 이 rewritten_query 를 원본 그대로 두는 경우."""
    question = "별표 2 학생인건비 지급 기준"
    raw = _mock_llm_response({
        "kind": "article_lookup",
        "chat_response": "",
        "target_pages": [],
        "target_articles": [],
        "target_paragraphs": [],
        "target_items": [],
        "target_appendices": ["별표2"],
        "target_forms": [],
        "target_sections": [],
        "doc_name_hint": "",
        "topic_keywords": ["학생인건비", "지급기준"],
        "comparison_intent": False,
        "rewritten_query": question,
    })
    with patch.object(query_analyzer, "_run_query_sync", return_value=raw):
        hints = query_analyzer.analyze_query(question, prior_turns=None)
    assert hints.rewritten_query == question
    assert hints.kind == "article_lookup"


def test_rewritten_query_first_turn_llm_omits_field():
    """LLM 이 rewritten_query 필드를 빠뜨리거나 빈 문자열로 출력 → 원본 question 으로 폴백."""
    question = "혁신법 시행령 제15조"
    raw = _mock_llm_response({
        "kind": "article_lookup",
        "chat_response": "",
        "target_pages": [],
        "target_articles": ["제15조"],
        "target_paragraphs": [],
        "target_items": [],
        "target_appendices": [],
        "target_forms": [],
        "target_sections": [],
        "doc_name_hint": "시행령",
        "topic_keywords": [],
        "comparison_intent": False,
        # rewritten_query 필드 자체 누락
    })
    with patch.object(query_analyzer, "_run_query_sync", return_value=raw):
        hints = query_analyzer.analyze_query(question)
    assert hints.rewritten_query == question  # 폴백


# ── 케이스 4: 일상 대화 — rewritten_query 빈 문자열 ────────────────────────


def test_rewritten_query_empty_for_chat():
    """chat 분류에서는 rewritten_query 가 비어 있어야 한다 (검색 안 함)."""
    raw = _mock_llm_response({
        "kind": "chat",
        "chat_response": "안녕하세요! 무엇을 도와드릴까요?",
        "target_pages": [],
        "target_articles": [],
        "target_paragraphs": [],
        "target_items": [],
        "target_appendices": [],
        "target_forms": [],
        "target_sections": [],
        "doc_name_hint": "",
        "topic_keywords": [],
        "comparison_intent": False,
        "rewritten_query": "",
    })
    with patch.object(query_analyzer, "_run_query_sync", return_value=raw):
        hints = query_analyzer.analyze_query("안녕")
    assert hints.kind == "chat"
    assert hints.rewritten_query == ""
    assert "안녕하세요" in hints.chat_response


# ── 케이스 5: 메타 질문 (페이지 직접) — rewritten 에 페이지 보존 ───────────


def test_rewritten_query_preserves_page_in_followup():
    """직전이 '151p FAQ' 였고 현재 'Q3 알려줘' 면 페이지 정보가 rewritten 에 포함."""
    raw = _mock_llm_response({
        "kind": "page_lookup",
        "chat_response": "",
        "target_pages": [151],
        "target_articles": [],
        "target_paragraphs": [],
        "target_items": [],
        "target_appendices": [],
        "target_forms": [],
        "target_sections": [],
        "doc_name_hint": "매뉴얼",
        "topic_keywords": ["FAQ", "Q3"],
        "comparison_intent": False,
        "rewritten_query": "매뉴얼 151페이지 FAQ Q3 내용",
    })
    prior = [
        {"role": "user", "content": "매뉴얼 151p에 뭐 있어?"},
        {"role": "assistant", "content": "FAQ Q1~Q7 이 있습니다."},
    ]
    with patch.object(query_analyzer, "_run_query_sync", return_value=raw):
        hints = query_analyzer.analyze_query("Q3 알려줘", prior_turns=prior)
    assert 151 in hints.target_pages
    assert "151" in hints.rewritten_query
    assert hints.kind == "page_lookup"


# ── 케이스 6: LLM 실패 → regex fallback (rewritten_query == question) ───────


def test_rewritten_query_regex_fallback_uses_original_question():
    """LLM 실패 시 parse_query 가 호출되고, 그 결과는 rewritten_query=question."""
    question = "별표 2 학생인건비"
    with patch.object(
        query_analyzer, "_run_query_sync",
        side_effect=RuntimeError("network down"),
    ):
        hints = query_analyzer.analyze_query(question, prior_turns=None)
    # regex 폴백은 멀티턴을 모르므로 question 그대로 들어가야 한다.
    assert hints.rewritten_query == question
    # regex 가 별표는 잡았는지도 확인 (parse_query 정상 동작 신호)
    assert "별표2" in hints.appendices


# ── 케이스 7: parse_query 단독 — rewritten_query=question 항상 보장 ─────


def test_parse_query_always_sets_rewritten_to_original():
    """retriever 가 hints=None 인 경우 parse_query 폴백을 받기 때문에
    rewritten_query 가 항상 채워져 있어야 caller 가 안전하게 사용 가능."""
    q = "회의비로 세미나 가능?"
    h = parse_query(q)
    assert h.rewritten_query == q


def test_parse_query_empty_returns_empty_hints():
    """빈 입력은 빈 QueryHints — rewritten_query 도 빈 문자열."""
    h = parse_query("")
    assert h.rewritten_query == ""


# ── 회귀: chat 응답 텍스트 자동 폴백 (LLM 이 chat 인데 chat_response 빈 경우) ──


def test_chat_with_empty_response_gets_safe_default():
    raw = _mock_llm_response({
        "kind": "chat",
        "chat_response": "",  # LLM 실수로 빈 응답
        "target_pages": [],
        "target_articles": [],
        "target_paragraphs": [],
        "target_items": [],
        "target_appendices": [],
        "target_forms": [],
        "target_sections": [],
        "doc_name_hint": "",
        "topic_keywords": [],
        "comparison_intent": False,
        "rewritten_query": "",
    })
    with patch.object(query_analyzer, "_run_query_sync", return_value=raw):
        hints = query_analyzer.analyze_query("안녕")
    assert hints.kind == "chat"
    assert hints.chat_response  # 안전한 기본 메시지가 들어 있어야 함
