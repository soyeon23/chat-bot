"""
Claude 기반 질의 의도 분석기.

기존 `query_parser.parse_query`는 정규식·키워드 화이트리스트에 의존해
새 표현(예: "151p", "백오십일쪽", "본권 매뉴얼 그 부분")이 추가될 때마다
패치를 늘려야 했다. 이 모듈은 사용자 질의를 Claude(Haiku 기본)에 한 번
던져 구조화된 의도(`QueryHints`)를 받아 그 한계를 우회한다.

호출 그래프:
    user query
        └─ analyze_query()  ── LLM ──▶ JSON
                              │
                              ├─(성공)→ regex 결과와 union → QueryHints
                              └─(실패)→ parse_query (정규식 fallback)

retriever 는 이 결과의 `target_pages` / `doc_name_hint` / `kind`를 활용해
페이지 직접 조회와 문서 한정을 수행한다.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any

from pipeline.answerer import _extract_json_object, _run_query_sync, RateLimitError
from pipeline.query_parser import QueryHints, parse_query


_ANALYZER_MODEL = os.getenv("CLAUDE_ANALYZER_MODEL", "claude-haiku-4-5")

_ANALYZER_SYSTEM = """당신은 한국 연구행정 RAG 챗봇의 질의 분석기 + 일상대화 응답기입니다.
사용자 입력을 분류해 JSON으로만 응답하세요.

도메인 문서:
- 국가연구개발혁신법 (법률)
- 동 시행령 (대통령령)
- 동 시행규칙 (과학기술정보통신부령)
- 매뉴얼/본권 (해설 PDF; "본권"은 매뉴얼을 의미)

[0단계 — 멀티턴 맥락 해석] 입력이 [이전 사용자 질문]/[이전 답변 요약] 블록과 함께
주어지면, 사용자는 *직전 대화의 연장선* 에서 후속 질문을 한 것일 수 있습니다.

후속어 신호 (직전 주제로 연결):
- "사례 있어?", "실제로?", "예시?", "예를 들면?", "더 자세히",
  "왜?", "이유는?", "근거는?", "그건 어디?", "그 조문은?",
  "어떻게?", "절차는?", "그럼?", "그러면?", "그게 무슨 뜻?",
  "다른 건?", "또?", "그 외에는?"

이런 신호가 있고 *주제 전환 단서가 없으면* 직전 주제(키워드·문서·페이지·조문)를
이어받아 검색 질의를 만듭니다.

주제 전환 신호 (이어받지 않음):
- 새로운 비목/조문/페이지/문서명을 명시 ("학생인건비는?", "별표 3은?", "151p 알려줘")
- 명백히 다른 주제어 ("그럼 회의비는?" 처럼 비교 의도가 있는 경우는 새 주제로 처리하되
  비교 대상이 직전 주제이면 doc_hint·키워드는 일부 보존 가능)
- 인사·감사 등 chat 신호

[1단계] 먼저 입력이 일상 대화인지 도메인 질문인지 판단:
- 일상 대화: 인사("안녕", "하이"), 감사("고마워", "수고"), 칭찬("잘했어"),
  봇 정체/능력 질문("너 누구야", "뭐 할 수 있어", "어떻게 사용해"),
  도메인과 무관한 잡담(날씨, 점심 등).
- 위 어디에도 해당하지 않으면 도메인 질문(법령·매뉴얼 관련)으로 본다.
  애매하면 도메인 질문으로 분류한다 ("혁신법 알려줘" 같이 짧고 모호한 도메인 키워드는
  chat 이 아니라 open).
- 멀티턴 맥락에서 후속어("왜?", "더 자세히")는 chat 으로 분류하지 말고 직전 주제에 대한
  도메인 질문으로 본다.

