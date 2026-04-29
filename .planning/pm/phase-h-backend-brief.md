# Phase H — Backend 위임 브리프 (self-contained)

> 이 문서는 backend agent 가 PM 세션 컨텍스트 없이도 Phase H 작업을 수행할 수 있도록 작성된 단독 브리프이다.
> backend 는 이 문서만 읽고 작업하면 된다.

작성일: 2026-04-29
대상 phase: H (Hybrid Agent-style Document Access)
관련 로드맵: `/Users/maro/dev/company/chatbot/.planning/roadmap-future.md` 의 "Phase H" 섹션
선행 phase: G4 (sync) 완료 권장 — 토픽 폴백 경로가 안정되어 있어야 함

---

## 1. 작업 목적

페이지·조문 직접 조회 케이스에서 chunker 의 페이지 태깅·청크 경계 버그가 끝없이 회귀하는 문제(1.3.0~1.3.3 4회 패치, 매 회 전체 sync 6~10분)를 **인덱싱 의존 자체를 우회**하는 방향으로 해결한다.

핵심 아이디어: Claude 가 PDF/HWP 원본을 *실시간으로 읽어* 응답한다. 단, 전면 Agent-style 은 비용·시간·구독 한도 부담이 크기 때문에 **Hybrid** 로 진행:

| 질의 종류 | 라우팅 |
|---|---|
| 일반 토픽 검색 | 현재대로 Qdrant 벡터 (빠름·저렴) |
| 페이지 직접 ("151p 알려줘") | 로컬 MCP `read_page(N)` 호출 |
| 조문 직접 ("제15조") | 로컬 MCP `get_article("제15조")` |
| FAQ 번호 직접 ("FAQ 27번") | 로컬 MCP `search_text` |
| 비교형 / 다중 조문 | 현재대로 Qdrant + Opus escalate |

사용자 결정 인용 (2026-04-29):
> "Agent_team 처럼 실시간으로 PDF 읽는 방식, 전면 도입은 부담이지만 페이지·조문 직접 조회만은 그렇게 가는 게 chunker 회귀에서 벗어나는 길"

---

## 2. 정확한 수정 / 신규 대상

### 2.1 신규 모듈: `pipeline/local_doc_mcp.py`

로컬 문서 MCP 서버 + 도구 5종 노출. claude-agent-sdk 의 `mcp_servers` 인자로 등록 가능한 형태여야 한다.

#### 2.1.1 도구 시그니처 (확정)

```python
async def list_documents() -> list[dict]:
    """
    인덱싱된 모든 문서 목록을 반환.
    config_store 의 pdf_dir / hwp_dir 를 스캔.

    returns: [
        {
            "doc_name": str,           # basename (확장자 포함)
            "doc_type": "pdf" | "hwp",
            "abs_path": str,
            "page_count": int | None,  # PDF 만, HWP 는 None
            "size_bytes": int,
            "mtime": float,
        },
        ...
    ]
    """

async def read_page(doc_name: str, page_num: int) -> str:
    """
    PDF 의 N 페이지 텍스트 반환 (1-based).
    HWP 는 page_num 무시 + 전체 텍스트 (또는 NotImplementedError + 안내 메시지).

    raises:
      - DocumentNotFoundError (doc_name 매칭 실패)
      - PageOutOfRangeError (page_num < 1 or > page_count)
    """

async def search_text(doc_name: str, query: str, max_results: int = 5) -> list[dict]:
    """
    문서 내 키워드 검색 (단순 substring).

    returns: [
        {
            "page_num": int,           # PDF 만
            "snippet": str,            # 매칭 위치 ±100자
            "match_offset": int,       # 페이지 내 offset (PDF) 또는 전체 offset (HWP)
        },
        ...
    ]

    PDF: pdfplumber 로 페이지별 텍스트 추출 후 검색.
    HWP: hwp_parser 로 전체 텍스트 추출 후 검색 (page_num=None).
    """

async def get_article(doc_name: str, article_no: str) -> dict:
    """
    "제15조" 같은 조문 식별자로 본문 + 페이지 범위 반환.

    returns: {
        "article_no": str,
        "title": str | None,           # "제15조(연구개발기관의 지정)" 의 괄호 제목
        "body": str,                   # 본문 전체 (다음 조문 시작 직전까지)
        "start_page": int | None,      # PDF 만
        "end_page": int | None,        # PDF 만
    }

    구현: pdfplumber 페이지 순회하며 article_no 시작 위치 발견 → 다음 article 시작
          또는 EOF 까지 누적. chunker.py 의 ARTICLE_PATTERNS 재사용 가능.

    raises:
      - DocumentNotFoundError
      - ArticleNotFoundError
    """

async def list_articles(doc_name: str) -> list[dict]:
    """
    문서 내 모든 article_no 목록.

    returns: [
        {"article_no": "제1조", "title": "목적", "start_page": 1},
        {"article_no": "제2조", "title": "정의", "start_page": 1},
        ...
    ]
    """
```

