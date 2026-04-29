# Phase G1 — Backend 위임 브리프 (self-contained)

> 이 문서는 backend agent 가 PM 세션 컨텍스트 없이도 G1 작업을 수행할 수 있도록 작성된 단독 브리프이다.
> backend 는 이 문서만 읽고 작업하면 된다. 다른 phase(G2, G3) 는 PM 이 별도 위임한다 — **G1 만 진행**.

작성일: 2026-04-29
대상 phase: G1 (별표 인덱싱 수정안 — 옵션 C, 정규식 강화)
관련 로드맵: `/Users/maro/dev/company/chatbot/.planning/roadmap-future.md` 의 "Phase G1" 섹션

---

## 1. 작업 목적

별표 본문(시행령 별표1~7) 안에 등장하는 인라인 참조 표현 — `(제20조제1항 관련)`, `법 제32조` 등 — 이 현재 chunker 의 `제\d+조` 정규식에 매칭되어 별표 본문이 잘못 split 된다. 본문 줄 시작에서만 매칭되도록 패턴을 강화해 인라인 참조를 split 트리거에서 제외한다.

---

## 2. 정확한 수정 대상

**파일**: `/Users/maro/dev/company/chatbot/pipeline/chunker.py`

**현재 코드 (14~19라인)**:
```python
# 조문 구조 분할 패턴 (우선순위 순)
ARTICLE_PATTERNS = [
    r"(제\d+조(?:의\d+)?(?:\s*\([^)]*\))?)",   # 제N조, 제N조의N, 제N조(제목)
    r"(별표\s*\d+)",                              # 별표N
    r"(별표(?!\s*\d))",                           # 별표 (번호 없는)
    r"(부\s*칙)",                                 # 부칙
]
```

**PM 이 지정한 새 패턴 (PM 이 직접 작성 — backend 는 임의로 다르게 짜지 말 것)**:
```python
# 조문 구조 분할 패턴 (우선순위 순)
# 줄 시작 강제 + 후행 컨텍스트 제약으로 인라인 참조 제외
# 예) "(제20조제1항 관련)", "법 제32조" 같은 본문 안 참조는 split 트리거 안 됨
ARTICLE_PATTERNS = [
    r"(?m)^\s*(제\d+조(?:의\d+)?(?:\s*\([^)]*\))?)(?=\s*\(|\s*$|\s*\n)",   # 제N조, 제N조의N, 제N조(제목)
    r"(?m)^\s*(별표\s*\d+)(?=\s*$|\s*\n|\s*\()",                              # 별표N
    r"(?m)^\s*(별표(?!\s*\d))(?=\s*$|\s*\n|\s*\()",                           # 별표 (번호 없는)
    r"(?m)^\s*(부\s*칙)(?=\s*$|\s*\n|\s*\()",                                 # 부칙
]
```

**적용 방식**:
- 위 4개 패턴을 그대로 교체.
- `_split_by_articles` 함수는 그대로 두되, `re.finditer(combined_pattern, full_text)` 호출이 multi-line 모드(`(?m)`) 인라인 플래그를 그대로 인식하므로 추가 플래그 인자 변경 불필요.
- `match.group()` 사용 부분은 그대로 두되, 캡처 그룹이 1번이 되도록 패턴 안 캡처 괄호 위치를 위와 같이 유지. 만약 `match.group()` (group 0) 가 선행 공백 포함해서 article_no 에 들어가는 문제가 있으면 `match.group(1).strip()` 으로 변경하거나 `_split_by_articles` 의 라인 118 (`article_no = match.group().strip()`) 을 `article_no = (match.group(1) or match.group()).strip()` 으로 보강. (group 1 이 비어있을 수 있으면 fallback)

---

## 3. 단위 테스트 스펙

**파일**: `/Users/maro/dev/company/chatbot/tests/test_chunker_patterns.py` (새로 생성)

**필수 케이스**:

### 3.1 별표2 synthetic 본문 (인라인 참조 회피)
```
입력 텍스트:
"""
[별표 2] (제20조제1항 관련)

연구개발비 사용용도

1. 인건비
가. 인건비는 법 제32조에 따라 지급한다.
나. 학생인건비는 별도 기준에 따른다.

2. 연구활동비
가. 클라우드컴퓨팅서비스 이용료
나. 회의비
"""

기대 결과:
- _split_by_articles 호출 시 split point 가 "별표 2" 1군데만 잡혀야 함
- "(제20조제1항 관련)" 안의 "제20조" 는 split 안 됨
- "법 제32조" 도 split 안 됨 (줄 시작 아님)
- 결과 청크에 "1. 인건비", "2. 연구활동비" 본문 포함되어야 함
```

### 3.2 정상 시행령 본체 본문 (회귀 확인)
```
입력 텍스트:
"""
제13조(연구개발비의 사용)
① 연구개발기관의 장은 ...
② 다음 각 호의 어느 하나에 해당하는 경우 ...

제15조(연구개발비의 정산)
① 연구개발기관의 장은 ...
"""

기대 결과:
- split point 2군데 ("제13조", "제15조") 정확히 잡힘
- article_no 가 "제13조(연구개발비의 사용)", "제15조(연구개발비의 정산)" 으로 추출됨
```

