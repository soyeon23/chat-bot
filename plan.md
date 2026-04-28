# 연구행정 챗봇 Streamlit UI 개발 계획

> spec.md v1.0 기반 | 작성일: 2026-04-24

---

## 1. UI 목표 및 설계 원칙

### 목표
- ResearchAdmin AI 레퍼런스 수준의 **전문적·신뢰감 있는** 인터페이스
- "보수형 RAG"의 철학(근거 없으면 답 안 함)을 UI에서도 시각적으로 명확히 전달
- 비개발자(연구행정 담당자)가 PDF 업로드부터 질문까지 **5분 이내** 사용 가능

### 핵심 UX 원칙
| 원칙 | 구현 방식 |
|------|-----------|
| 근거 투명성 | 모든 답변에 출처 배지 + 원문 토글 필수 노출 |
| 불확실성 표시 | Confidence Score 수치 + 색상 배지로 판단 강도 표현 |
| 전문가 경고 | CRITICAL CAUTION 블록으로 PM 최종 확인 필요성 강조 |
| 빠른 재질문 | 자주 쓰는 질문 칩(Quick Prompt) 하단 고정 배치 |

---

## 2. 화면 레이아웃 구조

```
┌─────────────────────────────────────────────────────────────┐
│  [사이드바 240px]        [메인 영역]                          │
│                                                             │
│  🔬 연구행정 AI          [채팅 히스토리 스크롤 영역]            │
│  ─────────────           ┌─────────────────────────────┐   │
│  📄 문서 라이브러리        │  사용자 질문 버블 (오른쪽)      │   │
│  💬 채팅                 │                             │   │
│  📊 이용 통계             │  ┌─────────────────────┐    │   │
│  📋 감사 로그             │  │  답변 카드 (왼쪽)     │    │   │
│  ⚙️ 시스템 상태           │  │  ├ 결론 배지          │    │   │
│  ─────────────           │  │  ├ Confidence Score  │    │   │
│  [+ 새 분석 시작]          │  │  ├ 요약 텍스트        │    │   │
│  ─────────────           │  │  ├ 근거·출처          │    │   │
│  PDF 업로드               │  │  ├ 주의사항 블록      │    │   │
│  [드래그&드롭 영역]         │  │  └ 원문 보기 토글     │    │   │
│                          │  └─────────────────────┘    │   │
│  ─────────────           └─────────────────────────────┘   │
│  업로드된 문서 목록         [Quick Prompt 칩 3개]              │
│  • 혁신법_2024.pdf ✓      [질문 입력창]  [📎] [▶]           │
│  • 운영요령_2024.pdf ✓                                       │
│                                                             │
│  필터                                                        │
│  문서종류 ▼   연도 ▼                                          │
└─────────────────────────────────────────────────────────────┘
```

---

## 3. 컴포넌트 명세

### 3-1. 사이드바

| 컴포넌트 | 구현 | 비고 |
|----------|------|------|
| 로고 + 앱 이름 | `st.markdown` + CSS | "연구행정 AI" |
| 네비게이션 | `st.radio` → 페이지 전환 | 채팅 / 문서 / 통계 / 감사로그 |
| 새 분석 버튼 | `st.button` | 대화 초기화 |
| PDF 업로드 | `st.file_uploader(accept_multiple_files=True)` | `.pdf` 전용 |
| 인덱싱 진행률 | `st.progress` + `st.status` | 업로드 후 자동 실행 |
| 업로드 문서 목록 | `st.expander` 내 리스트 | 파일명 + 체크 아이콘 |
| 필터 | `st.multiselect` (문서종류) + `st.selectbox` (연도) | 검색 시 Qdrant 필터 적용 |

### 3-2. 답변 카드 (핵심)

```
┌──────────────────────────────────────────────────────┐
│  🟡 조건부 가능    Confidence Score: 94%    [복사] [👍] │
├──────────────────────────────────────────────────────┤
│  연구기간이 6개월 이상이고 기관 자산으로 등록되는 경우     │
│  가능합니다.                                          │
├──────────────────────────────────────────────────────┤
│  GROUNDS & SOURCES                                    │
│  [📄 국가연구개발혁신법 제13조]  [📐 2024년 연구개발비 사용기준] │
├──────────────────────────────────────────────────────┤
│  ❗ CRITICAL CAUTION                                  │
│  반드시 전담기관 PM의 최종 확인이 필요합니다.             │
├──────────────────────────────────────────────────────┤
│  ▼ VIEW ORIGINAL TEXT                                 │
│  ┌────────────────────────────────────────────────┐  │
│  │  [국가연구개발혁신법 제13조]                        │  │
│  │  연구기관은 연구개발과제의 수행을 위하여…            │  │
│  └────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────┘
```

