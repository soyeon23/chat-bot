"""
법제처 law.go.kr 공식 REST API 검색 모듈.
DuckDuckGo 대신 사용 — 법령·판례만 검색한다.

엔드포인트:
  - 법령 검색: GET /DRF/lawSearch.do?target=law
  - 법령 본문: GET /DRF/lawService.do?target=law&MST=...
  - 판례 검색: GET /DRF/lawSearch.do?target=prec
  - 행정규칙:  GET /DRF/lawSearch.do?target=admrul
"""
from __future__ import annotations

import os
import xml.etree.ElementTree as ET

import requests
from dotenv import load_dotenv

load_dotenv()

_OC = os.getenv("KOREAN_LAW_OC", "")
_BASE = "https://www.law.go.kr/DRF"
_TIMEOUT = 10
_TEXT_LIMIT = 2000
_LOW_CONFIDENCE_THRESHOLD = 0.65


def should_trigger(qdrant_scores: list[float]) -> bool:
    """Qdrant 최고 유사도가 낮으면 공식 API 검색 필요."""
    if not qdrant_scores:
        return True
    return max(qdrant_scores) < _LOW_CONFIDENCE_THRESHOLD


def search_official_sources(question: str, max_per_type: int = 3) -> list[dict]:
    """
    법령 + 판례 + 행정규칙을 법제처 공식 API로 검색한다.
    실패 시 빈 리스트 반환 (파이프라인 중단 없음).
    """
    chunks: list[dict] = []
    chunks += _search_laws(question, max_per_type)
    chunks += _search_precedents(question, max_per_type)
    chunks += _search_admin_rules(question, max_per_type)

    if chunks:
        print(f"  [법제처 API] {len(chunks)}개 공식 문서 추가")
    else:
        print("  [법제처 API] 검색 결과 없음")
    return chunks


# ──────────────────────────────────────────────────────────────────
# 법령 검색
# ──────────────────────────────────────────────────────────────────

def _search_laws(query: str, display: int = 3) -> list[dict]:
    """법령 키워드 검색 → 상위 결과 본문 가져오기."""
    try:
        resp = requests.get(
            f"{_BASE}/lawSearch.do",
            params={"OC": _OC, "target": "law", "type": "XML",
                    "query": query, "display": display},
            timeout=_TIMEOUT,
        )
        root = ET.fromstring(resp.text)
    except Exception as exc:
        print(f"  [법제처 API] 법령 검색 오류: {exc}")
        return []

    chunks = []
    for law in root.findall(".//law"):
        law_name = _tx(law, "법령명한글")
        mst = _tx(law, "법령일련번호")
        ministry = _tx(law, "소관부처명")
        if not mst:
            continue
        text = _fetch_law_text(mst, law_name)
        if not text:
            continue
        chunks.append({
            "doc_name": law_name,
            "doc_type": "공식법령(API)",
            "article_no": f"소관: {ministry}",
            "article_title": "",
            "page": 0,
            "text": text[:_TEXT_LIMIT],
        })
    return chunks


def _fetch_law_text(mst: str, law_name: str = "") -> str:
    """MST로 법령 조문 전문 가져오기."""
    try:
        resp = requests.get(
            f"{_BASE}/lawService.do",
            params={"OC": _OC, "target": "law", "MST": mst, "type": "XML"},
            timeout=_TIMEOUT,
        )
        root = ET.fromstring(resp.text)
    except Exception:
        return ""

    parts: list[str] = []
    for jo in root.findall(".//조문단위"):
        no = _tx(jo, "조문번호")
        title = _tx(jo, "조문제목")
        content = _tx(jo, "조문내용")
        header = f"제{no}조" + (f"({title})" if title else "") if no and no != "0" else ""
        if content:
            parts.append(f"{header} {content}".strip())
        for hang in jo.findall("항"):
            h_no = _tx(hang, "항번호")
            h_content = _tx(hang, "항내용")
            if h_content:
                parts.append(f"  {h_no}항: {h_content}")
            for ho in hang.findall("호"):
                ho_no = _tx(ho, "호번호")
                ho_content = _tx(ho, "호내용")
                if ho_content:
                    parts.append(f"    {ho_no}호: {ho_content}")
    return "\n".join(parts)


# ──────────────────────────────────────────────────────────────────
# 판례 검색
# ──────────────────────────────────────────────────────────────────

def _search_precedents(query: str, display: int = 3) -> list[dict]:
    """판례 키워드 검색 (판시사항 기준)."""
    try:
        resp = requests.get(
            f"{_BASE}/lawSearch.do",
            params={"OC": _OC, "target": "prec", "type": "XML",
                    "query": query, "display": display, "section": "all"},
            timeout=_TIMEOUT,
        )
        root = ET.fromstring(resp.text)
    except Exception as exc:
        print(f"  [법제처 API] 판례 검색 오류: {exc}")
        return []

    chunks = []
    for prec in root.findall(".//prec"):
        case_no = _tx(prec, "사건번호")
        case_name = _tx(prec, "사건명")
        summary = _tx(prec, "판시사항") or _tx(prec, "판결요지")
        court = _tx(prec, "법원명")
        if not summary:
            continue
        chunks.append({
            "doc_name": f"[판례] {court} {case_no}",
            "doc_type": "판례(API)",
            "article_no": case_no,
            "article_title": case_name,
            "page": 0,
            "text": f"사건명: {case_name}\n판시사항: {summary}"[:_TEXT_LIMIT],
        })
    return chunks


# ──────────────────────────────────────────────────────────────────
# 행정규칙 검색
# ──────────────────────────────────────────────────────────────────

def _search_admin_rules(query: str, display: int = 2) -> list[dict]:
    """행정규칙(훈령·예규·고시) 키워드 검색."""
    try:
        resp = requests.get(
            f"{_BASE}/lawSearch.do",
            params={"OC": _OC, "target": "admrul", "type": "XML",
                    "query": query, "display": display},
            timeout=_TIMEOUT,
        )
        root = ET.fromstring(resp.text)
    except Exception as exc:
        print(f"  [법제처 API] 행정규칙 검색 오류: {exc}")
        return []

    chunks = []
    for rule in root.findall(".//admrul"):
        rule_name = _tx(rule, "행정규칙명")
        ministry = _tx(rule, "소관부처명")
        mst = _tx(rule, "행정규칙일련번호")
        if not rule_name:
            continue
        # 행정규칙 본문은 별도 endpoint 없이 제목+부처만 참고로 활용
        chunks.append({
            "doc_name": rule_name,
            "doc_type": "행정규칙(API)",
            "article_no": f"소관: {ministry}",
            "article_title": "",
            "page": 0,
            "text": f"[행정규칙] {rule_name} (소관: {ministry})",
        })
    return chunks


# ──────────────────────────────────────────────────────────────────
# 유틸
# ──────────────────────────────────────────────────────────────────

def _tx(element: ET.Element, tag: str) -> str:
    el = element.find(tag)
    return (el.text or "").strip() if el is not None else ""
