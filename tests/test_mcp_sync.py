"""mcp_sync 모듈 단위 테스트 (Phase G5).

검증 시나리오:
    - probe 성공: list_tools 가 expected 를 모두 노출 → schema_match=True
    - probe 실패: 일부 expected tool 누락 → missing_tools 에 포함, schema_match=False
    - probe 네트워크 실패 → ok=False, error 채워짐
    - hwp-mcp 버전 비교: installed < latest → update_available=True
    - hwp-mcp 동일 버전 → update_available=False
    - call_with_fallback: 1회 retry 후 성공 — 카운터 0 유지
    - call_with_fallback: 2회 모두 실패 — consecutive_failures += 1, 원본 예외 raise
    - call_with_fallback: 5회 누적 실패 → disabled_until 설정 → 다음 호출 즉시 MCPDisabledError
    - reset_channel: 카운터/비활성 초기화

실행:
    cd /Users/maro/dev/company/chatbot
    source .venv/bin/activate
    python -m unittest tests.test_mcp_sync -v
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

# 프로젝트 루트를 path 에 추가
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import mcp_sync
from pipeline.mcp_sync import (
    KOREAN_LAW_EXPECTED_TOOLS,
    MAX_CONSECUTIVE_FAILURES,
    MCPDisabledError,
    call_with_fallback,
    check_hwp_mcp_version,
    is_channel_disabled,
    load_status,
    probe_korean_law_mcp,
    reset_channel,
    save_status,
)


class _FakeTool:
    """list_tools() 응답에서 mcp.types.Tool 을 흉내내기 위한 더미."""

    def __init__(self, name: str, properties: list[str] | None = None,
                 required: list[str] | None = None):
        self.name = name
        self.inputSchema = {
            "type": "object",
            "properties": {p: {"type": "string"} for p in (properties or [])},
            "required": list(required or []),
        }


class _FakeToolsResponse:
    def __init__(self, tools):
        self.tools = tools


class TestProbeKoreanLawMCP(unittest.TestCase):
    """probe_korean_law_mcp — list_tools mock."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmp.close()
        self.status_path = Path(self.tmp.name)
        # 테스트 전에 비워둔다.
        self.status_path.write_text("{}", encoding="utf-8")

    def tearDown(self):
        try:
            self.status_path.unlink()
        except FileNotFoundError:
            pass

    def test_probe_success_all_tools_present(self):
        """expected tool 3개 모두 포함된 응답 → schema_match=True."""
        fake = [
            _FakeTool("search_law", ["query", "display"]),
            _FakeTool("get_law_text", ["mst", "jo"]),
            _FakeTool("get_annexes", ["lawName", "annexNo"]),
            _FakeTool("extra_unused_tool", ["foo"]),
        ]

        async def _stub(url):
            return fake

        with patch.object(mcp_sync, "_list_tools_async", _stub):
            result = probe_korean_law_mcp(
                "https://example.local/mcp",
                status_path=self.status_path,
            )

        self.assertTrue(result.ok)
        self.assertTrue(result.schema_match)
        self.assertEqual(result.missing_tools, [])
        self.assertIn("search_law", result.actual_tools)
        self.assertIn("extra_unused_tool", result.actual_tools)
        self.assertNotEqual(result.schema_hash, "")

        # 영속 검증
        status = load_status(self.status_path)
        self.assertIn("korean-law-mcp", status)
        self.assertTrue(status["korean-law-mcp"]["schema_match"])
        self.assertEqual(status["korean-law-mcp"]["missing_tools"], [])

    def test_probe_schema_mismatch(self):
        """get_law_text 가 빠진 응답 → missing_tools 에 포함, schema_match=False."""
        fake = [
            _FakeTool("search_law", ["query"]),
            _FakeTool("get_annexes", ["lawName"]),
            # get_law_text 가 사라진 상황
        ]

        async def _stub(url):
            return fake

        with patch.object(mcp_sync, "_list_tools_async", _stub):
            result = probe_korean_law_mcp(
                "https://example.local/mcp",
                status_path=self.status_path,
            )

        self.assertTrue(result.ok)  # probe 자체는 성공
        self.assertFalse(result.schema_match)
        self.assertIn("get_law_text", result.missing_tools)
        self.assertNotIn("search_law", result.missing_tools)

    def test_probe_network_failure(self):
        """list_tools 가 예외를 던지면 ok=False, error 가 채워진다."""
        async def _boom(url):
            raise ConnectionError("connection refused")

        with patch.object(mcp_sync, "_list_tools_async", _boom):
            result = probe_korean_law_mcp(
                "https://example.local/mcp",
                status_path=self.status_path,
            )

        self.assertFalse(result.ok)
        self.assertFalse(result.schema_match)
        self.assertIsNotNone(result.error)
        self.assertIn("ConnectionError", result.error)
        self.assertEqual(result.actual_tools, [])
        # missing_tools 는 expected 전체
        self.assertEqual(set(result.missing_tools), set(KOREAN_LAW_EXPECTED_TOOLS))

    def test_probe_persist_overwrites(self):
        """두 번 probe 시 last_probe_at 이 갱신된다."""
        async def _stub(url):
            return [_FakeTool(t) for t in KOREAN_LAW_EXPECTED_TOOLS]

        with patch.object(mcp_sync, "_list_tools_async", _stub):
            r1 = probe_korean_law_mcp("u1", status_path=self.status_path)
            r2 = probe_korean_law_mcp("u2", status_path=self.status_path)

        status = load_status(self.status_path)
        self.assertEqual(status["korean-law-mcp"]["url"], "u2")
        self.assertEqual(status["korean-law-mcp"]["last_probe_at"], r2.probed_at)


