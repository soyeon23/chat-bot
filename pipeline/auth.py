"""Claude Code 인증 상태 헬퍼 — 표시 전용.

답변 생성 경로는 `claude-agent-sdk` 가 로컬 `claude` CLI 를 통해 자체 인증을
처리하므로, 이 모듈은 더 이상 Anthropic SDK 클라이언트를 만들지 않는다.

다음 두 가지 용도로만 남아 있다:
  1. 사이드바/위저드의 사용자 친화 라벨 (`auth_status_label`)
  2. "사용자가 Claude Code 에 로그인 했는가?" 점검용 시그널 (`get_auth_source`)

우선순위:
1. macOS 키체인 — Claude Code 로그인 자격증명 자동 감지
2. Windows / Linux 자격증명 파일 — ~/.claude/.credentials.json (Claude Code 공식 위치)
3. ANTHROPIC_AUTH_TOKEN — Claude Code OAuth 토큰 (env, 비-macOS·CI용)

API 키(`ANTHROPIC_API_KEY`)는 *지원하지 않는다* — 이 프로젝트는 Claude Code 구독으로만 동작.
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_KEYCHAIN_SERVICE = "Claude Code-credentials"

# Claude Code 가 모든 플랫폼에서 사용하는 자격증명 파일 경로
_CREDENTIALS_FILE = Path.home() / ".claude" / ".credentials.json"


def _read_keychain_credentials() -> Optional[dict]:
    """macOS 키체인에서 Claude Code 자격증명 읽기.

    실패하거나 macOS가 아니면 None 반환. 사용자 키체인 잠금/거부 등도 None 처리.
    """
    if platform.system() != "Darwin":
        return None
    try:
        result = subprocess.run(
            [
                "security",
                "find-generic-password",
                "-s",
                _KEYCHAIN_SERVICE,
                "-w",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None

    if result.returncode != 0:
        return None

    raw = (result.stdout or "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _read_credentials_file() -> Optional[dict]:
    """~/.claude/.credentials.json 에서 Claude Code 자격증명 읽기.

    Windows 및 Linux 에서 Claude Code 가 공식적으로 사용하는 위치.
    파일이 없거나 파싱 실패 시 None 반환.
    """
    try:
        raw = _CREDENTIALS_FILE.read_text(encoding="utf-8")
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
        return None
    except (FileNotFoundError, PermissionError, json.JSONDecodeError, OSError):
        return None


def _is_token_valid(creds: dict) -> bool:
    """expiresAt 필드가 있으면 만료 검사. 없으면 일단 True."""
    raw = creds.get("expiresAt") or creds.get("expires_at")
    if raw is None:
        return True
    try:
        if isinstance(raw, (int, float)):
            # ms timestamp
            exp = datetime.fromtimestamp(raw / 1000, tz=timezone.utc)
        else:
            exp = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (ValueError, OSError):
        return True
    # 30초 여유
    return exp.timestamp() > datetime.now(timezone.utc).timestamp() + 30


def _extract_token(creds: dict | str) -> Optional[str]:
    """자격증명 dict 또는 raw 문자열에서 access token 추출.

    Claude Code는 `claudeAiOauth.accessToken` 형태로 저장한다.
    구버전/타 형식 호환을 위해 여러 키를 시도한다.
    문자열이 직접 전달된 경우 그대로 반환한다 (env 토큰 경로 호환).
    """
    # raw 문자열이 직접 전달된 경우 (env 토큰 등)
    if isinstance(creds, str):
        return creds or None
    if not isinstance(creds, dict):
        return None
    # 신형: claudeAiOauth.accessToken
    nested = creds.get("claudeAiOauth")
    if isinstance(nested, dict):
        token = nested.get("accessToken") or nested.get("access_token")
        if token:
            return str(token)
    # 평탄화된 케이스 (access_token / accessToken / oauth_token)
    token = (
        creds.get("accessToken")
        or creds.get("access_token")
        or creds.get("oauth_token")
    )
    if token:
        return str(token)
    return None


def get_auth_source() -> tuple[str, str]:
    """Claude Code OAuth 토큰 결정.

    우선순위:
      1. macOS 키체인 (security find-generic-password)
      2. Windows / Linux: ~/.claude/.credentials.json 파일
      3. ANTHROPIC_AUTH_TOKEN 환경변수 (모든 플랫폼, CI 포함)

    Returns:
        (mode, token) — mode ∈ {"oauth_keychain", "oauth_file", "oauth_env"}

    Raises:
        RuntimeError: Claude Code 로그인 안 됨 + ANTHROPIC_AUTH_TOKEN도 없음.
    """
    # 1) 키체인 (macOS Claude Code)
    creds = _read_keychain_credentials()
    if creds:
        token = _extract_token(creds)
        # expiresAt 는 외부 dict 에 있으므로 항상 creds 전체로 검사
        if token and _is_token_valid(creds):
            return "oauth_keychain", token

    # 2) 자격증명 파일 (Windows / Linux, 또는 macOS 키체인 실패 시 폴백)
    file_creds = _read_credentials_file()
    if file_creds:
        token = _extract_token(file_creds)
        if token and _is_token_valid(file_creds):
            return "oauth_file", token

    # 3) 환경변수 OAuth (비-macOS, CI, 또는 파일 읽기 실패 시)
    env_token = os.getenv("ANTHROPIC_AUTH_TOKEN")
    if env_token:
        return "oauth_env", env_token

    raise RuntimeError(
        "Claude Code OAuth 토큰을 찾을 수 없습니다.\n"
        "이 프로젝트는 Claude Code 구독으로만 동작합니다.\n"
        "  1. macOS: 터미널에서 `claude` 실행 후 로그인하면 자동 감지됩니다.\n"
        "  2. Windows / Linux: `claude` 실행 후 로그인하면 ~/.claude/.credentials.json에 저장됩니다.\n"
        "  3. 모든 OS / CI: ANTHROPIC_AUTH_TOKEN 환경변수에 OAuth 토큰을 넣어주세요.\n"
        "  (Anthropic 콘솔 API 키는 지원하지 않습니다 — 클로드 코드 OAuth 전용)"
    )


def auth_status_label() -> str:
    """현재 사용 중인 인증 소스의 사람-친화 라벨. 실패 시 '미설정'."""
    try:
        mode, _ = get_auth_source()
    except RuntimeError:
        return "미설정 (`claude login` 필요)"
    return {
        "oauth_keychain": "Claude Code (키체인)",
        "oauth_file": "Claude Code (자격증명 파일)",
        "oauth_env": "Claude Code (env 토큰)",
    }.get(mode, f"알 수 없음 ({mode})")


if __name__ == "__main__":
    # CLI 점검: python -m pipeline.auth
    try:
        mode, cred = get_auth_source()
    except RuntimeError as e:
        print(f"❌ {e}")
        raise SystemExit(1)
    masked = cred[:12] + "…" + cred[-4:] if len(cred) > 20 else "***"
    print(f"✅ 인증 소스: {auth_status_label()}")
    print(f"   토큰: {masked}")
