# 연구행정 보수형 RAG 챗봇 — 개발 진행이력

> 최종 업데이트: 2026-04-24 | 현재 버전: v0.9.1

---

## 프로젝트 한 줄 정의

중소기업 R&D 연구행정 PDF를 업로드하면, 질문에 대해 관련 조문·지침·공고를 검색해서  
**근거와 함께** 답하는 로컬 RAG 챗봇. 근거 없으면 답하지 않는다.

---

## 현재 시스템 상태 (v0.9 기준)

| 항목 | 상태 |
|------|------|
| 인덱싱된 문서 | **137개** (HWP 변환 포함 + 매뉴얼) |
| Qdrant 청크 수 | **5,091개** |
| 임베딩 모델 | jhgan/ko-sroberta-multitask (768차원) |
| LLM | claude-sonnet-4-5 (Anthropic API) |
| 법제처 MCP | 연결됨 (korean-law-mcp.fly.dev) |
| UI | Streamlit 4페이지 완성 |
| 엔드투엔드 테스트 | API 크레딧 있음 — 실행 가능 |

---

## 전체 아키텍처

```
[사용자 질문]
      │
      ▼
① 임베딩 생성
   jhgan/ko-sroberta-multitask → 768차원 벡터
      │
      ├──▶ ② Qdrant 벡터 검색 (top-5, doc_type 필터)
      │         └ 3,206개 청크에서 유사 조문 검색
      │
      └──▶ ③ 법제처 MCP 보완 (선택)
                └ korean-law-mcp.fly.dev → 공식 법령 검색 결과 추가
      │
      ▼
④ Claude API (tool_use)
   보수형 시스템 프롬프트 + 검색 근거 전달
   → AnswerPayload 구조화 출력 강제
      │
      ▼
⑤ Streamlit UI 렌더링
   verdict 배지 / Confidence Score / 근거 출처 / 원문 토글 / Caution 블록
```

---

## 개발 단계별 이력

### Phase 1 — 파이프라인 구축 (2026-04-23)

**목표:** PDF → 벡터 DB 적재까지 검증

| 버전 | 작업 내용 |
|------|-----------|
| v0.1 | PDF 파싱(`pdfplumber`), 조문 단위 청크 분할, 임베딩, Qdrant 적재 초기 구현 |
| v0.7 | Ollama → `sentence-transformers` 교체, Docker → Qdrant 로컬 파일 모드 전환, Phase 1 검증 완료 ✅ |

**완료 결과:**
- `pipeline/pdf_parser.py` — 페이지별 텍스트 추출, 머리말/꼬리말 제거, OCR 감지
- `pipeline/chunker.py` — 제N조 정규식 기반 조문 단위 분할, FAQ 1문1답 분할
- `pipeline/embedder.py` — 768차원 벡터 생성
- `pipeline/indexer.py` — Qdrant upsert (배치 100개)

---

### Phase 2 — Claude 답변 + MCP 연동 (2026-04-23 ~ 24)

**목표:** 검색 결과 → 구조화된 답변 생성

| 버전 | 작업 내용 |
|------|-----------|
| v0.2 | `pipeline/schemas.py` — Pydantic v2 AnswerPayload 스키마 정의 |
| v0.3 | `pipeline/prompts.py` — 보수형 시스템 프롬프트 (근거 외 추론 금지, 판단불가 처리 기준) |
| v0.4 | `pipeline/answerer.py` — Claude API `tool_use`로 AnswerPayload 강제 출력 |
| v0.5 | `pipeline/retriever.py` — Qdrant `query_points`, doc_type 필터 |
| v0.6 | `answer_cli.py` — 임베딩 → 검색 → Claude 답변 통합 CLI (터미널 테스트용) |
| v0.8 | `pipeline/korean_law_client.py` — 법제처 MCP 연동, `answer_cli.py` 4단계 파이프라인 확장 |

