# 향후 발전 로드맵

> 사용자 결정 사항을 기록한 후속 작업 로드맵.
> 우선순위는 의존성 + 트리거 조건에 따라 결정.
> 각 단계는 PM 에이전트가 페이즈로 분할해 plan-phase 흐름으로 진행.

작성일: 2026-04-28
최종 수정: 2026-04-29 (G3 폐기, G4 재정의, G5 신규 등록)
참조 베이스라인:
- `.planning/pm/retrieval-improvements.md` (검색 개선 로드맵)
- `.planning/research/scout-hwp-integration.md` (HWP MCP 통합 분석)

---

## 현재 상태 스냅샷 (2026-04-28)

| 영역 | 상태 |
|---|---|
| 답변 모델 | Haiku 4.5 (런타임 가변, 사이드바에서 즉시 변경 가능) |
| 답변 경로 | claude-agent-sdk → claude CLI 서브프로세스 (외부 API 직접 호출 회피) |
| 인증 | Claude Code OAuth 키체인 자동 감지 (macOS) / `ANTHROPIC_AUTH_TOKEN` env (그 외) |
| 인덱스 | Qdrant 로컬 파일 모드, 2,948 청크 (PDF 1개 + HWP 17개) |
| 검색 | smart (regex prefilter + payload boost) — eval 9/10 통과 |
| 배포 | Streamlit 웹 (localhost:8501) |
| 위저드 | 환경 검사 + 경로 설정 + 모델 선택 (사이드바) |
| 법제처 MCP | OC `maro202020`로 인증 통과, 동적 로드 |

---

## 단계별 발전 계획

### Phase F1 — 모델 격상 (Haiku → Sonnet → Opus)

**목적**: 단순 인용 답변에서 복잡한 다단계 해석·사례 판단까지 품질 확장.

**트리거 조건**:
- 사용자 피드백에서 "Haiku 답변이 표면적이다" 패턴 반복
- 또는 사용량 한도 부담 없이 Sonnet 운영 가능한 환경 확보 후

**작업 내용**:
- 모델 선택 UI는 이미 사이드바에 있음 — 추가 작업 없음
- (선택) 질문 *유형 분류기*로 자동 라우팅: 단순 인용 → Haiku, 사례 해석 → Sonnet, 복잡 다중 조문 결합 → Opus
  - 라우팅은 가벼운 정규식/키워드 룰 기반 (예: "이 경우 어디에 해당", "충돌하는 조문" 등 → Sonnet+)
- 답변 카드에 어떤 모델로 답변했는지 배지 추가

**예상 효과**: 사용자가 모델 선택 없이도 자동으로 적절한 품질의 답변 수신.

**의존성**: 없음. 언제든 진행 가능.

**주의**:
- 웹(Streamlit) 환경에서 Sonnet 호출 시 분당 burst 한도 더 빠르게 소진 — *Phase F2가 진행되지 않은 상태*에서는 Sonnet 자주 쓰면 429 빈번.

---

### Phase F2 — 배포 형태 전환 (웹 → 네이티브)

**목적**: Sonnet/Opus 같은 무거운 모델을 안정적으로 사용하기 위한 인프라 확보. 사용자 PC 단독 운영(오프라인 가능). 윈도우 환경 정식 지원.

**트리거 조건**:
- Phase F1 자동 라우팅이 실제로 Sonnet/Opus를 자주 호출하게 됨
- 또는 사용자 측 윈도우 PC 배포 일정이 잡힘

**작업 내용 (옵션 비교)**:

| 옵션 | 설명 | 장점 | 단점 |
|---|---|---|---|
| **A. PyInstaller 번들** | 현재 Streamlit 코드 그대로 단일 .exe 패키징 | 코드 변경 거의 없음 | 패키지 ~500MB, 부팅 느림 |
| **B. Tauri/Electron 래퍼** | 네이티브 앱 + 내부 Streamlit 서버 | 외형 polished, 자동 업데이트 가능 | 새 스택, 학습 곡선 |
| **C. Agent_team 스타일 SwiftUI/Electron** | 기존 Agent_team 프로젝트 패턴 그대로 — `claude` CLI 서브프로세스 + 네이티브 UI | 프로젝트 분리, 기존 RAG 백엔드 그대로 호출 | 프론트 재작성 필요 |
| **D. Claude Code 플러그인/MCP 서버** | RAG 검색 자체를 MCP 서버로 노출 → Claude Code 안에서 슬래시 커맨드로 사용 | 파워 유저(개발자)에게 강력 | 일반 R&D 담당자 쓰기 어려움 |

