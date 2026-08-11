"""T4.3-3 孤儿文件清理单测：只删 DB 无记录的残留，temp dir 隔离不触真实目录。"""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.api import files


class _FakeDb:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def query(self, model):
        class _Q:
            def all(self):
                return [SimpleNamespace(sha256="aaaa")]  # 只有 aaaa 有记录

        return _Q()


class TestOrphanCleanup(unittest.TestCase):
    def _setup(self):
        tmp = tempfile.TemporaryDirectory()
        up = Path(tmp.name) / "uploads"
        par = Path(tmp.name) / "parsed"
        up.mkdir()
        par.mkdir()
        # 有记录：aaaa（原件+txt）
        (up / "aaaa.pdf").write_bytes(b"x")
        (par / "aaaa.txt").write_text("x")
        # 孤儿：bbbb 原件、cccc txt、dddd.docx 原件
        (up / "bbbb.pdf").write_bytes(b"x")
        (par / "cccc.txt").write_text("x")
        (up / "dddd.docx").write_bytes(b"x")
        # 非目标扩展名不参与
        (up / "note.md").write_text("x")
        (par / "zzz.log").write_text("x")
        return tmp, up, par

    def test_deletes_only_orphans(self):
        tmp, up, par = self._setup()
        try:
            with patch("app.api.files.UPLOAD_DIR", up), \
                 patch("app.api.files.PARSED_DIR", par), \
                 patch("app.api.files.SessionLocal", _FakeDb), \
                 patch("app.api.files.STALE_ORPHAN_MINUTES", -1):  # 禁用 mtime 守卫
                n = files.cleanup_orphan_files()
            self.assertEqual(n, 3)  # bbbb.pdf + cccc.txt + dddd.docx
            # 有记录的原件/txt 保留
            self.assertTrue((up / "aaaa.pdf").exists())
            self.assertTrue((par / "aaaa.txt").exists())
            # 孤儿已删
            self.assertFalse((up / "bbbb.pdf").exists())
            self.assertFalse((par / "cccc.txt").exists())
            self.assertFalse((up / "dddd.docx").exists())
            # 非目标扩展名不受影响
            self.assertTrue((up / "note.md").exists())
            self.assertTrue((par / "zzz.log").exists())
        finally:
            tmp.cleanup()

    def test_no_known_files_removed(self):
        tmp, up, par = self._setup()
        try:
            before = sorted(p.name for p in up.glob("*")) + sorted(p.name for p in par.glob("*"))
            with patch("app.api.files.UPLOAD_DIR", up), \
                 patch("app.api.files.PARSED_DIR", par), \
                 patch("app.api.files.SessionLocal", _FakeDb), \
                 patch("app.api.files.STALE_ORPHAN_MINUTES", -1):  # 禁用 mtime 守卫
                files.cleanup_orphan_files()
            # aaaa 两处必须仍在
            self.assertIn("aaaa.pdf", [p.name for p in up.glob("*")])
            self.assertIn("aaaa.txt", [p.name for p in par.glob("*")])
            # 清理只减孤儿，不增不减有记录的
            self.assertLess(len(list(up.glob("*"))), len(before) // 2 + 1)
        finally:
            tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
