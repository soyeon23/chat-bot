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


# ──────────────────────────────────────────────────────────────────
# Phase H+1: max_turns graceful stub
# ──────────────────────────────────────────────────────────────────


def test_max_turns_graceful_stub_returns_judgment_undecided():
    """max_turns 도달 + 누적 텍스트 < 100자 시 RuntimeError 대신 stub JSON 반환.

    `_run_query_sync` 가 stub JSON 문자열을 반환하면 generate_answer 는 정상
    경로로 (JSON 파싱 + citations 보강) 흘려 보내야 한다.
    """
    stub_json = json.dumps(answerer._MAX_TURNS_STUB_PAYLOAD, ensure_ascii=False)

    captured: dict = {}

    def _fake_sync(model, system_prompt, user_prompt, *, enable_tools=False):
        captured["enable_tools"] = enable_tools
        return stub_json

    with patch.object(answerer, "_run_query_sync", side_effect=_fake_sync):
        result = answerer.generate_answer(
            "10조", _chunks(), kind="article_lookup",
        )

    # article_lookup 은 도구 활성화 경로
    assert captured["enable_tools"] is True
    assert result["verdict"] == "판단불가"
    assert "max_turns" in result["summary"] or "한도" in result["summary"]
    assert result["follow_up_needed"] is True
    # citations 는 generate_answer 가 chunks[0] 으로 보강 — 빈 배열이 아니어야 함
    assert len(result["citations"]) >= 1
    assert result["citations"][0]["document_name"] == "매뉴얼"


def test_max_turns_stub_payload_validates_against_schema():
    """stub payload 자체가 AnswerPayload 스키마를 통과해야 한다 — citations
    빈 배열은 generate_answer 가 보강하지만 스키마 검증은 *통과 후* 일어나므로
    citations 보강 후의 형태가 valid 해야 한다."""
    stub = dict(answerer._MAX_TURNS_STUB_PAYLOAD)
    # generate_answer 가 하는 보강을 흉내
    stub["citations"] = [
        {
            "document_name": "매뉴얼",
            "article_no": "별표2",
            "page": 100,
            "quote": "회의비는 ...",
        }
    ]
    from pipeline.schemas import AnswerPayload
    payload = AnswerPayload.model_validate(stub)
    assert payload.verdict == "판단불가"


def test_max_turns_min_chars_threshold_constant():
    """max_turns 누적 임계치 상수가 합리적인 범위에 있어야 한다."""
    assert 50 <= answerer._MAX_TURNS_STUB_MIN_CHARS <= 200


def test_run_query_returns_stub_when_max_turns_with_short_text():
    """_run_query: ResultMessage(subtype='error_max_turns') + 누적 텍스트 < 100자
    → RuntimeError 대신 stub JSON 문자열 반환.
    """
    import asyncio
    from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

    # 짧은 누적 텍스트 (도구만 호출하다 max_turns 도달한 시나리오)
    msgs = [
        AssistantMessage(content=[TextBlock(text="조회 중...")], model="claude-sonnet-4-6"),
        ResultMessage(
            subtype="error_max_turns",
            duration_ms=5000,
            duration_api_ms=4000,
            is_error=True,
            num_turns=8,
            session_id="test-session",
            errors=["max_turns reached"],
        ),
    ]

    async def _fake_query(prompt, options):
        for m in msgs:
            yield m

    with patch("claude_agent_sdk.query", side_effect=_fake_query):
        result = asyncio.run(
            answerer._run_query(
                "claude-sonnet-4-6",
                "system",
                "user prompt",
                enable_tools=True,
            )
        )

    # stub JSON 이 반환됐는지 — verdict=판단불가, summary 안에 max_turns 키워드
    assert result.strip().startswith("{")
    parsed = json.loads(result)
    assert parsed["verdict"] == "판단불가"
    assert parsed["follow_up_needed"] is True


def test_run_query_returns_stub_when_max_turns_then_transport_dies():
    """SDK transport 가 ResultMessage(error_max_turns) 직후 exit code 1 로 끊겨도
    RuntimeError 던지지 말고 stub JSON 반환.

    실제 SDK 1.x 에서 관찰되는 경로 — claude CLI 가 max_turns 도달 시 stream 을
    exit code 1 로 종료해 `Exception("Command failed with exit code 1")` 가
    async-for 안에서 raise 됨.
    """
    import asyncio
    from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

    msgs = [
        AssistantMessage(
            content=[TextBlock(text="조회 중...")], model="claude-sonnet-4-6"
        ),
        ResultMessage(
            subtype="error_max_turns",
            duration_ms=5000,
            duration_api_ms=4000,
            is_error=True,
            num_turns=8,
            session_id="test-session",
            errors=["max_turns reached"],
        ),
    ]

    async def _fake_query_then_raise(prompt, options):
        for m in msgs:
            yield m
        # ResultMessage 받은 직후 transport 가 끊김
        raise Exception("Command failed with exit code 1 (exit code: 1)")

    with patch("claude_agent_sdk.query", side_effect=_fake_query_then_raise):
        result = asyncio.run(
            answerer._run_query(
                "claude-sonnet-4-6",
                "system",
                "user prompt",
                enable_tools=True,
            )
        )

    parsed = json.loads(result)
    assert parsed["verdict"] == "판단불가"
    assert parsed["follow_up_needed"] is True


def test_run_query_does_not_swallow_unrelated_exception():
    """transport 끊김이 *max_turns 와 무관한* 예외라면 그대로 raise.

    예: SDK 가 'connection refused' 같은 진짜 실패를 던진 경우 stub 으로 가리면
    디버깅이 어려워진다.
    """
    import asyncio

    async def _fake_query_unrelated(prompt, options):
        # ResultMessage 도 도착 안 함
        if False:
            yield None  # pragma: no cover
        raise Exception("connection refused: claude CLI not found")

    with patch("claude_agent_sdk.query", side_effect=_fake_query_unrelated):
        with pytest.raises(Exception) as ei:
            asyncio.run(
                answerer._run_query(
                    "claude-sonnet-4-6",
                    "system",
                    "user prompt",
                    enable_tools=True,
                )
            )
        # 원본 예외가 그대로 올라와야 함
        assert "connection refused" in str(ei.value)


def test_run_query_no_stub_when_max_turns_with_partial_json():
    """_run_query: max_turns 도달했지만 누적 텍스트가 임계치 이상이면
    원본 텍스트를 그대로 반환 (generate_answer 가 거기서 JSON 추출 시도).
    """
    import asyncio
    from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock

    # 임계치 이상 누적 (예: 모델이 JSON 을 거의 다 만들었으나 마지막 턴에 잘림)
    long_partial = '{"verdict":"가능","summary":"' + "가" * 200 + '"...'
    msgs = [
        AssistantMessage(content=[TextBlock(text=long_partial)], model="claude-sonnet-4-6"),
        ResultMessage(
            subtype="error_max_turns",
            duration_ms=5000,
            duration_api_ms=4000,
            is_error=True,
            num_turns=8,
            session_id="test-session",
            errors=["max_turns reached"],
        ),
    ]

    async def _fake_query(prompt, options):
        for m in msgs:
            yield m

    with patch("claude_agent_sdk.query", side_effect=_fake_query):
        result = asyncio.run(
            answerer._run_query(
                "claude-sonnet-4-6",
                "system",
                "user prompt",
                enable_tools=True,
            )
        )

    # stub 으로 *덮어쓰지* 않음 — 원본 누적 텍스트가 그대로 반환됨
    assert "verdict" in result
    assert long_partial in result
    # stub 의 고정 문구는 들어있지 않음
    assert "max_turns=8" not in result