**현재 사용자 결정**: **C 옵션 우선** — 사용자가 이미 만들어둔 `Agent_team` 프로젝트 구조 활용. 기존 Streamlit 백엔드를 동일 머신에서 실행하면서, 네이티브 UI는 별도 프로세스로 backend HTTP 호출.

> 다만 Agent_team 자체도 "조금 느린 감" 있다는 사용자 피드백 → Phase F4 속도 최적화와 같이 진행해야 효과 큼.

**예상 효과**:
- Sonnet/Opus 운용 시 안정성 ↑ (Claude Code 큐잉 그대로 사용)
- 윈도우 PC 정식 지원
- 실행파일 클릭 한 번으로 시작 (UX ↑)

**의존성**:
- Phase F4(속도 최적화) 동반 진행 권장

---

### Phase F3 — 컨텍스트 관리 + 사용량 모니터링

**목적**: 업무 중 같은 주제로 여러 질문을 이어가도 답변이 일관성 유지. 사용량을 사용자가 직접 보고 조절 가능.

**트리거 조건**:
- 사용자 실제 업무 도입 시작 시점 (탐색 단계 종료)

**작업 내용**:

#### F3.1 컨텍스트 (대화 메모리)
- **이슈**: 현재는 매 질문이 *독립적*. 이전 질문/답변을 기억 못 함.
  - 사용자가 "그럼 이 경우는?"이라고 물으면 chatbot은 무슨 "이 경우"인지 모름
- **해결안**:
  - 세션별 대화 히스토리 → 시스템 프롬프트에 직전 N개 Q&A 포함
  - 또는 *주제 요약* 슬롯: 첫 질문에서 핵심 주제 추출 → 후속 질문 시 같은 주제로 묶음
  - 토큰 예산: 컨텍스트 50K + 시스템 프롬프트 2K + 새 청크 10K + 답변 2K = 64K (Haiku 200K 기준 안전)
- **저장 구조**: Streamlit `st.session_state.conversation_history` (메모리) + 옵션으로 `data/conversations/<session>.jsonl` 영구 저장

#### F3.2 사용량 모니터
- **이슈**: 사용자가 분당 burst 한도 / 5시간 메시지 한도 / 토큰 사용량을 알 수 없음
- **해결안**:
  - claude CLI에 사용량 조회 명령이 있으면 그것을 호출 (조사 필요)
  - 없으면: 우리가 호출 카운트 + 토큰 카운트를 *자체 추적*
    - 매 호출 입력/출력 토큰 추정 (텍스트 길이 / 4)
    - 일/시간 단위 누적
    - 사이드바에 "오늘 ?회 호출, 약 ?K 토큰" 배지
  - 한도 임박 시 시각 경고 (예: 80% 도달 시 노란색)

**예상 효과**: 멀티턴 업무 흐름 가능 + 사용자가 한도 내에서 안정 운영.

**의존성**: 없음. F1~F2와 병렬 가능.

---

### Phase F4 — 속도 최적화

**목적**: 답변 1회 ~60초 → ~10~15초 수준으로 단축. 사용자 경험에서 결정적.

**트리거 조건**:
- 사용자 실 사용 시작 후 "느리다" 피드백 발생 (이미 발생 — Agent_team에서도 느낌)

**작업 내용 (효과 큰 순)**:

