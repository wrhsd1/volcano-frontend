"""
账户管理 API 路由
"""

from datetime import datetime, date, timezone, timedelta
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from pydantic import BaseModel

from ..auth import get_current_user
from ..database import get_db, Account, DailyUsage
from ..config import get_settings

router = APIRouter(prefix="/api/accounts", tags=["账户管理"])

# 北京时区
BEIJING_TZ = timezone(timedelta(hours=8))


def get_beijing_date() -> date:
    """获取当前北京时间日期"""
    return datetime.now(BEIJING_TZ).date()


# ======================== 请求/响应模型 ========================

class AccountCreate(BaseModel):
    """创建账户请求"""
    name: str
    video_model_id: Optional[str] = None  # 视频生成端点ID
    image_model_id: Optional[str] = None  # 图片生成端点ID
    # Banana (Gemini) API 配置
    banana_base_url: Optional[str] = None
    banana_api_key: Optional[str] = None
    banana_model_name: Optional[str] = "gemini-3-pro-image-preview"
    api_key: str


class AccountUpdate(BaseModel):
    """更新账户请求"""
    name: Optional[str] = None
    video_model_id: Optional[str] = None
    image_model_id: Optional[str] = None
    # Banana (Gemini) API 配置
    banana_base_url: Optional[str] = None
    banana_api_key: Optional[str] = None
    banana_model_name: Optional[str] = None
    api_key: Optional[str] = None
    is_active: Optional[bool] = None


class AccountResponse(BaseModel):
    """账户响应"""
    id: int
    name: str
    video_model_id: Optional[str]
    image_model_id: Optional[str]
    # Banana (Gemini) API 配置
    banana_base_url: Optional[str]
    banana_model_name: Optional[str]
    is_active: bool
    # 视频配额
    daily_limit: int
    used_tokens: int
    remaining_tokens: int
    # 图片配额
    daily_image_limit: int
    used_images: int
    remaining_images: int
    created_at: Optional[str]
    updated_at: Optional[str]


# ======================== 辅助函数 ========================

async def get_daily_usage(db: AsyncSession, account_id: int) -> int:
    """获取账户当日已使用的 Token 数"""
    today = get_beijing_date()
    result = await db.execute(
        select(DailyUsage).where(
            and_(
                DailyUsage.account_id == account_id,
                DailyUsage.usage_date == today
            )
        )
    )
    usage = result.scalar_one_or_none()
    return usage.used_tokens if usage else 0


async def get_daily_image_usage(db: AsyncSession, account_id: int) -> int:
    """获取账户当日已使用的图片生成数量"""
    today = get_beijing_date()
    result = await db.execute(
        select(DailyUsage).where(
            and_(
                DailyUsage.account_id == account_id,
                DailyUsage.usage_date == today
            )
        )
    )
    usage = result.scalar_one_or_none()
    return usage.used_images if usage else 0


async def update_daily_usage(db: AsyncSession, account_id: int, tokens: int):
    """更新账户当日视频Token使用量"""
    today = get_beijing_date()
    result = await db.execute(
        select(DailyUsage).where(
            and_(
                DailyUsage.account_id == account_id,
                DailyUsage.usage_date == today
            )
        )
    )
    usage = result.scalar_one_or_none()
    
    if usage:
        usage.used_tokens += tokens
    else:
        usage = DailyUsage(
            account_id=account_id,
            usage_date=today,
            used_tokens=tokens,
            used_images=0
        )
        db.add(usage)
    
    await db.commit()


async def update_daily_image_usage(db: AsyncSession, account_id: int, images: int):
    """更新账户当日图片使用量"""
    today = get_beijing_date()
    result = await db.execute(
        select(DailyUsage).where(
            and_(
                DailyUsage.account_id == account_id,
                DailyUsage.usage_date == today
            )
        )
    )
    usage = result.scalar_one_or_none()
    
    if usage:
        usage.used_images += images
    else:
        usage = DailyUsage(
            account_id=account_id,
            usage_date=today,
            used_tokens=0,
            used_images=images
        )
        db.add(usage)
    
    await db.commit()


# ======================== API 端点 ========================

