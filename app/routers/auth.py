"""
认证 API 路由
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..auth import verify_password, create_access_token

router = APIRouter(prefix="/api/auth", tags=["认证"])


class LoginRequest(BaseModel):
    """登录请求"""
    password: str


class LoginResponse(BaseModel):
    """登录响应"""
    ok: bool
    token: str


@router.post("/login", response_model=LoginResponse)
async def login(request: LoginRequest):
    """登录"""
    if not verify_password(request.password):
        raise HTTPException(status_code=401, detail="密码错误")
    
    token = create_access_token({"sub": "user"})
    return LoginResponse(ok=True, token=token)


@router.post("/verify")
async def verify_token():
    """验证 token (仅测试用)"""
    return {"ok": True}