| # | 항목 | 예상 효과 | 작업량 |
|---|---|---|---|
| 1 | **프롬프트 캐싱** (Anthropic 5분 TTL) — 시스템 프롬프트 + 청크 묶음을 캐시 | TTFT 30~50% 단축, 비용 ↓ | 1시간 |
| 2 | **응답 스트리밍** — Claude 답변을 토큰 단위로 UI에 점진 출력 | 체감 속도 큰 향상 (전체 시간은 동일하지만 0.5초만에 첫 글자) | 반나절 (claude-agent-sdk 스트리밍) |
| 3 | **법제처 API 병렬화** — 법령/판례/행정규칙 3개 API를 직렬 → 동시 호출 | 2~5초 단축 | 1시간 |
| 4 | **MCP 호출 조건부** — 매번 호출하지 말고 *질의에 법령 키워드가 있을 때만* | 1~3초 단축 | 30분 |
| 5 | **Qdrant 클라이언트 캐싱** — 매 호출마다 재생성 → `@lru_cache` | 50ms 절감 | 5분 |
| 6 | **임베딩 모델 워밍업** — 첫 질문 전에 백그라운드로 미리 로드 | 첫 응답 10~30초 단축 | 30분 |
| 7 | **답변 청킹 필터** — top-K 5개 → 점수 임계값 + 다양성으로 3~4개로 줄임 | Claude 입력 토큰 ↓, 0.5~1초 단축 | 1시간 |

**누적 효과 추정**: 60초 → 12~18초.

**의존성**:
- 항목 2(스트리밍)는 Streamlit과 호환 작업 필요 → F2 네이티브 전환 시 더 자연스러움
- 항목 1(캐싱)은 claude-agent-sdk 캐싱 옵션 확인 후 진행

---

### Phase F5 — 코퍼스 / 검색 추가 개선 (PM 로드맵 후속)

**목적**: 한계 #1, #6 등 PM이 후순위로 둔 항목 진행.

**트리거 조건**:
- F1~F4 안정화 후
- 또는 사용자 검색 실패 사례가 *반복*되어 패턴화될 때

**작업 내용**:
- **OCR 강화** (Phase C from PM): Pillow 전처리(이진화/노이즈/대비) + DPI 400 → 잔여 ~21페이지 복구 시도
- **HWPML 형식 지원**: 메인 법령 3개(`혁신법.hwp`, `시행령.hwp`, `시행규칙.hwp`)는 HWPML(XML) 형식 → hwp-mcp 거부됨. mjyoo2/hwp-extension 도 HWPML 직접 지원하지 않음 (스카우트 확인). 후보:
  - LibreOffice headless 변환
  - Python stdlib `xml.etree.ElementTree` 로 직접 파서 작성 (스키마 단순함 — 텍스트만 추출하면 충분)
- **HWP 청킹 품질**: hwp_parser가 단일 페이지로 처리해 별표 구조가 깨짐. 논리 구조(별표/조/항/호) 기반 분할로 개선
- **하이브리드 검색 재시도** (BM42/SPLADE sparse vectors): smart-only로 못 잡는 케이스가 누적되면 재도전
- **계층적 청킹**: 조 → 항 → 호 단위로 다층 청크 + parent-doc retrieval

**대표 회귀 테스트 케이스**:
- **"참여제한 처분기준의 가중·감경 사유"** — 현재 가중기준 단편만 잡고 감경기준 누락. F5 완료 시 별표 6 전체가 인용되어야 정상.
- "혁신법 제15조 제2항 전문" — HWPML 시행령이 처리되면 정확한 조문 인용 가능.

---

## 우선순위 요약

```
지금 ──────────────────────────────────────► 나중

F4 (속도)        F1 (모델 라우팅)       F3 (컨텍스트)    F5 (잔여 검색 개선)
   ↓                  ↓                       ↓                 ↓
F2 (네이티브) ─────────────────────────────►
                        병렬 가능
```

**다음 1~2주 권장 진행 순서**:

1. **F4-1, F4-3, F4-4** (캐싱, 병렬, 조건부 MCP) — 코드 적은데 효과 큼
2. **F3.1** (대화 메모리) — 멀티턴 가능해지면 사용성 큰 도약
3. **F4-2** (스트리밍) — 답변 체감 속도 결정적
4. **F2-C** (네이티브 전환) — Agent_team 패턴 활용
5. **F1 자동 라우팅** + **F3.2 사용량 모니터**
6. F5 잔여

