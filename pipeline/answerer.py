"""Claude 답변 생성 — claude-agent-sdk 기반.

이전 버전은 `anthropic` Python SDK로 OAuth 토큰을 직접 보내 한 번 호출만에 429를
받는 경우가 잦았다. 이 모듈은 `claude-agent-sdk`를 사용해 로컬 `claude` CLI 서브
프로세스를 통해 호출한다. CLI는 Claude Code의 자체 인증/세션 관리 로직을 그대로
사용하므로 외부 직접 호출에 비해 throttling이 훨씬 관대하다.

호출 인터페이스(`generate_answer(question, chunks) -> dict`)는 그대로 유지한다.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from typing import Optional

from dotenv import load_dotenv

from pipeline.prompts import SYSTEM_PROMPT
from pipeline.schemas import AnswerPayload

load_dotenv()

_DEFAULT_MODEL = "claude-sonnet-4-6"
# 비교형(comparison) 질의는 추론량이 더 큰 Opus 4.6 으로 escalate.
# scout F1 (.planning/research/scout-f1-model-upgrade.md) 에 따라 Opus 4.7 은 한국 법령
# RAG 에서 회귀(MRCR 32→76% 등) 가 보고돼 사용하지 않는다.
_COMPARISON_MODEL = "claude-opus-4-6"

# JSON 출력 강제용 시스템 프롬프트 부록.
# 본 SYSTEM_PROMPT 자체가 JSON 스키마와 형식을 명시하지만, Agent SDK 경로에서는
# 모델이 마크다운 펜스로 감싸 출력하는 경우가 있어 더 단호한 지시를 추가한다.
_JSON_ONLY_ADDENDUM = """

## [추가 출력 규칙 — Agent SDK 경로]
- JSON 외의 텍스트(설명, 마크다운, 코드펜스, 머리말/꼬리말)를 절대 포함하지 마세요.
- 출력은 반드시 `{`로 시작해 `}`로 끝나야 합니다.
- ```json …``` 같은 펜스 없이 순수 JSON 객체만 한 번에 출력하세요.
"""

# Phase H — page_lookup / article_lookup 경로에서 사용할 도구 사용 가이드.
# Claude Agent SDK 가 `mcp__local_doc__*` 도구를 노출하므로 이 부록을
# system_prompt 에 붙여 모델이 도구를 자발적으로 호출하도록 유도한다.
_TOOL_USAGE_ADDENDUM = """

## [Phase H — 로컬 문서 직접 조회 도구 사용 가이드]

사용자가 특정 페이지 또는 조문 *본문* 을 요청한 경우, 아래 [검색된 근거] 가
부족하거나 본문이 잘려 보이면 다음 도구를 직접 호출해 보충하세요:

- `mcp__local_doc__list_documents()` — 사용 가능한 문서 목록과 정확한 doc_name.
  사용자가 말한 별칭("본권", "혁신법 매뉴얼", "시행령") 으로 doc_name 을 못
  찾을 때 먼저 호출해 정확한 파일명을 확인합니다.
- `mcp__local_doc__read_page(doc_name, page_num)` — 1-indexed 페이지의 정확한 본문.
  사용자가 "151p", "p.151", "151쪽" 등을 요구하면 호출하세요.
- `mcp__local_doc__get_article(doc_name, article_no)` — 조문(`제15조`, `별표 2`,
  `부칙`) 전체 본문 + 시작/끝 페이지.
- `mcp__local_doc__search_text(doc_name, query, max_results=5)` — 키워드 매칭
  페이지 다수. 페이지 번호를 모를 때 후보를 좁힙니다.
- `mcp__local_doc__list_articles(doc_name)` — 문서 안 모든 조문 헤더.

### 중요한 사용 규칙
1. **검색된 근거 우선** — 위 [검색된 근거] 안에 사용자가 요청한 페이지/조문의
   본문이 충분히 담겨 있다면 도구를 호출하지 말고 그대로 인용하세요.
2. **빠진 내용만 보충** — 검색된 근거에 페이지·조문은 있지만 본문이 잘려
   있거나(part 1/N), 사용자가 요구한 *바로 그 페이지* 가 검색 결과에 없을 때
   해당 페이지를 read_page 로 직접 가져옵니다.