**결론 배지 색상 규칙**
| 결론 | 배지 색 | 아이콘 |
|------|---------|--------|
| 가능 | `#22c55e` (초록) | ✅ |
| 불가능 | `#ef4444` (빨강) | ❌ |
| 조건부 가능 | `#f59e0b` (노랑) | ⚠️ |
| 판단 불가 | `#6b7280` (회색) | ❓ |

### 3-3. Quick Prompt 칩

입력창 위에 자주 쓰는 질문 3개 고정 배치:
- `학생인건비 지급 기준`
- `간접비 비율 확인`
- `회의비 증빙서류 목록`

클릭 시 해당 텍스트가 입력창에 자동 입력되어 바로 전송.

### 3-4. 입력 영역

| 요소 | 구현 |
|------|------|
| 텍스트 입력 | `st.chat_input("연구행정 관련 질문을 입력하세요...")` |
| 파일 첨부 (선택) | 사이드바 업로드로 대체 (MVP에서 입력창 내 첨부 생략) |
| 전송 | `st.chat_input` 기본 Enter/버튼 |

---

## 4. 페이지별 구성

### 4-1. 채팅 페이지 (메인)

- `st.chat_message("user")` / `st.chat_message("assistant")` 기반 대화 UI
- 답변은 Structured Output JSON을 파싱해 카드 형태로 렌더링
- `st.session_state.messages`로 세션 내 히스토리 유지

### 4-2. 문서 라이브러리 페이지

```
┌────────────────────────────────────────────────┐
│  문서 라이브러리              [+ PDF 추가]        │
│  ──────────────────────────────────────────    │
│  문서명              종류    연도   상태   조작   │
│  혁신법_2024.pdf     법률    2024   활성   [삭제] │
│  운영요령_2024.pdf   운영요령 2024  활성   [삭제] │
│  FAQ_2023.pdf        FAQ    2023  구버전  [조회] │
└────────────────────────────────────────────────┘
```

- `st.dataframe` 또는 커스텀 카드 리스트
- 문서별 청크 수, 인덱싱 일시 표시
- `is_current` 토글로 구버전 조회 전환

### 4-3. 이용 통계 페이지

- 일별 질문 수 (`st.line_chart`)
- 결론 유형 분포 (`st.bar_chart`)
- 가장 많이 참조된 문서 Top 5

### 4-4. 감사 로그 페이지

- 질문 / 답변 결론 / Confidence Score / 타임스탬프 테이블
- CSV 내보내기 버튼

---

## 5. 디자인 시스템

### 색상 팔레트

| 용도 | 색상 |
|------|------|
| Primary (브랜드) | `#2563eb` (Blue-600) |
| 사이드바 배경 | `#1e293b` (Slate-800) |
| 답변 카드 배경 | `#f8fafc` (Slate-50) |
| 원문 보기 배경 | `#0f172a` (Slate-900) |
| Critical Caution 선 | `#ef4444` (Red-500) |
| 텍스트 주색 | `#0f172a` |
| 텍스트 보조 | `#64748b` |

### 폰트 & 크기

- 본문: 14px / 한국어 가독성 우선
- 결론 배지: 12px bold, 대문자
- 원문 텍스트: 13px monospace (`Nanum Gothic Coding` 또는 기본 mono)

### Streamlit Custom CSS 적용 방식

```python
st.markdown("""
<style>
/* 사이드바 배경 */
[data-testid="stSidebar"] { background-color: #1e293b; }
/* 결론 배지 */
.badge-possible { background: #22c55e; color: white; ... }
/* 원문 토글 배경 */
.original-text-box { background: #0f172a; color: #e2e8f0; ... }
/* CRITICAL CAUTION */
.caution-block { border-left: 4px solid #ef4444; ... }
</style>
""", unsafe_allow_html=True)
```

---

## 6. 파일 구조 (UI 관련)