각 단계는 PM 에이전트에 `gsd:plan-phase`로 위임 → backend/frontend로 분할 실행.

---

## 메모

- 사용자 직접 인용 ("점차 소넷으로 발전시키는게 좋으려나 딥한것 까지 해야하니까")
- 사용자 직접 인용 ("웹은 소넷이 안되니까 아까 말 한것으로 나중에 교체")
- 사용자 직접 인용 ("업무에서 사용할때 컨텍스트 관리나 이런 관점도 진행")
- 사용자 직접 인용 ("에이전트 프로젝트 써봤는데 조금 느린 감")
- 사용자 직접 인용 (2026-04-29, G3 폐기 결정 시점) ("그 파일이 안일어났는데 전체를 재인덱싱은 너무 별로임")

---

## Phase G 시리즈 — 별표 인덱싱 수정안 (옵션 C + A)

작성일: 2026-04-29
최종 수정: 2026-04-29 (G3 폐기 → G4 흡수, G4 재정의, G5 신규 등록)
배경: 별표(별표1~7) 본문이 청크 분할 시 깨지는 문제 발견. 시행령/시행규칙의 별표 본문 안에 등장하는 인라인 참조 `(제20조제1항 관련)`, `법 제32조` 같은 문자열이 `제N조` 정규식과 매칭되어 잘못된 split point 가 만들어졌음. 사용자가 옵션 C(정규식 강화) + 옵션 A(별표 파일 라우팅) 결합안을 승인.

진행 순서: **G1 → G2 → G4 → G5** (G3 는 폐기, 본질 작업은 G4 첫 실행이 흡수).

### Phase G1 — Chunker 정규식 강화 (옵션 C) ✅ 완료

**입력**:
- `pipeline/chunker.py` 14~19라인 `ARTICLE_PATTERNS`

**작업**:
- `제\d+조...` 패턴에 줄 시작 강제(`(?m)^\s*`) + 후행 컨텍스트(`(?=\s*\(|\s*$|\s*\n)`) 추가
- 괄호 인라인 참조(`(제20조제1항 관련)`, `법 제32조`)는 split 트리거에서 제외
- `별표\s*\d+`, `별표(?!\s*\d)`, `부\s*칙` 도 동일하게 줄 시작 강제

**검증**:
- 별표2 단일 파일 재인덱싱 후 `article_no='별표 2'` 청크 ≥ 5개
- 별표2 본문 핵심 키워드(연구활동비, 클라우드컴퓨팅서비스, 학생인건비, 위탁연구개발비, 연구수당)가 별표2 청크에 묶여 있는지 확인 (각 키워드별 1건 이상)
- 시행령 본체의 정상 조문(제13조, 제15조 등)도 여전히 split 됨을 회귀 확인

**산출물**:
- `pipeline/chunker.py` 패치
- 단위 테스트 `tests/test_chunker_patterns.py` (synthetic 별표2 본문 + 정상 시행령 본체 본문 둘 다 커버)
- 별표2 인덱스 메트릭 before/after (article_no 분포, 청크 수, 텍스트 길이 합계)

**완료 조건**:
- 단위 테스트 통과
- 별표2 청크 수 ≥ 5, 키워드 5종 모두 별표2 청크에 포함
- 시행령 본체 회귀 없음 (제N조 분할 정상)

---

### Phase G2 — 별표 파일 라우팅 (옵션 A) ✅ 완료

**입력**:
- G1 통과 후 chunker.py
- 별표 파일 목록: 시행령 별표1·2·4·5·6·7 (총 6개, source_file에 `[별표` 패턴 포함)

**작업**:
- `chunk_document` 진입부에서 `source_file` 매칭 (`[별표\s*\d+]` 정규식)
- 매칭 시 `_split_by_articles` 우회, 대신 `1./가./1)/2)` 마커로만 sub-split (FAQ 분기와 유사)
- 별표 파일 청크는 `article_no='별표 N'` 일관 부착 (N은 source_file에서 추출)

