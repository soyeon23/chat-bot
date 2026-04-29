"""
Retrieval A/B 회귀 평가 — Phase A 4A (smart) vs 4B (hybrid).

각 케이스는 (질의, 합격 조건 람다)로 구성된다. 람다는 top-K 청크 중 한 개라도
조건을 만족하면 PASS.

사용:
    source .venv/bin/activate
    python scripts/eval_retrieval.py
종료 코드:
    0 — 두 방식 모두 회귀 0건 (또는 hybrid가 우세)
    1 — 회귀 또는 비교 실패
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

# 프로젝트 루트를 path에 추가
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.embedder import embed_query
from pipeline.retriever import search_chunks_smart, search_chunks_hybrid


# ──────────────────────────────────────────────────────────────────
# 합격 조건 헬퍼
# ──────────────────────────────────────────────────────────────────

def starts_with_article(article_no_prefix: str):
    def _check(r: dict) -> bool:
        return (r.get("article_no") or "").startswith(article_no_prefix)
    return _check


def article_contains(token: str):
    def _check(r: dict) -> bool:
        return token in (r.get("article_no") or "")
    return _check


def text_contains_any(*tokens: str):
    def _check(r: dict) -> bool:
        text = r.get("text", "") or ""
        return any(t in text for t in tokens)
    return _check


def text_contains_all(*tokens: str):
    def _check(r: dict) -> bool:
        text = r.get("text", "") or ""
        return all(t in text for t in tokens)
    return _check


def either(*checks):
    def _check(r: dict) -> bool:
        return any(c(r) for c in checks)
    return _check


def matches_p222_part5_9(r: dict) -> bool:
    art = r.get("article_no", "") or ""
    return "제23조" in art and "part 5/9" in art and r.get("page") == 222


def relevant_to_studentwage(r: dict) -> bool:
    text = r.get("text", "") or ""
    return "학생인건비" in text or "학생연구자" in text


# ──────────────────────────────────────────────────────────────────
# 케이스 정의 — 4 acceptance + 6 새 corpus-realistic
# ──────────────────────────────────────────────────────────────────

CASES = [
    # ─── 기존 acceptance 4건 ────────────────────────────────────
    {
        "name": "PM 버그 재현 (연구활동비 비목)",
        "query": "국가연구개발혁신법 본권에서 연구활동비를 사용할 수 있는 비목 뭐있는지 알려줘",
        "top_k": 5,
        "check": matches_p222_part5_9,
        "check_desc": "article_no startswith '제23조 (part 5/9)' AND page == 222",
    },
    {
        "name": "조문번호 직접 (제15조 제2항)",
        "query": "혁신법 제15조 제2항 전문",
        "top_k": 5,
        "check": starts_with_article("제15조"),
        "check_desc": "article_no startswith '제15조'",
    },
    {
        "name": "별표 직접 (별표 2 직접비)",
        "query": "별표 2 직접비 항목 기준",
        "top_k": 5,
        "check": lambda r: (r.get("article_no") or "").startswith("별표2")
                           or (r.get("article_no") or "").startswith("별표 2"),
        "check_desc": "article_no startswith '별표2' or '별표 2'",
    },
    {
        "name": "비-회귀: 학생인건비 일반 질의",
        "query": "학생인건비로 노트북 구매 가능한가요?",
        "top_k": 5,
        "check": relevant_to_studentwage,
        "check_desc": "top-5에 학생인건비/학생연구자 관련 청크",
    },

    # ─── 신규 6건 ──────────────────────────────────────────────
    {
        "name": "간접비 비율 한도 / 정산",
        "query": "간접비 비율 한도와 초과 시 처리",
        "top_k": 5,
        # 간접비 관련 핵심 조문은 제22~26조 범위, 제36조 (정산)
        "check": either(
            starts_with_article("제22조"),
            starts_with_article("제23조"),
            starts_with_article("제24조"),
            starts_with_article("제25조"),
            starts_with_article("제26조"),
            starts_with_article("제36조"),
            text_contains_all("간접비", "비율"),
        ),
        "check_desc": "제22~26조/제36조 OR text contains '간접비' AND '비율'",
    },
    {
        "name": "이월 신청 기한 / 절차",
        "query": "연구개발비 이월 신청 기한과 절차 알려줘",
        "top_k": 5,
        # 이월은 제27조 (이월 승인) 또는 제73조 (변경) 또는 텍스트에 '이월' + '승인'
        "check": either(
            starts_with_article("제27조"),
            starts_with_article("제73조"),
            text_contains_all("이월", "승인"),
            text_contains_all("이월", "신청"),
        ),
        "check_desc": "제27조/제73조 OR text contains '이월' + ('승인' or '신청')",
    },
    {
        "name": "기술료 사용 용도",
        "query": "기술료 징수율과 사용 용도",
        "top_k": 5,
        # 기술료 핵심 조문: 제18조, 제19조, 제35조, 제41조, 제17조(영), 제11조의4(법)
        "check": either(
            article_contains("기술료"),
            starts_with_article("제17조"),
            starts_with_article("제18조"),
            starts_with_article("제19조"),
            starts_with_article("제35조"),
            starts_with_article("제41조"),
            starts_with_article("제11조의4"),
            text_contains_all("기술료", "사용"),
        ),
        "check_desc": "기술료 관련 조문 OR text contains '기술료' + '사용'",
    },
    {
        "name": "별표 5 (시설장비/평가위원)",
        "query": "별표 5 평가위원 자격 또는 연구시설장비 심의",
        "top_k": 5,
        "check": either(
            lambda r: (r.get("article_no") or "").startswith("별표5"),
            lambda r: (r.get("article_no") or "").startswith("별표 5"),
        ),
        "check_desc": "article_no startswith '별표5' or '별표 5'",
    },
    {
        "name": "공모 절차 (연구개발과제)",
        "query": "연구개발과제 공모 절차와 기간",
        "top_k": 5,
        # 공모는 제9조 (공모 절차)
        "check": either(
            starts_with_article("제9조"),
            text_contains_all("공모", "절차"),
        ),
        "check_desc": "제9조 OR text contains '공모' + '절차'",
    },
    {
        "name": "참여연구원 변경 절차",
        "query": "참여연구원 변경 절차 단계",
        "top_k": 5,
        # 참여연구원/연구책임자 변경은 제15조(변경/중단), 제13조 (협약 변경)
        "check": either(
            starts_with_article("제13조"),
            starts_with_article("제15조"),
            starts_with_article("제64조"),
            text_contains_all("참여연구원"),
            text_contains_all("연구책임자", "변경"),
        ),
        "check_desc": "제13조/제15조/제64조 OR text contains '참여연구원' or '연구책임자'+'변경'",
    },
]


# ──────────────────────────────────────────────────────────────────
# Runner
# ──────────────────────────────────────────────────────────────────

def _evaluate(name: str, search_fn, query: str, top_k: int, check) -> dict:
    """단일 (search_fn, query) 실행 → {passed, top1, latency_ms, top5}."""
    t0 = time.time()
    try:
        res = search_fn(query, embed_query(query), top_k=top_k)
    except Exception as e:
        return {"passed": False, "top1": f"<error: {e}>", "latency_ms": 0.0, "top5": []}
    latency_ms = (time.time() - t0) * 1000
    if not res:
        return {"passed": False, "top1": "<no results>", "latency_ms": latency_ms, "top5": []}

    passed = any(check(r) for r in res)
    top1 = f"{res[0].get('article_no','')[:25]} p.{res[0].get('page',0)}"
    top5_summary = [
        (i + 1, check(r), f"{r.get('article_no','')[:30]} p.{r.get('page',0)}")
        for i, r in enumerate(res)
    ]
    return {
        "passed": passed,
        "top1": top1,
        "latency_ms": latency_ms,
        "top5": top5_summary,
    }


def run_case(case: dict) -> dict:
    print(f"\n--- {case['name']} ---")
    print(f"  query: {case['query']!r}")
    print(f"  expect: {case['check_desc']}")

    smart = _evaluate(
        "smart", search_chunks_smart, case["query"], case["top_k"], case["check"],
    )
    hybrid = _evaluate(
        "hybrid", search_chunks_hybrid, case["query"], case["top_k"], case["check"],
    )

    # 결과 표시
    print(f"  [SMART ] {'PASS' if smart['passed'] else 'FAIL'}  "
          f"top1={smart['top1']!r:35s}  {smart['latency_ms']:6.1f}ms")
    for rank, ok, label in smart["top5"]:
        marker = " *" if ok else "  "
        print(f"     #{rank}{marker} {label}")
    print(f"  [HYBRID] {'PASS' if hybrid['passed'] else 'FAIL'}  "
          f"top1={hybrid['top1']!r:35s}  {hybrid['latency_ms']:6.1f}ms")
    for rank, ok, label in hybrid["top5"]:
        marker = " *" if ok else "  "
        print(f"     #{rank}{marker} {label}")

    return {
        "name": case["name"],
        "smart": smart,
        "hybrid": hybrid,
    }


def main() -> int:
    print("=" * 80)
    print("Retrieval A/B harness — smart vs hybrid (10 cases)")
    print("=" * 80)

    # BM25 코퍼스 빌드 워밍업 — 케이스별 latency에 첫 빌드 비용이 섞이지 않도록
    print("\n[warmup] Bm25Corpus.get() ...")
    from pipeline.bm25_index import Bm25Corpus
    from pipeline.retriever import get_qdrant_client
    _client = get_qdrant_client()
    Bm25Corpus.get(client=_client, verbose=True)
    # 임베더 워밍업 (모델 로드)
    embed_query("워밍업")

    # ‼️ Qdrant local mode: 코퍼스 빌드 후 client 닫지 않으면 다음 search_chunks_*가
    # 동일 storage를 다시 열려 할 때 충돌. 명시적으로 close.
    try:
        _client.close()
    except Exception:
        pass

    rows = []
    for case in CASES:
        rows.append(run_case(case))

    # ── 표 요약 ──────────────────────────────────────────────────
    print("\n" + "=" * 80)
    print(f"{'Case':<40s} {'smart':<7s} {'hybrid':<7s} {'Δ':<6s} {'s_lat':<7s} {'h_lat':<7s}")
    print("-" * 80)

    smart_pass = 0
    hybrid_pass = 0
    smart_lat_sum = 0.0
    hybrid_lat_sum = 0.0

    for row in rows:
        s_ok = row["smart"]["passed"]
        h_ok = row["hybrid"]["passed"]
        smart_pass += int(s_ok)
        hybrid_pass += int(h_ok)
        smart_lat_sum += row["smart"]["latency_ms"]
        hybrid_lat_sum += row["hybrid"]["latency_ms"]
        delta = ""
        if s_ok and not h_ok:
            delta = "-1"
        elif h_ok and not s_ok:
            delta = "+1"
        else:
            delta = "="
        s_label = "PASS" if s_ok else "FAIL"
        h_label = "PASS" if h_ok else "FAIL"
        print(
            f"{row['name'][:40]:<40s} "
            f"{s_label:<7s} {h_label:<7s} {delta:<6s} "
            f"{row['smart']['latency_ms']:6.1f} {row['hybrid']['latency_ms']:6.1f}"
        )
    print("-" * 80)

    n = len(rows)
    s_avg = smart_lat_sum / max(n, 1)
    h_avg = hybrid_lat_sum / max(n, 1)
    print(f"\nAggregate:")
    print(f"  smart  : {smart_pass}/{n} passed   avg_latency={s_avg:6.1f}ms")
    print(f"  hybrid : {hybrid_pass}/{n} passed   avg_latency={h_avg:6.1f}ms")

    # 결정 룰
    print("\nDecision:")
    if hybrid_pass >= smart_pass:
        print(f"  → HYBRID wins ({hybrid_pass} ≥ {smart_pass}).")
    else:
        print(f"  → SMART wins ({smart_pass} > {hybrid_pass}).")
        print("    (hybrid 채택 보류 — smart 회귀 위험)")

    # 회귀 케이스 (smart에서 PASS였는데 hybrid에서 FAIL)
    regressions = [
        r["name"] for r in rows
        if r["smart"]["passed"] and not r["hybrid"]["passed"]
    ]
    gains = [
        r["name"] for r in rows
        if r["hybrid"]["passed"] and not r["smart"]["passed"]
    ]
    if regressions:
        print(f"\n  Regressions (smart→hybrid): {regressions}")
    if gains:
        print(f"  Gains (smart→hybrid): {gains}")

    # exit code: hybrid가 smart보다 적게 통과하면 1
    return 0 if hybrid_pass >= smart_pass else 1


if __name__ == "__main__":
    sys.exit(main())