```
rnd-law-chatbot/
├── app.py                      # Streamlit 진입점, 페이지 라우팅
├── pages/
│   ├── 01_chat.py              # 채팅 메인 페이지
│   ├── 02_documents.py         # 문서 라이브러리
│   ├── 03_analytics.py         # 이용 통계
│   └── 04_audit_log.py         # 감사 로그
├── ui/
│   ├── components.py           # 재사용 컴포넌트 함수
│   │   ├── render_answer_card()
│   │   ├── render_source_badge()
│   │   ├── render_caution_block()
│   │   └── render_original_text_toggle()
│   ├── styles.py               # CSS 문자열 상수
│   └── quick_prompts.py        # Quick Prompt 칩 데이터
└── (기존 pipeline/, retrieval/, llm/ 디렉토리)
```

---

## 7. 핵심 컴포넌트 구현 스케치

### render_answer_card() 예시

```python
def render_answer_card(result: dict):
    conclusion = result["conclusion"]
    badge_map = {
        "가능": ("✅ 가능", "#22c55e"),
        "불가능": ("❌ 불가능", "#ef4444"),
        "조건부 가능": ("⚠️ 조건부 가능", "#f59e0b"),
        "판단 불가": ("❓ 판단 불가", "#6b7280"),
    }
    label, color = badge_map.get(conclusion, ("❓", "#6b7280"))

    st.markdown(f"""
    <div class="answer-card">
      <div class="badge" style="background:{color}">{label}</div>
      <span class="confidence">Confidence Score: {result['confidence']}%</span>
      <p>{result['summary']}</p>
    </div>
    """, unsafe_allow_html=True)

    # 근거 출처 배지
    st.markdown("**GROUNDS & SOURCES**")
    cols = st.columns(len(result["grounds"]))
    for i, g in enumerate(result["grounds"]):
        cols[i].markdown(f"`📄 {g['doc_name']} {g['article_no']}`")

    # Critical Caution
    if result.get("caution"):
        st.markdown(f"""
        <div class="caution-block">
          <b>❗ CRITICAL CAUTION</b><br>{result['caution']}
        </div>
        """, unsafe_allow_html=True)

    # 원문 보기 토글
    with st.expander("VIEW ORIGINAL TEXT"):
        for g in result["grounds"]:
            st.markdown(f"""
            <div class="original-text-box">
              <b>[{g['doc_name']} {g['article_no']}]</b><br>
              {g['excerpt']}
            </div>
            """, unsafe_allow_html=True)
```

---

## 8. 개발 단계 (UI 중심)

### Phase 3-A: 기본 채팅 UI (Day 1~2)
- [ ] `app.py` + `pages/01_chat.py` 스캐폴딩
- [ ] `st.chat_message` 기반 대화 흐름 구현
- [ ] `st.session_state.messages` 히스토리 유지
- [ ] Mock JSON으로 답변 카드 렌더링 테스트

### Phase 3-B: 답변 카드 완성 (Day 3~4)
- [ ] `render_answer_card()` 컴포넌트 구현
- [ ] 결론 배지 색상 분기 처리
- [ ] 근거 출처 배지 렌더링
- [ ] CRITICAL CAUTION 블록 구현
- [ ] 원문 보기 토글 (`st.expander`)

### Phase 3-C: 사이드바 + 문서 관리 (Day 5~6)
- [ ] PDF 업로드 + `pipeline/` 연결
- [ ] `st.progress` 인덱싱 진행률 표시
- [ ] 업로드 문서 목록 + 필터 UI
- [ ] `pages/02_documents.py` 문서 라이브러리

### Phase 3-D: Quick Prompt + 입력 UI (Day 7)
- [ ] Quick Prompt 칩 3개 렌더링
- [ ] 칩 클릭 → `st.session_state`로 입력창 자동 채우기
- [ ] 전체 Custom CSS 적용 및 색상 통일

### Phase 3-E: 통계 + 감사 로그 (Day 8~9)
- [ ] `pages/03_analytics.py` 차트 구현
- [ ] `pages/04_audit_log.py` + CSV 내보내기
- [ ] 전체 페이지 연동 테스트

---

## 9. 주의 사항 및 제약

| 항목 | 내용 |
|------|------|
| `unsafe_allow_html` | 커스텀 카드 렌더링에 필수 사용, XSS 위험 없음 (내부 로컬 앱) |
| 상태 관리 | `st.session_state`만 사용, DB 세션 저장 없음 (MVP) |
| 성능 | 대화 히스토리가 길어지면 `st.container` + `height` 고정으로 스크롤 처리 |
| 한국어 폰트 | Streamlit 기본 폰트로도 가능하나, `Noto Sans KR` CDN 추가 권장 |
| 모바일 | MVP는 데스크톱 전용, 반응형 미지원 |

---

*plan version: 1.0 | based on spec.md v1.0*
