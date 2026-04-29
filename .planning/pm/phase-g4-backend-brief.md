# Phase G4 — Backend 위임 브리프 (self-contained)

> 이 문서는 backend agent 가 PM 세션 컨텍스트 없이도 G4 작업을 수행할 수 있도록 작성된 단독 브리프이다.
> backend 는 이 문서만 읽고 작업하면 된다. G5 는 PM 이 별도 위임한다 — **G4 만 진행**.

작성일: 2026-04-29
대상 phase: G4 (증분 sync + 코드 버전 인식)
관련 로드맵: `/Users/maro/dev/company/chatbot/.planning/roadmap-future.md` 의 "Phase G4" 섹션
선행 phase: G1 ✅ 완료, G2 ✅ 완료. G3 는 폐기되어 G4 가 본질 작업을 흡수.

---

## 1. 작업 목적

사용자가 PDF/HWP 폴더에 파일을 추가·수정·삭제할 때, 또는 chunker/embedder 코드가 업그레이드될 때 *영향 받는 파일만* 재인덱싱한다. Git 의 working-tree 변경 감지와 유사한 흐름.

핵심 차별점: **단순 mtime/sha256 변경 감지**에 더해 **chunker_version, embedder_version 메타도 추적**해 코드 변경(예: G1·G2 chunker 패치) 시 영향 받는 파일을 자동 stale 처리.

사용자 요구사항 인용 (2026-04-29):
> "그 파일이 안일어났는데 전체를 재인덱싱은 너무 별로임"

→ 사용자는 "동기화" 버튼 1회 클릭으로 변경된 파일만 재인덱싱되길 원함.

---

## 2. 첫 실행 시 동작 (G3 본질 흡수)

G3 (전체 재인덱싱) 가 폐기되었기 때문에 G4 의 **첫 실행이 G3 의 본질 작업을 자동 흡수**한다.

| 파일 그룹 | 청크 수 (추정) | chunker_version 상태 | 첫 실행 처리 |
|---|---|---|---|
| 별표 6개 (시행령 별표 1·2·4·5·6·7) | G2 산출물 | `"1.2.0"` (현재 코드와 일치) | no-op |
| 매뉴얼 PDF 1개 (`[본권] 25년도 ... 매뉴얼_배포용.pdf`) | ~1,065 | 미기록 → `stale_by_code` | 재인덱싱 |
| 별지 10개 서식 | ~92 | 미기록 → `stale_by_code` | 재인덱싱 |
| **첫 실행 합계** | **~1,157 청크 재인덱싱** | | |

즉 사용자는 "📂 동기화" 버튼 1회 클릭만으로 G3 가 의도했던 매뉴얼·별지 재인덱싱 작업을 자동 수행할 수 있어야 한다.

---

## 3. 정확한 수정 대상

### 3.1 새 상수 추가

**파일**: `/Users/maro/dev/company/chatbot/pipeline/chunker.py`

상단 import 블록 직후, `ARTICLE_PATTERNS` 정의 *앞에* 다음 상수 추가:

```python
# Chunker 버전 — G1·G2 patch 반영 후 1.2.0
# 코드가 변경되어 청킹 결과가 달라질 수 있을 때마다 bump.
# G4 sync 가 이 값을 메타에 기록 → 다음 sync 시 mismatch 감지 → stale 재인덱싱.
CHUNKER_VERSION = "1.2.0"
```

**버전 bump 가이드**:
- `ARTICLE_PATTERNS` 정규식이 바뀌면 bump
- `_split_by_articles`, `chunk_document` 의 분할 로직이 바뀌면 bump
- 청크 목표 길이(`max_chunk_chars` 등) 디폴트가 바뀌면 bump
- 단순 변수명 리네이밍, 주석 변경, 로깅 추가 등은 bump 불필요

### 3.2 embedder_version 의 출처

embedder_version 은 별도 상수 추가 없이 **현재 사용 모델명 그대로** 사용:
- 값: `"ko-sroberta-multitask"`
- 출처: `pipeline/retriever.py` 또는 인덱싱 모듈에 이미 정의된 모델명 상수
- 만약 단일 상수가 없다면 `pipeline/sync.py` 안에 `EMBEDDER_VERSION = "ko-sroberta-multitask"` 로 정의 (선택)