**검증**:
- 별표 6개 파일 재인덱싱
- 청크 메트릭: article_no 분포 (모두 `별표 N` 형태), 청크 수, 길이 분포 (min/median/max)
- 별표6 케이스(F5 회귀 — "참여제한 처분기준 가중·감경") 가중기준 + 감경기준이 같은 또는 인접 청크에 포함되는지 확인
- 별표2 케이스(연구개발비 사용용도) 7개 비목(인건비/학생인건비/연구활동비/연구재료비/위탁/국제공동/연구수당) 각각 청크에 키워드 포함

**산출물**:
- `pipeline/chunker.py` 분기 로직
- 별표 6개 파일 청크 메트릭 표

**완료 조건**:
- 6개 파일 재인덱싱 성공
- 별표6 가중·감경 사유 동일 청크 내 또는 인접(top-3 retrieval) 에 동시 등장
- 별표2 7개 비목 키워드 누락 0

---

### Phase G3 — [폐기 — G4 로 흡수, 2026-04-29]

**폐기 사유**:
- 사용자 결정 (2026-04-29): "그 파일이 안일어났는데 전체를 재인덱싱은 너무 별로임"
- G3 의 본질 목표(G1·G2 chunker 패치 결과를 매뉴얼 PDF·별지 등 *별표 외 문서* 에 반영) 는 G4 의 첫 실행에서 자연스럽게 처리됨 — `chunker_version` 메타 필드 mismatch 로 stale 자동 감지 → 영향 받는 파일만 재인덱싱.
- 따라서 별도 phase 로 두는 가치가 없음. G4 가 "코드 변경 인식 + 파일 변경 인식" 두 축을 모두 처리하도록 재정의됨 (아래 G4 참고).

**산출물 보존**:
- `.planning/pm/phase-g3-eval-spec.md` 의 30 케이스 평가 스펙은 **유지**. 추후 G4 검증 또는 일반 회귀 평가에 재사용. 폐기 금지.

---

### Phase G4 — 증분 sync + 코드 버전 인식 (재정의)

**배경**: 사용자가 PDF/HWP 폴더에 파일을 추가·수정·삭제할 때, 또는 chunker/embedder 코드가 업그레이드될 때 *영향 받는 파일만* 재인덱싱하는 sync 기능. Git 의 working-tree 변경 감지와 유사. G3 가 폐기되며 "G1·G2 산출물을 별표 외 문서에 반영" 작업도 G4 첫 실행에 흡수됨.

**입력**:
- G2 완료된 인덱스 (별표 6개 파일은 chunker_version='G2' 로 마크되어 current 상태)
- 매뉴얼 PDF 1,065 청크 + 별지 92 청크는 chunker_version 미기록 → 첫 실행 시 stale 처리 대상

**작업**:

1. **메타 스키마 확장**:
   `data/metadata/file_hashes.json` 신규 생성, 스키마:
   ```
   {
     "<source_file_absolute_path>": {
       "mtime": float,
       "sha256": str,
       "chunker_version": str,    # 예: "1.2.0" — pipeline/chunker.py 의 CHUNKER_VERSION 상수
       "embedder_version": str,   # 예: "ko-sroberta-multitask"
       "chunk_ids": [str, ...],
       "indexed_at": iso8601
     }
   }
   ```

2. **버전 상수 정의** (backend 자율 결정):
   - `pipeline/chunker.py` 상단에 `CHUNKER_VERSION = "1.2.0"` 추가 (G1·G2 patch 반영 의미)
   - embedder 버전은 현재 사용 모델명 그대로 (`ko-sroberta-multitask`)

3. **신규 모듈 `pipeline/sync.py`**:
   - `scan_changes(roots: list[Path]) -> {"added": [...], "modified": [...], "deleted": [...], "stale_by_code": [...]}`
   - 변경 감지 4축:
     - `added`: hashes.json 에 없는 파일
     - `modified`: mtime 또는 sha256 mismatch
     - `deleted`: hashes.json 에 있지만 디스크에서 사라진 파일
     - `stale_by_code`: 디스크엔 있고 hash 도 같지만 `chunker_version` 또는 `embedder_version` 이 현재 코드 상수와 mismatch
   - `apply_changes(changes)`:
     - `deleted` + `modified` + `stale_by_code`: Qdrant 에서 해당 source_file 의 chunk_id 일괄 삭제
     - `added` + `modified` + `stale_by_code`: 파싱·청킹·임베딩·업서트 → metadata 갱신 (현재 버전 상수 기록)

