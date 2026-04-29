"""sync 모듈 단위 테스트 (Phase G4).

검증 시나리오:
    1. 동일 파일 재실행 → 0개 변경 (no-op)
    2. 1개 파일만 mtime 갱신 (touch + 내용 동일) → 변경 없음
    3. 1개 파일 내용 변경 (mtime + sha256 둘 다 변경) → modified
    4. 1개 파일 삭제 → deleted
    5. 신규 1개 파일 추가 → added
    6. CHUNKER_VERSION 변경 (monkeypatch) → stale_code

이 테스트는 임시 디렉토리에 가짜 파일을 만들고, 메타파일을 수동으로 작성한 뒤
scan_changes() 동작만 검증한다 (apply_changes 는 Qdrant + 임베더가 필요해 e2e
검증은 통합 테스트에서).

실행:
    cd /Users/maro/dev/company/chatbot
    source .venv/bin/activate
    python -m unittest tests.test_sync -v
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unicodedata
import unittest
from datetime import datetime
from pathlib import Path

# 프로젝트 루트를 path 에 추가
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline import sync as sync_mod
from pipeline.sync import (
    CHUNKER_VERSION,
    EMBEDDER_VERSION,
    _abs_nfc,
    _sha256_of,
    load_metadata,
    save_metadata,
    scan_changes,
)


def _make_pdf_like(path: Path, content: bytes) -> None:
    """1KB 이상 가짜 PDF/HWP 파일 생성 (sync 의 MIN_FILE_SIZE 우회용)."""
    # 더미 헤더 + 패딩으로 1.5KB 만들기
    payload = content + b"\n" + (b"x" * (1500 - len(content) - 1))
    path.write_bytes(payload)


def _build_meta_record(path: Path, *, chunker_v: str = CHUNKER_VERSION) -> dict:
    stat = path.stat()
    return {
        "mtime": stat.st_mtime,
        "sha256": _sha256_of(path),
        "size_bytes": stat.st_size,
        "chunker_version": chunker_v,
        "embedder_version": EMBEDDER_VERSION,
        "chunk_ids": ["fake-id-1", "fake-id-2"],
        "indexed_at": datetime.now().isoformat(timespec="seconds"),
        "doc_type": "운영요령",
        "page_count": 10,
        "indexed": True,
    }


class TestScanChanges(unittest.TestCase):
    """scan_changes 의 분류 정확도."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tmpdir.name)
        self.meta_file = self.root / "metadata.json"
        # 가짜 PDF 1개
        self.pdf_a = self.root / "doc_a.pdf"
        _make_pdf_like(self.pdf_a, b"%PDF-1.4 doc_a content")
        # 가짜 HWP 1개
        self.hwp_b = self.root / "doc_b.hwp"
        _make_pdf_like(self.hwp_b, b"\xd0\xcf\x11\xe0 hwp doc_b content")

    def tearDown(self):
        self.tmpdir.cleanup()

    def _save_meta_for_both(self, *, chunker_v: str = CHUNKER_VERSION) -> dict:
        meta = {
            _abs_nfc(self.pdf_a): _build_meta_record(self.pdf_a, chunker_v=chunker_v),
            _abs_nfc(self.hwp_b): _build_meta_record(self.hwp_b, chunker_v=chunker_v),
        }
        save_metadata(meta, self.meta_file)
        return meta

    # ───── Case 1: no-op ─────
    def test_case1_no_changes(self):
        self._save_meta_for_both()
        changes = scan_changes(roots=[self.root], metadata_path=self.meta_file)
        self.assertEqual(changes["added"], [])
        self.assertEqual(changes["modified"], [])
        self.assertEqual(changes["deleted"], [])
        self.assertEqual(changes["stale_code"], [])
        self.assertEqual(len(changes["unchanged"]), 2)

    # ───── Case 2: touch (mtime 변경, 내용 동일) ─────
    def test_case2_touch_same_content(self):
        self._save_meta_for_both()
        # mtime 만 갱신 — 내용 그대로 다시 쓰면 sha 동일
        time.sleep(0.05)
        os.utime(self.pdf_a, None)
        changes = scan_changes(roots=[self.root], metadata_path=self.meta_file)
        # 내용 동일 → modified 가 아님 → unchanged 또는 stale_code 가 아닌 unchanged
        # (sha256 일치하므로 modified 처리 안 됨)
        self.assertEqual(changes["added"], [])
        self.assertEqual(changes["modified"], [])
        self.assertEqual(changes["deleted"], [])
        self.assertEqual(changes["stale_code"], [])
        # 두 파일 모두 unchanged 여야 함
        self.assertEqual(len(changes["unchanged"]), 2)

    # ───── Case 3: 내용 변경 (mtime + sha256 둘 다) ─────
    def test_case3_content_modified(self):
        self._save_meta_for_both()
        time.sleep(0.05)
        # 다른 내용 + 다른 길이로 갱신
        _make_pdf_like(self.pdf_a, b"%PDF-1.4 NEW content here")
        changes = scan_changes(roots=[self.root], metadata_path=self.meta_file)
        self.assertEqual(len(changes["modified"]), 1)
        self.assertEqual(_abs_nfc(changes["modified"][0]), _abs_nfc(self.pdf_a))
        self.assertEqual(changes["added"], [])
        self.assertEqual(changes["deleted"], [])
        self.assertEqual(len(changes["unchanged"]), 1)  # hwp_b 는 그대로

    # ───── Case 4: 파일 삭제 ─────
    def test_case4_file_deleted(self):
        self._save_meta_for_both()
        self.hwp_b.unlink()
        changes = scan_changes(roots=[self.root], metadata_path=self.meta_file)
        self.assertEqual(len(changes["deleted"]), 1)
        deleted_key = changes["deleted"][0]
        self.assertEqual(_nfc_path(deleted_key), _abs_nfc(self.hwp_b))
        self.assertEqual(len(changes["unchanged"]), 1)
        self.assertEqual(changes["modified"], [])

    # ───── Case 5: 신규 파일 추가 ─────
    def test_case5_file_added(self):
        self._save_meta_for_both()
        new_pdf = self.root / "doc_c.pdf"
        _make_pdf_like(new_pdf, b"%PDF-1.4 brand new doc_c")
        changes = scan_changes(roots=[self.root], metadata_path=self.meta_file)
        self.assertEqual(len(changes["added"]), 1)
        self.assertEqual(_abs_nfc(changes["added"][0]), _abs_nfc(new_pdf))
        self.assertEqual(len(changes["unchanged"]), 2)

    # ───── Case 6: CHUNKER_VERSION 변경 (monkeypatch) → stale_code ─────
    def test_case6_chunker_version_bump(self):
        # 메타에 옛 버전(pre-G1) 으로 저장 → 현재 코드는 1.2.0 → stale_code
        self._save_meta_for_both(chunker_v="pre-G1")
        changes = scan_changes(roots=[self.root], metadata_path=self.meta_file)
        self.assertEqual(len(changes["stale_code"]), 2)
        self.assertEqual(changes["modified"], [])
        self.assertEqual(changes["added"], [])
        self.assertEqual(changes["deleted"], [])
        # unchanged 0 — 둘 다 stale 로 분류
        self.assertEqual(changes["unchanged"], [])

    # ───── Case 7: indexed=False 파일은 skipped ─────
    def test_case7_indexed_false_skipped(self):
        rec_a = _build_meta_record(self.pdf_a)
        rec_b = _build_meta_record(self.hwp_b)
        rec_b["indexed"] = False
        rec_b["skip_reason"] = "parse_empty"
        meta = {_abs_nfc(self.pdf_a): rec_a, _abs_nfc(self.hwp_b): rec_b}
        save_metadata(meta, self.meta_file)
        changes = scan_changes(roots=[self.root], metadata_path=self.meta_file)
        self.assertEqual(len(changes["unchanged"]), 1)  # pdf_a 만
        self.assertEqual(len(changes["skipped"]), 1)    # hwp_b
        self.assertEqual(changes["added"], [])
        self.assertEqual(changes["modified"], [])

    # ───── Case 8: 새 파일 + 기존 stale + 삭제 동시 ─────
    def test_case8_combined_changes(self):
        # 메타: pdf_a (pre-G1), hwp_b (정상)
        meta = {
            _abs_nfc(self.pdf_a): _build_meta_record(self.pdf_a, chunker_v="pre-G1"),
            _abs_nfc(self.hwp_b): _build_meta_record(self.hwp_b),
        }
        save_metadata(meta, self.meta_file)
        # hwp_b 삭제
        self.hwp_b.unlink()
        # 새 파일 추가
        new_hwp = self.root / "doc_c.hwp"
        _make_pdf_like(new_hwp, b"new hwp content")

        changes = scan_changes(roots=[self.root], metadata_path=self.meta_file)
        self.assertEqual(len(changes["stale_code"]), 1)
        self.assertEqual(len(changes["added"]), 1)
        self.assertEqual(len(changes["deleted"]), 1)
        self.assertEqual(changes["modified"], [])
        self.assertEqual(changes["unchanged"], [])


