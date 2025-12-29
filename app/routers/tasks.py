"""
任务管理 API 路由
"""

import json
from datetime import datetime
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from sqlalchemy.orm import selectinload
from pydantic import BaseModel
import httpx

from ..auth import get_current_user
from ..database import get_db, Task, Account
from .accounts import get_daily_usage, update_daily_usage
from ..config import get_settings

router = APIRouter(prefix="/api/tasks", tags=["任务管理"])

# 火山 API 基础 URL
VOLCANO_API_BASE = "https://ark.cn-beijing.volces.com/api/v3"


# ======================== 请求/响应模型 ========================

class TaskCreateRequest(BaseModel):
    """创建任务请求"""
    account_id: int
    prompt: Optional[str] = None
    first_frame_base64: Optional[str] = None  # base64 格式: data:image/xxx;base64,xxx
    last_frame_base64: Optional[str] = None
    first_frame_url: Optional[str] = None
    last_frame_url: Optional[str] = None
    ratio: str = "16:9"
    resolution: str = "720p"
    duration: int = 5
    video_count: int = 1
    generate_audio: bool = True
    seed: int = -1
    watermark: bool = False
    camera_fixed: bool = False


class TaskResponse(BaseModel):
    """任务响应"""
    id: int
    task_id: str
    account_id: int
    account_name: Optional[str]
    status: str
    generation_type: Optional[str]
    result_url: Optional[str]
    last_frame_url: Optional[str]
    token_usage: Optional[int]
    error_message: Optional[str]
    created_at: Optional[str]
    updated_at: Optional[str]


class TaskListResponse(BaseModel):
    """任务列表响应"""
    ok: bool
    tasks: List[TaskResponse]
    total: int


class TokenEstimate(BaseModel):
    """Token 预估"""
    tokens: int
    price_with_audio: float
    price_without_audio: float


# ======================== Token 计算 ========================

# Seedance 1.5 Pro 分辨率对应的像素值
RESOLUTION_PIXELS = {
    '480p': {
        '16:9': (864, 496),
        '4:3': (752, 560),
        '1:1': (640, 640),
        '3:4': (560, 752),
        '9:16': (496, 864),
        '21:9': (992, 432),
    },
    '720p': {
        '16:9': (1280, 720),
        '4:3': (1112, 834),
        '1:1': (960, 960),
        '3:4': (834, 1112),
        '9:16': (720, 1280),
        '21:9': (1470, 630),
    },
}

# 价格 (元/千tokens)
PRICE_WITH_AUDIO = 0.0160
PRICE_WITHOUT_AUDIO = 0.0080


def calculate_tokens(resolution: str, ratio: str, duration: int, fps: int = 24) -> int:
    """
    计算 Token 数量
    公式: width * height * fps * duration / 1024
    """
    if resolution not in RESOLUTION_PIXELS:
        resolution = '720p'
    if ratio not in RESOLUTION_PIXELS[resolution]:
        ratio = '16:9'
    
    width, height = RESOLUTION_PIXELS[resolution][ratio]
    tokens = int(width * height * fps * duration / 1024)
    
    return tokens


def calculate_price(tokens: int, has_audio: bool) -> float:
    """计算价格"""
    price_per_k = PRICE_WITH_AUDIO if has_audio else PRICE_WITHOUT_AUDIO
    return round(tokens / 1000 * price_per_k, 4)


# ======================== API 端点 ========================

@router.post("/estimate", response_model=TokenEstimate)
async def estimate_tokens(
    resolution: str = Query("720p"),
    ratio: str = Query("16:9"),
    duration: int = Query(5),
    video_count: int = Query(1),
    user: dict = Depends(get_current_user)
):
    """预估 Token 消耗和价格"""
    tokens_per_video = calculate_tokens(resolution, ratio, duration)
    total_tokens = tokens_per_video * video_count
    
    return TokenEstimate(
        tokens=total_tokens,
        price_with_audio=calculate_price(total_tokens, True),
        price_without_audio=calculate_price(total_tokens, False),
    )