**AnswerPayload 구조:**
```json
{
  "verdict": "가능 | 불가 | 조건부 가능 | 판단불가",
  "summary": "한 줄 결론",
  "citations": [
    { "document_name": "문서명", "article_no": "제N조", "page": 12, "quote": "원문 50자" }
  ],
  "follow_up_needed": true,
  "follow_up_questions": ["전담기관 PM 확인 필요 사항"],
  "risk_notes": ["주의사항"]
}
```

**보수형 프롬프트 핵심 원칙:**
- 검색된 근거 외 추론·추정 금지
- 근거 불충분 시 `판단불가` 강제
- 문서 간 충돌 시 양쪽 조문 명시
- 최종 판단은 항상 PM·전담기관 확인 권고

---

### 특별 작업 — HWP 일괄 변환 (2026-04-24)

연구행정 문서 대부분이 HWP 형식이어서 별도 변환 로직 구현.

| 파일 | 설명 |
|------|------|
| `convert_hwp_to_pdf.py` | HWP 3종 형식 자동 감지 + fpdf2로 PDF 변환 |
| `batch_ingest.py` | PDF 폴더 일괄 인덱싱 (완료 파일 추적으로 재실행 안전) |

**HWP 형식 3종 처리:**
| 형식 | 감지 방법 | 변환 방법 |
|------|-----------|-----------|
| HWPX (ZIP 기반) | `PK` 매직 바이트 | ZIP 열어 `PrvText.txt` 또는 section XML 파싱 |
| HWP5 (OLE2 이진) | `D0CF` 매직 바이트 | zlib 해제 + tag_id=67 UTF-16LE 추출 |
| XML HWP | `<?xml` 헤더 | LibreOffice headless 변환 |

**결과:** 131개 HWP → PDF 변환 완료, 136개 PDF 전체 인덱싱 → **3,206 포인트**

---

### Phase 3 — Streamlit UI (2026-04-24)

**목표:** 비개발자도 5분 내 사용 가능한 전문가급 인터페이스

| 버전 | 작업 내용 |
|------|-----------|
| v0.9 | Streamlit 4페이지 + UI 컴포넌트 전체 구현 |

#### 페이지 구성

**① 메인 채팅 (`app.py`)**
- `st.chat_message` 기반 대화 히스토리 (세션 내 유지)
- 사이드바: 문서유형 필터, MCP 토글, PDF 업로드·즉시 인덱싱, Qdrant 포인트 수
- Quick Prompt 칩 3개 (학생인건비 / 간접비 / 회의비) — 클릭 시 즉시 처리
- 감사 로그 자동 저장 (`data/audit_log.jsonl`)

**② 문서 라이브러리 (`pages/01_Library.py`)**
- `data/metadata/*.csv` 기반 인덱싱 문서 목록
- 문서명 검색, 유형 필터, 청크 수·페이지 수·시행일 표시
- 현행/구버전 배지

**③ 이용 통계 (`pages/02_Analytics.py`)**
- KPI: 총 질문 수, 오늘 질문 수, MCP 활용률, 추가확인 필요율
- 일별 질문 수 꺾은선 차트
- 판단 결과 분포 막대 차트
- Confidence Score 구간별 분포

**④ 감사 로그 (`pages/03_Audit.py`)**
- 전체 질의 이력 역순 표시 (시각·질문·판단·Confidence·근거 수)
- 검색 필터 + 판단 결과 필터
- CSV 내보내기 버튼

#### 답변 카드 구조
```
┌─────────────────────────────────────────────────────┐
│  ✅ 가능   Confidence 72.4%                          │
├─────────────────────────────────────────────────────┤
│  연구활동비 소프트웨어 활용비 항목으로 집행 가능합니다. │
├─────────────────────────────────────────────────────┤
│  📚 GROUNDS & SOURCES                               │
│  [📄 국가연구개발혁신법 시행령 / 별표2 | p.0]         │
├─────────────────────────────────────────────────────┤
│  ❗ CRITICAL CAUTION                                │
│  전담기관 또는 담당 PM의 최종 확인이 필요합니다.      │
├─────────────────────────────────────────────────────┤
│  📖 VIEW ORIGINAL TEXT  ▼                           │
│  ┌─────────────────────────────────────────────┐   │
│  │ [국가연구개발혁신법 시행령 별표2]              │   │
│  │ 마) 소프트웨어 활용비: 소프트웨어의 구입·...   │   │
│  └─────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────┘
```

