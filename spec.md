# 연구행정 보수형 RAG 챗봇 — Project Spec

## 1. 프로젝트 개요

### 한 줄 정의
중소기업 R&D 연구행정 PDF를 업로드하면, 질문에 대해 관련 조문·지침·공고를 검색해서 **근거와 함께** 답하는 로컬 챗봇

### 핵심 원칙
- 근거 없으면 답하지 않는다
- 추정하지 않는다
- 반드시 출처(문서명 + 조문번호 + 페이지)를 표시한다
- 판단이 불확실하면 "확인 필요"로 유보한다

---

## 2. 타겟 유저

- 중소기업 R&D 담당자 (연구비 집행 판단이 필요한 사람)
- 연구책임자 (비목별 집행 가능 여부 확인)
- 사업기획팀 선임 (혁신법·운영요령 조문 검색 및 대응)

### 주요 질문 유형 예시
- "학생인건비로 노트북 구매 가능한가요?"
- "간접비 비율 초과 시 어떻게 처리하나요?"
- "연구개발비 이월 신청은 언제까지 해야 하나요?"
- "제13조 제2항 전문을 보여주세요"
- "별표 2 직접비 항목 기준이 뭔가요?"

---

## 3. 기술 스택

| 역할 | 선택 | 이유 |
|------|------|------|
| IDE | Google Antigravity | 에이전트 기반 바이브코딩, 구현계획 자동생성 |
| UI | Streamlit | 로컬 MVP에 최적, Python 기반 빠른 구성 |
| LLM | Claude API (claude-sonnet-4-20250514) | 한국어 법령 조문 해석 품질 최고 |
| 임베딩 | sentence-transformers (jhgan/ko-sroberta-multitask) | 한국어 특화 임베딩, pip 설치, 로컬 실행 |
| 벡터DB | Qdrant (로컬 파일 모드) | pip 설치, Docker 불필요, 하이브리드 검색 지원 |
| PDF 파싱 | pdfplumber | 한국어 표·조문 구조 보존에 강함 |
| RAG 프레임워크 | LangChain | RAG 파이프라인 표준, 문서 분할·검색 모듈 풍부 |
| 언어 | Python 3.11+ | |
| 패키지 관리 | pip + requirements.txt | |

---

## 4. 입력 문서 범위

### 우선순위 순서 (답변 시 이 순서로 우선 적용)
1. 해당 사업 공고문
2. 부처 운영요령
3. 시행규칙
4. 시행령
5. 국가연구개발혁신법 본문
6. FAQ / 질의회신
7. 내부 운영 가이드

### 최소 구성 문서
- 국가연구개발혁신법
- 시행령
- 시행규칙
- 연구개발비 사용 관련 기준
- 부처별 운영요령
- 사업 공고문
- FAQ / 질의회신
- 내부 운영 가이드

---

## 5. 데이터 파이프라인

### 5-1. PDF 파싱
```
PDF 업로드
  → pdfplumber로 텍스트 추출
  → 머리말/꼬리말 제거
  → 페이지 번호 보존
  → 표/별표/부칙 분리 처리
  → OCR 필요 여부 감지 (스캔 PDF는 pytesseract 사용)
  → raw_text.txt 저장
```

**주의사항**
- 조문 번호 깨짐 방지 최우선
- 별표 누락 시 가능/불가 판단 오류 발생
- 표 구조 보존 필수 (비목 기준 왜곡 방지)

### 5-2. 조문 단위 청크 분할
일반 500자 고정 분할 금지. 반드시 조문 구조 단위로 분할.

**분할 단위 (정규식 기반)**
```python
patterns = [
    r'제\d+조',      # 조
    r'제\d+항',      # 항
    r'제\d+호',      # 호
    r'별표\s*\d*',   # 별표
    r'부칙',         # 부칙
]
# FAQ는 1문1답 단위로 분할
```

**출력 파일**
- `chunks.json` — 분할된 청크 전체
- `metadata.csv` — 청크별 메타데이터

