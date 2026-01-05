"""
图片生成 API 路由
使用 doubao-seedream-4.5 模型
支持异步后台处理
"""

import json
import uuid
import asyncio
import threading
import os
import shutil
import base64
from pathlib import Path
from datetime import datetime
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Depends, Query, BackgroundTasks
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select, desc
from sqlalchemy.orm import selectinload
from pydantic import BaseModel
import httpx
import logging

from ..auth import get_current_user
from ..database import get_db, Task, Account, Base
from .accounts import get_daily_image_usage, update_daily_image_usage
from .upload import get_base64_from_file_id, delete_file_by_id
from ..config import get_settings

router = APIRouter(prefix="/api/images", tags=["图片生成"])

# 日志
logger = logging.getLogger(__name__)

# 火山图片生成 API URL
VOLCANO_IMAGE_API = "https://ark.cn-beijing.volces.com/api/v3/images/generations"

# doubao-seedream-4.5 推荐的尺寸
SIZE_MAP_2K = {
    "1:1": "2048x2048",
    "4:3": "2304x1728",
    "3:4": "1728x2304",
    "16:9": "2560x1440",
    "9:16": "1440x2560",
    "3:2": "2496x1664",
    "2:3": "1664x2496",
    "21:9": "3024x1296",
}

SIZE_MAP_4K = {
    "1:1": "4096x4096",
    "4:3": "4608x3456",
    "3:4": "3456x4608",
    "16:9": "5120x2880",
    "9:16": "2880x5120",
    "3:2": "4992x3328",
    "2:3": "3328x4992",
    "21:9": "6048x2592",
}

VOLCANO_SIZE_MAP = SIZE_MAP_2K | SIZE_MAP_4K

# 图片价格 (元/张)
IMAGE_PRICE = 0.25


# ======================== 辅助函数 ========================

def get_storage_size(path: str) -> tuple[int, int]:
    """获取目录大小和文件数量"""
    total_size = 0
    file_count = 0
    
    if not os.path.exists(path):
        return 0, 0
    
    for dirpath, dirnames, filenames in os.walk(path):
        for filename in filenames:
            filepath = os.path.join(dirpath, filename)
            if os.path.isfile(filepath):
                total_size += os.path.getsize(filepath)
                file_count += 1
    
    return total_size, file_count


def format_size(size_bytes: int) -> str:
    """格式化文件大小"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def save_ref_image(base64_data: str, task_dir: str, index: int) -> str:
    """保存参考图片到本地，返回文件路径"""
    Path(task_dir).mkdir(parents=True, exist_ok=True)
    
    # 处理 data:image/xxx;base64, 前缀
    if base64_data.startswith("data:"):
        base64_start = base64_data.find(",") + 1
        img_base64 = base64_data[base64_start:]
    else:
        img_base64 = base64_data
    
    image_data = base64.b64decode(img_base64)
    filename = f"ref_{index}.png"
    filepath = os.path.join(task_dir, filename)
    
    with open(filepath, 'wb') as f:
        f.write(image_data)
    
    return filepath


# ======================== 请求/响应模型 ========================

class ImageCreateRequest(BaseModel):
    """创建图片生成任务请求"""
    account_id: int
    prompt: str  # 提示词，必填
    
    # 参考图片 (可选，最多14张)
    images: Optional[List[str]] = None  # URL 或 base64 数组
    file_ids: Optional[List[str]] = None  # 服务端预上传的文件ID数组
    
    # 尺寸设置
    size: str = "2K"  # "2K" / "4K" / "2048x2048" 等
    
    # 生成数量 (非组图模式)
    count: int = 1  # 1-9
    
    # 组图设置
    sequential_image_generation: str = "disabled"  # "auto" / "disabled"
    max_images: int = 4  # 组图最大数量 1-15
    
    # 其他选项
    watermark: bool = False
    optimize_prompt: bool = True  # 是否开启提示词优化
    response_format: str = "url"  # "url" / "b64_json"


class ImageTaskResponse(BaseModel):
    """图片任务响应"""
    id: int
    task_id: str
    account_id: int
    account_name: Optional[str]
    task_type: str
    status: str
    generation_type: Optional[str]
    result_urls: Optional[str]  # JSON array
    image_count: Optional[int]
    token_usage: Optional[int]
    error_message: Optional[str]
    created_at: Optional[str]
    updated_at: Optional[str]


class ImageListResponse(BaseModel):
    """图片任务列表响应"""
    ok: bool
    tasks: List[ImageTaskResponse]
    total: int


class ImageEstimate(BaseModel):
    """图片生成预估"""
    count: int
    price: float


# ======================== 分辨率配置 ========================

# doubao-seedream-4.5 推荐的尺寸
RECOMMENDED_SIZES = {
    "1:1": "2048x2048",
    "4:3": "2304x1728",
    "3:4": "1728x2304",
    "16:9": "2560x1440",
    "9:16": "1440x2560",
    "3:2": "2496x1664",
    "2:3": "1664x2496",
    "21:9": "3024x1296",
}


# ======================== 后台任务处理 ========================

def run_async_task(coro):
    """在新的事件循环中运行异步任务"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(coro)
    finally:
        loop.close()


