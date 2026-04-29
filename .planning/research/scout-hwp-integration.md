# HWP Integration Research — 연구행정 RAG 챗봇

> 작성: 2026-04-28 | 대상 배포환경: Windows PC (개발: macOS)

---

## A. hwp-mcp 심층 분석

### treesoop/hwp-mcp

**요약**

`treesoop/hwp-mcp`는 HWP v5.0 및 HWPX 파일을 읽고 쓰는 MCP 서버다.
한컴 오피스 설치 불필요 — 자체 구현한 OLE/CFB 바이너리 파서로 동작한다.
의존성: `mcp>=1.0.0`, `olefile>=0.47` 두 가지만 사용. Python ≥3.10 필요.
최초 릴리스 2026-03-31(v0.1.0→v0.1.1). MIT 라이선스. 개발자 1인 프로젝트이며
`uvx`로 설치·실행하므로 Claude Code와 1-line 통합 가능.

**노출 도구 목록**

| 도구 | 기능 |
|------|------|
| `read_hwp` | 전체 문서 추출 (텍스트+표+이미지 정보) |
| `read_hwp_text` | 텍스트만 추출 |
| `read_hwp_tables` | 표를 마크다운으로 추출 |
| `list_hwp_images` | 포함된 이미지 목록 반환 |
| `extract_hwp_images` | 이미지를 디스크에 저장 |
| `fill_hwp_template` | 플레이스홀더 치환 (서식 자동화) |
| `replace_hwp_text` | 찾기-바꾸기 |
| `create_hwpx_document` | 새 HWPX 문서 생성 |

**설치 명령**

Claude Code에 MCP 서버로 등록:
```
claude mcp add hwp-mcp -- uvx --from hwp-mcp hwp-mcp
```

또는 Claude Desktop / cursor settings.json에 uvx 경로 직접 지정.

**채택 가능성: 조건부**

이유:
- 인덱싱 시점(batch_ingest.py)에서 MCP 도구 호출은 구조적 불일치. MCP 프로토콜은
  Claude 에이전트 ↔ 서버 간 런타임 통신이므로, Python 프로세스에서 직접 `import`할 수 없다.
  `read_hwp` 결과를 받으려면 별도 subprocess로 MCP 서버를 기동하고 JSON-RPC 호출을
  해야 한다 — 인덱싱 파이프라인에 과도한 복잡성을 추가한다.
- **런타임 답변 경로**에서는 유효하다: 사용자가 `.hwp` 파일을 업로드했을 때 Claude 에이전트가
  `read_hwp` 도구를 직접 호출해 텍스트를 추출하는 흐름은 자연스럽다.
- Windows / macOS 모두 동작 확인(공식 문서 명시).
- 프로젝트가 2026-03 신생이라 production 안정성 미검증. 대형 법령 문서(수백 페이지)
  테이블 파싱 품질은 테스트 필요.

**인덱싱 시점 통합 경로 (조건부 채택 시)**

`batch_ingest.py`의 `ingest_one()` 함수에 HWP 분기를 추가한다:

```python
# batch_ingest.py 변경 위치
def ingest_one(file_path: Path) -> bool:
    if file_path.suffix.lower() in (".hwp", ".hwpx"):
        text = _hwp_to_text_via_mcp(file_path)   # 신규 함수
        # text → ParseResult 래핑 후 기존 청크/임베딩 파이프라인으로 진입
    else:
        result = parse_pdf(file_path, save_raw=False)
```

`_hwp_to_text_via_mcp()`는 `subprocess`로 MCP 서버를 기동하고
`mcp` Python SDK의 `ClientSession`을 통해 `read_hwp_text` 호출:
```python
from mcp import ClientSession, StdioServerParameters
```
`mcp>=1.0.0`은 이미 `requirements.txt`에 있으므로 추가 패키지 불필요.

---

### jkf87/hwp-mcp (비교 참고)

Windows 전용. Hancom Office + ActiveX 컨트롤 설치 필수. 쓰기/편집 중심.
우리 파싱(읽기 전용) 요구에 부합하지 않으며 Windows 의존성이 개발환경(macOS)을 막는다.
**채택 아니오.**

---

## B. 직접 파서 비교

