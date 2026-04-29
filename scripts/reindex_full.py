"""전체 코퍼스 재인덱싱 스크립트 (Phase G3).

목적
----
G1 (chunker 정규식 강화) + G2 (별표 라우팅) 패치 적용 후, 인덱싱 가능한
**모든 문서**의 청크를 새 chunker 로 다시 만들어 Qdrant 컬렉션을 통째로
재구성한다.

전제
----
- 시행령 본체 HWP / 시행규칙 본체 HWP 는 HWPML(XML) 형식이라 hwp-mcp 가
  파싱 거부 (roadmap-future.md F5 명시). 본 스크립트 범위 외.
- 인덱싱 대상 = batch_ingest.py 가 자동 수집하는 파일 + 환경설정 hwp_dir.
  현재 코퍼스 기준:
    * 매뉴얼 PDF 1개 (510 페이지)
    * 시행령 별표 6개 HWP (별표 1·2·4·5·6·7)
    * 시행규칙 별지서식 10개 HWP (제1~10호서식)

흐름
----
1. Streamlit pause (있다면)
2. Qdrant 컬렉션 drop & recreate (`ensure_collection(recreate=True)`)
3. data/ingest_done.txt 초기화 (force 재인덱싱)
4. batch_ingest.main() 실행 — 진행률 stdout 라이브
5. 인덱싱 후 메트릭 수집 (전/후 비교)
6. Streamlit resume

실행
----
    cd /Users/maro/dev/company/chatbot
    source .venv/bin/activate
    python scripts/reindex_full.py

종료 코드
---------
- 0 — 정상 종료
- 1 — 인덱싱 도중 예외 발생

주의
----
- Streamlit 은 pause/resume 만. kill 금지 (위임서 제약).
- chunker.py 수정 금지. batch_ingest.py 의 인덱싱 로직 수정 금지
  (진행률 로그는 batch_ingest 가 이미 출력함).
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

# 프로젝트 루트
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ──────────────────────────────────────────────────────────────────
# Streamlit pause / resume
# ──────────────────────────────────────────────────────────────────

def _pgrep_streamlit() -> list[int]:
    try:
        out = subprocess.run(
            ["pgrep", "-f", "streamlit"],
            capture_output=True, text=True, check=False,
        )
        return [int(p) for p in out.stdout.strip().splitlines() if p.strip().isdigit()]
    except Exception:
        return []


def _stop_streamlit() -> list[int]:
    pids = _pgrep_streamlit()
    if not pids:
        return []
    print(f"  [streamlit] 일시정지: {pids}")
    for pid in pids:
        try:
            os.kill(pid, signal.SIGSTOP)
        except (ProcessLookupError, PermissionError):
            pass
    return pids


def _cont_streamlit(pids: list[int]) -> None:
    if not pids:
        return
    print(f"  [streamlit] 재개: {pids}")
    for pid in pids:
        try:
            os.kill(pid, signal.SIGCONT)
        except (ProcessLookupError, PermissionError):
            pass


# ──────────────────────────────────────────────────────────────────
# 메트릭
# ──────────────────────────────────────────────────────────────────

def _collect_metrics() -> dict:
    """현재 Qdrant 컬렉션의 핵심 메트릭."""
    from qdrant_client import QdrantClient

    client = QdrantClient(path=str(PROJECT_ROOT / "qdrant_storage"))
    try:
        info = client.get_collection("rnd_law_chunks")
        total = info.points_count

        records = []
        next_offset = None
        while True:
            batch, next_offset = client.scroll(
                collection_name="rnd_law_chunks",
                limit=500,
                offset=next_offset,
                with_payload=True,
                with_vectors=False,
            )
            records.extend(batch)
            if next_offset is None:
                break

        text_lens = [len(r.payload.get("text", "") or "") for r in records]
        avg_len = sum(text_lens) / len(text_lens) if text_lens else 0
        max_len = max(text_lens) if text_lens else 0

        empty_or_intro = sum(
            1 for r in records
            if (r.payload.get("article_no") or "") in ("", "서문")
        )

        # 매뉴얼 PDF 의 article_no='별표 *' 청크 — G1 misclassify 추적
        manual_byeolpyo = [
            r for r in records
            if (r.payload.get("article_no") or "").startswith("별표 ")
            and "pdf" in (r.payload.get("source_file", "") or "").lower()
        ]

        doc_name_dist = Counter(r.payload.get("doc_name", "") for r in records)
        doc_type_dist = Counter(r.payload.get("doc_type", "") for r in records)

        # 별표 N 청크 수 (시행령 별표 HWP 출처)
        byeolpyo_chunk_counts = Counter()
        for r in records:
            ar = r.payload.get("article_no", "") or ""
            sf = r.payload.get("source_file", "") or ""
            if ar.startswith("별표 ") and ".hwp" in sf.lower():
                # "별표 6 (part 2/3)" → "별표 6"
                base = ar.split(" (part")[0]
                byeolpyo_chunk_counts[base] += 1

        return {
            "total": total,
            "scroll_count": len(records),
            "avg_chunk_len": avg_len,
            "max_chunk_len": max_len,
            "empty_or_intro_count": empty_or_intro,
            "empty_or_intro_pct": (
                100 * empty_or_intro / len(records) if records else 0
            ),
            "manual_byeolpyo_count": len(manual_byeolpyo),
            "doc_name_dist": dict(doc_name_dist.most_common(15)),
            "doc_type_dist": dict(doc_type_dist),
            "byeolpyo_chunk_counts": dict(sorted(byeolpyo_chunk_counts.items())),
        }
    finally:
        client.close()


def _print_metrics(name: str, m: dict) -> None:
    print(f"\n  ── {name} ──")
    print(f"  총 포인트         : {m['total']}")
    print(f"  scroll 카운트     : {m['scroll_count']}")
    print(f"  평균 청크 길이    : {m['avg_chunk_len']:.0f}자  (max {m['max_chunk_len']})")
    print(
        f"  article_no 빈/서문: {m['empty_or_intro_count']}개 "
        f"({m['empty_or_intro_pct']:.1f}%)"
    )
    print(f"  매뉴얼 PDF '별표' misclassify: {m['manual_byeolpyo_count']}개")
    print(f"  doc_type 분포     :")
    for k, v in m["doc_type_dist"].items():
        print(f"    {v:5d}  {k!r}")
    print(f"  doc_name top5     :")
    for k, v in list(m["doc_name_dist"].items())[:5]:
        print(f"    {v:5d}  {k[:60]!r}")
    print(f"  별표 N 청크 분포  :")
    for k, v in m["byeolpyo_chunk_counts"].items():
        print(f"    {v:3d}  {k}")


def _print_diff(before: dict, after: dict) -> None:
    print("\n" + "=" * 78)
    print("재인덱싱 전/후 핵심 메트릭 변화")
    print("=" * 78)
    print(f"{'지표':<35s} {'BEFORE':>15s} {'AFTER':>15s} {'Δ':>10s}")
    print("-" * 78)
    rows = [
        ("총 포인트", before["total"], after["total"]),
        ("평균 청크 길이", round(before["avg_chunk_len"]), round(after["avg_chunk_len"])),
        ("max 청크 길이", before["max_chunk_len"], after["max_chunk_len"]),
        ("article_no 빈/서문", before["empty_or_intro_count"], after["empty_or_intro_count"]),
        ("매뉴얼 PDF '별표 *' 청크", before["manual_byeolpyo_count"], after["manual_byeolpyo_count"]),
    ]
    for label, b, a in rows:
        delta = a - b
        marker = "+" if delta > 0 else ""
        print(f"{label:<35s} {b:>15d} {a:>15d} {marker}{delta:>9d}")


# ──────────────────────────────────────────────────────────────────
# 재인덱싱
# ──────────────────────────────────────────────────────────────────

def _drop_collection() -> None:
    """rnd_law_chunks 컬렉션을 drop & recreate."""
    from pipeline.indexer import ensure_collection
    print("  컬렉션 drop & recreate...")
    ensure_collection(recreate=True)


def _reset_done_log() -> None:
    """data/ingest_done.txt 초기화."""
    done = PROJECT_ROOT / "data" / "ingest_done.txt"
    if done.exists():
        backup = done.with_suffix(".txt.bak")
        backup.write_bytes(done.read_bytes())
        done.unlink()
        print(f"  ingest_done.txt 백업 후 초기화 (백업: {backup.name})")
    else:
        print("  ingest_done.txt 없음 (skip)")


def main() -> int:
    print("=" * 78)
    print("Phase G3 — 전체 코퍼스 재인덱싱")
    print("=" * 78)

    # before 메트릭
    print("\n[1/6] 인덱싱 전 메트릭 수집")
    try:
        before = _collect_metrics()
        _print_metrics("BEFORE", before)
    except Exception as e:
        print(f"  [WARN] before 메트릭 수집 실패 (무시): {type(e).__name__}: {e}")
        before = None

    # streamlit stop
    print("\n[2/6] Streamlit pause")
    streamlit_pids = _stop_streamlit()
    if not streamlit_pids:
        print("  실행 중인 streamlit 없음")

    elapsed_s = 0.0
    rc = 0
    try:
        # drop
        print("\n[3/6] Qdrant 컬렉션 drop & recreate")
        _drop_collection()

        # done log 초기화
        print("\n[4/6] ingest_done.txt 초기화 (force 재인덱싱 효과)")
        _reset_done_log()

        # batch_ingest 실행
        print("\n[5/6] batch_ingest.main() 실행 — 진행률 stdout")
        print("-" * 78)
        t0 = time.time()
        try:
            from batch_ingest import main as ingest_main
            ingest_main()
        except SystemExit as e:
            # batch_ingest 가 sys.exit 호출하면 종료 코드 흡수
            if e.code not in (0, None):
                rc = int(e.code)
        elapsed_s = time.time() - t0
        print("-" * 78)
        print(f"\n  소요 시간: {elapsed_s:.1f}초 ({elapsed_s/60:.1f}분)")

        # after 메트릭
        print("\n[6/6] 인덱싱 후 메트릭 수집")
        after = _collect_metrics()
        _print_metrics("AFTER", after)
        if before is not None:
            _print_diff(before, after)

    except Exception as e:
        import traceback
        print(f"\n  [ERROR] {type(e).__name__}: {e}")
        traceback.print_exc()
        rc = 1
    finally:
        _cont_streamlit(streamlit_pids)

    print("\n" + "=" * 78)
    if rc == 0:
        print("Phase G3 재인덱싱 완료 — scripts/eval_full.py 실행 권장.")
    else:
        print(f"Phase G3 재인덱싱 실패 (rc={rc})")
    print("=" * 78)
    return rc


if __name__ == "__main__":
    sys.exit(main())