def start_background_task(task_id: str, api_request: dict, api_key: str, account_id: int):
    """启动后台线程执行图片生成"""
    thread = threading.Thread(
        target=run_async_task,
        args=(process_image_task(task_id, api_request, api_key, account_id),)
    )
    thread.daemon = True
    thread.start()


async def process_image_task(task_id: str, api_request: dict, api_key: str, account_id: int):
    """后台处理图片生成任务"""
    settings = get_settings()
    
    logger.info(f"[图片任务 {task_id}] 开始处理...")
    
    # 创建独立的数据库会话
    engine = create_async_engine(settings.database_url, echo=False)
    async_session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    async with async_session_maker() as db:
        try:
            # 调用火山图片生成 API
            logger.info(f"[图片任务 {task_id}] 调用火山API...")
            async with httpx.AsyncClient(timeout=180.0) as client:
                resp = await client.post(
                    VOLCANO_IMAGE_API,
                    json=api_request,
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {api_key}"
                    }
                )
                
                if resp.status_code != 200:
                    error_detail = resp.text
                    try:
                        error_json = resp.json()
                        error_detail = error_json.get("error", {}).get("message", resp.text)
                    except:
                        pass
                    
                    logger.error(f"[图片任务 {task_id}] API错误: {error_detail}")
                    
                    # 更新任务状态为失败
                    result = await db.execute(select(Task).where(Task.task_id == task_id))
                    task = result.scalar_one_or_none()
                    if task:
                        task.status = "failed"
                        task.error_message = f"火山图片API错误: {error_detail}"
                        task.updated_at = datetime.utcnow()
                        await db.commit()
                    return
                
                data = resp.json()
            
            logger.info(f"[图片任务 {task_id}] API返回成功，解析结果...")
            
            # 解析响应
            image_data = data.get("data", [])
            usage_info = data.get("usage", {})
            generated_count = usage_info.get("generated_images", len(image_data))
            
            # 提取图片URL
            result_urls = []
            for img in image_data:
                if "url" in img:
                    result_urls.append({
                        "url": img["url"],
                        "size": img.get("size", "")
                    })
                elif "b64_json" in img:
                    result_urls.append({
                        "b64_json": img["b64_json"],
                        "size": img.get("size", "")
                    })
                elif "error" in img:
                    result_urls.append({
                        "error": img["error"].get("message", "生成失败")
                    })
            
            # 更新任务状态为成功
            result = await db.execute(select(Task).where(Task.task_id == task_id))
            task = result.scalar_one_or_none()
            if task:
                task.status = "succeeded"
                task.result_urls = json.dumps(result_urls, ensure_ascii=False)
                task.image_count = generated_count
                task.token_usage = usage_info.get("total_tokens")
                task.updated_at = datetime.utcnow()
                await db.commit()
                
                logger.info(f"[图片任务 {task_id}] 完成，生成了 {generated_count} 张图片")
                
                # 更新图片使用量
                await update_daily_image_usage(db, account_id, generated_count)
            else:
                logger.error(f"[图片任务 {task_id}] 未找到任务记录")
                
        except httpx.RequestError as e:
            logger.error(f"[图片任务 {task_id}] 网络错误: {str(e)}")
            # 网络错误
            result = await db.execute(select(Task).where(Task.task_id == task_id))
            task = result.scalar_one_or_none()
            if task:
                task.status = "failed"
                task.error_message = f"请求火山API失败: {str(e)}"
                task.updated_at = datetime.utcnow()
                await db.commit()
        except Exception as e:
            logger.error(f"[图片任务 {task_id}] 处理异常: {str(e)}")
            # 其他错误
            result = await db.execute(select(Task).where(Task.task_id == task_id))
            task = result.scalar_one_or_none()
            if task:
                task.status = "failed"
                task.error_message = f"处理失败: {str(e)}"
                task.updated_at = datetime.utcnow()
                await db.commit()
    
    await engine.dispose()


