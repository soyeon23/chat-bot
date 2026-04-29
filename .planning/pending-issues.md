# 현재 해결해야 할 이슈 정리

작성일: 2026-04-29
최종 갱신: 2026-04-29 (커밋 `73a3a08` 이후)
기준 커밋: `73a3a08` (chunker 1.4.0 + 컬렉션 graceful)

> ⚠️ **이 파일이 단일 진실 — 작업 전후 반드시 갱신**
> compact / 세션 종료 전 / 작업 완료 시 즉시 상태 업데이트.
> 해결된 이슈는 strikethrough(~~취소선~~) + ✅ 표시, 남은 이슈는 우선순위 유지.

---

## ✅ 최근 해결됨

| 이슈 | 해결 방식 | 커밋 |
|---|---|---|
| ~~매뉴얼 PDF chunker 1.3.4 회귀~~ | chunker 1.4.0 페이지 기반 라우팅 (`_split_by_pages`). 청크 742→844, 페이지 커버리지 32.8%→64.3%, "151p" 회복 | `73a3a08` |
| ~~청크 수 감소 추세~~ | 1.4.0 으로 회복 | `73a3a08` |
| ~~Phase H max_turns RuntimeError~~ | `_run_query_sync` graceful transport 폴백 → "판단불가" stub | `73a3a08` |
| ~~다른 PC ValueError (collection not found)~~ | `_collection_exists` 검사 + 빈 결과 graceful | `73a3a08` |
| ~~Phase H get_article 매뉴얼 PDF 매칭 실패~~ | search_text 자동 폴백 (Phase H+1) | 이전 |
| ~~멀티턴 대화 컨텍스트 단절~~ | rewritten_query + prior_turns 통합 | `bc2362a` |

---

## 🔴 Critical — 즉시 해결 필요

### 1. HWPML 본체 3종 미인덱싱

**증상**: 가장 중요한 법령 본문 3개가 검색 인덱스에 0 청크.

| 파일 | 포맷 | 현재 |
|---|---|---|
| `국가연구개발혁신법(법률).hwp` | HWPML(XML) | ✗ 0 chunks |
| `국가연구개발혁신법 시행령(대통령령).hwp` | HWPML(XML) | ✗ 0 chunks |
| `국가연구개발혁신법 시행규칙(과학기술정보통신부령).hwp` | HWPML(XML) | ✗ 0 chunks |

**원인**: hwp-mcp 패키지가 OLE2 binary HWP 만 지원. HWPML(`<?xml version` + `<!DOCTYPE HWPML`) 거부.

**영향**:
- "혁신법 제15조 제2항 전문" → 매뉴얼 안 인용에만 의존
- "시행령 제20조 제1항" → 별표2 헤더 인용 정도만
- "시행규칙 제5조 절차" → 답변 불가
- Phase H `get_article` 도구도 같은 파일을 못 읽음
- 평가셋 카테고리 4.2 (시행령 vs 시행규칙 비교) 영구 FAIL

**해결안**: `pipeline/hwpml_parser.py` 신규 — Python stdlib `xml.etree.ElementTree` 로 직접 파싱. `<BODY><SECTION><P><TEXT><CHAR>` 구조 추출.

**작업량**: 1~2시간. 본체 3종 즉시 인덱싱 → 1500~2500 청크 추가 예상.

**상태**: 미시작. 사용자가 "추후 진행" 으로 보류 → 다시 우선순위 끌어올림 권장.

---

### ~~2. 매뉴얼 PDF chunker 회귀 (1.3.4)~~ ✅ 해결 (`73a3a08`)

**해결 결과**:
- chunker 1.4.0 — `_split_by_pages()` 페이지 기반 라우팅
- 매뉴얼 청크 742 → 844, 페이지 커버리지 32.8% → 64.3%
- article_no 모두 "p.N" 형식 (부조리 라벨 0개)
- "151p 알려줘" 등 페이지 직접 조회 정상 회복

---

## 🟡 High — 검색 품질 직접 영향

### ~~3. 청크 수 감소 추세 (정보 손실)~~ ✅ 해결 (`73a3a08`)

1.4.0 페이지 기반 라우팅으로 회복. 1.3.2 의 742 → 1.4.0 sync 후 844 청크.

---

### 4. 평가셋 baseline 미갱신

**증상**: `data/eval_baseline.json` 이 1.3.2 sync 시점 baseline. 1.4.0 sync 후 재실행 필요.

**해결안**: `source .venv/bin/activate && python scripts/eval_full.py` 실행 → baseline 갱신.

**작업량**: 10분 (자동).

**상태**: chunker 1.4.0 sync 완료 → 즉시 실행 가능.