4. **첫 실행 시 동작 (G3 본질 흡수)**:
   - 기존 인덱스의 별표 6개 파일은 G2 작업 시 `chunker_version='1.2.0'` 로 미리 마킹된다고 가정 (G2 backend 가 hashes.json 초기 채움). 만약 G2 시점에 작성되지 않았다면 sync 첫 실행 시 마이그레이션 단계로 채움.
   - 매뉴얼 PDF 1,065 청크 + 별지 10개 (~92 청크) 는 chunker_version 미기록 → `stale_by_code` 분류 → 자동 재인덱싱.
   - 즉 사용자는 환경설정 페이지의 **"📂 동기화"** 버튼 1회 클릭만으로 G3 가 의도했던 "전체 매뉴얼/별지 재인덱싱" 작업이 자동 수행됨.

5. **UI 통합**:
   - `pages/00_⚙️_환경설정.py` 에 **"📂 동기화"** 버튼 추가 (현재 인덱싱 버튼 옆)
   - 진행률 표시 (Streamlit progress bar — `n/total 파일 처리 중...`)
   - 결과 요약 (added X, modified Y, deleted Z, stale_by_code W, elapsed Xs)

6. **옵션**: 앱 시작 시 자동 sync 토글 (`config.auto_sync_on_start: bool`)

**검증**:
- 동일 파일 재실행 → 0개 변경 (no-op 검증)
- 1개 파일만 mtime 갱신 → 그 파일만 재인덱싱
- 1개 파일 삭제 → Qdrant 에서 그 청크들만 삭제 (다른 청크 영향 없음)
- 신규 1개 파일 추가 → 그 파일만 인덱싱 후 검색 가능
- **버전 인식 케이스**: chunker.py 의 `CHUNKER_VERSION` 을 `"1.2.0"` → `"1.3.0"` 로 변경 후 sync → 모든 파일 stale_by_code 감지 → 재인덱싱
- **첫 실행 시나리오**: 별표 6개는 no-op, 매뉴얼 PDF 1,065청크 + 별지 92청크는 stale_by_code 로 재인덱싱 (예상 처리량 ~1,157청크)

**산출물**:
- `pipeline/sync.py` (신규)
- `pipeline/chunker.py` 의 `CHUNKER_VERSION` 상수 추가
- `data/metadata/file_hashes.json` 스키마 + 마이그레이션 함수 (G2 완료 인덱스로부터 hash + 버전 채우기)
- `pages/00_⚙️_환경설정.py` UI 통합
- 단위 테스트 + sync 6 시나리오 통합 테스트

**완료 조건**:
- 6가지 검증 시나리오 모두 통과 (위 4 + 버전 인식 1 + 첫 실행 1)
- 50개 파일 폴더에서 1개만 변경했을 때 sync 시간 < 30초 (전체 재인덱싱 대비 95% 이상 절약)
- 첫 실행 후 평가 회귀 — `phase-g3-eval-spec.md` 30 케이스 통과율 ≥ 80%, 기존 9/10 baseline 회귀 0건

**의존성**: G2 완료 후 진행. backend 위임 브리프는 `.planning/pm/phase-g4-backend-brief.md`.

---

### Phase G5 — MCP 동기화 (korean-law-mcp + hwp-mcp)

**배경**: 외부 MCP 서버(korean-law-mcp, hwp-mcp) 가 업데이트되면 도구 스키마가 바뀌거나 새 도구가 추가될 수 있음. 현재는 호출 실패 시 에러를 그대로 노출하고 사용자에게 책임 떠넘김. MCP 서버 변경에 자동 적응할 필요.

