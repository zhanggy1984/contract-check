# -*- coding: utf-8 -*-
"""verify 脚本共用：鉴权感知的 httpx client。

后端开鉴权（AUTH_ENABLED=true，生产默认）时，受保护接口需 Bearer token；
评测/开发可设 AUTH_ENABLED=false 免登录（登录接口此时 401，本模块靠
/api/health 的 auth_required 判断走哪条路）。需要时用环境变量
AUTH_USERNAME/AUTH_PASSWORD 登录换 token，注入 client.headers。
"""
import os
import sys

import httpx

# 脚本各自定义 BASE，这里只按缺省兜底；防撞系统代理必须 trust_env=False
_DEFAULT_BASE = os.environ.get("CC_BASE", "http://127.0.0.1:8001")


def make_client(base: str | None = None) -> httpx.Client:
    base = base or _DEFAULT_BASE
    client = httpx.Client(timeout=httpx.Timeout(120.0, connect=10.0), trust_env=False)
    try:
        r = client.get(f"{base}/api/health", timeout=5)
        auth_required = r.status_code == 200 and r.json().get("auth_required", False)
    except Exception:
        # 探不到 health 按需鉴权处理，真被拒时登录/报错再兜底
        auth_required = True
    if not auth_required:
        return client
    user = os.environ.get("AUTH_USERNAME", "admin")
    pwd = os.environ.get("AUTH_PASSWORD", "")
    if not pwd:
        print("✗ 后端要求登录，但未设置环境变量 AUTH_PASSWORD（评测环境可设 AUTH_ENABLED=false）")
        sys.exit(2)
    r = client.post(f"{base}/api/auth/login", json={"username": user, "password": pwd})
    if r.status_code != 200:
        print(f"✗ 登录失败: HTTP {r.status_code} {r.text[:200]}")
        sys.exit(2)
    client.headers["Authorization"] = f"Bearer {r.json()['token']}"
    print(f"✓ 已用 {user} 登录（AUTH_ENABLED=true）")
    return client
