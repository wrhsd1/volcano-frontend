"""
API Key 认证模块
支持使用 ACCESS_PASSWORD 或 GUEST_PASSWORD 作为 API Key
"""

from typing import Optional
from fastapi import HTTPException, Header, status

from .config import get_settings


async def get_api_user(
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    authorization: Optional[str] = Header(None)
) -> dict:
    """
    获取 API 用户 (FastAPI 依赖)
    
    支持两种认证方式:
    1. X-API-Key: your-password
    2. Authorization: Bearer your-password
    
    Returns:
        dict: {"authenticated": True, "role": "admin"|"guest", "guest_id": ""|"1"|"2"...}
    """
    api_key = None
    
    # 优先使用 X-API-Key
    if x_api_key:
        api_key = x_api_key
    # 其次使用 Authorization: Bearer xxx
    elif authorization:
        if authorization.startswith("Bearer "):
            api_key = authorization[7:]
        else:
            api_key = authorization
    
    if not api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未提供 API Key，请在 Header 中设置 X-API-Key 或 Authorization: Bearer xxx",
        )
    
    # 验证 API Key
    settings = get_settings()
    
    # 检查管理员密码
    if api_key == settings.access_password:
        return {
            "authenticated": True,
            "role": "admin",
            "guest_id": ""
        }
    
    # 检查访客密码
    for guest_id, pwd in settings.guest_passwords.items():
        if api_key == pwd:
            return {
                "authenticated": True,
                "role": "guest",
                "guest_id": guest_id
            }
    
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="API Key 无效",
    )
