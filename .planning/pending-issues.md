# 현재 해결해야 할 이슈 정리

작성일: 2026-04-29
최종 갱신: 2026-05-04 (커밋 `09d04f5` 이후)
기준 커밋: `09d04f5` (HWPML 본체 3종 인덱싱 — 평가 97.4% ACCEPT)

> ⚠️ **이 파일이 단일 진실 — 작업 전후 반드시 갱신**
> compact / 세션 종료 전 / 작업 완료 시 즉시 상태 업데이트.
> 해결된 이슈는 strikethrough(~~취소선~~) + ✅ 표시, 남은 이슈는 우선순위 유지.

---

## ✅ 최근 해결됨

| 이슈 | 해결 방식 | 커밋 |
|---|---|---|
| ~~#1 HWPML 본체 3종 미인덱싱~~ | `pipeline/hwpml_parser.py` 신규 (stdlib `xml.etree.ElementTree` 직접 파싱) → 혁신법·시행령·시행규칙 본체 인덱싱. 청크 940→1074 (+134), 평가 92.3%→**97.4% ACCEPT**. 카테고리 3·8 모두 100% 회복. hwp-mcp RecursionError 부작용도 동시 해결 | `09d04f5` |
| ~~사이드바 첫 진입 시 노출~~ | `pages/00_⚙️_환경설정.py` — `is_first_run` 분기에서 `[data-testid="stSidebar"]` `display: none` CSS 주입. 시작하기 → 직접 챗봇 진입 (환경설정 재방문 없음) | `665b7e9` |
| ~~멀티턴 부분 전환 (문서 동일, 조문·페이지만 변경) 컨텍스트 손실~~ | `pipeline/query_analyzer.py` — *부분 전환* 룰 신설. 짧은 "11조"/"다음 페이지" 등은 직전 doc_name_hint 강제 이어받음. 12조 이어 11조 95% confidence 회복 | `665b7e9` |
| ~~#5 Phase H HWP 도구 워밍업 비용~~ | `app.py` 부팅 시 `hwp-warmup` 데몬 스레드 — `_scan_dirs()` 의 모든 .hwp/.hwpx 를 `_load_pages()` 로 사전 파싱. 첫 호출 30~60초 → 즉시 응답 | `665b7e9` |
| ~~#6 답변 응답 시간 (속도 3-pack)~~ | (a) `pipeline/answer_cache.py` 디스크 JSON 캐시 — 단발 질의 + 비-판단불가 만 저장, chunker.CHUNKER_VERSION 변경 시 자동 무효화. 재질의 0.1초. (b) `pipeline/answerer.py` `progress_cb` 체인 + `app.py` 라이브 진행 UI — 도구 호출 단계 (📖/📄/🔍) ✓/⚠ 마커로 가시화 | `665b7e9` |
| ~~매뉴얼 PDF chunker 1.3.4 회귀~~ | chunker 1.4.0 페이지 기반 라우팅 (`_split_by_pages`). 청크 742→844, 페이지 커버리지 32.8%→64.3%, "151p" 회복 | `73a3a08` |
| ~~청크 수 감소 추세~~ | 1.4.0 으로 회복 | `73a3a08` |
| ~~Phase H max_turns RuntimeError~~ | `_run_query_sync` graceful transport 폴백 → "판단불가" stub | `73a3a08` |
| ~~다른 PC ValueError (collection not found)~~ | `_collection_exists` 검사 + 빈 결과 graceful | `73a3a08` |
| ~~Phase H get_article 매뉴얼 PDF 매칭 실패~~ | search_text 자동 폴백 (Phase H+1) | 이전 |
| ~~멀티턴 대화 컨텍스트 단절~~ | rewritten_query + prior_turns 통합 | `bc2362a` |

---

## 🔴 Critical — 즉시 해결 필요

(현재 없음 — #1 HWPML 해결 완료)

---

### ~~1. HWPML 본체 3종 미인덱싱~~ ✅ 해결 (`09d04f5`)

`pipeline/hwpml_parser.py` 신규 (stdlib XML 직접 파싱) → 혁신법 56·시행령 73·시행규칙 5 = **134 청크 추가**. hwp-mcp RecursionError 우회 부작용 추가 효과. 평가 97.4% ACCEPT.

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

### ~~4. 평가셋 baseline 미갱신~~ ✅ 해결 (`09d04f5`)

`scripts/eval_full.py` 재실행 — 38/39 (97.4%) ACCEPT. baseline 갱신 완료.

---

### 4-A. 비교형 케이스 약함 (4.2 시행령 vs 시행규칙)

**증상**: 39 케이스 중 유일한 잔여 FAIL. retrieval top-1 정책에서 매뉴얼 페이지가 두 본체보다 점수 높게 잡힘.

**해결안**: 비교 의도 감지 시 *문서별 top-1* 회수 후 두 결과 묶기 (retriever multi-doc fan-out) — 또는 query_analyzer 의 doc_name_hint 가 두 개일 때 강제 분리 인덱스 검색.

**작업량**: 2~3시간 (retriever 분기 + 답변 프롬프트 보강).

**상태**: 잔여 1건이라 신규 phase 로 분리할지, 정합 후 0% 잔여 push 할지 판단 필요.

---

## 🟢 Medium — 운영·품질

### ~~5. Phase H 도구 모드 워밍업 비용~~ ✅ 해결 (`665b7e9`)

`app.py` `hwp-warmup` 데몬 스레드 — 부팅 시 모든 .hwp/.hwpx 파싱 캐시 채움.

---

### ~~6. 답변 응답 시간 (속도 3-pack 적용)~~ ✅ 해결 (`665b7e9`)

채택안: 답변 캐시(C) + HWP 워밍(B) + 출력 진행 UI(A). 미적용: 프롬프트 캐싱·page_lookup retrieval 스킵 (필요 시 향후 추가).

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
- 3-pack(A 진행 UI + B HWP 워밍 + C 답변 캐시) 적용 — 이슈 #5/#6 참조
- 잔여 옵션: 프롬프트 캐싱, page_lookup retrieval 스킵 (필요 시 향후)

### ~~12. F5 (코퍼스 — HWPML 지원)~~ ✅ 해결 (`09d04f5`)
이슈 #1 과 동일. stdlib XML 파서로 마무리.

### 12-A. F5+ (외부 OpenAPI fetch)
- 국가법령정보센터 OpenAPI 로 본문 자동 갱신 + 외부 법령 보완
- 라이센스·캐시·갱신 정책 검토 필요
- korean-law-mcp 가 답변 시 *조회용*으로 이미 동작 — 인덱싱 layer 만 추가하면 됨
- 우선도: 코퍼스 정적 인덱싱 안정 후

### 13. Phase J (실시간 풀 마이그레이션)
- 의미 검색 약화 위험으로 비추 (Hybrid 유지가 정답)
- 상태: 검토 종료

---

## 진행 권장 순서

| 우선 | 이슈 | 작업량 | 효과 |
|---|---|---|---|
| 1 | **#4-A 비교형 케이스 (4.2)** — retriever multi-doc fan-out | 2~3시간 | 평가 39/39 100% 가능 |
| 2 | **#12 F5 (외부 OpenAPI fetch — 미래)** — 본문 자동 갱신 + 외부 법령 보완 | 4~6시간 | 코퍼스 stale 방지, 다른 법령 즉석 인용 |

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