#### 2.1.2 캐시 정책

- pdfplumber 페이지 추출은 비싸므로 LRU 캐시:
  ```python
  @lru_cache(maxsize=128)
  def _pdf_page_text(abs_path: str, mtime: float, page_num: int) -> str:
      ...
  ```
  → mtime 을 캐시 키에 포함해 파일 변경 시 자동 무효화.
- 도구 호출 1회당 평균 1~3 페이지만 읽으므로 메모리 부담 적음.

#### 2.1.3 MCP 서버 구현 옵션

**옵션 A — claude-agent-sdk 의 in-process tool**:

claude-agent-sdk 가 in-process Python 함수를 직접 도구로 노출하는 방식을 지원하면 그것을 사용. (별도 서버 프로세스 불필요)

```python
from claude_agent_sdk import ClaudeAgentOptions, tool

@tool
async def read_page(doc_name: str, page_num: int) -> str:
    ...

options = ClaudeAgentOptions(
    ...,
    tools=[read_page, get_article, search_text, list_articles, list_documents],
    allowed_tools=["read_page", "get_article", "search_text", "list_articles", "list_documents"],
    max_turns=5,
)
```

**옵션 B — FastMCP 서버**:

별도 stdio MCP 서버로 띄우고 `mcp_servers` 인자에 등록:

```python
options = ClaudeAgentOptions(
    ...,
    mcp_servers={
        "local-doc": {
            "command": "python",
            "args": ["-m", "pipeline.local_doc_mcp"],
        }
    },
    allowed_tools=[
        "mcp__local-doc__read_page",
        "mcp__local-doc__get_article",
        "mcp__local-doc__search_text",
        "mcp__local-doc__list_articles",
        "mcp__local-doc__list_documents",
    ],
    max_turns=5,
)
```

**선택 기준**: backend 가 claude-agent-sdk 의 현행 API 를 확인 후 자율 결정. 옵션 A 가 가능하면 우선 (서브프로세스 오버헤드 없음). 불가능하거나 SDK 가 stdio MCP 만 지원하면 옵션 B.

### 2.2 `pipeline/answerer.py` 분기 로직

기존 `_run_query` 는 `allowed_tools=[]`, `max_turns=1` 로 도구 사용 차단. 새로운 분기를 추가:

```python
async def _run_query_with_tools(
    model: str,
    system_prompt: str,
    user_prompt: str,
    tool_set: str,  # "page_lookup" | "article_lookup"
) -> str:
    """페이지/조문 직접 조회 시 호출. 로컬 MCP 도구 사용 허용."""
    options = ClaudeAgentOptions(
        system_prompt=system_prompt,
        model=model,
        allowed_tools=[
            "read_page", "get_article", "search_text",
            "list_articles", "list_documents",
        ],
        # ↑ 옵션 B 사용 시 "mcp__local-doc__read_page" 등으로 prefix
        permission_mode="bypassPermissions",
        max_turns=5,
        tools=[read_page, get_article, search_text, list_articles, list_documents],
        # ↑ 옵션 A 일 때만
    )

    # 기존 _run_query 와 동일하게 AssistantMessage / ResultMessage 처리.
    # 단, ToolUseBlock / ToolResultBlock 도 stderr 로그로 가시화:
    #   sys.stderr.write(f"[H] tool_use: {block.name}({block.input})\n")
    ...
```

호출 분기는 `answer()` 진입부에서:

```python
def answer(query: str, ...) -> dict:
    parsed = query_parser.analyze(query)

    if parsed.kind == "page_lookup":
        return _answer_page_lookup(parsed, model, ...)
    elif parsed.kind == "article_lookup":
        return _answer_article_lookup(parsed, model, ...)
    else:
        return _answer_topic(parsed, model, ...)  # 기존 경로
```

JSON 출력 강제는 그대로 유지 — system_prompt 에서 JSON schema 명시.

### 2.3 query_parser 확장

`pipeline/query_parser.py` 의 `kind` 분류에 `page_lookup`, `article_lookup` 추가 (이미 있으면 기존 분류 활용).

분류 규칙:
- `page_lookup`: `r"\d+\s*(p|페이지|쪽)"` 매칭
- `article_lookup`: `r"제\s*\d+\s*조"` 매칭 + 다른 토픽 키워드 부재
- 그 외: 기존 `topic` / `comparison` 등

### 2.4 `app.py` 라우팅 분기