---

## 4. 신규 모듈: `pipeline/sync.py`

### 4.1 데이터 스키마

**경로**: `data/metadata/file_hashes.json`

```json
{
  "/Users/maro/dev/company/chatbot/path/to/source.pdf": {
    "mtime": 1761746400.123,
    "sha256": "abc123...",
    "chunker_version": "1.2.0",
    "embedder_version": "ko-sroberta-multitask",
    "chunk_ids": ["uuid-1", "uuid-2", "..."],
    "indexed_at": "2026-04-29T12:34:56Z"
  },
  "/Users/maro/dev/company/chatbot/path/to/another.hwp": {
    ...
  }
}
```

**키**: 절대경로 문자열. 윈도우 호환 위해 항상 `Path.resolve()` 결과를 `str()` 처리.

**chunk_ids**: Qdrant 의 point id 리스트. 삭제·갱신 시 `client.delete(points_selector=PointIdsList(points=chunk_ids))` 로 일괄 제거.

### 4.2 모듈 구조

```
pipeline/sync.py
├── HASHES_PATH = Path("data/metadata/file_hashes.json")
├── EMBEDDER_VERSION = "ko-sroberta-multitask"
│
├── def _load_hashes() -> dict
├── def _save_hashes(hashes: dict) -> None
├── def _file_sha256(path: Path) -> str
├── def _file_mtime(path: Path) -> float
│
├── def scan_changes(roots: list[Path]) -> dict:
│       """
│       returns: {
│         "added":         [Path, ...],
│         "modified":      [Path, ...],
│         "deleted":       [str, ...],   # 절대경로 문자열 (디스크에 없음)
│         "stale_by_code": [Path, ...],  # 디스크엔 있고 hash 같지만 버전 mismatch
│         "unchanged":     [Path, ...],  # no-op (보고용)
│       }
│       """
│
├── def apply_changes(changes: dict, qdrant_client, collection_name: str,
│                     parser_fn, chunker_fn, embedder_fn,
│                     progress_cb=None) -> dict:
│       """
│       deleted + modified + stale_by_code: Qdrant 에서 chunk_ids 삭제
│       added + modified + stale_by_code: 파싱·청킹·임베딩·업서트 → hashes.json 갱신
│
│       returns: {
│         "added": int, "modified": int, "deleted": int,
│         "stale_by_code": int, "elapsed_sec": float,
│         "errors": [(path, exception_msg), ...]
│       }
│       """
│
└── def sync(roots: list[Path], qdrant_client, collection_name: str, ...) -> dict:
       """scan_changes + apply_changes 를 하나로 묶은 편의 함수. UI 가 호출."""
```

### 4.3 변경 분류 로직

`scan_changes` 의 분류 우선순위 (위에서 아래로 평가, 첫 매칭으로 분류):

```
1. hashes.json 에 없는 파일                        → added
2. hashes.json 에 있지만 디스크에 없음              → deleted
3. mtime 또는 sha256 mismatch                      → modified
4. mtime/sha256 일치하지만 버전 mismatch:
   - chunker_version != CHUNKER_VERSION 또는
   - embedder_version != EMBEDDER_VERSION         → stale_by_code
5. 그 외                                           → unchanged
```

**최적화 노트**: sha256 계산은 비싸므로 mtime 이 같으면 sha256 skip (단, 파일 크기도 같이 비교). 사용자가 mtime 만 갱신하고 내용은 그대로인 경우 reindex 불필요.

```python
def _is_modified(path, meta):
    if abs(_file_mtime(path) - meta["mtime"]) < 0.001 and \
       path.stat().st_size == meta.get("size", path.stat().st_size):
        return False  # mtime 일치 → sha256 skip
    return _file_sha256(path) != meta["sha256"]
```

→ 위 최적화 적용 시 hashes.json 메타에 `"size": int` 필드도 함께 저장.

