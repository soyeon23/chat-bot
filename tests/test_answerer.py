"""pipeline.answerer.get_model 라우팅 테스트.

scout F1 권고:
- 기본: Sonnet 4.6 (`claude-sonnet-4-6`)
- 비교형(comparison) 질의 + escalate 토글 ON: Opus 4.6 (`claude-opus-4-6`)
- 사용자가 환경설정에서 모델을 명시적으로 골랐다면 그 선택이 모든 kind 에 우선
- escalate 토글 OFF 면 comparison 도 sonnet 유지
- page_lookup / article_lookup / open / chat 은 escalate 대상 아님
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from unittest.mock import patch

import pytest

from pipeline import answerer
from pipeline.answerer import _COMPARISON_MODEL, _DEFAULT_MODEL, get_model


# ── 헬퍼 ─────────────────────────────────────────────────────


@dataclass
class _FakeConfig:
    """ProjectConfig 의 최소 stand-in. 실제 ProjectConfig 와 동일 필드명."""

    claude_model: str = ""
    enable_comparison_escalate: bool = True


def _patch_config(cfg: _FakeConfig):
    """answerer.get_model 이 import 하는 load_config 를 fake 로 대체."""
    return patch("pipeline.config_store.load_config", return_value=cfg)


@pytest.fixture(autouse=True)
def _no_env_claude_model(monkeypatch):
    """CLAUDE_MODEL 환경변수가 외부에서 설정돼 있어도 테스트는 깨끗한 상태에서."""
    monkeypatch.delenv("CLAUDE_MODEL", raising=False)


# ── 상수 검증 — 권고된 모델 alias 가 정확한지 ─────────────────


def test_default_model_is_sonnet_4_6():
    assert _DEFAULT_MODEL == "claude-sonnet-4-6"


def test_comparison_model_is_opus_4_6():
    assert _COMPARISON_MODEL == "claude-opus-4-6"


# ── 자동 모드 (사용자 명시 선택 없음) ─────────────────────────


def test_open_returns_sonnet_default():
    """일반 open 질의는 Sonnet 4.6."""
    with _patch_config(_FakeConfig(claude_model="", enable_comparison_escalate=True)):
        assert get_model("open") == "claude-sonnet-4-6"


def test_comparison_escalates_to_opus():
    """comparison + escalate ON → Opus 4.6."""
    with _patch_config(_FakeConfig(claude_model="", enable_comparison_escalate=True)):
        assert get_model("comparison") == "claude-opus-4-6"


def test_comparison_without_escalate_stays_sonnet():
    """comparison 이어도 escalate OFF 면 Sonnet 유지."""
    with _patch_config(_FakeConfig(claude_model="", enable_comparison_escalate=False)):
        assert get_model("comparison") == "claude-sonnet-4-6"


def test_page_lookup_does_not_escalate():
    """page_lookup 은 비교형이 아니므로 escalate 대상 아님."""
    with _patch_config(_FakeConfig(claude_model="", enable_comparison_escalate=True)):
        assert get_model("page_lookup") == "claude-sonnet-4-6"


def test_article_lookup_does_not_escalate():
    with _patch_config(_FakeConfig(claude_model="", enable_comparison_escalate=True)):
        assert get_model("article_lookup") == "claude-sonnet-4-6"


def test_chat_does_not_escalate():
    with _patch_config(_FakeConfig(claude_model="", enable_comparison_escalate=True)):
        assert get_model("chat") == "claude-sonnet-4-6"


# ── 사용자가 명시적으로 모델 선택한 경우 — 항상 그 선택이 이김 ─


def test_explicit_sonnet_overrides_for_open():
    with _patch_config(_FakeConfig(claude_model="claude-sonnet-4-6", enable_comparison_escalate=True)):
        assert get_model("open") == "claude-sonnet-4-6"


def test_explicit_sonnet_overrides_even_for_comparison():
    """사용자가 Sonnet 을 명시적으로 골랐다면 비교형 질의도 그 선택을 따른다."""
    with _patch_config(_FakeConfig(claude_model="claude-sonnet-4-6", enable_comparison_escalate=True)):
        assert get_model("comparison") == "claude-sonnet-4-6"


def test_explicit_opus_used_for_all_kinds():
    """Opus 강제 — 모든 kind 가 Opus 로 나가야 한다."""
    cfg = _FakeConfig(claude_model="claude-opus-4-6", enable_comparison_escalate=True)
    with _patch_config(cfg):
        assert get_model("open") == "claude-opus-4-6"
        assert get_model("comparison") == "claude-opus-4-6"
        assert get_model("page_lookup") == "claude-opus-4-6"


# ── 환경변수 폴백 (config 가 깨져 load 실패할 때) ────────────


def test_env_fallback_when_config_load_fails(monkeypatch):
    """config 로드가 예외를 던지면 env CLAUDE_MODEL 로 폴백, 거기에도 없으면 default."""
    monkeypatch.setenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001")
    with patch("pipeline.config_store.load_config", side_effect=RuntimeError("config corrupt")):
        # config 로드 실패 → cfg=None → claude_model 빈 값으로 처리 → comparison 만 escalate
        # open 은 env 변수로 폴백.
        assert get_model("open") == "claude-haiku-4-5-20251001"


def test_default_when_no_config_no_env():
    """config 실패 + env 없음 → DEFAULT_MODEL."""
    with patch("pipeline.config_store.load_config", side_effect=RuntimeError("boom")):
        assert get_model("open") == _DEFAULT_MODEL


def test_default_when_no_config_no_env_comparison_still_escalates():
    """config 실패해도 comparison 은 escalate (cfg=None 일 때 기본값 True 가정)."""
    with patch("pipeline.config_store.load_config", side_effect=RuntimeError("boom")):
        assert get_model("comparison") == _COMPARISON_MODEL


# ── 시그니처 호환 — kind 인자 없이 호출해도 동작해야 함 ───────


def test_get_model_default_kind_is_open():
    """kind 인자 없이 호출하면 open 으로 취급 (sonnet)."""
    with _patch_config(_FakeConfig(claude_model="", enable_comparison_escalate=True)):
        assert get_model() == "claude-sonnet-4-6"