**트리거 조건**:
- G4 완료 후 진행
- F1, F2 보다 우선순위 높음 (사용자 결정, 2026-04-29)

**작업**:

1. **korean-law-mcp 스키마 probe**:
   - 앱 시작 시 또는 사이드바 새로고침 버튼 클릭 시 `list_tools()` 호출
   - 반환된 tool 스키마를 `data/metadata/mcp_schema_cache.json` 에 저장
   - retriever / answerer 가 호출 전 cache 를 조회해 인자명·필수 필드 변경에 대응
   - 스키마 변경 감지 시 환경설정 페이지에 "MCP 도구 스키마가 업데이트되었습니다" 배지

2. **hwp-mcp pip 업데이트 체커**:
   - 백그라운드로 `pip index versions hwp-mcp` (또는 PyPI JSON API) 호출
   - 현재 설치 버전과 비교해 신규 버전 존재 시 환경설정 페이지에 알림
   - 사용자 클릭 시 `pip install -U hwp-mcp` 실행 (확인 모달)

3. **호출 실패 자동 fallback**:
   - MCP 호출 실패(ToolError, timeout, 연결 실패) 시:
     - 1회 자동 재시도 (백오프 0.5초)
     - 재시도 실패 시 검색 결과 source_type 에서 해당 채널 제외하고 진행
     - 답변 카드 하단에 "법제처 MCP 일시 장애로 외부 검색 미수행" 경고 배지
   - 연속 5회 실패 시 해당 채널 자동 비활성 (사이드바 토글에 disabled 표시 + 사유 툴팁)

**검증**:
- 스키마 cache 파일 정상 생성/갱신
- 의도적으로 mcp 서버 다운 → fallback 동작 + 배지 표시
- 5회 연속 실패 → 자동 비활성 토글
- pip 신규 버전 시뮬레이션 → 알림 배지 표시

**산출물**:
- `pipeline/mcp_sync.py` (신규) — schema probe + pip 체커
- `pipeline/answerer.py` 의 MCP 호출 부에 retry/fallback 래퍼
- `pages/00_⚙️_환경설정.py` 의 MCP 섹션 확장
- 단위 테스트 (mock mcp 서버)

**완료 조건**:
- 4 가지 검증 시나리오 모두 통과
- mcp 서버 장애 시 답변 자체는 vector-only 로 정상 응답 (degrade gracefully)

**의존성**: G4 완료 후. 우선순위는 F1·F2 보다 위.

---

## Phase G 시리즈 진행 규칙

- 진행 순서: G1 ✅ → G2 ✅ → G4 → G5. (G3 폐기됨)
- PM 이 각 단계 완료 보고 받은 후 다음 단계 위임.
- 코드 수정은 backend agent 만 담당. PM 직접 수정 금지.
- 각 단계는 독립 커밋.
- backend 위임 브리프 위치:
  - G1: `.planning/pm/phase-g1-backend-brief.md` (보존)
  - G3: `.planning/pm/phase-g3-eval-spec.md` (폐기되었으나 평가 스펙은 G4 검증·회귀에 재사용 — 보존)
  - G4: `.planning/pm/phase-g4-backend-brief.md` (신규)
  - G5: 미작성 — G4 완료 후 PM 이 작성

---

## Phase H — Hybrid Agent-style Document Access

작성일: 2026-04-29
배경 메모: 사용자 결정 (2026-04-29) — chunker 페이지 태깅·청크 경계 버그가 끝없이 회귀(1.3.0~1.3.3 4회 패치, 매번 전체 sync 6~10분). 페이지/조문 직접 조회 케이스는 Pre-index 인덱싱의 한계 자체가 원인. Claude 가 PDF/HWP 를 *실시간으로 읽어 응답*하는 Agent-style 패턴(참고: Agent_team 프로젝트, Claude Code MCP 패턴)을 부분 도입한다.

전면 Agent-style 은 비용·시간·구독 한도 부담(쿼리당 응답시간 3~6배, 한도 빠르게 소진) 때문에 **Hybrid** 채택.

### 라우팅 정책

