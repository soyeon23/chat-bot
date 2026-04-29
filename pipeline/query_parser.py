"""
질의에서 한국 법령/공문서 메타 힌트를 정규식으로 추출.

retriever 의 prefilter 단계에서 사용:
1) 조문번호 직접 질의("제15조 제2항") → article_no 페이로드 매칭
2) 별표/별지 질의 → article_no 매칭
3) 핵심 키워드(4글자+ 한글 명사구) → text 페이로드 매칭

벡터 검색을 보강하는 신호로만 사용된다. 추출 실패해도 실패하지 않는다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List


# ──────────────────────────────────────────────────────────────────
# 정규식 패턴
# 한국 법령 표기 관용을 모두 흡수: "제 15 조", "15조", "제15조2항", "별표2", "별표 2"
# ──────────────────────────────────────────────────────────────────

# "제15조", "제 15조", "제15조의2", "15조" → "제15조" / "제15조의2"
_ARTICLE_RE = re.compile(
    r"제?\s*(\d+)\s*조(?:\s*의\s*(\d+))?"
)

# "제2항" / "제 2 항" / "2항"
_PARAGRAPH_RE = re.compile(r"제?\s*(\d+)\s*항")

# "제3호" / "3호"
_ITEM_RE = re.compile(r"제?\s*(\d+)\s*호")

# "별표2" / "별표 2"
_APPENDIX_RE = re.compile(r"별표\s*(\d+)")

# "별지2" / "별지 2"
_FORM_RE = re.compile(r"별지\s*(\d+)")

# "제7절"
_SECTION_RE = re.compile(r"제?\s*(\d+)\s*절")

# 문서 종류 힌트
_DOC_TYPE_HINTS = {
    "시행령": "시행령",
    "시행규칙": "시행규칙",
    "법률": "법률",
    "본법": "법률",
    "본권": "법률",     # 매뉴얼 "본권" 표현
    "운영요령": "운영요령",
    "공고문": "공고문",
    "FAQ": "FAQ",
    "가이드": "가이드",
}

# 4글자 미만이라도 살리고 싶은 도메인 키워드 (예: "비목", "단가")
# 행위 동사 어간(사용/구매/지급)도 포함 — 다른 키워드와 결합한 구(phrase) 매칭에 활용.
_DOMAIN_KEYWORDS = {
    # 비목/금액/한도
    "비목", "단가", "한도", "범위", "비율", "공고", "직접비", "간접비",
    "수당", "용역", "재료", "장비", "출장", "회의비", "활동비",
    "인건비", "재료비", "활용비", "임차료", "운영비",
    # 행위 동사 어간 (구문 매칭 용도)
    "사용", "구매", "집행", "지급", "정산", "변경", "신청",
}

# 코퍼스 전반에 너무 자주 등장해 정보량이 거의 없는 키워드.
# 키워드 추출 시 제외 — 페이로드 필터에서 200+ 매치를 유발해 노이즈가 됨.
# (실제 운영 단계에서 IDF 계산으로 자동 산출하는 것이 이상적이나, 1차 릴리스에서는 화이트리스트 운영.)
_CORPUS_STOPWORDS = {
    "국가연구개발혁신법", "혁신법",
    "연구개발과제", "연구개발기관",
}

# 키워드 추출 시 제외할 불용어 (조사·동사·의문사 등)
# 도메인 화이트리스트(_DOMAIN_KEYWORDS)에 있는 단어는 추출 단계에서 우선순위 높음.
_STOPWORDS = {
    # 동사·어미
    "가능", "있는지", "있나요", "있나", "있어요",
    "있는", "있다", "관련", "필요", "해당", "포함", "제외",
    "방법", "처리", "절차", "안내",
    # 의문사
    "뭐", "무엇", "어떤", "어느", "어디", "왜", "언제", "얼마",
    "알려줘", "알려주세요", "알려", "보여줘", "보여주세요",
    # 조사·접속어
    "그리고", "그러면", "또한", "또는", "에서", "에게", "으로",
    "하는", "하지", "되는", "되어", "되며",
    # 일반 명사 (질문 문맥에서 의미 약함)
    "문서", "내용", "조문", "사항", "경우", "다음",
    # 기존 도메인 어휘 중 과대매칭 위험 (전부 매칭해버림)
    "연구개발", "연구개발비", "연구개발과제",
}


@dataclass
class QueryHints:
    article_nos: List[str] = field(default_factory=list)   # ["제15조", "제15조의2"]
    paragraphs: List[str] = field(default_factory=list)    # ["제2항"]
    items: List[str] = field(default_factory=list)         # ["제3호"]
    appendices: List[str] = field(default_factory=list)    # ["별표2"]
    forms: List[str] = field(default_factory=list)         # ["별지1"]
    sections: List[str] = field(default_factory=list)      # ["제7절"]
    keywords: List[str] = field(default_factory=list)      # ["연구활동비", "비목"]
    doc_type_hints: List[str] = field(default_factory=list)  # ["시행령"]
    # 메타-의도 플래그: 질의가 "종전 vs 혁신법" 같은 비교/변경 정보 요구인지.
    # True 시 retriever 가 "종전" + "혁신법" 동시 포함 청크에 추가 부스트.
    comparison_intent: bool = False
    # LLM 분석기(`query_analyzer.py`)에서 채워지는 추가 힌트.
    # 정규식 모드에서도 페이지 직접 표기는 가볍게 잡는다.
    target_pages: List[int] = field(default_factory=list)   # [151]
    doc_name_hint: str = ""                                 # "본권" / "매뉴얼" / "시행령"
    # 검색 라우팅 신호. "page_lookup" | "article_lookup" | "comparison" | "open" | "chat"
    # "chat" 인 경우 retrieval/answerer 단계를 스킵하고 chat_response 를 그대로 사용한다.
    kind: str = "open"
    # kind == "chat" 일 때 분석기가 함께 생성한 즉답 텍스트.
    # 다른 kind 에서는 빈 문자열.
    chat_response: str = ""

    def has_structural(self) -> bool:
        """조문/항/호/별표/별지/절 중 하나라도 추출됐는지."""
        return bool(
            self.article_nos
            or self.appendices
            or self.forms
            or self.sections
        )

    def has_any(self) -> bool:
        return bool(
            self.article_nos
            or self.paragraphs
            or self.items
            or self.appendices
            or self.forms
            or self.sections
            or self.keywords
            or self.doc_type_hints
            or self.target_pages
            or self.doc_name_hint
        )

    def to_dict(self) -> dict:
        return {
            "article_nos": self.article_nos,
            "paragraphs": self.paragraphs,
            "items": self.items,
            "appendices": self.appendices,
            "forms": self.forms,
            "sections": self.sections,
            "keywords": self.keywords,
            "doc_type_hints": self.doc_type_hints,
            "comparison_intent": self.comparison_intent,
            "target_pages": self.target_pages,
            "doc_name_hint": self.doc_name_hint,
            "kind": self.kind,
            "chat_response": self.chat_response,
        }


# ──────────────────────────────────────────────────────────────────
# 추출 헬퍼
# ──────────────────────────────────────────────────────────────────

def _extract_articles(question: str) -> List[str]:
    """제N조 / 제N조의M 추출. 정규형으로 변환해 반환 (공백 제거)."""
    out: List[str] = []
    for m in _ARTICLE_RE.finditer(question):
        n, sub = m.group(1), m.group(2)
        if sub:
            out.append(f"제{n}조의{sub}")
        else:
            out.append(f"제{n}조")
    # 중복 제거 (순서 보존)
    seen = set()
    deduped = []
    for a in out:
        if a not in seen:
            seen.add(a)
            deduped.append(a)
    return deduped


def _extract_simple(pattern: re.Pattern, prefix: str, suffix: str, question: str) -> List[str]:
    """제N항/제N호/제N절 등 단순 패턴 → '{prefix}{N}{suffix}'."""
    out: List[str] = []
    for m in pattern.finditer(question):
        out.append(f"{prefix}{m.group(1)}{suffix}")
    seen = set()
    deduped = []
    for x in out:
        if x not in seen:
            seen.add(x)
            deduped.append(x)
    return deduped


def _extract_appendix(pattern: re.Pattern, prefix: str, question: str) -> List[str]:
    """'별표 N' / '별표N' → '별표N' (공백 제거 정규형)."""
    out: List[str] = []
    for m in pattern.finditer(question):
        out.append(f"{prefix}{m.group(1)}")
    seen = set()
    deduped = []
    for x in out:
        if x not in seen:
            seen.add(x)
            deduped.append(x)
    return deduped


# 한국어 명사구 추출용: 한글/숫자 토큰만 남기고 split
_TOKEN_SPLIT_RE = re.compile(r"[^가-힣A-Za-z0-9]+")

# 한국어 조사 / 어미 — 키워드 끝에서 제거.
# 길이 긴 것부터 매칭해야 함 (예: "에서는" 먼저, "는"은 나중).
_PARTICLE_SUFFIXES = (
    # 격조사 결합형 (긴 것 먼저)
    "에서는", "에게서", "으로부터", "로부터",
    "에서", "에게", "으로", "로서", "로써",
    "께서", "한테",
    # 보조사
    "까지", "마저", "조차", "부터", "이라도", "라도", "이라는", "라는",
    # 단순 조사
    "은", "는", "이", "가", "을", "를", "의", "에", "와", "과",
    "도", "만", "야", "여", "로",
    # 어미·종결사
    "이다", "입니다", "인지", "한지", "는지", "을지", "ㄹ지",
    "한가요", "인가요", "나요", "어요", "아요",
    "할까요", "되나요", "하지", "되며", "하며",
    "할", "한", "함", "함은", "함이",
    # 호칭/지시
    "라고",
)


def _strip_particle(tok: str) -> str:
    """단어 끝의 한국어 조사·어미를 제거. 너무 짧아지면 원본 유지."""
    for suf in _PARTICLE_SUFFIXES:
        if tok.endswith(suf) and len(tok) - len(suf) >= 2:
            return tok[: -len(suf)]
    return tok


def _extract_keywords(question: str) -> List[str]:
    """
    한글 4자+ 토큰을 키워드로 추출. 도메인 화이트리스트(`_DOMAIN_KEYWORDS`)는
    글자 길이 제한과 무관하게 통과시킨다. `_STOPWORDS`는 전체 길이에 관계없이 제외.
    조사/어미는 끝에서 한 번 제거한다.
    """
    raw_tokens = [t for t in _TOKEN_SPLIT_RE.split(question) if t]
    out: List[str] = []
    for tok in raw_tokens:
        # 조사 제거 시도
        stripped = _strip_particle(tok)
        # 도메인 화이트리스트는 stopwords보다 우선순위 높음 (예: '사용'은 일반적으론 stopword여도
        # 도메인 어휘로 보존해 다른 키워드와 결합 phrase 매칭에 사용).
        if stripped in _DOMAIN_KEYWORDS:
            out.append(stripped)
            continue
        if stripped in _STOPWORDS:
            continue
        if stripped in _CORPUS_STOPWORDS:
            continue
        # 한글 위주 4자 이상 (예: "연구활동비", "사용용도")
        if len(stripped) >= 4 and re.search(r"[가-힣]", stripped):
            out.append(stripped)
    # dedupe
    seen = set()
    deduped = []
    for x in out:
        if x not in seen:
            seen.add(x)
            deduped.append(x)
    return deduped


# 비교/변경 의도 단어 — 매뉴얼의 "종전 vs 혁신법" 비교표를 우선 검색하기 위함.
_COMPARISON_TRIGGERS = (
    "달라진", "달라지는", "달라짐", "달라졌", "차이",
    "변경된", "바뀐", "바뀌었", "전과 후", "이전과", "예전과",
    "종전", "기존과", "비교",
)


def _detect_comparison_intent(question: str) -> bool:
    """질의가 종전 vs 혁신법 비교/변경을 묻는지 감지."""
    return any(t in question for t in _COMPARISON_TRIGGERS)


def _extract_doc_type_hints(question: str) -> List[str]:
    out: List[str] = []
    for surface, canonical in _DOC_TYPE_HINTS.items():
        if surface in question:
            out.append(canonical)
    seen = set()
    deduped = []
    for x in out:
        if x not in seen:
            seen.add(x)
            deduped.append(x)
    return deduped


# ──────────────────────────────────────────────────────────────────
# 공개 API
# ──────────────────────────────────────────────────────────────────

def parse_query(question: str) -> QueryHints:
    """
    질의를 파싱해 메타 힌트를 반환.

    실패해도 예외를 던지지 않는다. 빈 리스트가 들어 있을 수 있다.
    """
    if not question:
        return QueryHints()

    return QueryHints(
        article_nos=_extract_articles(question),
        paragraphs=_extract_simple(_PARAGRAPH_RE, "제", "항", question),
        items=_extract_simple(_ITEM_RE, "제", "호", question),
        appendices=_extract_appendix(_APPENDIX_RE, "별표", question),
        forms=_extract_appendix(_FORM_RE, "별지", question),
        sections=_extract_simple(_SECTION_RE, "제", "절", question),
        keywords=_extract_keywords(question),
        doc_type_hints=_extract_doc_type_hints(question),
        comparison_intent=_detect_comparison_intent(question),
        target_pages=_extract_pages(question),
    )


# 페이지 표기 정규식 — LLM 분석기가 죽었을 때 fallback 용.
# "151p", "151 페이지", "151쪽", "p. 151", "p151"
_PAGE_RE = re.compile(
    r"(?:p\.?\s*(\d{1,4})\b|(\d{1,4})\s*(?:p\b|페이지|쪽))",
    re.IGNORECASE,
)


def _extract_pages(question: str) -> List[int]:
    out: List[int] = []
    for m in _PAGE_RE.finditer(question):
        raw = m.group(1) or m.group(2)
        try:
            n = int(raw)
        except (TypeError, ValueError):
            continue
        if 1 <= n <= 9999 and n not in out:
            out.append(n)
    return out
