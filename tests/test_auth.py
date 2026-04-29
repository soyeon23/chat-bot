"""pipeline.auth 단위 테스트 — 플랫폼별 인증 경로 검증.

커버리지:
  - macOS 키체인 성공 / 실패
  - Windows/Linux 자격증명 파일 성공 / 실패
  - ANTHROPIC_AUTH_TOKEN 환경변수 폴백 (모든 플랫폼)
  - _extract_token: dict / str / 중첩 구조 / 알 수 없는 구조
  - _is_token_valid: 만료/유효 케이스

실행:
    cd /Users/maro/dev/company/chatbot
    source .venv/bin/activate
    python -m unittest tests.test_auth -v
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch


# ── 헬퍼 ─────────────────────────────────────────────────────

def _future_ts_ms(seconds: int = 3600) -> int:
    """현재 시각 + seconds 를 밀리초 타임스탬프로 반환."""
    return int((datetime.now(timezone.utc) + timedelta(seconds=seconds)).timestamp() * 1000)


def _past_ts_ms(seconds: int = 60) -> int:
    """현재 시각 - seconds 를 밀리초 타임스탬프로 반환."""
    return int((datetime.now(timezone.utc) - timedelta(seconds=seconds)).timestamp() * 1000)


# ── _extract_token ────────────────────────────────────────────

class TestExtractToken(unittest.TestCase):
    def setUp(self):
        from pipeline.auth import _extract_token
        self.extract = _extract_token

    def test_nested_claude_ai_oauth(self):
        creds = {"claudeAiOauth": {"accessToken": "tok-nested"}}
        self.assertEqual(self.extract(creds), "tok-nested")

    def test_nested_access_token_snake(self):
        creds = {"claudeAiOauth": {"access_token": "tok-snake"}}
        self.assertEqual(self.extract(creds), "tok-snake")

    def test_flat_access_token(self):
        creds = {"accessToken": "tok-flat"}
        self.assertEqual(self.extract(creds), "tok-flat")

    def test_flat_access_token_snake(self):
        creds = {"access_token": "tok-flat-snake"}
        self.assertEqual(self.extract(creds), "tok-flat-snake")

    def test_flat_oauth_token(self):
        creds = {"oauth_token": "tok-oauth"}
        self.assertEqual(self.extract(creds), "tok-oauth")

    def test_raw_string(self):
        self.assertEqual(self.extract("raw-string-token"), "raw-string-token")

    def test_empty_string(self):
        self.assertIsNone(self.extract(""))

    def test_unknown_dict(self):
        self.assertIsNone(self.extract({"foo": "bar"}))

    def test_non_dict_non_str(self):
        self.assertIsNone(self.extract(12345))  # type: ignore[arg-type]

    def test_none(self):
        self.assertIsNone(self.extract(None))  # type: ignore[arg-type]


# ── _is_token_valid ───────────────────────────────────────────

class TestIsTokenValid(unittest.TestCase):
    def setUp(self):
        from pipeline.auth import _is_token_valid
        self.valid = _is_token_valid

    def test_no_expiry_field(self):
        self.assertTrue(self.valid({}))

    def test_future_ms_timestamp(self):
        self.assertTrue(self.valid({"expiresAt": _future_ts_ms()}))

    def test_past_ms_timestamp(self):
        self.assertFalse(self.valid({"expiresAt": _past_ts_ms()}))

    def test_future_iso_string(self):
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        self.assertTrue(self.valid({"expires_at": future}))

    def test_past_iso_string(self):
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        self.assertFalse(self.valid({"expires_at": past}))

    def test_bad_value_defaults_valid(self):
        self.assertTrue(self.valid({"expiresAt": "not-a-date"}))


# ── _read_keychain_credentials ────────────────────────────────

class TestReadKeychainCredentials(unittest.TestCase):
    def setUp(self):
        from pipeline import auth
        self.auth = auth

    def test_skips_on_non_darwin(self):
        with patch("platform.system", return_value="Windows"):
            result = self.auth._read_keychain_credentials()
        self.assertIsNone(result)

    def test_skips_on_linux(self):
        with patch("platform.system", return_value="Linux"):
            result = self.auth._read_keychain_credentials()
        self.assertIsNone(result)

    @patch("platform.system", return_value="Darwin")
    def test_success_on_darwin(self, _mock_system):
        creds = {"claudeAiOauth": {"accessToken": "tok-mac"}}
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = json.dumps(creds)
        with patch("subprocess.run", return_value=mock_result):
            result = self.auth._read_keychain_credentials()
        self.assertEqual(result, creds)

    @patch("platform.system", return_value="Darwin")
    def test_nonzero_returncode(self, _mock_system):
        mock_result = MagicMock()
        mock_result.returncode = 44
        mock_result.stdout = ""
        with patch("subprocess.run", return_value=mock_result):
            result = self.auth._read_keychain_credentials()
        self.assertIsNone(result)

    @patch("platform.system", return_value="Darwin")
    def test_timeout_returns_none(self, _mock_system):
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("security", 5)):
            result = self.auth._read_keychain_credentials()
        self.assertIsNone(result)

    @patch("platform.system", return_value="Darwin")
    def test_invalid_json_returns_none(self, _mock_system):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "not-json"
        with patch("subprocess.run", return_value=mock_result):
            result = self.auth._read_keychain_credentials()
        self.assertIsNone(result)


# ── _read_credentials_file ────────────────────────────────────

class TestReadCredentialsFile(unittest.TestCase):
    def setUp(self):
        from pipeline import auth
        self.auth = auth

    def _patch_file(self, content: str | None):
        """_CREDENTIALS_FILE 경로를 임시 파일(또는 존재하지 않는 경로)로 대체하는 컨텍스트 관리자."""
        import contextlib

        @contextlib.contextmanager
        def _ctx():
            if content is None:
                # 파일 없음 시뮬레이션: 존재하지 않는 임시 경로로 대체
                missing = Path(tempfile.mktemp(suffix=".json"))
                with patch.object(self.auth, "_CREDENTIALS_FILE", missing):
                    yield
            else:
                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".json", delete=False, encoding="utf-8"
                ) as f:
                    f.write(content)
                    tmp_path = Path(f.name)
                with patch.object(self.auth, "_CREDENTIALS_FILE", tmp_path):
                    try:
                        yield
                    finally:
                        tmp_path.unlink(missing_ok=True)

        return _ctx()

    def test_file_not_found_returns_none(self):
        with self._patch_file(None):
            result = self.auth._read_credentials_file()
        self.assertIsNone(result)

    def test_valid_nested_creds(self):
        creds = {"claudeAiOauth": {"accessToken": "tok-file"}}
        with self._patch_file(json.dumps(creds)):
            result = self.auth._read_credentials_file()
        self.assertEqual(result, creds)

    def test_valid_flat_creds(self):
        creds = {"access_token": "tok-flat"}
        with self._patch_file(json.dumps(creds)):
            result = self.auth._read_credentials_file()
        self.assertEqual(result, creds)

    def test_invalid_json_returns_none(self):
        with self._patch_file("not-valid-json"):
            result = self.auth._read_credentials_file()
        self.assertIsNone(result)

    def test_non_dict_json_returns_none(self):
        with self._patch_file("[1, 2, 3]"):
            result = self.auth._read_credentials_file()
        self.assertIsNone(result)


# ── get_auth_source 통합 ──────────────────────────────────────

class TestGetAuthSource(unittest.TestCase):
    """각 플랫폼 분기와 폴백 순서를 검증."""

    def setUp(self):
        from pipeline import auth
        self.auth = auth

    # --- macOS 키체인 경로 ---

    @patch("platform.system", return_value="Darwin")
    def test_macos_keychain_success(self, _):
        creds = {"claudeAiOauth": {"accessToken": "kc-tok"}, "expiresAt": _future_ts_ms()}
        with patch.object(self.auth, "_read_keychain_credentials", return_value=creds), \
             patch.object(self.auth, "_read_credentials_file", return_value=None), \
             patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)
            mode, token = self.auth.get_auth_source()
        self.assertEqual(mode, "oauth_keychain")
        self.assertEqual(token, "kc-tok")

    @patch("platform.system", return_value="Darwin")
    def test_macos_keychain_expired_falls_to_env(self, _):
        # expiresAt 는 외부 dict 에 위치 — 만료된 경우 env 폴백으로 떨어져야 함
        creds = {
            "claudeAiOauth": {"accessToken": "expired-tok"},
            "expiresAt": _past_ts_ms(),  # 과거 타임스탬프
        }
        with patch.object(self.auth, "_read_keychain_credentials", return_value=creds), \
             patch.object(self.auth, "_read_credentials_file", return_value=None), \
             patch.dict(os.environ, {"ANTHROPIC_AUTH_TOKEN": "env-tok"}):
            mode, token = self.auth.get_auth_source()
        self.assertEqual(mode, "oauth_env")
        self.assertEqual(token, "env-tok")

    # --- Windows 자격증명 파일 경로 ---

    @patch("platform.system", return_value="Windows")
    def test_windows_credentials_file(self, _):
        creds = {"claudeAiOauth": {"accessToken": "win-tok"}, "expiresAt": _future_ts_ms()}
        with patch.object(self.auth, "_read_keychain_credentials", return_value=None), \
             patch.object(self.auth, "_read_credentials_file", return_value=creds), \
             patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)
            mode, token = self.auth.get_auth_source()
        self.assertEqual(mode, "oauth_file")
        self.assertEqual(token, "win-tok")

    @patch("platform.system", return_value="Windows")
    def test_windows_file_missing_falls_to_env(self, _):
        with patch.object(self.auth, "_read_keychain_credentials", return_value=None), \
             patch.object(self.auth, "_read_credentials_file", return_value=None), \
             patch.dict(os.environ, {"ANTHROPIC_AUTH_TOKEN": "win-env-tok"}):
            mode, token = self.auth.get_auth_source()
        self.assertEqual(mode, "oauth_env")
        self.assertEqual(token, "win-env-tok")

    # --- Linux 자격증명 파일 경로 ---

    @patch("platform.system", return_value="Linux")
    def test_linux_credentials_file(self, _):
        creds = {"access_token": "linux-tok", "expiresAt": _future_ts_ms()}
        with patch.object(self.auth, "_read_keychain_credentials", return_value=None), \
             patch.object(self.auth, "_read_credentials_file", return_value=creds), \
             patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)
            mode, token = self.auth.get_auth_source()
        self.assertEqual(mode, "oauth_file")
        self.assertEqual(token, "linux-tok")

    @patch("platform.system", return_value="Linux")
    def test_linux_no_file_env_fallback(self, _):
        with patch.object(self.auth, "_read_keychain_credentials", return_value=None), \
             patch.object(self.auth, "_read_credentials_file", return_value=None), \
             patch.dict(os.environ, {"ANTHROPIC_AUTH_TOKEN": "linux-env"}):
            mode, token = self.auth.get_auth_source()
        self.assertEqual(mode, "oauth_env")
        self.assertEqual(token, "linux-env")

    # --- 환경변수 폴백 (모든 플랫폼) ---

    @patch("platform.system", return_value="Darwin")
    def test_env_fallback_all_else_fails(self, _):
        with patch.object(self.auth, "_read_keychain_credentials", return_value=None), \
             patch.object(self.auth, "_read_credentials_file", return_value=None), \
             patch.dict(os.environ, {"ANTHROPIC_AUTH_TOKEN": "env-only"}):
            mode, token = self.auth.get_auth_source()
        self.assertEqual(mode, "oauth_env")
        self.assertEqual(token, "env-only")

    # --- 완전 실패 ---

    @patch("platform.system", return_value="Darwin")
    def test_all_fail_raises(self, _):
        with patch.object(self.auth, "_read_keychain_credentials", return_value=None), \
             patch.object(self.auth, "_read_credentials_file", return_value=None), \
             patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)
            with self.assertRaises(RuntimeError):
                self.auth.get_auth_source()


# ── auth_status_label ─────────────────────────────────────────

class TestAuthStatusLabel(unittest.TestCase):
    def setUp(self):
        from pipeline import auth
        self.auth = auth

    def test_keychain_label(self):
        with patch.object(self.auth, "get_auth_source", return_value=("oauth_keychain", "t")):
            self.assertEqual(self.auth.auth_status_label(), "Claude Code (키체인)")

    def test_file_label(self):
        with patch.object(self.auth, "get_auth_source", return_value=("oauth_file", "t")):
            self.assertEqual(self.auth.auth_status_label(), "Claude Code (자격증명 파일)")

    def test_env_label(self):
        with patch.object(self.auth, "get_auth_source", return_value=("oauth_env", "t")):
            self.assertEqual(self.auth.auth_status_label(), "Claude Code (env 토큰)")

    def test_runtime_error_label(self):
        with patch.object(self.auth, "get_auth_source", side_effect=RuntimeError("no auth")):
            label = self.auth.auth_status_label()
        self.assertIn("미설정", label)


if __name__ == "__main__":
    unittest.main(verbosity=2)
