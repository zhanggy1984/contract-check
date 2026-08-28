"""认证工具：HS256 JWT（标准库实现，零第三方依赖）+ FastAPI 鉴权依赖。

单用户 intranet 场景：用户名/密码来自 env（AUTH_USERNAME/AUTH_PASSWORD），JWT 用
hmac-SHA256 签名（secret 来自 env JWT_SECRET）。口令用 secrets.compare_digest 常量时间比较。
升级路径：如需多用户/更严格口令存储，换 passlib+bcrypt，保留本模块函数签名即可。
"""
import base64
import hashlib
import hmac
import json
import secrets
import time

from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings

_bearer = HTTPBearer(auto_error=False)


def _b64url(data: bytes) -> str:
    """URL-safe base64（去 padding），JWT 段编码。"""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(seg: str) -> bytes:
    """JWT 段解码（补回 padding）。"""
    return base64.urlsafe_b64decode(seg + "=" * (-len(seg) % 4))


def create_token(username: str, secret: str, expire_minutes: int) -> str:
    """HS256 签发 JWT：header.payload.signature。"""
    header = _b64url(json.dumps({"alg": "HS256", "typ": "JWT"}).encode("utf-8"))
    now = int(time.time())
    payload = _b64url(json.dumps({
        "sub": username,
        "iat": now,
        "exp": now + expire_minutes * 60,
    }).encode("utf-8"))
    msg = f"{header}.{payload}"
    sig = _b64url(hmac.new(secret.encode("utf-8"), msg.encode("utf-8"), hashlib.sha256).digest())
    return f"{msg}.{sig}"


def verify_token(token: str, secret: str) -> dict | None:
    """校验 JWT 签名与过期时间，返回 payload；签名错/过期/格式坏一律返回 None。"""
    try:
        header, payload, sig = token.split(".")
        msg = f"{header}.{payload}"
        expected = hmac.new(secret.encode("utf-8"), msg.encode("utf-8"), hashlib.sha256).digest()
        if not hmac.compare_digest(_b64url_decode(sig), expected):
            return None
        data = json.loads(_b64url_decode(payload).decode("utf-8"))
        if int(data.get("exp", 0)) < int(time.time()):
            return None
        return data
    except Exception:
        return None


def verify_password(provided: str, expected: str) -> bool:
    """常量时间比较口令（单用户 env 明文场景够用；升级 bcrypt 时替换实现）。"""
    return secrets.compare_digest(provided or "", expected or "")


def require_auth(cred: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> str:
    """FastAPI 依赖：校验 Bearer token，返回用户名。

    - auth_enabled=False：跳过鉴权（评测脚本 / 本地开发直连）
    - 未配置 jwt_secret：503 失败关闭——"开了鉴权但没配密钥"的部署不该裸奔放行
    """
    if not settings.auth_enabled:
        return "public"
    if not settings.jwt_secret:
        raise HTTPException(503, "认证服务未配置（缺 JWT_SECRET）")
    if cred is None or cred.scheme.lower() != "bearer":
        raise HTTPException(401, "未认证，请先登录")
    payload = verify_token(cred.credentials, settings.jwt_secret)
    if payload is None:
        raise HTTPException(401, "登录已过期，请重新登录")
    return payload.get("sub", "unknown")