### 5-3. 메타데이터 스키마
각 청크에 반드시 부착:

```json
{
  "chunk_id": "uuid",
  "doc_name": "국가연구개발혁신법",
  "doc_type": "법률|시행령|시행규칙|운영요령|공고문|FAQ|가이드",
  "article_no": "제13조",
  "article_title": "연구개발비의 사용",
  "page": 12,
  "effective_date": "2024-01-01",
  "revised_date": "2023-12-15",
  "is_current": true,
  "source_file": "혁신법_2024.pdf",
  "text": "원문 텍스트"
}
```

### 5-4. 임베딩 및 벡터DB 적재
```
chunks.json 로드
  → sentence-transformers (jhgan/ko-sroberta-multitask)로 임베딩 생성
  → Qdrant 로컬 파일 모드(./qdrant_storage)에 적재
  → payload에 메타데이터 전체 저장
```

---

## 6. 검색 전략

### MVP: Dense Search (의미 유사도)
- 질문 임베딩 → Qdrant top-k 검색 (k=5)
- 메타데이터 필터 지원 (문서종류, 연도, 부처)

### 이후 단계: 하이브리드 검색
- Dense search + BM25 Sparse search 병행
- 이유: "학생인건비통합관리", "제13조", "별표 2", "직접비" 같은 정확 단어 매칭 필요
- Rerank 적용으로 최종 정렬

---

## 7. 답변 생성

### 프롬프트 규칙 (시스템 프롬프트에 고정)
```
당신은 국가연구개발혁신법 및 연구행정 지침 전문 검색 도우미입니다.

[필수 규칙]
1. 제공된 문서 조각(context) 외의 내용은 절대 추정하지 마세요.
2. 근거가 없으면 반드시 "현재 검색된 자료만으로는 판단하기 어렵습니다"라고 답하세요.
3. 모든 답변에 반드시 문서명, 조문번호, 페이지를 포함하세요.
4. 상충하는 규정이 있으면 두 조문을 모두 보여주고 우선순위를 안내하세요.
5. 공고문이 없으면 "해당 사업 공고문 확인이 필요합니다"라고 명시하세요.
6. 최종 판단은 반드시 "전담기관 또는 담당 PM 최종 확인 필요"를 붙이세요.
```

### 답변 출력 형식 (Structured Output)
```json
{
  "conclusion": "가능 | 불가능 | 조건부 가능 | 판단 불가",
  "summary": "한 줄 결론",
  "grounds": [
    {
      "doc_name": "문서명",
      "article_no": "제n조 제n항",
      "page": 12,
      "excerpt": "관련 원문 발췌 (50자 이내)",
      "explanation": "이 조문이 왜 관련되는지 설명"
    }
  ],
  "conflicts": "상충 조문 있을 시 설명, 없으면 null",
  "missing_docs": "판단에 필요하지만 없는 문서 목록",
  "caution": "추가 확인 필요사항"
}
```

---

## 8. UI 구성 (Streamlit)

### 화면 레이아웃
```
[사이드바]
- PDF 업로드 버튼
- 인덱싱 시작 버튼
- 문서 목록 (업로드된 파일)
- 필터: 문서종류 / 연도 / 부처

[메인 영역]
- 질문 입력창
- 답변 카드
  ├── 결론 (가능/불가/조건부/판단불가 배지)
  ├── 한 줄 요약
  ├── 근거 목록 (문서명 + 조문 + 페이지 + 원문 발췌)
  ├── 주의사항
  └── 원문 보기 버튼 (해당 페이지 발췌 토글)
```

### 필수 UI 요소
- PDF 업로드 → 인덱싱 진행률 표시
- 답변 결론에 색상 배지 (초록/빨강/노랑/회색)
- 근거 원문 토글 (접었다 펼치기)
- 질문 히스토리 (세션 내 유지)

---

## 9. 버전 관리

법령은 개정됩니다. 반드시 구현:
- 각 문서에 `effective_date`, `revised_date`, `is_current` 저장
- 동일 법령 구버전 보관 (삭제 금지)
- 검색 시 기본값: `is_current=true` 필터
- 구버전 조회 옵션 제공

