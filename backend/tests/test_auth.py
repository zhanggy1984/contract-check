"""认证鉴权单测：JWT 签发/校验 + 登录接口 + require_auth 依赖 + 豁免端点。

Settings 是 import 时单例（默认 AUTH_ENABLED=false，见 test_api_pagination_validation
顶部引导）。本测试按需改 settings 属性（pydantic 模型运行期可变）启/停鉴权，
tearDown 恢复原值，不污染其他测试。
"""
import os
import unittest
from unittest import mock

from fastapi.testclient import TestClient

from app.common.security import create_token, verify_token
from app.config import settings
from app.db.session import get_db
from app.main import _is_external_llm, app
from app.service import check_task_service as svc


class _StubDb:
    """空结果查询链（对齐 test_api_pagination_validation 的 stub，满足 list 端点）。"""

    def query(self, model): return self
    def options(self, *a, **k): return self
    def filter(self, *a, **k): return self
    def join(self, *a, **k): return self
    def order_by(self, *a, **k): return self
    def offset(self, *a, **k): return self
    def limit(self, *a, **k): return self
    def count(self): return 0
    def all(self): return []


class AuthBase(unittest.TestCase):
    """启用/关闭鉴权并起 TestClient，tearDown 恢复 settings 单例与 dependency_overrides。"""

    def enable_auth(self, enabled: bool = True):
        self._orig = {
            "auth_enabled": settings.auth_enabled,
            "auth_username": settings.auth_username,
            "auth_password": settings.auth_password,
            "jwt_secret": settings.jwt_secret,
            "jwt_expire_minutes": settings.jwt_expire_minutes,
        }
        settings.auth_enabled = enabled
        settings.auth_username = "admin"
        settings.auth_password = "pw123"
        settings.jwt_secret = "test-secret"
        settings.jwt_expire_minutes = 60
        app.dependency_overrides[get_db] = _StubDb
        # 交互层收口后 /api/tasks list 走 svc（内部 SessionLocal），桩方法防触真实库；
        # 鉴权测试只关心 200/401 分支，list 返回空页即可
        self._svc_patch = mock.patch.object(
            svc, "list_tasks", return_value={"total": 0, "page": 1, "size": 10, "items": []})
        self._svc_patch.start()
        self.client = TestClient(app)

    def tearDown(self):
        if hasattr(self, "_orig"):
            for k, v in self._orig.items():
                setattr(settings, k, v)
        app.dependency_overrides.clear()
        if hasattr(self, "_svc_patch"):
            self._svc_patch.stop()


class TestJwt(unittest.TestCase):
    """security.py 纯函数：签发/校验/篡改/过期。"""

    def test_roundtrip(self):
        token = create_token("admin", "secret", 60)
        payload = verify_token(token, "secret")
        self.assertIsNotNone(payload)
        self.assertEqual(payload["sub"], "admin")
        self.assertGreater(payload["exp"], payload["iat"])

    def test_wrong_secret_rejected(self):
        token = create_token("admin", "secret", 60)
        self.assertIsNone(verify_token(token, "other"))

    def test_tampered_token_rejected(self):
        token = create_token("admin", "secret", 60)
        tampered = token[:-1] + ("A" if token[-1] != "A" else "B")  # 改末位签名
        self.assertIsNone(verify_token(tampered, "secret"))

    def test_expired_rejected(self):
        token = create_token("admin", "secret", -1)  # exp 在过去
        self.assertIsNone(verify_token(token, "secret"))

    def test_garbage_rejected(self):
        self.assertIsNone(verify_token("not.a.jwt", "secret"))
        self.assertIsNone(verify_token("", "secret"))


