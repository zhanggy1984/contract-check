"""F3 单测：上传文件名归属「本次上传」而非「这份内容」。

缺陷现象：`contract_file` 按 sha256 去重，同一行被多个 task 引用（实测 id=94 → #660/661/663），
名字存在那边会「首次叫什么、以后永远叫什么」——#664 传 `f3probe_918a.pdf`（1695B，good.pdf
副本）落库却显示 `data/acceptance/good.pdf`。且就地改 `contract_file.file_name` 会波及历史记录
⇒ 名字必须落在 task 侧（`check_task.original_name`），存量行为 "" 时展示层回退读去重名。

覆盖三条分支：剥路径（安全）、去重复用时仍记本次名（主路径）、存量行回退（兼容路径）。
mock 隔离 SessionLocal，不触真实 MySQL；unittest 风格（与既有测试一致），pytest 作 runner。
"""
import unittest
from unittest import mock
from unittest.mock import MagicMock

from app.service import check_task_service as svc


class _FakeCf:
    """已有的 ContractFile 去重行（save_uploaded_file 复用分支返回它）。"""

    def __init__(self):
        self.id = 94
        self.file_name = "data/acceptance/good.pdf"   # 首次上传时被原样存下的路径形态名
        self.file_type = "PDF"
        self.storage_path = "/app/data/uploads/abc.pdf"
        self.file_size = 1695
        self.sha256 = "x" * 64
        # has_scanned=True：跳过函数尾部「重建 parsed txt」分支，测试只关心名字归属
        self.has_scanned = True
        self.ocr_applied = False


class _FakeSaveDb:
    """save_uploaded_file 的去重命中场景：query 首次即返回已有 ContractFile。"""

    def __init__(self, existing):
        self.existing = existing
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
        return self.existing

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        pass

    def rollback(self):
        pass

    def refresh(self, obj):
        # 真实 refresh 会回填自增主键；这里给个固定值让 task_id 可断言
        obj.id = 900


class _FakeListQuery:
    """list_tasks 的链式查询：只关心最终 items 与 count。"""

    def __init__(self, items):
        self._items = items

    def options(self, *a, **k):
        return self

    def filter(self, *a, **k):
        return self

    def join(self, *a, **k):
        return self

    def count(self):
        return len(self._items)

    def order_by(self, *a, **k):
        return self

    def offset(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def all(self):
        return self._items


class _FakeListDb:
    def __init__(self, items):
        self._items = items

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def query(self, model):
        return _FakeListQuery(self._items)


def _fake_task(task_id, original_name, cf_name):
    t = MagicMock()
    t.id = task_id
    t.status = "SUCCESS"
    t.extraction_status = "COMPLETE"
    t.original_name = original_name
    t.create_time = None
    t.contract_file.file_name = cf_name
    return t


class TestSanitizeFilename(unittest.TestCase):
    """剥路径：multipart 的 filename 由客户端声明、后端原样信任（api/files.py:22），
    非浏览器调用方（脚本/curl）常塞整条路径进来，实测库里存过 data/acceptance/good.pdf。"""

    def test_strips_posix_path(self):
        self.assertEqual(svc._sanitize_filename("data/acceptance/good.pdf"), "good.pdf")

    def test_strips_windows_path(self):
        self.assertEqual(svc._sanitize_filename(r"C:\Users\me\a\合同.pdf"), "合同.pdf")

    def test_strips_parent_traversal(self):
        self.assertEqual(svc._sanitize_filename("../../etc/passwd.pdf"), "passwd.pdf")

    def test_plain_name_untouched(self):
        self.assertEqual(svc._sanitize_filename("合同A.pdf"), "合同A.pdf")

    def test_none_and_empty(self):
        self.assertEqual(svc._sanitize_filename(None), "")
        self.assertEqual(svc._sanitize_filename(""), "")


class TestUploadNameBelongsToTask(unittest.TestCase):
    """主路径（F3 核心）：sha 去重命中已有 contract_file 时，本次上传声明的名字不能丢。"""

    def test_dedup_hit_still_records_this_upload_name(self):
        cf = _FakeCf()
        db = _FakeSaveDb(cf)
        with mock.patch.object(svc, "SessionLocal", return_value=db), \
                mock.patch.object(svc, "run_task_async"):
            svc.save_uploaded_file("f3probe_919b.pdf", "pdf", "PDF", b"payload")

        tasks = [o for o in db.added if hasattr(o, "original_name")]
        self.assertEqual(len(tasks), 1)
        # 命中去重行（cf.file_name 是旧名）也不影响本次名字落库
        self.assertEqual(tasks[0].original_name, "f3probe_919b.pdf")
        self.assertEqual(tasks[0].contract_file_id, 94)

    def test_dedup_hit_does_not_touch_contract_file_name(self):
        """就地改 contract_file.file_name 会波及引用同一行的历史 task ⇒ 必须不动它。"""
        cf = _FakeCf()
        db = _FakeSaveDb(cf)
        with mock.patch.object(svc, "SessionLocal", return_value=db), \
                mock.patch.object(svc, "run_task_async"):
            svc.save_uploaded_file("f3probe_919b.pdf", "pdf", "PDF", b"payload")
        self.assertEqual(cf.file_name, "data/acceptance/good.pdf")

    def test_upload_name_also_stripped_of_path(self):
        """落 task 侧的名字同样过剥路径，否则脚本调用方会把路径写进历史记录。"""
        db = _FakeSaveDb(_FakeCf())
        with mock.patch.object(svc, "SessionLocal", return_value=db), \
                mock.patch.object(svc, "run_task_async"):
            svc.save_uploaded_file("D:/tmp/xx/合同B.pdf", "pdf", "PDF", b"payload")
        tasks = [o for o in db.added if hasattr(o, "original_name")]
        self.assertEqual(tasks[0].original_name, "合同B.pdf")


class TestListTasksNameFallback(unittest.TestCase):
    """兼容路径：存量行 original_name='' ⇒ 回退读 contract_file.file_name，
    升级后历史记录显示不变（这是「修法没波及历史」的判据）。"""

    def _list(self, items):
        with mock.patch.object(svc, "SessionLocal", return_value=_FakeListDb(items)):
            return svc.list_tasks(page=1, size=10)

    def test_legacy_row_falls_back_to_contract_file_name(self):
        r = self._list([_fake_task(664, "", "good.pdf")])
        self.assertEqual(r["items"][0]["file_name"], "good.pdf")

    def test_new_row_uses_its_own_name(self):
        r = self._list([_fake_task(665, "f3probe_919b.pdf", "good.pdf")])
        self.assertEqual(r["items"][0]["file_name"], "f3probe_919b.pdf")

    def test_both_shapes_in_one_page(self):
        r = self._list([
            _fake_task(665, "f3probe_919b.pdf", "good.pdf"),
            _fake_task(664, "", "good.pdf"),
        ])
        self.assertEqual([i["file_name"] for i in r["items"]],
                         ["f3probe_919b.pdf", "good.pdf"])


if __name__ == "__main__":
    unittest.main()