3. **citations 작성** — 도구로 가져온 본문도 정확히 citations 에 반영.
   document_name 은 도구가 돌려준 doc_name(파일명) 그대로, page 는 read_page
   가 돌려준 정수 그대로, quote 는 본문에서 50자 이내 발췌.
4. **메타 요약 금지** — page_lookup/article_lookup 경로에서는 "X 페이지에는
   FAQ 가 있습니다" 같은 1줄 요약으로 끝내지 말고, 페이지 본문의 모든 항목
   (FAQ Q1~Qn, 표 행, 목록) 을 summary 에 그대로 풀어 옮깁니다.
5. **최종 출력은 여전히 JSON** — 도구 호출이 끝나면 평소와 동일한 AnswerPayload
   JSON 객체 한 개만 출력하세요. 도구 호출 메시지·중간 분석 텍스트를 JSON 앞뒤에
   남기지 마세요.
"""


class RateLimitError(RuntimeError):
    """Claude Code 사용량 한도 초과. UI에서 별도 안내 가능 (기존 호환용)."""


def get_model(kind: str = "open") -> str:
    """런타임 모델 결정.

    Args:
        kind: 질의 종류 — "open" | "page_lookup" | "article_lookup" | "comparison" | "chat".
              "comparison" 이면서 사용자 config 에 명시적 모델이 없고
              `enable_comparison_escalate` 가 True 면 `_COMPARISON_MODEL`(Opus 4.6) 로 자동
              승격. 그 외에는 기존 우선순위(config.claude_model > .env CLAUDE_MODEL > sonnet)를
              따른다.

    Returns:
        Claude 모델 alias 문자열.

    Notes:
        - 사용자가 환경설정에서 모델을 명시적으로 골랐다면(config.claude_model 비어있지 않음)
          그 선택은 모든 kind 에 적용한다 — 사용자의 명시적 선호가 자동 escalate 보다 우선.
        - escalate 토글(`enable_comparison_escalate`)을 OFF 로 두면 comparison 도 sonnet 유지.
    """
    cfg = None
    try:
        from pipeline.config_store import load_config
        cfg = load_config()
    except Exception:
        cfg = None

    # 사용자가 명시적으로 모델을 골랐다면 그게 우선 (모든 kind 동일 적용).
    if cfg is not None and cfg.claude_model:
        return cfg.claude_model

    # 비교형은 자동 escalate (토글 ON 일 때).
    escalate_on = True if cfg is None else bool(getattr(cfg, "enable_comparison_escalate", True))
    if kind == "comparison" and escalate_on:
        return _COMPARISON_MODEL

    return os.getenv("CLAUDE_MODEL", _DEFAULT_MODEL)


def build_context(chunks: list[dict]) -> str:
    """검색된 chunk 리스트를 [근거 N] 형태의 문자열로 변환한다."""
    blocks = []
    for i, chunk in enumerate(chunks, start=1):
        block = (
            f"[근거 {i}]\n"
            f"문서명: {chunk.get('doc_name', '')}\n"
            f"문서유형: {chunk.get('doc_type', '')}\n"
            f"조문번호: {chunk.get('article_no', '')}\n"
            f"조문제목: {chunk.get('article_title', '')}\n"
            f"페이지: {chunk.get('page', 0)}\n"
            f"내용:\n{chunk.get('text', '')}"
        )
        blocks.append(block)
    return "\n\n".join(blocks)


# ──────────────────────────────────────────────────────────────────
# JSON 추출
# ──────────────────────────────────────────────────────────────────

# max_turns 도달 시 누적 텍스트가 이 길이 미만이면 JSON 추출 대신 graceful
# stub 으로 폴백한다. 100자는 SYSTEM_PROMPT 가 강제하는 최소 JSON 한 줄
# (`{"verdict":"...","summary":"..."...}`) 보다도 짧은 임계치라, 사실상 모델이
# *시작도 못 했을 때* 만 발동된다. 모델이 부분 JSON 을 만들었다면 정상 추출
# 경로로 진입.
_MAX_TURNS_STUB_MIN_CHARS = 100


# max_turns graceful stub — Phase H 도구 모드에서 모델이 도구만 부르다 한도에
# 걸려 최종 JSON 답변을 못 낸 경우, RuntimeError 대신 본 stub 을 generate_answer
# 가 받아 정상 경로(JSON 파싱 + citations 보강) 로 흘려보낸다. 사용자에게
# `Command failed with exit code 1` 가 노출되는 것을 차단하기 위함.
_MAX_TURNS_STUB_PAYLOAD = {
    "verdict": "판단불가",
    "summary": (
        "검색 도구가 한도(max_turns=8) 안에 충분한 답변을 만들지 못했습니다. "
        "질문을 더 구체적으로 다시 시도해 주세요. "
        "(예: '국가연구개발혁신법 제10조 본문', '제10조 시행령')"
    ),
    "citations": [],
    "follow_up_needed": True,
    "follow_up_questions": [
        "질문이 너무 짧을 수 있습니다 — 문서명/조문번호 명시 권장",
    ],
    "risk_notes": ["도구 모드 max_turns 도달"],
}


_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _extract_json_object(text: str) -> Optional[str]:
    """모델 응답 텍스트에서 첫 번째 JSON 객체를 추출.

    1) ```json ... ``` 펜스 우선
    2) 펜스 없으면 첫 `{`부터 균형 맞는 `}`까지 추출
    """
    if not text:
        return None
    m = _FENCE_RE.search(text)
    if m:
        return m.group(1)

    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None


# ──────────────────────────────────────────────────────────────────
# Agent SDK 호출
# ──────────────────────────────────────────────────────────────────

# rate-limit 시그널 매칭용 (CLI가 emit 하는 다양한 표현 커버)
_RATE_LIMIT_PATTERNS = re.compile(
    r"rate[_\s-]?limit|usage[_\s-]?limit|429|quota|five[_\s-]?hour",
    re.IGNORECASE,
)


def _is_rate_limit_signal(*texts: str) -> bool:
    return any(t and _RATE_LIMIT_PATTERNS.search(t) for t in texts)


async def _run_query(
    model: str,
    system_prompt: str,
    user_prompt: str,
    *,
    enable_tools: bool = False,
    progress_cb=None,
) -> str:
    """Agent SDK 호출. 모든 AssistantMessage 의 TextBlock 을 이어붙여 반환.

    Args:
        enable_tools: True 면 Phase H 의 로컬 PDF/HWP 직접 접근 도구
            (`mcp__local_doc__read_page` 등)를 활성화하고 max_turns 를 늘린다.
            page_lookup·article_lookup 경로에서만 켠다.
        progress_cb: Optional[Callable[[dict], None]]. 호출 시 다음 형식의
            이벤트를 받는다 (UI 진행상황 표시용):
              {"type": "tool_use", "name": str, "input": dict}
              {"type": "tool_result", "name": str, "is_error": bool}
            예외 안전 — callback 에서 raise 해도 본 코루틴은 무영향.
    """
    # 지연 import: streamlit 워커 스레드에서 import 비용 분산.
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        ResultMessage,
        TextBlock,
        ToolUseBlock,
        ToolResultBlock,
        UserMessage,
        query,
    )

    def _emit(event: dict) -> None:
        if progress_cb is None:
            return
        try:
            progress_cb(event)
        except Exception:
            pass

    if enable_tools:
        # Phase H: in-process MCP 서버를 띄워 read_page / get_article / search_text
        # / list_articles / list_documents 를 노출. permission_mode 는
        # bypassPermissions 유지 — 사용자에게 도구 사용 권한 prompt 안 뜸.
        from pipeline.local_doc_mcp import build_local_doc_server, TOOL_NAMES

        # max_turns=8 — 도구 호출 1~3회 + 최종 답변 1턴 + 모델이 여러 문서를
        # 순회 탐색하는 경우의 여유. 5턴은 page_lookup 단순 케이스에는 충분
        # 했지만 article_lookup 에서 매뉴얼/시행령/시행규칙/법률 본권 4개 PDF
        # 를 차례로 시도하다 한도에 걸리는 회귀가 보였다.
        options = ClaudeAgentOptions(
            system_prompt=system_prompt,
            model=model,
            mcp_servers={"local_doc": build_local_doc_server()},
            allowed_tools=TOOL_NAMES,
            permission_mode="bypassPermissions",
            max_turns=8,
        )
    else:
        options = ClaudeAgentOptions(
            system_prompt=system_prompt,
            model=model,
            allowed_tools=[],          # 답변 생성에 도구 사용 금지
            permission_mode="bypassPermissions",  # 어차피 도구 비허용이라 prompt 안 뜸
            max_turns=1,
        )

    text_chunks: list[str] = []
    rate_limited = False
    rate_limit_msg = ""
    final_result: Optional[str] = None
    max_turns_hit = False

    # SDK 의 `query()` async generator 는 ResultMessage(subtype=error_max_turns) 를
    # yield 한 직후 transport 레이어가 exit code 1 로 stream 을 끊으면서
    # `Exception("Command failed with exit code 1")` 를 *raise* 하는 경우가 있다.
    # 이는 SDK 1.x 에서 max_turns 도달 시 관찰되는 정상 동작에 가깝지만, 사용자
    # 에게 Fatal error 로 노출되면 안 된다. try/except 로 감싸 stream 종료 후
    # 누적 텍스트 + max_turns 플래그 조합을 평가하는 단일 경로로 모은다.
    # 도구별 이름 매핑 — UI 라벨 생성용. tool_use_id → name 보관해 결과 매칭.
    tool_id_to_name: dict[str, str] = {}

    try:
        async for msg in query(prompt=user_prompt, options=options):
            if isinstance(msg, AssistantMessage):
                # SDK 가 assistant 의 error 필드에 'rate_limit' 등 문자열을 채워 주기도 함
                if msg.error == "rate_limit":
                    rate_limited = True
                    rate_limit_msg = "Claude Code 사용량 한도 초과 (assistant rate_limit)"
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        text_chunks.append(block.text)
                    elif isinstance(block, ToolUseBlock):
                        tool_id_to_name[block.id] = block.name
                        _emit({
                            "type": "tool_use",
                            "name": block.name,
                            "input": dict(block.input or {}),
                        })
            elif isinstance(msg, UserMessage):
                # 도구 결과 — Claude 가 이전 턴에 호출한 mcp 결과를 받은 시점.
                for block in getattr(msg, "content", []) or []:
                    if isinstance(block, ToolResultBlock):
                        name = tool_id_to_name.get(block.tool_use_id, "")
                        _emit({
                            "type": "tool_result",
                            "name": name,
                            "is_error": bool(block.is_error),
                        })
            elif isinstance(msg, ResultMessage):
                if msg.is_error:
                    err_blob = " | ".join(msg.errors or [])
                    if _is_rate_limit_signal(err_blob, msg.subtype or ""):
                        rate_limited = True
                        rate_limit_msg = err_blob or "rate limit"
                    elif (msg.subtype or "") == "error_max_turns":
                        # 도구 모드(Phase H) 에서 모델이 5~8 턴 안에 최종 답변 JSON 을
                        # 못 내는 경우가 있다. 누적 텍스트 안에 이미 JSON 이 있으면
                        # 그대로 반환해 answer 측에서 추출·검증하도록 폴백.
                        # 누적이 너무 짧으면 stub JSON 을 만들어 graceful 응답.
                        max_turns_hit = True
                        print(
                            f"[answerer] max_turns 도달 (subtype={msg.subtype}) — "
                            f"누적 텍스트 {sum(len(t) for t in text_chunks)}자에서 JSON 추출 시도",
                            file=sys.stderr,
                        )
                    else:
                        # 비-rate-limit 오류: 그대로 던진다.
                        raise RuntimeError(
                            f"Claude CLI returned error (subtype={msg.subtype}): {err_blob or msg.result!r}"
                        )
                if msg.result:
                    final_result = msg.result
    except RuntimeError:
        raise
    except Exception as exc:
        # SDK transport 가 exit code 1 등으로 stream 을 끊은 경우.
        # ResultMessage(error_max_turns) 가 *직전에* 도착했었다면 graceful stub
        # 으로 흡수, 그 외에는 그대로 재raise.
        msg_lower = str(exc).lower()
        is_max_turns_transport = (
            max_turns_hit
            or "exit code 1" in msg_lower
            or "max_turns" in msg_lower
        )
        if not is_max_turns_transport:
            raise
        # max_turns 후 transport 끊김 → 다음 블록의 stub 경로로 합류
        max_turns_hit = True
        print(
            f"[answerer] SDK stream 끊김 ({type(exc).__name__}: {exc}) — "
            f"max_turns 직후 transport 레이어 종료로 간주, stub 경로 진입",
            file=sys.stderr,
        )

    if rate_limited:
        raise RateLimitError(
            f"Claude Code 사용량 한도 초과: {rate_limit_msg}. 잠시 후 다시 시도해 주세요."
        )

    # AssistantMessage 의 TextBlock 들을 우선 사용, 비어 있으면 ResultMessage.result 폴백
    full = "\n".join(t for t in text_chunks if t)
    combined = full or (final_result or "")

    # max_turns 도달 + 누적 텍스트 부족 → graceful stub JSON 반환.
    # generate_answer 의 JSON 파싱·citations 보강 로직이 정상 경로로 흡수.
    if max_turns_hit and len(combined.strip()) < _MAX_TURNS_STUB_MIN_CHARS:
        print(
            f"[answerer] max_turns graceful stub "
            f"(accumulated={len(combined.strip())}자, threshold={_MAX_TURNS_STUB_MIN_CHARS}자)",
            file=sys.stderr,
        )
        return json.dumps(_MAX_TURNS_STUB_PAYLOAD, ensure_ascii=False)

    return combined


def _run_query_sync(
    model: str,
    system_prompt: str,
    user_prompt: str,
    *,
    enable_tools: bool = False,
    progress_cb=None,
) -> str:
    """동기 인터페이스 래퍼. Streamlit/CLI 가 그대로 호출할 수 있도록 한다.

    `_run_query` 내부에 이미 max_turns transport 폴백이 있지만, claude-agent-sdk
    의 message reader 가 *이벤트루프 정리* 단계에서 늦게 Exception 을 띄우는
    케이스가 있어 (`Fatal error in message reader: Command failed with exit code 1`)
    여기서 한 번 더 graceful 폴백을 둔다. 이 폴백은 *항상* stub 을 반환하지 않고,
    예외 메시지에 transport 종료 시그니처가 있을 때만 stub 으로 흡수한다.
    """
    def _invoke() -> str:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # 이미 이벤트루프 안인 경우 (드물지만 streamlit/jupyter 등) — 새 루프
                new_loop = asyncio.new_event_loop()
                try:
                    return new_loop.run_until_complete(
                        _run_query(
                            model, system_prompt, user_prompt,
                            enable_tools=enable_tools, progress_cb=progress_cb,
                        )
                    )
                finally:
                    new_loop.close()
        except RuntimeError:
            # no current event loop
            pass
        return asyncio.run(
            _run_query(
                model, system_prompt, user_prompt,
                enable_tools=enable_tools, progress_cb=progress_cb,
            )
        )

    try:
        return _invoke()
    except RateLimitError:
        raise
    except Exception as exc:
        # SDK transport 가 이벤트루프 정리 단계에서 늦게 raise 하는 경우.
        # `_run_query` 내부 폴백을 통과한 뒤 발생 → 여기서 흡수.
        msg = str(exc).lower()
        is_transport_close = (
            "exit code 1" in msg
            or "max_turns" in msg
            or "command failed" in msg
            or "message reader" in msg
        )
        if is_transport_close:
            print(
                f"[answerer] _run_query_sync transport graceful "
                f"({type(exc).__name__}: {exc})",
                file=sys.stderr,
            )
            return json.dumps(_MAX_TURNS_STUB_PAYLOAD, ensure_ascii=False)
        raise


# ──────────────────────────────────────────────────────────────────
# 공개 API
# ──────────────────────────────────────────────────────────────────

def generate_answer(
    question: str,
    chunks: list[dict],
    kind: str = "open",
    prior_turns: list[dict] | None = None,
    progress_cb=None,
) -> dict:
    """검색된 chunks를 근거로 Claude에게 질문하고 AnswerPayload dict를 반환한다.

    Args:
        question: 사용자 질문 원문
        chunks: Qdrant payload dict 리스트 (doc_name, doc_type, article_no, article_title, page, text)
        kind: 분석기가 추론한 질의 종류
              ("page_lookup" | "article_lookup" | "comparison" | "open").
              page_lookup·article_lookup 인 경우 user 프롬프트에 "본문을 풀어 쓰라"는
              힌트 블록이 추가돼, summary 가 메타 요약 1줄로 끝나는 패턴을 막는다.
        prior_turns: 직전 N 턴 대화 (멀티턴 대화 모드).
                     각 항목은 `{"role": "user"|"assistant", "content": str}`.
                     assistant content 는 *summary 만* 들어 있어야 한다 (citations
                     없이 — 토큰 절약 + 모델이 이전 답변 요지만 알면 충분).
                     None 또는 빈 리스트면 단발 호출 (기존 동작과 100% 동일).

    Returns:
        AnswerPayload.model_dump() 결과 dict

    Raises:
        ValueError: chunks가 비어 있을 때
        RateLimitError: Claude Code 사용량 한도 초과
        RuntimeError: 응답 파싱 실패 등 일반 오류
    """
    if not chunks:
        raise ValueError(
            "검색된 청크가 없습니다. 먼저 관련 문서를 인덱싱하고 검색 결과를 확인하세요."
        )

    # kind 별 모드 힌트는 build_user_prompt 가 user 턴 앞단에 삽입.
    # answerer 는 build_context 와 별도로 build_user_prompt 호출로 일관화.
    from pipeline.prompts import build_user_prompt as _build_user_prompt
    # build_user_prompt 는 chunks에서 doc_name/article_no/page/text 키를 읽으므로
    # chunks가 doc_name 등의 키를 갖고 있어야 한다 (caller 가 정규화 필수).
    user_prompt = _build_user_prompt(
        question, chunks, kind=kind, prior_turns=prior_turns,
    )

    # Phase H — 페이지/조문 직접 조회 경로에서는 로컬 PDF 직접 접근 도구를
    # 활성화한다. 도구 사용 가이드는 system_prompt 부록으로 합치고, max_turns
    # 도 이 경로만 5 턴까지 허용 (도구 호출 1~2회 + 최종 답변 1턴 + 여유).
    enable_tools = kind in {"page_lookup", "article_lookup"}
    system_prompt = SYSTEM_PROMPT + _JSON_ONLY_ADDENDUM
    if enable_tools:
        system_prompt = system_prompt + _TOOL_USAGE_ADDENDUM

    model = get_model(kind=kind)
    # CLI / Streamlit 모두 stderr 를 보여주므로 어떤 모델로 보냈는지 명확히 남긴다.
    # answer_cli 가 비교형 escalate 가 동작했는지 확인하는 신호.
    n_prior = len(prior_turns or [])
    print(
        f"[answerer] model={model} kind={kind} enable_tools={enable_tools} "
        f"prior_turns={n_prior}",
        file=__import__("sys").stderr,
    )

    raw_text = _run_query_sync(
        model, system_prompt, user_prompt,
        enable_tools=enable_tools, progress_cb=progress_cb,
    )
    if not raw_text.strip():
        raise RuntimeError("Claude 응답이 비어 있습니다.")

    json_str = _extract_json_object(raw_text)
    if json_str is None:
        raise RuntimeError(
            "Claude 응답에서 JSON 객체를 찾지 못했습니다. "
            f"응답 일부: {raw_text[:300]!r}"
        )

    try:
        raw = json.loads(json_str)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Claude 응답 JSON 파싱 실패: {exc}\n원본: {json_str[:500]!r}"
        ) from exc

    # citations가 비어 있으면 chunks에서 직접 채워 min_length=1 보장 (기존 동작 유지)
    if not raw.get("citations"):
        raw["citations"] = [
            {
                "document_name": chunks[0].get("doc_name", "알 수 없음"),
                "article_no": chunks[0].get("article_no", "알 수 없음"),
                "page": chunks[0].get("page", 0),
                "quote": chunks[0].get("text", "")[:50],
            }
        ]

    try:
        payload = AnswerPayload.model_validate(raw)
    except Exception as exc:
        raise RuntimeError(
            f"AnswerPayload 파싱 실패: {exc}\n수신된 입력값: {raw}"
        ) from exc

    return payload.model_dump()