### 4.4 마이그레이션 (G2 인덱스 → 첫 hashes.json)

`pipeline/sync.py` 안에 별도 함수:

```python
def migrate_from_existing_index(qdrant_client, collection_name: str,
                                roots: list[Path]) -> None:
    """
    G2 까지 이미 인덱싱된 컬렉션에서 source_file 별 chunk_ids 를 모아
    hashes.json 초기 entry 를 생성한다. chunker_version 은 별표 6개 파일에만
    "1.2.0" 부착. 매뉴얼 PDF / 별지는 chunker_version 미기록 → stale_by_code 로
    분류되도록 비워둔다 (또는 "unknown" 마커).
    """
```

호출 방식: 환경설정 페이지의 "📂 동기화" 버튼 첫 클릭 시, hashes.json 이 없으면 자동으로 한 번 호출.

**별표 파일 식별**: `[별표\s*\d+]` 정규식이 source_file basename 에 매칭되면 G2 산출물 → `chunker_version="1.2.0"` 마킹.

**매뉴얼 PDF / 별지 식별**: 위에 매칭 안 되면 `chunker_version="unknown"` 또는 키 누락 → 첫 sync 에서 `stale_by_code` 분류.

---

## 5. UI 통합

### 5.1 환경설정 페이지

**파일**: `/Users/maro/dev/company/chatbot/pages/00_⚙️_환경설정.py`

기존 인덱싱 버튼 옆에 새 버튼:

```python
col1, col2 = st.columns(2)
with col1:
    if st.button("🔁 전체 재인덱싱", use_container_width=True):
        ...  # 기존 batch_ingest 로직

with col2:
    if st.button("📂 동기화", use_container_width=True, type="primary"):
        progress = st.progress(0.0, text="변경 사항 스캔 중...")
        result = sync.sync(
            roots=[Path(p) for p in config.data_paths],
            qdrant_client=client,
            collection_name="rnd_law_chunks",
            parser_fn=...,
            chunker_fn=...,
            embedder_fn=...,
            progress_cb=lambda done, total, name:
                progress.progress(done / total, text=f"{done}/{total} {name}")
        )
        st.success(
            f"완료 — added: {result['added']}, "
            f"modified: {result['modified']}, "
            f"deleted: {result['deleted']}, "
            f"stale_by_code: {result['stale_by_code']}, "
            f"소요: {result['elapsed_sec']:.1f}s"
        )
```

### 5.2 옵션: 자동 sync

`pipeline/config_store.py` 에 `auto_sync_on_start: bool` 추가. `app.py` 시작 시 토글 ON 이면 background 로 sync 1회 실행 (실패해도 앱 부팅에 지장 없도록 try/except).

---

## 6. 단위 테스트

**파일**: `/Users/maro/dev/company/chatbot/tests/test_sync.py` (신규)

### 6.1 단위 테스트 케이스

```
test_scan_changes_added_file()
test_scan_changes_modified_file_mtime()
test_scan_changes_modified_file_sha256_only()  # mtime 같지만 내용 다름 (touch + edit)
test_scan_changes_deleted_file()
test_scan_changes_stale_by_chunker_version()
test_scan_changes_stale_by_embedder_version()
test_scan_changes_unchanged()
test_apply_changes_deletes_chunks_from_qdrant()  # mock client
test_apply_changes_upserts_new_chunks()
test_apply_changes_updates_metadata()
test_migrate_from_existing_index_marks_byeolpyo_files()
test_migrate_from_existing_index_marks_others_as_unknown()
```

### 6.2 통합 테스트 케이스 (`tests/test_sync_integration.py`)

실제 임시 디렉토리 + Qdrant in-memory 사용:

```
test_first_run_indexes_all_files()              # 빈 hashes.json 으로 시작 → 모두 added
test_no_op_after_first_run()                    # 즉시 재실행 → 0개 변경
test_modify_one_file_only()                     # 1개 파일 mtime+내용 변경 → 그 파일만 reindex
test_delete_one_file()                          # 1개 파일 삭제 → 그 파일 청크만 삭제
test_chunker_version_bump_triggers_full()       # CHUNKER_VERSION 변경 → 모든 파일 stale_by_code
test_first_real_run_scenario()                  # G3 본질 흡수 시나리오 — 별표 6개 no-op + 나머지 stale_by_code
```