| 질의 종류 | 라우팅 | 비고 |
|---|---|---|
| 일반 토픽 ("연구노트 보존기간") | 현재대로 Qdrant 벡터 | 빠름·저렴, 회귀 0 |
| **페이지 직접** ("151p 알려줘") | **로컬 MCP `read_page(N)` 직접 호출** | chunker 의존 제거 |
| **조문 직접** ("제15조") | 로컬 MCP `get_article("제15조")` | 본문 + 페이지 범위 |
| **FAQ 번호 직접** ("FAQ 27번") | 로컬 MCP `search_text` 또는 전용 도구 | 정확 일치 |
| 비교형 / 다중조문 | 현재대로 Qdrant + Opus escalate | F1 라우팅 활용 |

### 입력

- G4 sync 후 안정된 인덱스 (토픽 검색 폴백 경로로 유지)
- 기존 `pipeline/pdf_parser.py`, `pipeline/hwp_parser.py` (재사용)
- `claude-agent-sdk` (이미 답변 경로에 사용 중)

### 작업

1. **신규 모듈 `pipeline/local_doc_mcp.py`**:
   - MCP 서버 (FastMCP 또는 자체 구현)
   - 도구 5종:
     - `list_documents() -> list[dict]` — 인덱싱된 모든 문서 목록 (config 의 `pdf_dir`/`hwp_dir` 스캔)
     - `read_page(doc_name: str, page_num: int) -> str` — PDF 인덱스 N 페이지 텍스트
     - `search_text(doc_name: str, query: str, max_results: int = 5) -> list[dict]` — 키워드 검색 (위치/페이지 포함)
     - `get_article(doc_name: str, article_no: str) -> dict` — 조문 본문 + 시작/끝 페이지
     - `list_articles(doc_name: str) -> list[dict]` — 모든 article_no 목록
   - PDF: pdfplumber 즉석 호출 (캐시: file mtime 기준 LRU)
   - HWP: hwp-mcp 통한 텍스트 추출 (현재 `hwp_parser` 재사용)

2. **answerer.py 도구 호출 경로 추가**:
   - `kind=page_lookup` 또는 `kind=article_lookup` 시 `allowed_tools=["read_page", "get_article", "search_text", "list_articles", "list_documents"]` 활성화
   - `max_turns=3~5` 로 multi-turn 허용
   - JSON 출력 강제 그대로 유지
   - 도구 호출 trace 는 stderr 로그로 가시화

3. **retriever 통합 / 라우팅**:
   - `query_analyzer` 가 `kind=page_lookup` 으로 분류 시 retriever 우회 또는 보조용으로만 호출
   - `kind=article_lookup` 도 동일 처리
   - `app.py` 에서 라우팅 분기 (Qdrant vs Agent-style)

### 검증

- "151p 알려줘" → Claude 가 `read_page(매뉴얼, 151)` 호출 → 정확한 콘텐츠 반환
- "제15조" → `get_article` 호출 → 본문 + 페이지 범위
- 일반 토픽 ("연구노트 보존기간") → 기존 Qdrant 경로 그대로 (회귀 0)
- 응답 시간: 페이지 조회 ~15~30초, 조문 조회 ~10~20초, 토픽 변화 없음

### 산출물

- `pipeline/local_doc_mcp.py`
- `tests/test_local_doc_mcp.py`
- `pipeline/answerer.py` 분기 로직 (도구 호출 경로)
- 라우팅 테스트 케이스 (페이지/조문/토픽 각 1건씩)

### 완료 조건

- 페이지 직접 조회 케이스 100% 정확 (151p → 진짜 151p 콘텐츠)
- 토픽 회귀 0건 (기존 평가셋 30/31 유지)
- 응답 시간 페이지 조회 30초 이내

### 의존성

- G4 완료 후 진행 권장 (sync 안정된 인덱스가 토픽 폴백 경로)
- F1·F2·G5 와 우선순위 비교: 사용자 일상 사용에서 페이지/조문 직접 조회 빈도가 높으므로 **G4 직후 우선 진행** 가능

### 위임

- backend 위임 브리프: `.planning/pm/phase-h-backend-brief.md`
