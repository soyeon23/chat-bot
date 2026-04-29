# 프로젝트 컨텍스트 — Claude 세션 시작 시 자동 로드

> **이 파일은 Claude Code 가 세션 시작·resume 시 자동으로 읽어 들입니다.**
> 현재 진행 상황·할 일·운영 규칙을 한눈에 파악하기 위한 단일 진입점.

---

## 🎯 프로젝트

한국 연구행정 RAG 챗봇 — 국가연구개발혁신법 매뉴얼·시행령·시행규칙 기반 보수형 답변.

- 자세한 원칙: [`.claude/agents/PROJECT_BRIEF.md`](.claude/agents/PROJECT_BRIEF.md)
- 셋업·사용: [`README.md`](README.md)
- 로드맵: [`.planning/roadmap-future.md`](.planning/roadmap-future.md)

---

## 📋 현재 해야 할 일 — 단일 진실

> **항상 이 파일부터 확인:** [`.planning/pending-issues.md`](.planning/pending-issues.md)
>
> 현재 우선순위·해결된 항목·작업량 추정 모두 그 파일에 정리됨.

**바로 실행 가능한 다음 액션**:
1. `python scripts/eval_full.py` — 평가셋 baseline 갱신 (10분)
2. 본체 3종 PDF 가용 시 프로젝트 루트에 드롭 + 동기화 → 검색 커버리지 30%+ ↑

---

## ⚠️ 운영 규칙 — Claude 세션 종료 / compact 직전

다음 시점에 **반드시 [`.planning/pending-issues.md`](.planning/pending-issues.md) 갱신**:

1. **`/compact` 호출 직전** — 진행 상황 / 해결된 이슈 / 새로 발견한 이슈 반영
2. **세션 종료 직전** (사용자가 끄거나 Claude 가 wrap up 시점)
3. **새 phase·기능 작업 *완료 직후*** — 해결된 이슈는 ✅ + strikethrough, 커밋 해시 명시
4. **새 이슈 발견 시** — 즉시 추가 (우선순위 분류 + 작업량 추정 포함)

갱신 양식:
```markdown
## ✅ 최근 해결됨
| 이슈 | 해결 방식 | 커밋 |
|---|---|---|
| ~~기존 이슈명~~ | 한 줄 요약 | `해시7자리` |

## 🔴 Critical / 🟡 High / 🟢 Medium / 🔵 Future
(남은 이슈만 — 해결된 건 위로 이동)
```

이 규칙 지켜야 다음 세션에서 *즉시* 컨텍스트 복원 가능. 안 지키면 매번 처음부터 파악해야 함.

---

## 🏗️ 아키텍처 한눈에

```
사용자 질의 ─→ query_analyzer (Haiku, kind 분류 + rewritten_query)
              │
              ├─ kind=chat → 즉답 (검색 스킵)
              │
              └─ kind=open/page_lookup/article_lookup/comparison
                    │
                    ├─→ Qdrant 벡터 검색 (의미 검색, 빠름)
                    ├─→ Phase H 도구 (read_page/get_article/search_text — 페이지·조문 직접)
                    ├─→ 법제처 MCP (외부 법령 보완)
                    │
                    └─→ generate_answer (Sonnet 4.6, 비교형은 Opus 4.6 escalate)
                            └─ JSON 답변 (verdict / summary / citations)
```

핵심 모듈:
- `pipeline/query_analyzer.py` — Claude 의도 분석
- `pipeline/retriever.py` — Qdrant smart 검색 (부스트 + 부스트 분리 confidence)
- `pipeline/local_doc_mcp.py` — Phase H 로컬 문서 도구
- `pipeline/answerer.py` — claude-agent-sdk 답변 생성
- `pipeline/chunker.py` — 1.4.0 (매뉴얼 페이지 라우팅)
- `pipeline/sync.py` — 증분 동기화 (file_hashes.json)
- `pipeline/mcp_sync.py` — MCP 헬스체크 / 업데이트

---

## 🔑 인증 / 환경

- Claude Code OAuth (Anthropic API 키 미사용)
- macOS keychain 또는 `~/.claude/.credentials.json` (Windows/Linux) 자동 감지
- 법제처 OC 키: `data/config.json` 의 `korean_law_oc` (gitignore — 사용자별)

---

## 📊 핵심 메트릭 (마지막 sync 기준 — 1.4.0)

- 총 청크: **940**
  - 매뉴얼 PDF: 844 (페이지 커버리지 64.3%, article_no = "p.N" 형식)
  - 시행규칙 별지 10개: 84
  - 시행령 별표 7개: 12
- 평가셋 통과율: 96.8% (1.3.2 baseline — 1.4.0 재실행 필요)
- 단위 테스트: 전체 PASS

---

## 🚧 알려진 한계

- HWPML 본체 3종 (혁신법·시행령·시행규칙 본문) 미인덱싱 — PDF 추가 또는 XML 파서 작성 필요
- 답변 응답 시간 1~2분 (멀티턴 누적 시 더 느림) — Phase H1 속도 최적화 보류 중
- 매뉴얼 PDF 페이지 커버리지 64% — 책 인쇄 layout 의 빈 짝수 페이지 (의도된 동작)

---

## 🤖 4-에이전트 팀 ([`.claude/agents/`](.claude/agents/))

- **PM** (`pm.md`) — 요구사항·로드맵·페이즈 분할·위임
- **Backend** (`backend.md`) — RAG·MCP·Qdrant·평가
- **Frontend** (`frontend.md`) — Streamlit UI·인용·디자인
- **Scout** (`scout.md`) — 외부 패턴·라이브러리 발굴

위임 시 각 에이전트는 self-contained 브리프 받음 (이 파일 + PROJECT_BRIEF.md 참고).