[2단계 — chat 인 경우] kind="chat" 으로 두고 chat_response 에 짧고 자연스러운 한국어
답변을 직접 작성. 다른 추출 필드는 모두 비움(빈 배열/false/빈 문자열).
rewritten_query 도 빈 문자열로 둡니다.
- 인사 → 예: "안녕하세요! 😊 국가연구개발혁신법 매뉴얼/시행령 등 연구행정 질문이 있으면 편하게 물어봐 주세요."
- 감사 → 예: "도움이 되었다니 다행입니다. 더 궁금한 거 있으면 알려주세요."
- 봇 정체 → 능력을 한 문단으로 안내 (혁신법·시행령·시행규칙·매뉴얼 검색, 페이지/조문 직접 조회, 비교).
- 도메인 무관 잡담 → 정중히 안내하되 챗봇 용도를 다시 알려줌.
chat_response 는 간결(2~4문장), 친근, 존댓말 유지, 이모지 1개 이내.

[2단계 — 도메인 질문인 경우] 아래 추출 규칙 적용:
1) 페이지 직접 언급은 모두 target_pages에 정수로. 표현 자유 — "151p", "151 페이지",
   "151쪽", "p.151", "백오십일쪽", "151번째 페이지" 모두 [151].
2) 조문/항/호/별표/별지/절은 한국 법령 표기로 정규화.
   "15조" → "제15조", "15조의2" → "제15조의2", "2항" → "제2항",
   "별표 2" / "별표2" → "별표2".
3) doc_name_hint: 사용자가 가리키는 문서 식별 단서. "본권"·"매뉴얼"이면 "매뉴얼",
   "시행령" "시행규칙" "법률(법)" 그대로. 명확하지 않으면 빈 문자열.
   멀티턴: 직전 대화에서 doc_name_hint 가 명확했고 현재 질문이 후속어면 이어받기.
4) topic_keywords: 질문 핵심 주제어 2~5개. 한국어 명사구 위주, 조사·동사·의문사 제외.
   멀티턴: 후속 질문이면 직전 주제어를 핵심으로 포함시키되, 새 키워드(예: "사례",
   "이유")가 추가되면 같이 넣는다.
5) comparison_intent: 변경/차이/비교 의도. "달라진", "차이", "종전 vs", "변경된",
   "바뀐", "이전과", "비교" 등이 신호.
6) kind: 가장 강한 신호 하나.
   - "page_lookup": 페이지 직접 언급이 있으면 무조건 이 값.
   - "article_lookup": 조문/별표/별지/절 언급이 있고 페이지 언급이 없으면.
   - "comparison": comparison_intent가 true이고 위 두 신호가 약하면.
   - "open": 위 어디에도 해당하지 않는 일반 도메인 질의.
7) **rewritten_query** (멀티턴 핵심):
   - 직전 대화 컨텍스트를 흡수해 *self-contained* 한 검색 질의로 변환한다.
   - retriever 가 이 텍스트를 임베딩해 벡터 검색하므로, 직전 주제어를 명시적으로
     포함시켜야 한다.
   - 예: 직전 "회의비로 세미나 사용 가능?" + 현재 "실제 사례 있어?"
        → rewritten_query="회의비로 세미나 개최한 실제 사례"
   - 예: 직전 "151p FAQ" + 현재 "그 중 Q3은?"
        → rewritten_query="매뉴얼 151페이지 FAQ Q3"
   - 예: 직전 "학생인건비 한도" + 현재 "왜?"
        → rewritten_query="학생인건비 한도 설정 이유 근거"
   - 첫 질문 (이전 대화 없음) 이거나 명확히 self-contained 하면 원본 그대로 둔다.
   - 주제 전환이면 새 주제로 그대로 (직전 주제어 끼워 넣지 말 것).
   - 페이지·조문 번호가 있으면 반드시 포함 (예: "151p", "제15조").
   - 자연스러운 한국어 한 문장 또는 짧은 명사구. 따옴표·대괄호 없이.
   - chat 으로 분류된 경우 빈 문자열.

반드시 다음 JSON 스키마를 정확히 준수해 출력:

