# 연구행정 RAG 챗봇

한국 연구행정 PDF·HWP 매뉴얼·법령(국가연구개발혁신법 등) 기반 보수형 RAG 챗봇.
근거 없으면 답하지 않고, 출처(문서명·조문·페이지)를 표시한다.

- **Streamlit web UI** (현재) / 윈도우 네이티브 배포 옵션 검토 중
- **Claude Code OAuth** 인증 (Anthropic API 키 미사용)
- **Hybrid 검색**: Qdrant 벡터(의미 검색) + Phase H 도구(`read_page` / `get_article` / `search_text`)
- **2 MCP 통합**: 법제처 (`korean-law-mcp`) + HWP (`hwp-mcp`)

---

## 1. 셋업 (다른 PC 첫 설치)

### 1-1. 시스템 의존성

| 항목 | macOS | Windows |
|---|---|---|
| Python 3.12+ | `brew install python@3.12` | python.org installer |
| Tesseract OCR + 한국어 | `brew install tesseract tesseract-lang` | tesseract installer + kor.traineddata |
| Node.js (Claude CLI 용) | `brew install node` | nodejs.org installer |

### 1-2. Claude Code CLI

```bash
npm install -g @anthropic-ai/claude-code
claude   # 브라우저로 OAuth 로그인 — Pro/Max 구독 필요
```

### 1-3. 프로젝트 셋업