---

## 7. 합격기준 (완료 조건)

다음을 모두 충족해야 G4 완료로 보고:

1. **단위 테스트 12 케이스** 모두 통과
2. **통합 테스트 6 케이스** 모두 통과
3. **첫 실행 시나리오 검증**:
   - 별표 6개 파일: no-op (0 reindex)
   - 매뉴얼 PDF + 별지 10개: stale_by_code 로 자동 reindex
   - 예상 처리량 ~1,157 청크 (매뉴얼 ~1,065 + 별지 ~92)
   - 처리 후 hashes.json 의 모든 entry 가 `chunker_version="1.2.0"`, `embedder_version="ko-sroberta-multitask"` 로 통일
4. **성능**: 50개 파일 폴더에서 1개만 변경했을 때 sync 시간 < 30초 (전체 재인덱싱 대비 95% 이상 절약)
5. **회귀**:
   - 첫 실행 후 `phase-g3-eval-spec.md` 30 케이스 평가 ≥ 80% 통과
   - 기존 9/10 baseline (smart 검색) 회귀 0건
6. **UI**: 환경설정 페이지 "📂 동기화" 버튼 클릭 → 진행률 → 결과 요약 1초 내 표시
7. **버전 인식**: chunker.py 의 `CHUNKER_VERSION` 을 `"1.2.0"` → `"1.3.0"` 로 변경 후 동기화 → 모든 파일 stale_by_code 감지 후 reindex

---

## 8. 상호작용 / 의존성

- **선행**: G1 ✅, G2 ✅ 완료된 인덱스 필요
- **후행**: G5 (MCP 동기화) — G4 완료 후 진행
- **테스트 데이터**: 기존 `data/metadata/*.csv` 의 source_file 목록을 참조해 마이그레이션 검증 가능
- **Qdrant 컬렉션**: `rnd_law_chunks` (현행 그대로)

---

## 9. 자율 결정 권한 (PM 에게 묻지 말 것)

다음 결정은 backend 가 자체 판단:
- `CHUNKER_VERSION` 의 다음 bump 시점·정책 (위 3.1 가이드 참고)
- `_is_modified` 의 sha256 skip 최적화 적용 여부 (권장: 적용)
- progress_cb 시그니처 세부사항
- 테스트에 사용할 임시 PDF/HWP fixtures (작은 합성 파일이면 충분)
- hashes.json 의 사소한 필드 추가 (예: `size`, `parser_version` 등)
- 마이그레이션 시 별표 식별 정규식 미세 조정

다음은 PM 결정 사항이라 backend 는 임의로 변경 금지:
- 진행 순서 (G4 → G5)
- 첫 실행 시 별표 6개 no-op + 나머지 reindex 라는 동작 (G3 본질 흡수 정책)
- 사용자 UX: 단일 "📂 동기화" 버튼 1회 클릭

---

## 10. 보고 형식 (작업 완료 시 PM 에게 회신)

```
## G4 완료 보고

### 변경 파일
- pipeline/chunker.py (CHUNKER_VERSION 추가)
- pipeline/sync.py (신규)
- pages/00_⚙️_환경설정.py (UI 통합)
- tests/test_sync.py (신규)
- tests/test_sync_integration.py (신규)
- pipeline/config_store.py (auto_sync_on_start 옵션, 선택)
- data/metadata/file_hashes.json (런타임 생성)

### 테스트 결과
- 단위 테스트: X/12 통과
- 통합 테스트: Y/6 통과
- 첫 실행 시나리오: 별표 6개 no-op ✓, 매뉴얼+별지 1,157 청크 reindex ✓
- 성능 측정: ?초 (1개 변경 시), ?초 (전체 재인덱싱 시)

### 회귀
- 30 케이스 평가: ?/30 통과
- 9/10 baseline: 회귀 ?건

### 알려진 이슈 / 후속
- (있으면 기재)
```