def _nfc_path(p) -> str:
    """헬퍼: 절대경로 NFC. Path 도 str 도 받기."""
    return _abs_nfc(Path(p)) if Path(p).is_absolute() else unicodedata.normalize("NFC", str(p))


class TestHelpers(unittest.TestCase):
    """sync 내부 헬퍼 sanity 검증."""

    def test_sha256_streaming(self):
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"hello sync world")
            tmp = Path(f.name)
        try:
            import hashlib
            expected = hashlib.sha256(b"hello sync world").hexdigest()
            self.assertEqual(_sha256_of(tmp), expected)
        finally:
            tmp.unlink()

    def test_iter_files_excludes_hidden_dirs(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            # 정상 파일
            ok = root / "ok.pdf"
            _make_pdf_like(ok, b"%PDF ok")
            # __pycache__ 안의 pdf — 제외되어야
            pycache = root / "__pycache__"
            pycache.mkdir()
            _make_pdf_like(pycache / "skip.pdf", b"%PDF skip")
            # versions/ 안의 pdf — 제외
            ver = root / "versions"
            ver.mkdir()
            _make_pdf_like(ver / "old.pdf", b"%PDF old")
            # .git 안 — 제외
            git = root / ".git"
            git.mkdir()
            _make_pdf_like(git / "repo.pdf", b"%PDF repo")

            paths = sync_mod._iter_files([root])
            names = sorted(p.name for p in paths)
            self.assertEqual(names, ["ok.pdf"])

    def test_iter_files_size_filter(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            small = root / "tiny.pdf"
            small.write_bytes(b"%PDF tiny")  # < 1KB
            big = root / "big.pdf"
            _make_pdf_like(big, b"%PDF big")
            paths = sync_mod._iter_files([root])
            names = [p.name for p in paths]
            self.assertEqual(names, ["big.pdf"])

    def test_initial_chunker_version_for(self):
        # 별표 파일 (G2 산출물) → 1.2.0
        for name in [
            "[별표 1] xxx.hwp",
            "[별표 2] yyy.hwp",
            "[별표 4] zzz.hwp",
            "[별표 5] aaa.hwp",
            "[별표 6] bbb.hwp",
            "[별표 7] ccc.hwp",
        ]:
            self.assertEqual(
                sync_mod._initial_chunker_version_for(name),
                CHUNKER_VERSION,
                f"{name} 은 G2 산출물 → {CHUNKER_VERSION}",
            )
        # 그 외 파일 → 'pre-G1'
        for name in [
            "[본권] 매뉴얼.pdf",
            "[별표 3] 삭제 .hwp",  # 별표 3 은 G2 범위 밖
            "[별지 제1호서식] xxx.hwp",
            "국가연구개발혁신법.hwp",
        ]:
            self.assertEqual(
                sync_mod._initial_chunker_version_for(name),
                "pre-G1",
                f"{name} 은 사전 G1 → 'pre-G1'",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