### 3.3 부칙 + 별표 혼합
```
입력 텍스트:
"""
제32조 (시행일)
이 영은 공포한 날부터 시행한다.

부칙

별표 1
정부지원기준
"""

기대 결과:
- split point 3군데 ("제32조", "부칙", "별표 1")
- 인라인 표현 "법 제32조" 가 다른 청크 본문에 있다면 추가 split 안 됨
```

테스트 프레임워크: `pytest`. 프로젝트에 pytest 설정이 없으면 `python -m unittest` 호환되도록 작성해도 무방. 실행 명령:
```bash
cd /Users/maro/dev/company/chatbot
source .venv/bin/activate
python -m pytest tests/test_chunker_patterns.py -v
# 또는
python -m unittest tests.test_chunker_patterns -v
```

---

## 4. 별표2 단일 파일 재인덱싱 + 검증

### 4.1 재인덱싱 명령

별표2 파일 위치는 `pipeline/` 또는 프로젝트 루트의 PDF/HWP 파일을 직접 확인 (현재 git status 에 시행령 별표 디렉토리 존재). 재인덱싱은 `batch_ingest.py` 를 단일 파일 모드로 실행하거나, 임시 스크립트로 다음을 수행:

```python
# scripts/reindex_byeolpyo2.py (임시)
from pathlib import Path
from pipeline.pdf_parser import parse_pdf
from pipeline.chunker import chunk_document
from pipeline.embedder import embed_texts
from pipeline.indexer import upsert_chunks

# 별표2 파일 경로 확인 후 지정
# 예: data/raw/별표2_연구개발비_사용용도.pdf
file_path = Path("...")  # backend 가 실제 경로 확인
parse_result = parse_pdf(file_path)
chunks = chunk_document(parse_result, doc_name="국가연구개발혁신법 시행령 별표2", doc_type="시행령")

# 기존 별표2 청크 삭제 후 재upsert (Qdrant filter delete)
# ...
```

backend 가 이 임시 스크립트를 만들어 실행하든, `batch_ingest.py` 에 단일 파일 인자를 넣어 실행하든 무방.

### 4.2 Qdrant scroll 검증 쿼리

```python
from qdrant_client import QdrantClient
from qdrant_client.http import models as qmodels

client = QdrantClient(path="./qdrant_storage")
scroll = client.scroll(
    collection_name="rnd_law_chunks",
    scroll_filter=qmodels.Filter(
        must=[qmodels.FieldCondition(key="article_no", match=qmodels.MatchText(text="별표 2"))]
    ),
    limit=200,
    with_payload=True,
    with_vectors=False,
)

records = scroll[0]
print(f"별표2 청크 수: {len(records)}")
total_len = sum(len(r.payload.get("text", "")) for r in records)
print(f"별표2 텍스트 길이 합계: {total_len}")

# 키워드 포함 검증
keywords = ["연구활동비", "클라우드컴퓨팅서비스", "학생인건비", "위탁연구개발비", "연구수당"]
for kw in keywords:
    hits = [r for r in records if kw in (r.payload.get("text") or "")]
    print(f"  {kw}: {len(hits)}개 청크")
```

**합격 기준**:
- 별표2 청크 수 ≥ 5
- 키워드 5종 모두 ≥ 1개 청크에 포함

---

## 5. 보고 형식 (G1 완료 후 PM 에게 제출)

다음 항목을 마크다운으로 정리해 보고:

1. **변경 파일 목록** (절대경로)
2. **수정된 ARTICLE_PATTERNS 정확한 새 코드** (실제 적용된 정규식)
3. **단위 테스트 결과** — 케이스별 PASS/FAIL + 실행 명령 + 출력 발췌
4. **별표2 인덱스 메트릭 before/after**
   - before: 청크 수, 텍스트 길이 합계, 키워드 5종 포함 청크 수
   - after: 동일
5. **회귀 확인** — 시행령 본체(제N조) 분할이 정상 작동하는지 샘플 1~2개 (예: 제13조, 제15조 청크 존재 확인)
6. **이슈/막힌 점** (있다면)

---

## 6. 작업 범위 외 (G1 에서 하지 말 것)

- **G2 (별표 파일 라우팅, 옵션 A) 진행 금지** — G1 통과 후 PM 이 별도 위임.
- **G3 (전체 재인덱싱) 진행 금지** — 별표2 단일 파일만 재인덱싱.
- 평가 스크립트 `scripts/eval_full.py` 는 이 phase 에서 작성하지 않음.
- 다른 chunker 함수(_split_long_text, _split_by_lines 등) 수정 금지.
- 정규식 외 코드 리팩토링 금지.

---

## 7. 사전 점검 (작업 시작 전 backend 가 확인할 것)

```bash
cd /Users/maro/dev/company/chatbot
ls -la pipeline/chunker.py    # 수정 대상 존재 확인
ls -la qdrant_storage/        # 인덱스 디렉토리 존재 확인
ls tests/ 2>/dev/null || echo "tests/ 없음 — 새로 생성 필요"
source .venv/bin/activate && python -c "import pytest; print(pytest.__version__)" 2>/dev/null || echo "pytest 없으면 unittest 로 작성"
```
