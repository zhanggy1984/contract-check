"""并发上传同 sha 竞态单测（T4.3-7 修复）：撞唯一键回退复用不 500。

upload 先查后插：并发同内容双线程都 query 无 → 都写盘 → 双 insert → 一撞 sha256 唯一键。
修复：捕获 IntegrityError → rollback → 复用已有记录（同 sha 同路径，写盘文件即成功线程
引用的文件，无孤儿残留）。交互层收口后逻辑在 check_task_service.save_uploaded_file，
mock 隔离 SessionLocal/文件系统/解析，不触真实 MySQL。
unittest 风格（与既有测试一致），pytest 作 runner。
"""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sqlalchemy.exc import IntegrityError

from app.db.models import ContractFile
from app.service import check_task_service as svc


class _FakeDb:
    """模拟并发快照：入口 query 无 → 首次 commit 撞唯一键 → 回退 query 返回已有。

    支持 `with SessionLocal() as db:` 上下文管理器（save_uploaded_file 的会话模式）。
    """

    def __init__(self, existing=None, fail_first_commit=True):
        self.existing = existing
        self.fail_first_commit = fail_first_commit
        self.commits = 0
        self.rolled_back = False
        self._first_calls = 0
        self.added = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def query(self, model):
        return self

    def filter(self, *a, **k):
        return self

    def first(self):
        self._first_calls += 1
        return None if self._first_calls == 1 else self.existing

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        self.commits += 1
        if self.fail_first_commit and self.commits == 1:
            raise IntegrityError("INSERT", {}, Exception("1062 duplicate"))

    def rollback(self):
        self.rolled_back = True

    def refresh(self, obj):
        obj.id = 7


def _save(db, data=b"pdf-data"):
    with mock.patch.object(svc, "SessionLocal", return_value=db):
        return svc.save_uploaded_file(original_name="合同.pdf", ext="pdf",
                                      file_type="PDF", data=data)


class TestUploadConcurrency(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.patches = [
            mock.patch.object(svc, "UPLOAD_DIR", Path(self.tmp.name)),
            mock.patch.object(svc, "PARSED_DIR", Path(self.tmp.name)),
            mock.patch.object(svc, "_extract", return_value=("合同文本", None)),
            mock.patch.object(svc, "run_task_async"),
        ]
        for p in self.patches:
            p.start()
        self.addCleanup(self._stop)

    def _stop(self):
        for p in self.patches:
            p.stop()

    def test_normal_upload_creates_task(self):
        db = _FakeDb(fail_first_commit=False)
        out = _save(db)
        self.assertEqual(out["file_id"], 7)
        self.assertEqual(out["has_scanned"], False)
        self.assertEqual(out["char_count"], len("合同文本"))
        self.assertEqual(db.commits, 2, "cf commit + task commit")
        self.assertEqual(svc.run_task_async.call_count, 1)

    def test_duplicate_sha_rolls_back_and_reuses(self):
        """另一线程已提交同 sha：首次 commit 撞键 → rollback + 复用已有记录，不 500。"""
        existing = ContractFile(file_name="合同.pdf", file_type="PDF",
                                storage_path="/x.pdf", file_size=8,
                                sha256="abc", has_scanned=False)
        existing.id = 7
        db = _FakeDb(existing=existing)
        out = _save(db)
        self.assertEqual(out["file_id"], 7, "撞唯一键应回退复用已有 file_id")
        self.assertTrue(db.rolled_back, "撞唯一键应 rollback")
        self.assertEqual(svc.run_task_async.call_count, 1, "复用后仍创建独立任务")


if __name__ == "__main__":
    unittest.main()