| 라이브러리 | 라이선스 | 마지막 릴리스 | macOS | Windows | HWP5 | HWPX | 채택 가능 |
|---|---|---|---|---|---|---|---|
| **pyhwp (mete0r)** | AGPL-3.0 | 2020-05 (v0.1b15) | O | O (OS독립) | O | X | 아니오 |
| **hwp5txt CLI** | AGPL-3.0 | pyhwp 동일 | O | 미검증 | O | X | 아니오 |
| **olefile 직접** | BSD-2 | 활발 (2024) | O | O | 부분(PrvText만) | X | 조건부 |
| **treesoop/hwp-mcp** | MIT | 2026-03 | O | O | O | O | 조건부 |
| **LibreOffice headless** | MPL-2.0 | 활발 | O | O | O(확장필요) | 제한 | 조건부 |

**pyhwp 상세**: PyPI 최종 배포 2020-05. Python 3.8까지만 공식 지원.
Python 3.11/3.12에서 동작 미보장. AGPL 라이선스는 SaaS 배포 시 소스 공개 의무.
`hwp5txt` 텍스트 추출 품질은 표·각주 누락 많음. **사용 불가.**

**olefile 직접**: `pip install olefile` 후 HWP의 `PrvText` 스트림을 UTF-16으로 디코딩하면
제목+본문 텍스트 일부를 꺼낼 수 있다. 그러나 `PrvText`는 미리보기 요약 스트림으로
전체 내용이 잘리는 경우 있음. 표 구조 없음. 수백 페이지 법령 파일에 부적합.

**LibreOffice headless**: `soffice --headless --convert-to pdf` 로 HWP→PDF 변환 후
기존 `pdf_parser.py`를 그대로 재사용할 수 있다. HWP 지원은 기본 포함이나
복잡한 한글 서식/표는 렌더링 차이 발생. Windows 경로: `C:\Program Files\LibreOffice\program\soffice.exe`.
추가 ML 의존성 없고 코드 변경 최소 — 현 파이프라인 재사용이 강점.

---

## C. 권고

### Path A: hwp-mcp 활용 — 런타임 전용 (단기)

**언제**: 사용자가 앱 UI에서 HWP 파일을 직접 업로드해 즉시 질의하는 시나리오.
`app.py`의 업로드 처리 경로에서 Claude 에이전트가 `read_hwp_text` MCP 도구를
호출해 텍스트를 추출하면 된다. MCP 등록은 1-line(`claude mcp add`), 코드 변경은
`app.py` 업로드 핸들러에 hwp 분기 추가 정도.

**주의**: 인덱싱 파이프라인(`batch_ingest.py`)에 직접 쓰려면 MCP ClientSession 기동 코드
필요. 프로젝트 신생(v0.1.1)이므로 대형 문서 안정성 검증 후 적용 권장.

### Path B: LibreOffice headless 변환 우회 — 인덱싱 시점 (권장)

**언제**: `batch_ingest.py` 실행 시 `.hwp` 파일을 일괄 처리해야 할 때.

흐름:
1. `batch_ingest.py`에서 `.hwp` 확장자 감지
2. `subprocess.run(["soffice", "--headless", "--convert-to", "pdf", hwp_path])`
3. 생성된 PDF를 기존 `parse_pdf()` → `chunk_document()` → `embed_chunks()` 파이프라인 그대로 통과

**이유**:
- `pdf_parser.py` 재사용 → 코드 추가 최소 (변환 함수 20줄 내외)
- Windows/macOS 동일 코드 (LibreOffice 경로만 분기)
- OCR 폴백도 기존 tesseract 로직 그대로 유효
- ML 의존성 없음

**단점**: 사용자 PC에 LibreOffice 설치 필요. 한글 레이아웃 복잡 문서는 변환 오차 가능.

### Path C: 현 상태 유지 (보류 해제 전)

프로젝트 브리프(#7 항목)에 이미 "PDF 변환 우회"로 보류됨.
HWP 원본 파일이 연구 담당자 PC에서 PDF로 사전 변환 후 업로드되는 운영 시나리오라면
Path C (현 보류 유지)가 가장 낮은 리스크다.

**결론**: 단기는 LibreOffice headless 변환(Path B)이 현 파이프라인 재사용성 측면에서 최적.
런타임 직접 질의 요구가 생기면 hwp-mcp Path A를 추가. pyhwp는 AGPL + 비활성으로 제외.