class TestCheckHwpMcpVersion(unittest.TestCase):
    """check_hwp_mcp_version — pip / PyPI hook mock."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmp.close()
        self.status_path = Path(self.tmp.name)
        self.status_path.write_text("{}", encoding="utf-8")

    def tearDown(self):
        try:
            self.status_path.unlink()
        except FileNotFoundError:
            pass

    def test_update_available(self):
        result = check_hwp_mcp_version(
            status_path=self.status_path,
            _pip_show=lambda pkg: "0.1.5",
            _pypi_latest=lambda pkg: "0.2.0",
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.installed_version, "0.1.5")
        self.assertEqual(result.pypi_latest, "0.2.0")
        self.assertTrue(result.update_available)
        self.assertIsNone(result.error)

    def test_already_latest(self):
        result = check_hwp_mcp_version(
            status_path=self.status_path,
            _pip_show=lambda pkg: "0.2.0",
            _pypi_latest=lambda pkg: "0.2.0",
        )
        self.assertTrue(result.ok)
        self.assertFalse(result.update_available)

    def test_local_newer_than_pypi(self):
        """로컬이 더 최신이어도 update_available=False (다운그레이드 권유 안 함)."""
        result = check_hwp_mcp_version(
            status_path=self.status_path,
            _pip_show=lambda pkg: "0.2.5",
            _pypi_latest=lambda pkg: "0.2.0",
        )
        self.assertFalse(result.update_available)

    def test_not_installed(self):
        result = check_hwp_mcp_version(
            status_path=self.status_path,
            _pip_show=lambda pkg: None,
            _pypi_latest=lambda pkg: "0.2.0",
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.installed_version, None)
        self.assertEqual(result.pypi_latest, "0.2.0")
        self.assertIn("미설치", result.error)

    def test_pypi_unreachable(self):
        result = check_hwp_mcp_version(
            status_path=self.status_path,
            _pip_show=lambda pkg: "0.1.1",
            _pypi_latest=lambda pkg: None,
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.installed_version, "0.1.1")
        self.assertIn("PyPI", result.error)

    def test_persist(self):
        check_hwp_mcp_version(
            status_path=self.status_path,
            _pip_show=lambda pkg: "0.1.5",
            _pypi_latest=lambda pkg: "0.2.0",
        )
        status = load_status(self.status_path)
        self.assertEqual(status["hwp-mcp"]["installed_version"], "0.1.5")
        self.assertEqual(status["hwp-mcp"]["pypi_latest"], "0.2.0")
        self.assertTrue(status["hwp-mcp"]["update_available"])

    def test_version_tuple_handles_dot_segments(self):
        """0.1.10 > 0.1.2 (정수 비교)."""
        from pipeline.mcp_sync import _version_tuple
        self.assertGreater(_version_tuple("0.1.10"), _version_tuple("0.1.2"))
        self.assertGreater(_version_tuple("1.0.0"), _version_tuple("0.99.99"))


class TestCallWithFallback(unittest.TestCase):
    """call_with_fallback retry / disable 흐름."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        self.tmp.close()
        self.status_path = Path(self.tmp.name)
        self.status_path.write_text("{}", encoding="utf-8")
        self.channel = "test-mcp"

    def tearDown(self):
        try:
            self.status_path.unlink()
        except FileNotFoundError:
            pass

    def _calls(self):
        return self._counter

    def test_success_first_try(self):
        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            return "ok"

        result = call_with_fallback(
            self.channel, fn,
            status_path=self.status_path,
            retry_delay_sec=0,
        )
        self.assertEqual(result, "ok")
        self.assertEqual(calls["n"], 1)
        # 카운터 0 유지
        status = load_status(self.status_path)
        self.assertEqual(status[self.channel]["consecutive_failures"], 0)
        self.assertIsNone(status[self.channel]["disabled_until"])

    def test_retry_then_success(self):
        calls = {"n": 0}

        def fn():
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("transient")
            return "ok"

        result = call_with_fallback(
            self.channel, fn,
            status_path=self.status_path,
            retry_delay_sec=0,
        )
        self.assertEqual(result, "ok")
        self.assertEqual(calls["n"], 2)
        # 2회만에 성공 — 실패로 안 침
        status = load_status(self.status_path)
        self.assertEqual(status[self.channel]["consecutive_failures"], 0)

    def test_two_failures_increment_counter(self):
        def fn():
            raise RuntimeError("boom")

        with self.assertRaises(RuntimeError) as cm:
            call_with_fallback(
                self.channel, fn,
                status_path=self.status_path,
                retry_delay_sec=0,
            )
        self.assertIn("boom", str(cm.exception))
        status = load_status(self.status_path)
        self.assertEqual(status[self.channel]["consecutive_failures"], 1)
        self.assertIsNone(status[self.channel]["disabled_until"])

    def test_disable_after_max_consecutive_failures(self):
        def fn():
            raise RuntimeError("boom")

        for _ in range(MAX_CONSECUTIVE_FAILURES):
            with self.assertRaises(RuntimeError):
                call_with_fallback(
                    self.channel, fn,
                    status_path=self.status_path,
                    retry_delay_sec=0,
                )

        status = load_status(self.status_path)
        self.assertEqual(
            status[self.channel]["consecutive_failures"],
            MAX_CONSECUTIVE_FAILURES,
        )
        self.assertIsNotNone(status[self.channel]["disabled_until"])
        self.assertTrue(is_channel_disabled(self.channel, self.status_path))

        # 다음 호출은 즉시 MCPDisabledError, fn 호출 안 됨
        called = {"n": 0}

        def fn2():
            called["n"] += 1
            return "should-not-be-called"

        with self.assertRaises(MCPDisabledError):
            call_with_fallback(
                self.channel, fn2,
                status_path=self.status_path,
                retry_delay_sec=0,
            )
        self.assertEqual(called["n"], 0)

    def test_success_after_partial_failures_resets_counter(self):
        """1, 2회 실패 후 3회차 성공 → 카운터 0 으로 리셋."""
        # 두 번 실패 누적
        def fail():
            raise RuntimeError("boom")

        for _ in range(2):
            with self.assertRaises(RuntimeError):
                call_with_fallback(
                    self.channel, fail,
                    status_path=self.status_path,
                    retry_delay_sec=0,
                )
        status = load_status(self.status_path)
        self.assertEqual(status[self.channel]["consecutive_failures"], 2)

        # 성공 호출
        result = call_with_fallback(
            self.channel, lambda: "ok",
            status_path=self.status_path,
            retry_delay_sec=0,
        )
        self.assertEqual(result, "ok")

        status = load_status(self.status_path)
        self.assertEqual(status[self.channel]["consecutive_failures"], 0)
        self.assertIsNone(status[self.channel]["disabled_until"])

    def test_reset_channel_clears_state(self):
        # 비활성 상태로 만든다
        save_status({
            self.channel: {
                "consecutive_failures": MAX_CONSECUTIVE_FAILURES,
                "disabled_until": (datetime.now() + timedelta(hours=1)).isoformat(timespec="seconds"),
            }
        }, self.status_path)
        self.assertTrue(is_channel_disabled(self.channel, self.status_path))

        reset_channel(self.channel, self.status_path)
        self.assertFalse(is_channel_disabled(self.channel, self.status_path))
        status = load_status(self.status_path)
        self.assertEqual(status[self.channel]["consecutive_failures"], 0)
        self.assertIsNone(status[self.channel]["disabled_until"])

    def test_disabled_until_in_past_is_not_disabled(self):
        """disabled_until 이 과거 시각이면 활성으로 간주되어야 한다."""
        save_status({
            self.channel: {
                "consecutive_failures": MAX_CONSECUTIVE_FAILURES,
                "disabled_until": (datetime.now() - timedelta(seconds=10)).isoformat(timespec="seconds"),
            }
        }, self.status_path)
        self.assertFalse(is_channel_disabled(self.channel, self.status_path))

        # 호출이 즉시 가능해야 함
        result = call_with_fallback(
            self.channel, lambda: "ok",
            status_path=self.status_path,
            retry_delay_sec=0,
        )
        self.assertEqual(result, "ok")

    def test_passes_args_kwargs(self):
        captured = {}

        def fn(a, b, *, c):
            captured["args"] = (a, b)
            captured["kwargs"] = {"c": c}
            return a + b + c

        result = call_with_fallback(
            self.channel, fn, 1, 2,
            status_path=self.status_path,
            retry_delay_sec=0,
            c=10,
        )
        self.assertEqual(result, 13)
        self.assertEqual(captured["args"], (1, 2))
        self.assertEqual(captured["kwargs"], {"c": 10})


if __name__ == "__main__":
    unittest.main(verbosity=2)