@router.post("", response_model=List[TaskResponse])
async def create_task(
    request: TaskCreateRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """创建视频生成任务"""
    settings = get_settings()
    
    # 获取账户
    result = await db.execute(select(Account).where(Account.id == request.account_id))
    account = result.scalar_one_or_none()
    
    if not account:
        raise HTTPException(status_code=404, detail="账户不存在")
    
    if not account.is_active:
        raise HTTPException(status_code=400, detail="账户已禁用")
    
    # 检查剩余额度
    used = await get_daily_usage(db, account.id)
    tokens_per_video = calculate_tokens(request.resolution, request.ratio, request.duration)
    total_needed = tokens_per_video * request.video_count
    remaining = settings.daily_token_limit - used
    
    if total_needed > remaining:
        raise HTTPException(
            status_code=400, 
            detail=f"额度不足，需要 {total_needed} tokens，剩余 {remaining} tokens"
        )
    
    # 确定生成类型和构建 content
    has_first_frame = bool(request.first_frame_base64 or request.first_frame_url)
    has_last_frame = bool(request.last_frame_base64 or request.last_frame_url)
    
    if has_last_frame and not has_first_frame:
        raise HTTPException(status_code=400, detail="缺失首帧图片：仅提供尾帧图片时，必须同时提供首帧图片")
    
    if has_first_frame and has_last_frame:
        generation_type = "first_last_frame"
    elif has_first_frame:
        generation_type = "first_frame"
    else:
        generation_type = "text_to_video"
        if not request.prompt:
            raise HTTPException(status_code=400, detail="文生视频模式需要提供提示词")
    
    # 构建参数字符串
    params_str = f"--rs {request.resolution} --rt {request.ratio} --dur {request.duration} --wm {'true' if request.watermark else 'false'} --cf {'true' if request.camera_fixed else 'false'}"
    if request.seed != -1:
        params_str += f" --seed {request.seed}"
    
    prompt_with_params = f"{request.prompt or ''} {params_str}".strip()
    
    # 创建任务列表
    created_tasks = []
    
    for i in range(request.video_count):
        # 构建 content 数组
        content = []
        
        # 添加文本
        if prompt_with_params:
            content.append({
                "type": "text",
                "text": prompt_with_params
            })
        
        # 添加首帧图片
        if has_first_frame:
            first_url = request.first_frame_url or request.first_frame_base64
            img_obj = {
                "type": "image_url",
                "image_url": {"url": first_url}
            }
            if has_last_frame:
                img_obj["role"] = "first_frame"
            content.append(img_obj)
        
        # 添加尾帧图片
        if has_last_frame:
            last_url = request.last_frame_url or request.last_frame_base64
            content.append({
                "type": "image_url",
                "image_url": {"url": last_url},
                "role": "last_frame"
            })
        
        # 构建请求体
        api_request = {
            "model": account.model_id,
            "content": content,
            "generate_audio": request.generate_audio,
        }
        
        # 调用火山 API
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    f"{VOLCANO_API_BASE}/contents/generations/tasks",
                    json=api_request,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {account.api_key}"
                    }
                )
                
                if resp.status_code != 200:
                    error_detail = resp.text
                    try:
                        error_json = resp.json()
                        error_detail = error_json.get("error", {}).get("message", resp.text)
                    except:
                        pass
                    raise HTTPException(status_code=resp.status_code, detail=f"火山 API 错误: {error_detail}")
                
                data = resp.json()
                task_id = data.get("id")
                
                if not task_id:
                    raise HTTPException(status_code=500, detail="火山 API 未返回任务 ID")
        
        except httpx.RequestError as e:
            raise HTTPException(status_code=500, detail=f"请求火山 API 失败: {str(e)}")
        
        # 保存任务到数据库
        task = Task(
            task_id=task_id,
            account_id=account.id,
            status="queued",
            generation_type=generation_type,
            params=json.dumps(api_request, ensure_ascii=False),
        )
        
        db.add(task)
        await db.commit()
        await db.refresh(task)
        
        # 更新使用量
        await update_daily_usage(db, account.id, tokens_per_video)
        
        created_tasks.append(TaskResponse(**task.to_dict()))
    
    return created_tasks


@router.get("", response_model=TaskListResponse)
async def list_tasks(
    account_id: Optional[int] = Query(None, description="按账户筛选"),
    status: Optional[str] = Query(None, description="按状态筛选"),
    limit: int = Query(50, le=100),
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """列出所有任务"""
    query = select(Task).options(selectinload(Task.account)).order_by(desc(Task.created_at))
    
    if account_id is not None:
        query = query.where(Task.account_id == account_id)
    if status is not None:
        query = query.where(Task.status == status)
    
    query = query.limit(limit)
    
    result = await db.execute(query)
    tasks = result.scalars().all()
    
    return TaskListResponse(
        ok=True,
        tasks=[TaskResponse(**t.to_dict()) for t in tasks],
        total=len(tasks)
    )


@router.get("/{task_id}", response_model=TaskResponse)
async def get_task(
    task_id: str,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """获取任务详情（同时从火山 API 同步状态）"""
    result = await db.execute(
        select(Task).options(selectinload(Task.account)).where(Task.task_id == task_id)
    )
    task = result.scalar_one_or_none()
    
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    # 如果任务未完成，从火山 API 同步状态
    if task.status in ["queued", "running"]:
        await sync_task_status(task, db)
    
    return TaskResponse(**task.to_dict())


@router.post("/{task_id}/sync", response_model=TaskResponse)
async def sync_task(
    task_id: str,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """手动同步任务状态"""
    result = await db.execute(
        select(Task).options(selectinload(Task.account)).where(Task.task_id == task_id)
    )
    task = result.scalar_one_or_none()
    
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    await sync_task_status(task, db)
    
    return TaskResponse(**task.to_dict())


@router.delete("/{task_id}")
async def delete_task(
    task_id: str,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """删除任务"""
    result = await db.execute(
        select(Task).options(selectinload(Task.account)).where(Task.task_id == task_id)
    )
    task = result.scalar_one_or_none()
    
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    # 尝试从火山 API 删除（目前火山 API 可能不支持删除，仅删除本地记录）
    # 本地删除
    await db.delete(task)
    await db.commit()
    
    return {"ok": True, "message": "任务已删除"}


# ======================== 辅助函数 ========================

async def sync_task_status(task: Task, db: AsyncSession):
    """从火山 API 同步任务状态"""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{VOLCANO_API_BASE}/contents/generations/tasks/{task.task_id}",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {task.account.api_key}"
                }
            )
            
            if resp.status_code == 200:
                data = resp.json()
                
                task.status = data.get("status", task.status)
                task.updated_at = datetime.utcnow()
                
                # 获取结果
                content = data.get("content", {})
                if content:
                    task.result_url = content.get("video_url")
                    task.last_frame_url = content.get("last_frame_url")
                
                # 获取 token 使用量
                usage = data.get("usage", {})
                if usage:
                    task.token_usage = usage.get("total_tokens")
                
                # 获取错误信息
                error = data.get("error")
                if error:
                    task.error_message = error.get("message", str(error))
                
                await db.commit()
    except Exception as e:
        print(f"同步任务状态失败: {e}")
