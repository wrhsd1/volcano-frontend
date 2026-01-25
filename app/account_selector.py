"""
账户自动选择模块
根据任务类型和剩余额度自动选择最优账户
"""

from typing import Optional, List
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException

from .database import Account
from .routers.accounts import get_daily_usage, get_daily_image_usage
from .config import get_settings


async def get_active_accounts(db: AsyncSession) -> List[Account]:
    """获取所有激活的账户"""
    result = await db.execute(
        select(Account).where(Account.is_active == True)
    )
    return list(result.scalars().all())


async def select_best_account(
    db: AsyncSession,
    task_type: str,
    specified_account_id: Optional[int] = None
) -> Account:
    """
    自动选择最优账户
    
    Args:
        db: 数据库会话
        task_type: 任务类型 (video/image/banana)
        specified_account_id: 可选，指定的账户ID
    
    Returns:
        选择的账户对象
        
    Raises:
        HTTPException: 没有可用账户或账户额度已用尽
    """
    settings = get_settings()
    
    # 如果指定了账户ID，直接使用
    if specified_account_id is not None:
        result = await db.execute(
            select(Account).where(
                Account.id == specified_account_id,
                Account.is_active == True
            )
        )
        account = result.scalar_one_or_none()
        
        if not account:
            raise HTTPException(
                status_code=400,
                detail=f"账户 ID {specified_account_id} 不存在或已禁用"
            )
        
        # 检查账户是否有对应的端点配置
        if task_type == "video" and not account.video_model_id:
            raise HTTPException(
                status_code=400,
                detail=f"账户 '{account.name}' 未配置视频端点"
            )
        elif task_type == "image" and not account.image_model_id:
            raise HTTPException(
                status_code=400,
                detail=f"账户 '{account.name}' 未配置图片端点"
            )
        elif task_type == "banana" and not account.banana_api_key:
            raise HTTPException(
                status_code=400,
                detail=f"账户 '{account.name}' 未配置 Banana API"
            )
        
        # 检查额度
        if task_type == "video":
            used = await get_daily_usage(db, account.id)
            remaining = settings.daily_token_limit - used
            if remaining <= 0:
                raise HTTPException(
                    status_code=400,
                    detail=f"账户 '{account.name}' 今日视频 Token 额度已用尽"
                )
        else:  # image / banana
            used = await get_daily_image_usage(db, account.id)
            remaining = settings.daily_image_limit - used
            if remaining <= 0:
                raise HTTPException(
                    status_code=400,
                    detail=f"账户 '{account.name}' 今日图片额度已用尽"
                )
        
        return account
    
    # 自动选择: 获取所有激活的账户
    accounts = await get_active_accounts(db)
    
    if not accounts:
        raise HTTPException(
            status_code=400,
            detail="没有可用的账户"
        )
    
    # 根据任务类型过滤有对应端点的账户
    if task_type == "video":
        accounts = [a for a in accounts if a.video_model_id]
    elif task_type == "image":
        accounts = [a for a in accounts if a.image_model_id]
    elif task_type == "banana":
        accounts = [a for a in accounts if a.banana_api_key]
    
    if not accounts:
        raise HTTPException(
            status_code=400,
            detail=f"没有配置 {task_type} 端点的账户"
        )
    
    # 计算每个账户的剩余额度
    account_quotas = []
    for account in accounts:
        if task_type == "video":
            used = await get_daily_usage(db, account.id)
            remaining = settings.daily_token_limit - used
        else:  # image / banana
            used = await get_daily_image_usage(db, account.id)
            remaining = settings.daily_image_limit - used
        
        if remaining > 0:
            account_quotas.append((account, remaining))
    
    if not account_quotas:
        raise HTTPException(
            status_code=400,
            detail="所有账户的今日额度已用尽"
        )
    
    # 按剩余额度降序排序，选择最高的
    account_quotas.sort(key=lambda x: x[1], reverse=True)
    return account_quotas[0][0]


async def get_accounts_with_quota(db: AsyncSession) -> List[dict]:
    """
    获取所有账户及其剩余额度信息
    
    Returns:
        账户信息列表，包含剩余额度
    """
    settings = get_settings()
    accounts = await get_active_accounts(db)
    
    result = []
    for account in accounts:
        video_used = await get_daily_usage(db, account.id)
        image_used = await get_daily_image_usage(db, account.id)
        
        result.append({
            "id": account.id,
            "name": account.name,
            "has_video": bool(account.video_model_id),
            "has_image": bool(account.image_model_id),
            "has_banana": bool(account.banana_api_key),
            "video_daily_limit": settings.daily_token_limit,
            "video_used_tokens": video_used,
            "video_remaining_tokens": settings.daily_token_limit - video_used,
            "image_daily_limit": settings.daily_image_limit,
            "image_used": image_used,
            "image_remaining": settings.daily_image_limit - image_used,
        })
    
    return result