---

## 10. 판단 유보 조건

아래 경우 LLM이 결론을 내지 않고 유보:
- 검색된 근거 청크가 2개 미만
- 문서 간 내용 충돌
- 공고문 없이 사업별 판단 필요
- 내부기관 자체 기준이 필요한 질문
- 법률 해석 수준으로 넘어가는 질문

---

## 11. 구현 단계 (MVP 우선순위)

### Phase 1 — 파이프라인 검증 (1주차)
- [ ] sentence-transformers 설치 및 jhgan/ko-sroberta-multitask 모델 다운로드
- [ ] Qdrant 로컬 파일 모드 초기화 (pip, Docker 불필요)
- [ ] PDF 1개 (관리지침 본문)로 파싱 → 청크 → 임베딩 → 적재 테스트
- [ ] 질문 1개 입력 → 검색 → 콘솔 출력 확인

### Phase 2 — 답변 품질 확보 (2주차)
- [ ] Claude API 연결
- [ ] 보수형 시스템 프롬프트 적용
- [ ] Structured Output 구현
- [ ] 테스트 질문 10개 정확도 검증

### Phase 3 — UI 완성 (3주차)
- [ ] Streamlit UI 구성
- [ ] PDF 업로드 + 인덱싱 UI
- [ ] 답변 카드 + 원문 토글
- [ ] 문서 필터 기능

### Phase 4 — 안정화 (4주차)
- [ ] 하이브리드 검색 (BM25 추가)
- [ ] 버전 관리 로직
- [ ] 판단 유보 자동 감지
- [ ] 오답 케이스 수집 및 프롬프트 개선

---

## 12. 자주 틀리는 지점 (사전 주의)

| 위험 포인트 | 대응 방법 |
|-------------|-----------|
| PDF 파싱 깨짐 (조문번호 사라짐) | pdfplumber + 정규식 파싱 후 chunks.json 육안 확인 필수 |
| chunk 경계 오류 (예외조건이 다음 chunk로 넘어감) | 조문 단위 분할, 고정 자르기 금지 |
| 공고문 미포함 → 실무 답 불가 | Phase 1부터 공고문 1개 이상 포함 |
| 답변에 근거 없음 | Structured Output 강제, 근거 없으면 출력 차단 |
| "가능합니다" 과잉 판단 | 시스템 프롬프트 + 판단 유보 조건 코드로 강제 |
| 한 번에 전체 시스템 구현 시도 | Phase별 단계 구현, 각 단계 콘솔 출력 확인 후 진행 |

---

## 13. 디렉토리 구조 (예상)

```
rnd-law-chatbot/
├── app.py                  # Streamlit 메인
├── requirements.txt
├── .env                    # ANTHROPIC_API_KEY
├── spec.md                 # 이 파일
│
├── pipeline/
│   ├── pdf_parser.py       # PDF → raw text
│   ├── chunker.py          # 조문 단위 분할
│   ├── embedder.py         # sentence-transformers 임베딩
│   └── indexer.py          # Qdrant 적재
│
├── retrieval/
│   ├── searcher.py         # 벡터 검색
│   └── reranker.py         # 추후 rerank
│
├── llm/
│   ├── prompts.py          # 시스템 프롬프트 관리
│   └── chain.py            # LangChain RAG 체인
│
├── data/
│   ├── raw/                # 업로드된 원본 PDF
│   ├── chunks/             # chunks.json
│   └── metadata/           # metadata.csv
│
└── tests/
    └── test_questions.json # 테스트 질문 10개 + 예상 답변
```

---

## 14. 환경 변수

```env
ANTHROPIC_API_KEY=your_key_here
QDRANT_PATH=./qdrant_storage
QDRANT_COLLECTION=rnd_law_chunks
CLAUDE_MODEL=claude-sonnet-4-5
TOP_K=5
```

---

*spec version: 1.0 | 작성일: 2026-04*