기존 호출부에서 `parsed.kind` 에 따라 다른 답변 경로 선택. UI 카드는 동일 — 답변 카드 하단에 "📄 직접 조회" 또는 "🔍 검색 기반" 같은 출처 배지만 추가 (선택).

---

## 3. 정확한 SDK 사용법

### 3.1 `ClaudeAgentOptions` 의 도구 관련 인자

```python
from claude_agent_sdk import ClaudeAgentOptions

options = ClaudeAgentOptions(
    system_prompt=...,
    model=...,

    # 도구 노출 (옵션 A: in-process)
    tools=[read_page, get_article, ...],   # @tool 데코레이터된 함수 리스트

    # 도구 노출 (옵션 B: stdio MCP)
    mcp_servers={
        "local-doc": {"command": "python", "args": ["-m", "pipeline.local_doc_mcp"]},
    },

    # 화이트리스트 — 도구 이름 정확히 일치해야 호출 허용
    allowed_tools=["read_page", "get_article", ...],
    # ↑ 옵션 B 사용 시 "mcp__<server-name>__<tool-name>" 형식

    # 권한 — 도구 호출 시 prompt 띄우지 않고 자동 허용
    permission_mode="bypassPermissions",

    # multi-turn 허용 — Claude 가 도구 호출 → 결과 받아 재추론 → 답변 생성
    # max_turns=1 이면 첫 답변만 받고 종료, 도구 호출 결과 활용 불가.
    # 페이지 조회는 1턴(도구 1회 + 답변 1회)이면 충분 → 안전마진 5
    max_turns=5,
)
```

### 3.2 도구 호출 결과 가시화 (stderr 로그)

답변 스트림에서 `ToolUseBlock`, `ToolResultBlock` 도 처리:

```python
from claude_agent_sdk import (
    AssistantMessage, ToolUseBlock, ToolResultBlock,
    UserMessage,  # tool result 는 UserMessage 안에 옴
)

async for msg in query(prompt=user_prompt, options=options):
    if isinstance(msg, AssistantMessage):
        for block in msg.content:
            if isinstance(block, TextBlock):
                text_chunks.append(block.text)
            elif isinstance(block, ToolUseBlock):
                sys.stderr.write(
                    f"[H] tool_use: {block.name}({block.input})\n"
                )
    elif isinstance(msg, UserMessage):
        for block in msg.content:
            if isinstance(block, ToolResultBlock):
                snippet = str(block.content)[:200]
                sys.stderr.write(
                    f"[H] tool_result[{block.tool_use_id}]: {snippet}\n"
                )
```

이렇게 하면 사용자가 streamlit 콘솔에서 도구 호출 흐름을 실시간 확인 가능.

### 3.3 SDK 버전 확인

backend 는 작업 시작 시 다음을 먼저 확인:

```bash
python -c "import claude_agent_sdk; print(claude_agent_sdk.__version__)"
python -c "from claude_agent_sdk import ClaudeAgentOptions; help(ClaudeAgentOptions)"
```

`tools=`, `mcp_servers=`, `max_turns=` 인자가 모두 지원되는지 확인. 미지원이면 SDK 업그레이드 또는 옵션 B(stdio MCP) 로 대체.

---

## 4. 단위 테스트 / 통합 테스트

### 4.1 `tests/test_local_doc_mcp.py`

```
test_list_documents_returns_pdf_and_hwp()
test_read_page_returns_correct_text()           # fixture PDF page 5
test_read_page_raises_on_out_of_range()
test_read_page_raises_on_unknown_doc()
test_search_text_finds_matches_with_snippet()
test_get_article_returns_body_and_page_range()  # "제5조" → 본문 + start/end
test_get_article_raises_on_unknown_article()
test_list_articles_returns_all()
test_pdf_page_cache_invalidates_on_mtime_change()
```

### 4.2 통합 테스트 (`tests/test_phase_h_routing.py`)

```
test_page_lookup_query_routes_to_agent_path()
    # "151p 알려줘" → kind=page_lookup → tools 활성화 → read_page 호출
test_article_lookup_query_routes_to_agent_path()
    # "제15조" → kind=article_lookup → get_article 호출
test_topic_query_uses_legacy_qdrant_path()
    # "연구노트 보존기간" → kind=topic → Qdrant 그대로 (도구 호출 0회)
test_page_lookup_returns_actual_page_content()
    # 매뉴얼 PDF 151페이지의 실제 키워드가 답변에 포함
test_topic_regression_set_passes()
    # 기존 30 케이스 평가셋 통과율 ≥ 기존 baseline
```

---

## 5. 합격기준 (완료 조건)

다음을 모두 충족해야 H 완료로 보고:

