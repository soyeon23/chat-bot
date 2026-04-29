"""generate_answer 의 멀티턴 (prior_turns) 처리 테스트.

- claude-agent-sdk 호출은 mock 으로 대체. 우리가 검증하는 것은
  *user_prompt 에 [이전 대화] 블록이 정확히 포함되는지* + 단발 호환성.
- prompts.build_user_prompt 단독 단위 테스트도 함께 둔다.
"""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from pipeline import answerer
from pipeline.prompts import build_user_prompt


# ── 헬퍼 ────────────────────────────────────────────────────────────


_VALID_ANSWER = {
    "verdict": "가능",
    "summary": "회의비로 세미나 사용 가능 (직전 답변 이어감).",
    "citations": [
        {
            "document_name": "매뉴얼",
            "article_no": "별표2",
            "page": 100,
            "quote": "회의비는 ...",
        }
    ],
    "follow_up_needed": False,
    "follow_up_questions": [],
    "risk_notes": [],
}


def _chunks() -> list[dict]:
    return [
        {
            "doc_name": "매뉴얼",
            "doc_type": "매뉴얼",
            "article_no": "별표2",
            "article_title": "비목별 사용 기준",
            "page": 100,
            "text": "회의비는 ...",
        }
    ]


# ── build_user_prompt 단위 테스트 ───────────────────────────────────


def test_build_user_prompt_no_prior_turns_unchanged():
    """prior_turns 없으면 [이전 대화] 블록이 들어가지 않는다 (단발 호환)."""
    prompt = build_user_prompt("회의비?", _chunks())
    assert "[이전 대화]" not in prompt
    assert "[검색된 근거]" in prompt
    assert "[질문]" in prompt
    assert "회의비?" in prompt


def test_build_user_prompt_empty_prior_turns_unchanged():
    """빈 리스트도 단발과 동일."""
    prompt = build_user_prompt("회의비?", _chunks(), prior_turns=[])
    assert "[이전 대화]" not in prompt


def test_build_user_prompt_includes_prior_turns_block():
    """prior_turns 가 있으면 user 프롬프트 머리에 [이전 대화] 블록이 들어가야 한다."""
    prior = [
        {"role": "user", "content": "회의비로 세미나 사용 가능?"},
        {"role": "assistant", "content": "가능합니다. 회의비 사용 기준에 따라..."},
    ]
    prompt = build_user_prompt("실제 사례 있어?", _chunks(), prior_turns=prior)
    assert "[이전 대화]" in prompt
    assert "[이전 사용자 질문]" in prompt
    assert "[이전 답변 요약]" in prompt
    assert "회의비로 세미나 사용 가능?" in prompt
    assert "가능합니다. 회의비 사용 기준에 따라..." in prompt
    # [이전 대화] 가 [검색된 근거] 앞에 와야 함
    assert prompt.index("[이전 대화]") < prompt.index("[검색된 근거]")
    # 현재 질문은 마지막에
    assert prompt.rindex("[질문]") > prompt.rindex("[이전 답변 요약]")


def test_build_user_prompt_truncates_long_prior_turns():
    """길이 1만 자 assistant 답변도 잘려서 들어가야 한다 (토큰 폭주 방지)."""
    huge = "가" * 5000
    prior = [
        {"role": "assistant", "content": huge},
    ]
    prompt = build_user_prompt("Q?", _chunks(), prior_turns=prior)
    # 잘렸음을 표시하는 ellipsis 가 있어야 함
    assert "…" in prompt
    # 5000자 그대로는 들어가면 안 됨
    assert huge not in prompt


def test_build_user_prompt_keeps_only_last_n_turns():
    """매우 긴 history → 최신 N 턴만 남는다."""
    prior = [
        {"role": "user", "content": f"질문{i}"} for i in range(20)
    ]
    prompt = build_user_prompt("Q?", _chunks(), prior_turns=prior)
    # 가장 오래된 질문0~13 은 잘려서 안 들어감 (limit=6)
    assert "질문0" not in prompt
    assert "질문19" in prompt  # 최신은 남음