---

## 🟢 Medium — 운영·품질

### 5. Phase H 도구 모드 워밍업 비용

**증상**: HWP 파일 첫 호출 시 hwp-mcp 서브프로세스 띄우는 데 30~60초. 페이지/조문 조회 첫 응답 ~2:11.

**해결안**: 앱 시작 시 백그라운드 thread 로 전체 파일 캐시 워밍.

**작업량**: 1시간.

**상태**: Phase H1 (속도 최적화 패키지) 의 일부. 사용자 보류 결정.

---

### 6. 답변 응답 시간 (1~2분)

**증상**: 일반 토픽 ~1:30, 페이지 조회 ~2:11.

**개선 옵션** (Phase H1):
- 캐시 사전 워밍 (-30초)
- retrieval 스킵 page_lookup 시 (-5초)
- 프롬프트 캐싱 (-20초, TTFT 절반)
- 출력 스트리밍 (체감 -25초)
- 답변 캐시 (재요청 0.1초)

**상태**: 사용자가 "속도보다 품질 우선" 선언 → 보류.

---

### 7. confidence score 검증 미흡

**현재**: 부스트 분리해 vector-only 평균 산출. 페이지 직접 매칭은 N/A 또는 신호 라벨 표시.

**미검증**: 실제 사용에서 신뢰도가 사용자 의사결정에 도움이 되는지. 실 사용 데이터 모니터링 필요.

**작업량**: 관찰만, 코드 변경 없음.

---

### 8. MCP 동기화 (G5) 운영 검증

**현재**: korean-law-mcp 스키마 probe + hwp-mcp 버전 체커 + 5회 실패 자동 disable. 47/47 단위 테스트 PASS.

**미검증**: 실제 운영에서 스키마 변경/네트워크 오류 시 사용자 가시성.

**작업량**: UI 알림 디테일 보강 시 30분.

---

## 🔵 Future / Roadmap

### 9. F1 (모델 업그레이드 — 적용됨)
- 기본 Sonnet 4.6, 비교형은 Opus 4.6 자동 escalate. 환경설정에서 토글.
- 운영 모니터링 단계.

### 10. F2 (윈도우 네이티브 배포)
- 단기: 셋업 스크립트 + tkinter 폴더 픽커 + auth.py Windows 폴백 (적용됨)
- 중기: PyInstaller / streamlit-desktop-app
- 상태: scout 리서치 완료 (`.planning/research/scout-f2-native-deploy.md`), 단기 적용됨

### 11. F4 (답변 속도)
- 옵션 A·B·D·E 4종 패키지 (Phase H1)
- 상태: 사용자 보류

### 12. F5 (코퍼스 — HWPML 지원)
- 이슈 #1 과 동일

### 13. Phase J (실시간 풀 마이그레이션)
- 의미 검색 약화 위험으로 비추 (Hybrid 유지가 정답)
- 상태: 검토 종료

---

## 진행 권장 순서

| 우선 | 이슈 | 작업량 | 효과 |
|---|---|---|---|
| 1 | **#4 평가셋 baseline 갱신** | 10분 | 1.4.0 효과 정량화 + 회귀 안전망 |
| 2 | **#1 HWPML 본체 3종** — PDF 가지면 드롭+sync, 없으면 XML 파서 | 5분 (PDF) / 1~2시간 (파서) | 검색 커버리지 30%+ ↑, "혁신법 제15조 본문" 답변 가능 |
| 3 | (선택) Phase H1 속도 최적화 | 6시간 | 응답 시간 50% 단축 (사용자 보류 결정) |

---

## 알려진 작은 이슈

- `app.py:377-381` 사이드바 모델 셀렉터에 옛 옵션 (Sonnet 4.5, Opus 4.7) 노출 — 환경설정 페이지 토글로 일원화 필요
- `data/config.json` 의 `claude_model` 이 옛 모델명 (`claude-haiku-4-5-20251001`) — 환경설정에서 다시 저장하면 정리됨
- chunker 1.3.4 sync 가 이미 적용된 상태 — 이슈 #2 해결 시 1.4.0 으로 재sync 필요

---

## 참고 파일

- `.planning/coverage-full-audit.md` — 전체 코퍼스 풀 분석 결과 (2026-04-29)
- `.planning/roadmap-future.md` — F1~F5 / G1~G5 / H 페이즈 정의
- `.planning/pm/phase-h-backend-brief.md` — Phase H 작업지시서 (완료)
- `scripts/eval_full.py` — 39 케이스 회귀 평가셋
- `scripts/coverage_report.py` — 인덱스 품질 진단 도구
- `data/eval_baseline.json` — 1.3.2 시점 baseline