@router.get("", response_model=List[AccountResponse])
async def list_accounts(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """列出所有账户(含当日剩余额度)"""
    settings = get_settings()
    result = await db.execute(select(Account).order_by(Account.id))
    accounts = result.scalars().all()
    
    response = []
    for account in accounts:
        used_tokens = await get_daily_usage(db, account.id)
        remaining_tokens = max(0, settings.daily_token_limit - used_tokens)
        used_images = await get_daily_image_usage(db, account.id)
        remaining_images = max(0, settings.daily_image_limit - used_images)
        
        response.append(AccountResponse(
            id=account.id,
            name=account.name,
            video_model_id=account.video_model_id,
            image_model_id=account.image_model_id,
            banana_base_url=account.banana_base_url,
            banana_model_name=account.banana_model_name,
            is_active=account.is_active,
            daily_limit=settings.daily_token_limit,
            used_tokens=used_tokens,
            remaining_tokens=remaining_tokens,
            daily_image_limit=settings.daily_image_limit,
            used_images=used_images,
            remaining_images=remaining_images,
            created_at=account.created_at.isoformat() if account.created_at else None,
            updated_at=account.updated_at.isoformat() if account.updated_at else None,
        ))
    
    return response


@router.post("", response_model=AccountResponse)
async def create_account(
    request: AccountCreate,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """创建新账户"""
    settings = get_settings()
    
    # 至少需要一个 model_id 或 Banana 配置
    if not request.video_model_id and not request.image_model_id and not request.banana_base_url:
        raise HTTPException(status_code=400, detail="至少需要提供 video_model_id、image_model_id 或 Banana API 配置")
    
    account = Account(
        name=request.name,
        video_model_id=request.video_model_id,
        image_model_id=request.image_model_id,
        banana_base_url=request.banana_base_url,
        banana_api_key=request.banana_api_key,
        banana_model_name=request.banana_model_name,
        api_key=request.api_key,
    )
    
    db.add(account)
    await db.commit()
    await db.refresh(account)
    
    return AccountResponse(
        id=account.id,
        name=account.name,
        video_model_id=account.video_model_id,
        image_model_id=account.image_model_id,
        banana_base_url=account.banana_base_url,
        banana_model_name=account.banana_model_name,
        is_active=account.is_active,
        daily_limit=settings.daily_token_limit,
        used_tokens=0,
        remaining_tokens=settings.daily_token_limit,
        daily_image_limit=settings.daily_image_limit,
        used_images=0,
        remaining_images=settings.daily_image_limit,
        created_at=account.created_at.isoformat() if account.created_at else None,
        updated_at=account.updated_at.isoformat() if account.updated_at else None,
    )


@router.get("/{account_id}", response_model=AccountResponse)
async def get_account(
    account_id: int,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """获取账户详情"""
    settings = get_settings()
    result = await db.execute(select(Account).where(Account.id == account_id))
    account = result.scalar_one_or_none()
    
    if not account:
        raise HTTPException(status_code=404, detail="账户不存在")
    
    used_tokens = await get_daily_usage(db, account.id)
    remaining_tokens = max(0, settings.daily_token_limit - used_tokens)
    used_images = await get_daily_image_usage(db, account.id)
    remaining_images = max(0, settings.daily_image_limit - used_images)
    
    return AccountResponse(
        id=account.id,
        name=account.name,
        video_model_id=account.video_model_id,
        image_model_id=account.image_model_id,
        banana_base_url=account.banana_base_url,
        banana_model_name=account.banana_model_name,
        is_active=account.is_active,
        daily_limit=settings.daily_token_limit,
        used_tokens=used_tokens,
        remaining_tokens=remaining_tokens,
        daily_image_limit=settings.daily_image_limit,
        used_images=used_images,
        remaining_images=remaining_images,
        created_at=account.created_at.isoformat() if account.created_at else None,
        updated_at=account.updated_at.isoformat() if account.updated_at else None,
    )


@router.put("/{account_id}", response_model=AccountResponse)
async def update_account(
    account_id: int,
    request: AccountUpdate,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """更新账户配置"""
    settings = get_settings()
    result = await db.execute(select(Account).where(Account.id == account_id))
    account = result.scalar_one_or_none()
    
    if not account:
        raise HTTPException(status_code=404, detail="账户不存在")
    
    # 更新字段
    if request.name is not None:
        account.name = request.name
    if request.video_model_id is not None:
        account.video_model_id = request.video_model_id
    if request.image_model_id is not None:
        account.image_model_id = request.image_model_id
    if request.banana_base_url is not None:
        account.banana_base_url = request.banana_base_url
    if request.banana_api_key is not None:
        account.banana_api_key = request.banana_api_key
    if request.banana_model_name is not None:
        account.banana_model_name = request.banana_model_name
    if request.api_key is not None:
        account.api_key = request.api_key
    if request.is_active is not None:
        account.is_active = request.is_active
    
    await db.commit()
    await db.refresh(account)
    
    used_tokens = await get_daily_usage(db, account.id)
    remaining_tokens = max(0, settings.daily_token_limit - used_tokens)
    used_images = await get_daily_image_usage(db, account.id)
    remaining_images = max(0, settings.daily_image_limit - used_images)
    
    return AccountResponse(
        id=account.id,
        name=account.name,
        video_model_id=account.video_model_id,
        image_model_id=account.image_model_id,
        banana_base_url=account.banana_base_url,
        banana_model_name=account.banana_model_name,
        is_active=account.is_active,
        daily_limit=settings.daily_token_limit,
        used_tokens=used_tokens,
        remaining_tokens=remaining_tokens,
        daily_image_limit=settings.daily_image_limit,
        used_images=used_images,
        remaining_images=remaining_images,
        created_at=account.created_at.isoformat() if account.created_at else None,
        updated_at=account.updated_at.isoformat() if account.updated_at else None,
    )


@router.delete("/{account_id}")
async def delete_account(
    account_id: int,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """删除账户"""
    result = await db.execute(select(Account).where(Account.id == account_id))
    account = result.scalar_one_or_none()
    
    if not account:
        raise HTTPException(status_code=404, detail="账户不存在")
    
    await db.delete(account)
    await db.commit()
    
    return {"ok": True, "message": "账户已删除"}