```bash
git clone <repo-url>
cd chatbot

# 가상환경
python3.12 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 1-4. 데이터 파일 배치 (gitignore 됨, 별도 다운로드)

다음 파일들을 프로젝트 루트에 배치:

- `[본권] 25년도 국가연구개발혁신법 매뉴얼_배포용.pdf` — 매뉴얼 PDF
- `국가연구개발혁신법 시행령(...)/` — HWP 별표 파일들
- `국가연구개발혁신법 시행규칙(...)/` — HWP 별지 파일들
- (선택) `국가연구개발혁신법(법률)(...).hwp` — HWPML 본체

### 1-5. 환경설정 파일

```bash
cp data/config.example.json data/config.json
```

`data/config.json` 편집:

```json
{
  "korean_law_oc": "본인OC키",     # 법제처 발급 OC 코드
  "pdf_dir": ".",                  # PDF 검색 루트
  "hwp_dir": ""                    # HWP 폴더 (비우면 프로젝트 루트만 스캔)
}
```

### 1-6. 첫 실행 + 인덱싱

```bash
streamlit run app.py --server.port 8501
```

브라우저 자동 오픈. **환경설정 페이지 → "📂 동기화" 클릭** → 6~10분 인덱싱.

---

## 2. 사용

### 2-1. 일반 토픽 질의

```
연구노트 보존기간이 왜 30년인가?
학생인건비로 노트북 살 수 있어?
간접비 비율 한도?
```

→ Qdrant 벡터 검색 → 답변 (~1~1.5분)

### 2-2. 페이지 직접 조회

```
국가연구개발혁신법 151p 알려줘
혁신법 매뉴얼 222쪽 내용
```

→ Phase H `read_page` 도구 호출 → 답변 (~2분)

### 2-3. 조문 직접 조회

```
혁신법 시행령 제15조 본문
제32조 시행일
```

→ Phase H `get_article` 도구 호출 → 답변 (~2분)

### 2-4. 비교형

```
혁신법으로 달라진 점
종전 vs 혁신법
```

→ Qdrant 검색 + Opus 4.6 자동 escalate → 답변 (~2분)

### 2-5. 일상 대화

```
안녕하세요, 너 누구야, 고마워
```

→ 분석기가 즉답 (검색·생성 스킵)

---

## 3. 디렉토리 구조

```
chatbot/
├── app.py                          # Streamlit 메인
├── answer_cli.py                   # CLI 진입 (테스트용)
├── batch_ingest.py                 # 일괄 인덱싱
├── pages/
│   └── 00_⚙️_환경설정.py            # 환경설정 / 동기화 / MCP 상태
├── pipeline/                       # 핵심 로직
│   ├── auth.py                     # Claude Code OAuth 검증
│   ├── pdf_parser.py               # pdfplumber + OCR
│   ├── hwp_parser.py               # hwp-mcp 통신
│   ├── chunker.py                  # 의미 단위 청킹 (article/page 라우팅)
│   ├── embedder.py                 # ko-sroberta-multitask
│   ├── retriever.py                # Qdrant 벡터 + 부스트
│   ├── query_analyzer.py           # Claude 의도 분석 (kind 라우팅)
│   ├── answerer.py                 # claude-agent-sdk 답변 생성
│   ├── local_doc_mcp.py            # Phase H 로컬 문서 도구
│   ├── korean_law_client.py        # 법제처 MCP
│   ├── mcp_sync.py                 # MCP 헬스체크 / 업데이트
│   ├── sync.py                     # 증분 동기화 (file_hashes 기반)
│   └── prompts.py                  # 시스템 프롬프트
├── ui/
│   └── components.py               # 답변 카드, 신뢰도, 컨텍스트 표시
├── scripts/                        # 유틸 / 평가
│   ├── eval_full.py                # 종합 평가셋 (39 케이스)
│   ├── eval_retrieval.py           # 회귀 비교 헬퍼
│   ├── coverage_report.py          # 인덱스 품질 진단
│   └── reindex_*.py                # 부분 재인덱싱 도구
├── tests/                          # 단위 테스트
├── data/                           # 로컬 데이터 (대부분 gitignore)
│   ├── config.example.json         # 환경설정 템플릿 (커밋 됨)
│   ├── config.json                 # 본인 환경설정 (gitignore)
│   ├── chunks/                     # 청킹 산출물 (gitignore)
│   ├── metadata/file_hashes.json   # sync 베이스라인 (gitignore)
│   └── audit_log.jsonl             # 질의 로그 (gitignore)
├── qdrant_storage/                 # 벡터 DB (gitignore, ~12MB)
├── .claude/agents/                 # 4-에이전트 팀 정의 (PM/Backend/Frontend/Scout)
└── .planning/                      # PM 산출물 (로드맵 / 브리프 / 리서치)
```

---

## 4. 4-에이전트 개발팀

`.claude/agents/` 안에 정의된 페르소나로 멀티-에이전트 협업:

- **PM** — 요구사항·로드맵·페이즈 분할·우선순위
- **Backend** — RAG 파이프라인·MCP·Qdrant·평가
- **Frontend** — Streamlit UI·인용 표시·디자인
- **Scout** — 외부 사례·라이브러리·아키텍처 패턴 발굴

---

## 5. 로드맵 / 알려진 이슈

자세한 내용은 `.planning/roadmap-future.md` 참고.

**진행 중**:
- Phase H — Hybrid 도구 모드 (구현 완료, 운영 검증 중)

**대기**:
- HWPML 본체 3종 파싱 (혁신법/시행령/시행규칙 본문) — XML 직접 파서 필요
- F2 — 윈도우 네이티브 배포 (PyInstaller / 셋업 스크립트)
- F4 — 답변 속도 최적화 (스트리밍, 캐시, 프롬프트 캐싱)

---

## 6. 트러블슈팅

| 증상 | 해결 |
|---|---|
| `ModuleNotFoundError: claude_agent_sdk` | `pip install claude-agent-sdk` |
| `Claude Code 로그인 필요` | `claude` 실행 후 브라우저 OAuth |
| OCR 페이지 한국어 깨짐 | `brew install tesseract-lang` (kor 팩) |
| 동기화 무한 대기 | Streamlit 종료 후 다시 클릭 (Qdrant 동시성 충돌 가능) |
| 답변에 페이지 표시 안 됨 | 페이지 직접 조회는 Phase H 도구 호출 — 1.4.0 이후 정상 |

---

## 7. 라이선스

내부 프로젝트. 사용 문서(국가연구개발혁신법 등)는 정부 공개 자료(공공누리/CC-BY).