# ======================== API 端点 ========================

@router.post("/estimate", response_model=ImageEstimate)
async def estimate_images(
    count: int = Query(1, ge=1, le=9),
    sequential: bool = Query(False),
    max_images: int = Query(4, ge=1, le=15),
    user: dict = Depends(get_current_user)
):
    """预估图片生成消耗"""
    if sequential:
        actual_count = max_images
    else:
        actual_count = count
    
    return ImageEstimate(
        count=actual_count,
        price=round(actual_count * IMAGE_PRICE, 2)
    )


@router.post("", response_model=List[ImageTaskResponse])
async def create_image_task(
    request: ImageCreateRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """创建图片生成任务 (异步处理)"""
    settings = get_settings()
    
    # 获取账户
    result = await db.execute(select(Account).where(Account.id == request.account_id))
    account = result.scalar_one_or_none()
    
    if not account:
        raise HTTPException(status_code=404, detail="账户不存在")
    
    if not account.is_active:
        raise HTTPException(status_code=400, detail="账户已禁用")
    
    if not account.image_model_id:
        raise HTTPException(status_code=400, detail="该账户未配置图片生成端点ID")
    
    # 检查剩余额度
    used = await get_daily_image_usage(db, account.id)
    
    # 计算预计使用量
    if request.sequential_image_generation == "auto":
        estimated_count = request.max_images
    else:
        estimated_count = request.count
    
    remaining = settings.daily_image_limit - used
    
    if estimated_count > remaining:
        raise HTTPException(
            status_code=400, 
            detail=f"额度不足，需要 {estimated_count} 张，剩余 {remaining} 张"
        )
    
    # 处理 file_ids - 转换为 base64
    final_images = list(request.images) if request.images else []
    uploaded_file_ids = []  # 记录使用的临时文件
    
    if request.file_ids:
        for file_id in request.file_ids:
            b64 = get_base64_from_file_id(file_id)
            if b64:
                final_images.append(b64)
                uploaded_file_ids.append(file_id)
            else:
                raise HTTPException(status_code=400, detail=f"参考图片文件 {file_id} 不存在或已过期")
    
    # 确定生成类型
    has_images = len(final_images) > 0
    if has_images:
        if len(final_images) > 1:
            generation_type = "multi_image"  # 多图融合
        else:
            generation_type = "image_to_image"  # 单图参考
    else:
        generation_type = "text_to_image"  # 纯文生图
    
    # 验证参考图片数量
    if request.images and len(request.images) > 14:
        raise HTTPException(status_code=400, detail="参考图片最多14张")
    
    # 验证组图模式下的参数
    if request.sequential_image_generation == "auto":
        if request.max_images < 1 or request.max_images > 15:
            raise HTTPException(status_code=400, detail="组图数量范围为1-15")
        # 组图限制: 输入图片数+生成图片数 <= 15
        input_count = len(request.images) if request.images else 0
        if input_count + request.max_images > 15:
            max_allowed = 15 - input_count
            raise HTTPException(
                status_code=400, 
                detail=f"参考图片{input_count}张 + 组图数量不能超过15张，最多可生成{max_allowed}张"
            )
    
    created_tasks = []
    
    # 非组图模式下需要创建多个任务
    task_count = request.count if request.sequential_image_generation == "disabled" else 1
    
    # 保存账户信息供后台任务使用
    api_key = account.api_key
    account_name = account.name
    
    for i in range(task_count):
        # 构建请求体
        api_request = {
            "model": account.image_model_id,
            "prompt": request.prompt,
            "size": request.size,
            "watermark": request.watermark,
            "response_format": request.response_format,
        }
        
        # 优化提示词选项 (仅在开启时添加)
        if request.optimize_prompt:
            api_request["optimize_prompt_options"] = {
                "mode": "standard"
            }
        
        # 添加参考图片
        if has_images:
            if len(final_images) == 1:
                api_request["image"] = final_images[0]
            else:
                api_request["image"] = final_images
        
        # 组图设置
        if request.sequential_image_generation == "auto":
            api_request["sequential_image_generation"] = "auto"
            api_request["sequential_image_generation_options"] = {
                "max_images": request.max_images
            }
        else:
            api_request["sequential_image_generation"] = "disabled"
        
        # 生成本地任务ID
        task_id = f"img-{uuid.uuid4().hex[:16]}"
        
        # 保存参考图片到本地 (如果有)
        saved_ref_paths = []
        if has_images:
            settings.ensure_volcano_ref_dir()
            task_dir = os.path.join(settings.volcano_ref_images_dir, task_id)
            for idx, img_data in enumerate(final_images):
                try:
                    filepath = save_ref_image(img_data, task_dir, idx)
                    saved_ref_paths.append(filepath)
                except Exception as e:
                    logger.warning(f"保存参考图片失败: {e}")
        
        # 为数据库存储创建不含 base64 的 params
        params_to_store = {
            "model": account.image_model_id,
            "prompt": request.prompt,
            "size": request.size,
            "watermark": request.watermark,
            "response_format": request.response_format,
            "sequential_image_generation": request.sequential_image_generation,
            "ref_image_count": len(request.images) if request.images else 0,
            "ref_image_paths": saved_ref_paths,  # 存储本地路径而非 base64
        }
        if request.optimize_prompt:
            params_to_store["optimize_prompt"] = True
        
        # 立即创建任务记录 (状态为 running)
        task = Task(
            task_id=task_id,
            account_id=account.id,
            task_type="image",
            status="running",  # 任务正在处理中
            generation_type=generation_type,
            params=json.dumps(params_to_store, ensure_ascii=False),
            image_count=request.max_images if request.sequential_image_generation == "auto" else 1,
        )
        
        db.add(task)
        await db.commit()
        await db.refresh(task)
        
        logger.info(f"创建图片任务: {task_id}")
        
        # 启动后台线程处理
        start_background_task(task_id, api_request, api_key, account.id)
        
        created_tasks.append(ImageTaskResponse(
            id=task.id,
            task_id=task.task_id,
            account_id=task.account_id,
            account_name=account_name,
            task_type=task.task_type,
            status=task.status,
            generation_type=task.generation_type,
            result_urls=task.result_urls,
            image_count=task.image_count,
            token_usage=task.token_usage,
            error_message=task.error_message,
            created_at=task.created_at.isoformat() if task.created_at else None,
            updated_at=task.updated_at.isoformat() if task.updated_at else None,
        ))
    
    # 清理使用完毕的临时上传文件
    for file_id in uploaded_file_ids:
        try:
            delete_file_by_id(file_id)
        except:
            pass  # 忽略清理错误
    
    return created_tasks


@router.get("", response_model=ImageListResponse)
async def list_image_tasks(
    account_id: Optional[int] = Query(None, description="按账户筛选"),
    status: Optional[str] = Query(None, description="按状态筛选"),
    limit: int = Query(50, le=100),
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """列出图片生成任务"""
    query = select(Task).options(selectinload(Task.account)).where(
        Task.task_type == "image"
    ).order_by(desc(Task.created_at))
    
    if account_id is not None:
        query = query.where(Task.account_id == account_id)
    if status is not None:
        query = query.where(Task.status == status)
    
    query = query.limit(limit)
    
    result = await db.execute(query)
    tasks = result.scalars().all()
    
    return ImageListResponse(
        ok=True,
        tasks=[ImageTaskResponse(
            id=t.id,
            task_id=t.task_id,
            account_id=t.account_id,
            account_name=t.account.name if t.account else None,
            task_type=t.task_type,
            status=t.status,
            generation_type=t.generation_type,
            result_urls=t.result_urls,
            image_count=t.image_count,
            token_usage=t.token_usage,
            error_message=t.error_message,
            created_at=t.created_at.isoformat() if t.created_at else None,
            updated_at=t.updated_at.isoformat() if t.updated_at else None,
        ) for t in tasks],
        total=len(tasks)
    )


@router.get("/{task_id}", response_model=ImageTaskResponse)
async def get_image_task(
    task_id: str,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """获取图片任务详情"""
    result = await db.execute(
        select(Task).options(selectinload(Task.account)).where(Task.task_id == task_id)
    )
    task = result.scalar_one_or_none()
    
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    if task.task_type != "image":
        raise HTTPException(status_code=400, detail="该任务不是图片任务")
    
    return ImageTaskResponse(
        id=task.id,
        task_id=task.task_id,
        account_id=task.account_id,
        account_name=task.account.name if task.account else None,
        task_type=task.task_type,
        status=task.status,
        generation_type=task.generation_type,
        result_urls=task.result_urls,
        image_count=task.image_count,
        token_usage=task.token_usage,
        error_message=task.error_message,
        created_at=task.created_at.isoformat() if task.created_at else None,
        updated_at=task.updated_at.isoformat() if task.updated_at else None,
    )


@router.get("/file/{task_id}/{filename}")
async def get_volcano_ref_image_file(
    task_id: str,
    filename: str
):
    """获取火山参考图片文件 (无需认证)"""
    settings = get_settings()
    
    # 安全检查
    if ".." in task_id or ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="非法路径")
    
    filepath = os.path.join(settings.volcano_ref_images_dir, task_id, filename)
    
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="图片不存在")
    
    return FileResponse(filepath, media_type="image/png")

async def delete_image_task(
    task_id: str,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """删除图片任务"""
    settings = get_settings()
    
    result = await db.execute(
        select(Task).where(Task.task_id == task_id)
    )
    task = result.scalar_one_or_none()
    
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    # 删除本地参考图片目录
    task_dir = os.path.join(settings.volcano_ref_images_dir, task_id)
    if os.path.exists(task_dir):
        shutil.rmtree(task_dir)
        logger.info(f"已删除参考图片目录: {task_dir}")
    
    await db.delete(task)
    await db.commit()
    
    return {"ok": True, "message": "任务已删除"}


@router.get("/storage/info")
async def get_volcano_storage(
    user: dict = Depends(get_current_user)
):
    """获取火山图片参考图存储空间占用"""
    settings = get_settings()
    
    size_bytes, file_count = get_storage_size(settings.volcano_ref_images_dir)
    
    return {
        "size_bytes": size_bytes,
        "size_display": format_size(size_bytes),
        "file_count": file_count
    }


@router.post("/storage/cleanup")
async def cleanup_volcano_storage(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """清理所有火山图片参考图存储"""
    settings = get_settings()
    
    # 获取清理前的大小
    size_before, count_before = get_storage_size(settings.volcano_ref_images_dir)
    
    # 删除整个目录并重建
    if os.path.exists(settings.volcano_ref_images_dir):
        shutil.rmtree(settings.volcano_ref_images_dir)
        Path(settings.volcano_ref_images_dir).mkdir(parents=True, exist_ok=True)
    
    # 更新数据库中的任务，清空 ref_image_paths
    result = await db.execute(
        select(Task).where(Task.task_type == "image")
    )
    tasks = result.scalars().all()
    
    for task in tasks:
        if task.params:
            try:
                params = json.loads(task.params)
                if "ref_image_paths" in params:
                    params["ref_image_paths"] = []
                    task.params = json.dumps(params, ensure_ascii=False)
            except:
                pass
    
    await db.commit()
    
    logger.info(f"已清理火山参考图存储: {count_before} 个文件, {format_size(size_before)}")
    
    return {
        "ok": True,
        "message": f"已清理 {count_before} 个文件，释放 {format_size(size_before)} 空间"
    }

