"""
认证 API 路由
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..auth import verify_password_and_role, create_access_token

router = APIRouter(prefix="/api/auth", tags=["认证"])


class LoginRequest(BaseModel):
    """登录请求"""
    password: str


class LoginResponse(BaseModel):
    """登录响应"""
    ok: bool
    token: str
    role: str      # "admin" | "guest"
    guest_id: str  # "" for admin, "1"/"2" for guests


@router.post("/login", response_model=LoginResponse)
async def login(request: LoginRequest):
    """登录"""
    is_valid, role, guest_id = verify_password_and_role(request.password)
    
    if not is_valid:
        raise HTTPException(status_code=401, detail="密码错误")
    
    # 在 token 中包含角色信息
    token = create_access_token({
        "sub": "user",
        "role": role,
        "guest_id": guest_id
    })
    
    return LoginResponse(ok=True, token=token, role=role, guest_id=guest_id)


@router.post("/verify")
async def verify_token():
    """验证 token (仅测试用)"""
    return {"ok": True}

