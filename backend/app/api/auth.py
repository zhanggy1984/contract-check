"""登录接口：单用户账号密码换 JWT。未配置认证时失败关闭（不裸奔）。"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.common.security import create_token, verify_password
from app.config import settings

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginBody(BaseModel):
    username: str
    password: str


@router.post("/login")
def login(body: LoginBody) -> dict:
    """校验账号密码，签发 Bearer token。前端登录页唯一入口（豁免鉴权）。"""
    if not settings.auth_enabled:
        # 鉴权关闭模式前端不展示登录页，正常流程不会走到这里；保留 401 语义防误用
        raise HTTPException(401, "鉴权未开启")
    if not settings.jwt_secret or not settings.auth_password:
        raise HTTPException(503, "认证服务未配置（缺 AUTH_PASSWORD / JWT_SECRET）")
    if body.username != settings.auth_username or not verify_password(body.password, settings.auth_password):
        raise HTTPException(401, "用户名或密码错误")
    token = create_token(body.username, settings.jwt_secret, settings.jwt_expire_minutes)
    return {"token": token, "token_type": "bearer", "expires_in": settings.jwt_expire_minutes * 60}