---

## 파일 구조 (현재)

```
Chat-bot/
│
├── app.py                          ← Streamlit 메인 (채팅)
├── answer_cli.py                   ← 터미널 테스트용 CLI
├── batch_ingest.py                 ← PDF 일괄 인덱싱
├── convert_hwp_to_pdf.py           ← HWP → PDF 변환
├── .env                            ← API 키, 경로 설정
├── PROGRESS.md                     ← 이 파일
│
├── pipeline/
│   ├── pdf_parser.py               ← PDF → 텍스트
│   ├── chunker.py                  ← 조문 단위 청크 분할
│   ├── embedder.py                 ← 768차원 벡터 생성
│   ├── indexer.py                  ← Qdrant upsert
│   ├── retriever.py                ← 벡터 검색
│   ├── answerer.py                 ← Claude API 구조화 답변
│   ├── schemas.py                  ← AnswerPayload Pydantic 모델
│   ├── prompts.py                  ← 시스템 프롬프트
│   └── korean_law_client.py        ← 법제처 MCP 클라이언트
│
├── pages/
│   ├── 01_Library.py               ← 문서 라이브러리
│   ├── 02_Analytics.py             ← 이용 통계
│   └── 03_Audit.py                 ← 감사 로그
│
├── ui/
│   ├── styles.py                   ← 전체 CSS
│   └── components.py               ← UI 컴포넌트 함수
│
├── data/
│   ├── metadata/                   ← 136개 문서 메타데이터 CSV
│   ├── raw/                        ← PDF 원문 텍스트
│   ├── ingest_done.txt             ← 인덱싱 완료 파일 목록
│   └── audit_log.jsonl             ← 질의 이력 로그
│
├── qdrant_storage/                 ← 벡터 DB (3,206 포인트)
│
└── versions/
    ├── CHANGELOG.md                ← 버전 인덱스
    ├── PROGRESS.md                 → (루트의 이 파일 참조)
    └── v0.1 ~ v0.9/
        ├── RELEASE_NOTES.md
        └── files/                  ← 버전별 파일 스냅샷
```

---

## 환경 변수 (.env)

```env
ANTHROPIC_API_KEY=...              # Claude API 키
QDRANT_PATH=./qdrant_storage       # Qdrant 로컬 경로
QDRANT_COLLECTION=rnd_law_chunks   # 컬렉션 이름
CLAUDE_MODEL=claude-sonnet-4-5     # 사용 모델
TOP_K=5                            # 검색 결과 수
KOREAN_LAW_OC=tlathdus30214        # 법제처 MCP 인증키
KOREAN_LAW_MCP_URL=https://korean-law-mcp.fly.dev/mcp
```

---

## 실행 방법

```bash
# UI 실행
cd C:\Users\ssy49\Desktop\Chat-bot
streamlit run app.py

# CLI 테스트 (API 크레딧 필요)
python answer_cli.py --query "학생인건비로 노트북 구매 가능한가요?"

# 새 PDF 일괄 인덱싱
python batch_ingest.py
```

---

## 다음 개발 단계 (Phase 4)

| 우선순위 | 기능 | 설명 |
|----------|------|------|
| 높음 | 엔드투엔드 검증 | 테스트 질문 10개 정확도 측정 |
| 중간 | 하이브리드 검색 | BM25 + Dense 병행 (제N조, 별표2 등 정확 매칭 강화) |
| 중간 | 법령 버전 관리 UI | is_current 토글로 구버전 조회 |
| 낮음 | 판단 유보 자동 감지 | 근거 2개 미만 시 강제 판단불가 처리 |

---

*spec.md v1.0 / plan.md v1.0 기반 | 작성: Claude Sonnet 4.6*