1. **단위 테스트 9 케이스** 모두 통과
2. **통합 테스트 5 케이스** 모두 통과
3. **페이지 직접 조회 정확도**: "151p 알려줘", "37페이지", "270쪽" 등 5개 변형 모두 정확한 페이지 콘텐츠 반환 (100%)
4. **조문 직접 조회 정확도**: "제5조", "제15조 제2항", "혁신법 제32조" 등 5개 변형 모두 정확한 본문 반환 (100%)
5. **토픽 회귀**: 기존 30 케이스 평가셋 통과율이 baseline 대비 회귀 0건
6. **응답 시간**:
   - 페이지 조회: ≤ 30초 (Haiku 기준)
   - 조문 조회: ≤ 25초
   - 토픽 검색: 변화 없음 (기존 ~12~18초 유지)
7. **stderr 로그 가시성**: 도구 호출 시 `[H] tool_use:` 라인이 streamlit 콘솔에 출력됨
8. **JSON 출력 강제 유지**: 도구 사용 답변도 기존 `{verdict, evidence, ...}` schema 그대로

---

## 6. 상호작용 / 의존성

- **선행**: G4 sync 완료 (토픽 폴백 경로 안정)
- **병렬 가능**: F1(모델 라우팅), G5(MCP 동기화) 와 충돌 없음
- **외부 의존**:
  - `claude-agent-sdk` ≥ tools/mcp_servers 인자 지원 버전
  - `pdfplumber` (이미 사용 중)
  - `hwp_parser` (재사용)

---

## 7. 자율 결정 권한 (PM 에게 묻지 말 것)

다음 결정은 backend 가 자체 판단:
- 옵션 A(in-process tool) vs 옵션 B(stdio MCP) 선택 — SDK 지원 여부에 따름
- LRU 캐시 크기 (기본 128 권장)
- `search_text` 의 snippet 길이 (기본 ±100자)
- `get_article` 의 article_no 정규화 규칙 ("제 15 조" → "제15조" 등)
- query_parser 의 page_lookup 정규식 정확한 패턴
- stderr 로그의 prefix 문자열 (`[H]` 권장)
- 테스트 fixture 로 사용할 작은 합성 PDF/HWP

다음은 PM 결정 사항이라 backend 는 임의로 변경 금지:
- Hybrid 라우팅 정책 (페이지/조문은 도구, 토픽은 Qdrant)
- 도구 5종의 시그니처 (인자명/반환형)
- max_turns 상한 (5 — 더 늘리면 비용/지연 증가)
- 토픽 회귀 0건 기준
- JSON 출력 schema 변경 금지

---

## 8. 알려진 위험 / 주의

1. **SDK 도구 인자 미지원 가능성**: claude-agent-sdk 가 `tools=` 또는 `mcp_servers=` 를 지원하지 않으면 작업 차단. 작업 시작 시 가장 먼저 확인.
2. **PDF 페이지 인덱싱 차이**: pdfplumber 는 0-based, 사용자는 1-based. `read_page` 의 page_num 인자는 사용자 노출용 1-based, 내부 변환 필요.
3. **HWP 페이지 개념 부재**: HWP 는 페이지 단위가 명확하지 않음. `read_page(hwp_doc, N)` 는 NotImplementedError + "HWP 는 페이지 조회 미지원, search_text 또는 get_article 사용" 안내 메시지 반환.
4. **토픽 회귀**: 기존 경로를 절대 건드리지 말 것. answerer 의 `_answer_topic` 함수는 그대로 유지 (분기만 추가).
5. **응답 시간 폭주**: max_turns=5 + 도구 호출 latency 합치면 30초 초과 가능. read_page/get_article 단일 호출로 끝나도록 system_prompt 가 명확히 지시해야 함 ("Use the smallest number of tool calls. For 'NN페이지' queries, call read_page once and answer.").

---

## 9. 보고 형식 (작업 완료 시 PM 에게 회신)

```
## Phase H 완료 보고

### 변경 / 신규 파일
- pipeline/local_doc_mcp.py (신규)
- pipeline/answerer.py (분기 로직 추가)
- pipeline/query_parser.py (page_lookup/article_lookup 분류)
- app.py (라우팅 분기, 선택)
- tests/test_local_doc_mcp.py (신규)
- tests/test_phase_h_routing.py (신규)

### 선택 결과
- 옵션 A(in-process) / 옵션 B(stdio MCP) 중 선택: ?
- 선택 사유: ?

### 테스트 결과
- 단위 테스트: ?/9 통과
- 통합 테스트: ?/5 통과
- 페이지 직접 조회 5개 변형: ?/5
- 조문 직접 조회 5개 변형: ?/5
- 토픽 30 케이스: ?/30 (baseline ?/30)
- 응답 시간 측정: 페이지 ?초 / 조문 ?초 / 토픽 ?초

### 알려진 이슈 / 후속
- (있으면 기재)
```
