"""分页/长度超限参数校验单测（T4.3-8 修复）：非法分页与超长字段不再 500。

- page<1 / size<0 → FastAPI Query 校验 422（page=0 原会 OFFSET 负 → MySQL 语法错 500）
- size=0 保留返回空（原 LIMIT 0 合法不 500，ge=0 只防负，最小化契约漂移）
- confirm_user 超 50 → Pydantic max_length 422（对齐 DB VARCHAR(50)，防 DataError 1406）
- 上传文件名超 255 → 截断落库（DB VARCHAR(255)，防 DataError 1406）

TestClient 非 with 模式不发 lifespan（不触发 startup 连 MySQL），get_db 用 stub 覆盖；
confirm_user / _sanitize_filename 走纯单测不依赖 app。unittest 风格，pytest 作 runner。
"""
import unittest

from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.api.files import _sanitize_filename
from app.api.violations import StatusBody
from app.db.session import get_db
from app.main import app


class _StubDb:
    """空结果查询链：query→filter→count→offset→limit→all 全返回空。"""

    def query(self, model):
        return self

    def options(self, *a, **k):
        return self

    def filter(self, *a, **k):
        return self

    def join(self, *a, **k):
        return self

    def count(self):
        return 0

    def order_by(self, *a, **k):
        return self

    def offset(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    def all(self):
        return []


class TestPaginationValidation(unittest.TestCase):
    """非法分页参数在进 handler 前被 422 拦下（TestClient 不触发 startup）。"""

    def setUp(self):
        app.dependency_overrides[get_db] = _StubDb
        self.client = TestClient(app)
        self.addCleanup(self._clear_override)

    def _clear_override(self):
        app.dependency_overrides.clear()

    def test_tasks_page_zero_rejected(self):
        r = self.client.get("/api/tasks", params={"page": 0})
        self.assertEqual(r.status_code, 422, "page=0 原会 OFFSET 负 → 500，应 422")

    def test_tasks_page_negative_rejected(self):
        r = self.client.get("/api/tasks", params={"page": -1})
        self.assertEqual(r.status_code, 422)

    def test_tasks_size_negative_rejected(self):
        r = self.client.get("/api/tasks", params={"size": -5})
        self.assertEqual(r.status_code, 422, "size<0 原会 LIMIT 负 → 500，应 422")

    def test_tasks_size_zero_keeps_empty_page(self):
        r = self.client.get("/api/tasks", params={"size": 0})
        self.assertEqual(r.status_code, 200, "size=0 原合法（LIMIT 0 空页），保留行为")
        self.assertEqual(r.json()["items"], [])

    def test_tasks_normal_page_ok(self):
        r = self.client.get("/api/tasks", params={"page": 1, "size": 10})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["total"], 0)

    def test_violations_page_zero_rejected(self):
        r = self.client.get("/api/violations", params={"page": 0})
        self.assertEqual(r.status_code, 422)

    def test_violations_normal_page_ok(self):
        r = self.client.get("/api/violations", params={"page": 1, "size": 20})
        self.assertEqual(r.status_code, 200)

    def test_rules_page_zero_rejected(self):
        r = self.client.get("/api/rules", params={"page": 0})
        self.assertEqual(r.status_code, 422)


class TestStatusBodyLength(unittest.TestCase):
    def test_confirm_user_over_50_rejected(self):
        with self.assertRaises(ValidationError):
            StatusBody(status="CONFIRMED", confirm_user="x" * 51)

    def test_confirm_user_50_ok(self):
        b = StatusBody(status="CONFIRMED", confirm_user="x" * 50)
        self.assertEqual(b.confirm_user, "x" * 50)


class TestSanitizeFilename(unittest.TestCase):
    def test_none_to_empty(self):
        self.assertEqual(_sanitize_filename(None), "")

    def test_over_255_keeps_extension(self):
        s = _sanitize_filename("x" * 300 + ".pdf")
        self.assertEqual(len(s), 255, "超长应截到 255 且保留扩展名")
        self.assertTrue(s.endswith(".pdf"), "截断应保留扩展名")

    def test_over_255_no_ext_raw_truncate(self):
        self.assertEqual(_sanitize_filename("x" * 300), "x" * 255)

    def test_over_255_long_ext_fallback(self):
        """扩展名自身超长无法保留 → 退化裸截（长度 255）。"""
        s = _sanitize_filename("a" * 100 + "." + "b" * 300)
        self.assertEqual(len(s), 255)

    def test_normal_unchanged(self):
        self.assertEqual(_sanitize_filename("合同.pdf"), "合同.pdf")


if __name__ == "__main__":
    unittest.main()