def test_build_user_prompt_skips_unknown_roles():
    """system / tool 같은 role 은 무시 (혼동 방지)."""
    prior = [
        {"role": "system", "content": "ignored"},
        {"role": "user", "content": "hello"},
    ]
    prompt = build_user_prompt("Q?", _chunks(), prior_turns=prior)
    assert "ignored" not in prompt
    assert "hello" in prompt


# ── generate_answer 통합 테스트 (SDK mock) ─────────────────────────


def test_generate_answer_passes_prior_turns_to_user_prompt():
    """generate_answer(prior_turns=...) → 내부적으로 user_prompt 에 [이전 대화] 포함."""
    prior = [
        {"role": "user", "content": "회의비로 세미나 사용 가능?"},
        {"role": "assistant", "content": "가능합니다."},
    ]

    captured: dict = {}

    def _fake_sync(model, system_prompt, user_prompt, *, enable_tools=False):
        captured["user_prompt"] = user_prompt
        captured["system_prompt"] = system_prompt
        return json.dumps(_VALID_ANSWER, ensure_ascii=False)

    with patch.object(answerer, "_run_query_sync", side_effect=_fake_sync):
        result = answerer.generate_answer(
            "실제 사례 있어?", _chunks(), kind="open", prior_turns=prior,
        )

    assert result["verdict"] == "가능"
    assert "[이전 대화]" in captured["user_prompt"]
    assert "회의비로 세미나 사용 가능?" in captured["user_prompt"]
    assert "가능합니다." in captured["user_prompt"]
    # system_prompt 에 원칙 9 (멀티턴) 가 적힌 SYSTEM_PROMPT 가 들어가야 함
    assert "다중 턴 대화" in captured["system_prompt"] or "멀티턴" in captured["system_prompt"] \
           or "이전 대화" in captured["system_prompt"]


def test_generate_answer_no_prior_turns_keeps_old_format():
    """단발 호출 — prior_turns 미지정. user_prompt 에 [이전 대화] 블록 없어야 함."""
    captured: dict = {}

    def _fake_sync(model, system_prompt, user_prompt, *, enable_tools=False):
        captured["user_prompt"] = user_prompt
        return json.dumps(_VALID_ANSWER, ensure_ascii=False)

    with patch.object(answerer, "_run_query_sync", side_effect=_fake_sync):
        result = answerer.generate_answer(
            "회의비 사용 가능?", _chunks(), kind="open",
        )

    assert result["verdict"] == "가능"
    assert "[이전 대화]" not in captured["user_prompt"]


def test_generate_answer_empty_prior_turns_keeps_old_format():
    captured: dict = {}

    def _fake_sync(model, system_prompt, user_prompt, *, enable_tools=False):
        captured["user_prompt"] = user_prompt
        return json.dumps(_VALID_ANSWER, ensure_ascii=False)

    with patch.object(answerer, "_run_query_sync", side_effect=_fake_sync):
        answerer.generate_answer(
            "회의비?", _chunks(), kind="open", prior_turns=[],
        )

    assert "[이전 대화]" not in captured["user_prompt"]


def test_generate_answer_signature_back_compat():
    """기존 시그니처 (question, chunks, kind=...) 그대로 호출돼야 한다."""
    with patch.object(
        answerer, "_run_query_sync",
        return_value=json.dumps(_VALID_ANSWER, ensure_ascii=False),
    ):
        # kind 도 위치 인자로 들어갈 수 있는지 + 없을 때 default 도 OK
        result1 = answerer.generate_answer("회의비?", _chunks())
        result2 = answerer.generate_answer("회의비?", _chunks(), kind="page_lookup")
    assert result1["verdict"] == "가능"
    assert result2["verdict"] == "가능"


def test_generate_answer_raises_valueerror_when_no_chunks():
    """기존 동작 그대로 — 빈 chunks 면 ValueError. prior_turns 가 있어도 동일."""
    with pytest.raises(ValueError):
        answerer.generate_answer(
            "Q?", [], kind="open",
            prior_turns=[{"role": "user", "content": "prior"}],
        )
