"""
认证模块
"""

from datetime import datetime, timedelta
from typing import Optional
from fastapi import HTTPException, Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from jose import JWTError, jwt
from passlib.context import CryptContext

from .config import get_settings

# 密码上下文
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Bearer token 认证
security = HTTPBearer(auto_error=False)


def create_access_token(data: dict) -> str:
    """创建 JWT token"""
    settings = get_settings()
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(hours=settings.jwt_expire_hours)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.jwt_secret, algorithm=settings.jwt_algorithm)
    return encoded_jwt


def verify_password_and_role(plain_password: str) -> tuple:
    """
    验证密码并返回角色信息
    返回: (is_valid, role, guest_id)
    - 管理员: (True, "admin", "")
    - 访客: (True, "guest", "1"/"2"/...)
    - 无效: (False, "", "")
    """
    settings = get_settings()
    
    # 检查管理员密码
    if plain_password == settings.access_password:
        return (True, "admin", "")
    
    # 检查访客密码
    for guest_id, pwd in settings.guest_passwords.items():
        if plain_password == pwd:
            return (True, "guest", guest_id)
    
    return (False, "", "")


def decode_token(token: str) -> Optional[dict]:
    """解码 JWT token"""
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        return payload
    except JWTError:
        return None


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> dict:
    """获取当前用户 (FastAPI 依赖)"""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未提供认证凭据",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    token = credentials.credentials
    payload = decode_token(token)
    
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token 无效或已过期",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    return {
        "authenticated": True,
        "role": payload.get("role", "admin"),  # 兼容旧token，默认admin
        "guest_id": payload.get("guest_id", "")
    }