class TestLogin(AuthBase):
    """登录接口：成功换 token / 密码错 401 / 未配置 503 / 关鉴权 401。"""

    def test_login_success(self):
        self.enable_auth()
        r = self.client.post("/api/auth/login",
                             json={"username": "admin", "password": "pw123"})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("token", body)
        self.assertEqual(body["token_type"], "bearer")

    def test_login_wrong_password(self):
        self.enable_auth()
        r = self.client.post("/api/auth/login",
                             json={"username": "admin", "password": "wrong"})
        self.assertEqual(r.status_code, 401)

    def test_login_wrong_username(self):
        self.enable_auth()
        r = self.client.post("/api/auth/login",
                             json={"username": "root", "password": "pw123"})
        self.assertEqual(r.status_code, 401)

    def test_login_not_configured_fail_closed(self):
        """开了鉴权但没配密码 → 503（fail-closed，不裸奔）。"""
        self.enable_auth()
        settings.auth_password = ""
        r = self.client.post("/api/auth/login",
                             json={"username": "admin", "password": "pw123"})
        self.assertEqual(r.status_code, 503)

    def test_login_when_auth_disabled(self):
        """鉴权关闭时登录接口 401（前端不展示登录页，正常流程不会调）。"""
        self.enable_auth(enabled=False)
        r = self.client.post("/api/auth/login",
                             json={"username": "admin", "password": "pw123"})
        self.assertEqual(r.status_code, 401)


class TestRequireAuth(AuthBase):
    """require_auth 依赖：无 token 401 / 有效 token 200 / 坏 token 401 / 豁免端点 / 关鉴权放行。"""

    def test_protected_without_token(self):
        self.enable_auth()
        r = self.client.get("/api/tasks")
        self.assertEqual(r.status_code, 401)

    def test_protected_with_valid_token(self):
        self.enable_auth()
        token = create_token("admin", settings.jwt_secret, 60)
        r = self.client.get("/api/tasks",
                            headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(r.status_code, 200)

    def test_protected_with_bad_token(self):
        self.enable_auth()
        r = self.client.get("/api/tasks",
                            headers={"Authorization": "Bearer bad.token.here"})
        self.assertEqual(r.status_code, 401)

    def test_protected_without_bearer_scheme(self):
        self.enable_auth()
        r = self.client.get("/api/tasks",
                            headers={"Authorization": "Basic dXNlcjpwYXNz"})
        self.assertEqual(r.status_code, 401)

    def test_exempt_health_and_contracts(self):
        """豁免端点免鉴权：健康探针 + B.4 契约清单（平台自动发现）。"""
        self.enable_auth()
        self.assertEqual(self.client.get("/api/health").status_code, 200)
        self.assertEqual(self.client.get("/api/contracts").status_code, 200)
        # health 需透出 auth_required，供前端决定是否展示登录页
        self.assertTrue(self.client.get("/api/health").json()["auth_required"])

    def test_auth_disabled_bypass(self):
        """AUTH_ENABLED=false：受保护端点免鉴权放行（评测/开发模式）。"""
        self.enable_auth(enabled=False)
        r = self.client.get("/api/tasks")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(self.client.get("/api/health").json()["auth_required"])


class TestExternalLlm(unittest.TestCase):
    """LLM 端点内外网判定：回环/内网段 = 内部（不外发告警）；其余 = 外部（数据出域告警）。"""

    def _check(self, url, expected):
        self.assertEqual(_is_external_llm(url), expected, url)

    def test_loopback_internal(self):
        for u in ("http://localhost:8000", "http://127.0.0.1:8000", "http://127.5.5.5", "http://[::1]:8000"):
            self._check(u, False)

    def test_private_networks_internal(self):
        for u in ("http://10.1.2.3", "http://192.168.1.10", "http://172.16.0.1", "http://172.20.0.2", "http://172.31.255.255"):
            self._check(u, False)

    def test_public_external(self):
        for u in ("https://api.deepseek.com", "https://api.deepseek.com/v1", "http://172.15.0.2", "http://172.32.0.1", "https://openai.com"):
            self._check(u, True)

    def test_garbage_external(self):
        """无法解析主机名按外部处理（不误报"内部"导致漏告警）。"""
        self._check("", True)
        self._check("not-a-url", True)


if __name__ == "__main__":
    unittest.main()