{
  "kind": "chat | page_lookup | article_lookup | comparison | open",
  "chat_response": "",
  "target_pages": [],
  "target_articles": [],
  "target_paragraphs": [],
  "target_items": [],
  "target_appendices": [],
  "target_forms": [],
  "target_sections": [],
  "doc_name_hint": "",
  "topic_keywords": [],
  "comparison_intent": false,
  "rewritten_query": ""
}

JSON 외 설명·주석·코드펜스 금지. 첫 글자는 `{`, 마지막 글자는 `}`."""


def analyze_query(
    question: str,
    prior_turns: list[dict] | None = None,
) -> QueryHints:
    """질의를 Claude로 분석. 실패 시 정규식 fallback.

    Args:
        question: 현재 사용자 질문.
        prior_turns: 직전 대화 턴 리스트. 각 항목은 `{"role": "user"|"assistant",
                     "content": str}` 형태. 후속질문(예: 직전에 "151p" 얘기 후
                     "그 페이지 FAQ 내용 알려줘")에서 분석기가 페이지·문서 컨텍스트를
                     이어받을 수 있도록 사용된다. None 또는 빈 리스트면 단일 질문 모드.

    Returns:
        QueryHints — `target_pages`, `doc_name_hint`, `kind`까지 채워진 객체.
    """
    if not question or not question.strip():
        return parse_query("")

    user_turn = _build_user_turn(question, prior_turns or [])

    try:
        text = _run_query_sync(_ANALYZER_MODEL, _ANALYZER_SYSTEM, user_turn)
    except RateLimitError as exc:
        print(f"[query_analyzer] rate-limited, fallback to regex: {exc}", file=sys.stderr)
        return parse_query(question)
    except Exception as exc:
        print(f"[query_analyzer] LLM 호출 실패, regex fallback: {exc}", file=sys.stderr)
        return parse_query(question)

    json_str = _extract_json_object(text)
    if not json_str:
        print("[query_analyzer] JSON 추출 실패, regex fallback", file=sys.stderr)
        return parse_query(question)

    try:
        obj = json.loads(json_str)
    except json.JSONDecodeError as exc:
        print(f"[query_analyzer] JSON 파싱 실패: {exc}", file=sys.stderr)
        return parse_query(question)

    return _to_hints(obj, question)


def _to_hints(obj: dict[str, Any], question: str) -> QueryHints:
    """LLM 결과 dict + 정규식 결과를 union 해 QueryHints로 변환.

    LLM이 놓친 항목을 정규식이 보강하고, 반대로 정규식이 못 잡은 자유 표현을
    LLM이 채운다. 결과는 둘의 합집합 (LLM 우선 순서).
    """
    rx = parse_query(question)

    target_pages: list[int] = []
    for p in obj.get("target_pages") or []:
        try:
            n = int(p)
        except (TypeError, ValueError):
            continue
        if 1 <= n <= 9999 and n not in target_pages:
            target_pages.append(n)
    # regex가 추가로 잡은 페이지도 흡수
    for n in rx.target_pages:
        if n not in target_pages:
            target_pages.append(n)

    kind = str(obj.get("kind") or "open").strip()
    if kind not in {"page_lookup", "article_lookup", "comparison", "open", "chat"}:
        kind = "open"

    chat_response = str(obj.get("chat_response") or "").strip()
    # chat 으로 분류됐는데 응답 텍스트가 비어 있으면 안전한 기본 메시지로 대체.
    if kind == "chat" and not chat_response:
        chat_response = (
            "안녕하세요! 😊 국가연구개발혁신법 매뉴얼·시행령·시행규칙 관련 질문이 있으면 "
            "편하게 물어봐 주세요."
        )
    # 도메인 질문(non-chat)인데 응답 텍스트가 들어왔다면 무시.
    if kind != "chat":
        chat_response = ""

    # kind 후처리 — 페이지 있는데 LLM이 다른 값을 골랐으면 page_lookup으로 강제.
    # 단 chat 은 침범하지 않음.
    if target_pages and kind not in {"page_lookup", "chat"}:
        kind = "page_lookup"

    # rewritten_query: LLM 이 직전 컨텍스트를 흡수해 standalone 으로 만든 검색 질의.
    # 비어 있거나 chat 이면 원본 question 으로 폴백 (caller 측이 다시 폴백할 수도 있음).
    rewritten = str(obj.get("rewritten_query") or "").strip()
    if kind == "chat":
        rewritten = ""
    elif not rewritten:
        # LLM 이 필드를 누락했거나 빈 값으로 출력 → 원본 question 사용.
        rewritten = question

    return QueryHints(
        article_nos=_union(obj.get("target_articles"), rx.article_nos) if kind != "chat" else [],
        paragraphs=_union(obj.get("target_paragraphs"), rx.paragraphs) if kind != "chat" else [],
        items=_union(obj.get("target_items"), rx.items) if kind != "chat" else [],
        appendices=_union(obj.get("target_appendices"), rx.appendices) if kind != "chat" else [],
        forms=_union(obj.get("target_forms"), rx.forms) if kind != "chat" else [],
        sections=_union(obj.get("target_sections"), rx.sections) if kind != "chat" else [],
        keywords=_union(obj.get("topic_keywords"), rx.keywords) if kind != "chat" else [],
        doc_type_hints=rx.doc_type_hints if kind != "chat" else [],
        comparison_intent=(bool(obj.get("comparison_intent", False)) or rx.comparison_intent)
            if kind != "chat" else False,
        target_pages=target_pages if kind != "chat" else [],
        doc_name_hint=str(obj.get("doc_name_hint") or "").strip() if kind != "chat" else "",
        kind=kind,
        chat_response=chat_response,
        rewritten_query=rewritten,
    )


def _build_user_turn(question: str, prior_turns: list[dict]) -> str:
    """현재 질문 + 직전 대화 컨텍스트를 분석기 user 턴으로 직렬화.

    분석기는 단순 키워드 추출이 아니라 *맥락 이해* 가 필요하다.
    예) 직전 답변이 "151p에는 FAQ Q1~Q7이 있다" 였다면, 후속 질문 "FAQ Q1~Q7
    내용 알려줘" 에서 page=151 을 이어받아야 한다.

    너무 길어지지 않게 직전 2 턴(최대 user 1 + assistant 1)만 사용하고,
    각 턴은 800자에서 자른다.
    """
    if not prior_turns:
        return question

    # 직전 턴부터 역순으로 최대 2개만 (보통 user→assistant→user 순)
    tail = prior_turns[-3:]  # 최대 3개까지 받아 user→assistant 페어 + 직전 user

    blocks: list[str] = []
    for turn in tail:
        role = (turn.get("role") or "").strip()
        content = (turn.get("content") or "").strip()
        if not content or role not in {"user", "assistant"}:
            continue
        # assistant 답변이 dict 였을 가능성 — content 가 이미 str 이라고 가정.
        # 너무 긴 답변은 잘라낸다.
        if len(content) > 800:
            content = content[:800] + "…"
        label = "이전 사용자 질문" if role == "user" else "이전 답변 요약"
        blocks.append(f"[{label}]\n{content}")

    if not blocks:
        return question

    history_section = "\n\n".join(blocks)
    return (
        f"{history_section}\n\n"
        f"[현재 질문]\n{question}\n\n"
        f"위 이전 대화에서 페이지·문서·조문 컨텍스트가 이어지면, 현재 질문에도 그 값을 적용해 추출하세요. "
        f"예: 이전에 '151p' 가 언급됐고 현재 질문이 그 페이지의 FAQ를 가리키면 target_pages=[151]."
    )


def _union(a, b) -> list[str]:
    """두 리스트를 순서 보존 union (LLM 결과 우선)."""
    seen: set[str] = set()
    out: list[str] = []
    for item in list(a or []) + list(b or []):
        if item is None:
            continue
        s = str(item).strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out
