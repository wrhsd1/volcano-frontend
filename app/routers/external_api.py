"""
对外 API 路由
提供视频、图片、Banana 生图的外部 API 接口
支持 X-API-Key 认证和账户自动选择
"""

import json
import os
import base64
import uuid
from datetime import datetime
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from pydantic import BaseModel
import httpx

from ..api_auth import get_api_user
from ..account_selector import select_best_account, get_accounts_with_quota
from ..database import get_db, Task, Account
from ..config import get_settings

# 复用现有路由的功能
from .tasks import (
    calculate_tokens, 
    calculate_price, 
    save_frame_image,
    sync_task_status,
    VOLCANO_API_BASE,
    RESOLUTION_PIXELS,
)
from .images import (
    start_background_task as start_image_task,
    save_ref_image,
    SIZE_MAP_2K,
    SIZE_MAP_4K,
    VOLCANO_IMAGE_API,
)
from .banana_images import (
    start_banana_background_task,
    save_banana_ref_image,
)
from .accounts import (
    get_daily_usage, 
    update_daily_usage,
    get_daily_image_usage,
)
from .upload import get_base64_from_file_id, delete_file_by_id

router = APIRouter(prefix="/api/v1", tags=["外部API"])


# ======================== 请求/响应模型 ========================

class VideoGenerateRequest(BaseModel):
    """视频生成请求"""
    prompt: Optional[str] = None
    account_id: Optional[int] = None  # 可选，不指定则自动选择
    
    # 首尾帧图片 (Base64 或 URL)
    first_frame_base64: Optional[str] = None
    last_frame_base64: Optional[str] = None
    first_frame_url: Optional[str] = None
    last_frame_url: Optional[str] = None
    
    # 生成参数
    ratio: str = "16:9"
    resolution: str = "720p"
    duration: int = 5
    generate_audio: bool = True
    seed: int = -1
    watermark: bool = False
    camera_fixed: bool = False


class ImageGenerateRequest(BaseModel):
    """图片生成请求"""
    prompt: str
    account_id: Optional[int] = None  # 可选，不指定则自动选择
    
    # 参考图片 (Base64 或 URL 数组)
    images: Optional[List[str]] = None
    
    # 尺寸设置
    size: str = "2K"  # "2K" / "4K" / "2048x2048" 等
    ratio: str = "1:1"  # 比例 (当 size 为 2K/4K 时使用)
    
    # 生成数量
    count: int = 1  # 1-9
    
    # 组图设置
    sequential_image_generation: str = "disabled"  # "auto" / "disabled"
    max_images: int = 4  # 组图最大数量 1-15
    
    # 其他选项
    optimize_prompt: bool = True
    watermark: bool = False


class BananaGenerateRequest(BaseModel):
    """Banana 生图请求"""
    prompt: str
    account_id: Optional[int] = None  # 可选，不指定则自动选择
    
    # 参考图片 (Base64 数组)
    images: Optional[List[str]] = None
    
    # 生成参数
    aspect_ratio: str = "1:1"
    resolution: str = "1K"  # "1K" / "2K"


class BananaContinueRequest(BaseModel):
    """Banana 多轮对话请求"""
    prompt: str


class TaskStatusResponse(BaseModel):
    """任务状态响应"""
    ok: bool
    task_id: str
    task_type: str  # video / image / banana
    status: str  # queued / running / succeeded / failed
    account_id: int
    account_name: Optional[str]
    
    # 视频结果
    result_url: Optional[str] = None
    last_frame_url: Optional[str] = None
    
    # 图片结果
    result_urls: Optional[List[dict]] = None
    
    # 使用量
    token_usage: Optional[int] = None
    image_count: Optional[int] = None
    
    # 错误信息
    error_message: Optional[str] = None
    
    created_at: Optional[str]
    updated_at: Optional[str]


class AccountQuotaResponse(BaseModel):
    """账户额度响应"""
    ok: bool
    accounts: List[dict]


# ======================== 视频生成 API ========================

@router.post("/video/generate")
async def generate_video(
    request: VideoGenerateRequest,
    user: dict = Depends(get_api_user),
    db: AsyncSession = Depends(get_db)
):
    """
    创建视频生成任务
    
    - 支持文生视频、首帧图生视频、首尾帧图生视频
    - 不指定 account_id 时自动选择剩余额度最高的账户
    """
    settings = get_settings()
    
    # 选择账户
    account = await select_best_account(db, "video", request.account_id)
    
    # 检查剩余额度
    used = await get_daily_usage(db, account.id)
    tokens_needed = calculate_tokens(request.resolution, request.ratio, request.duration)
    remaining = settings.daily_token_limit - used
    
    if tokens_needed > remaining:
        raise HTTPException(
            status_code=400,
            detail=f"账户 '{account.name}' 额度不足，需要 {tokens_needed} tokens，剩余 {remaining} tokens"
        )
    
    # 确定生成类型
    has_first_frame = bool(request.first_frame_base64 or request.first_frame_url)
    has_last_frame = bool(request.last_frame_base64 or request.last_frame_url)
    
    if has_last_frame and not has_first_frame:
        raise HTTPException(status_code=400, detail="仅提供尾帧时必须同时提供首帧")
    
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
    
    # 生成任务ID
    local_task_id = f"vid-{uuid.uuid4().hex[:16]}"
    
    # 保存首帧/尾帧到本地
    saved_frame_paths = {}
    if request.first_frame_base64:
        settings.ensure_volcano_video_frames_dir()
        task_dir = os.path.join(settings.volcano_video_frames_dir, local_task_id)
        first_path = save_frame_image(request.first_frame_base64, task_dir, "first_frame.png")
        saved_frame_paths["first_frame"] = first_path
    
    if request.last_frame_base64:
        settings.ensure_volcano_video_frames_dir()
        task_dir = os.path.join(settings.volcano_video_frames_dir, local_task_id)
        last_path = save_frame_image(request.last_frame_base64, task_dir, "last_frame.png")
        saved_frame_paths["last_frame"] = last_path
    
    # 构建 content 数组
    content = []
    
    if prompt_with_params:
        content.append({
            "type": "text",
            "text": prompt_with_params
        })
    
    if has_first_frame:
        first_url = request.first_frame_url or request.first_frame_base64
        img_obj = {
            "type": "image_url",
            "image_url": {"url": first_url}
        }
        if has_last_frame:
            img_obj["role"] = "first_frame"
        content.append(img_obj)
    
    if has_last_frame:
        last_url = request.last_frame_url or request.last_frame_base64
        content.append({
            "type": "image_url",
            "image_url": {"url": last_url},
            "role": "last_frame"
        })
    
    # 调用火山 API
    api_request = {
        "model": account.video_model_id,
        "content": content,
        "generate_audio": request.generate_audio,
    }
    
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
    
    # 重命名本地目录到正式 task_id
    if saved_frame_paths:
        old_dir = os.path.join(settings.volcano_video_frames_dir, local_task_id)
        new_dir = os.path.join(settings.volcano_video_frames_dir, task_id)
        if os.path.exists(old_dir):
            os.rename(old_dir, new_dir)
            for key in saved_frame_paths:
                saved_frame_paths[key] = saved_frame_paths[key].replace(local_task_id, task_id)
    
    # 保存任务到数据库
    params_to_store = {
        "model": account.video_model_id,
        "generate_audio": request.generate_audio,
        "prompt": request.prompt,
        "ratio": request.ratio,
        "resolution": request.resolution,
        "duration": request.duration,
        "frame_paths": saved_frame_paths,
    }
    
    submitted_by = "admin" if user.get("role") == "admin" else f"guest_{user.get('guest_id', '')}"
    
    task = Task(
        task_id=task_id,
        account_id=account.id,
        task_type="video",
        status="queued",
        generation_type=generation_type,
        params=json.dumps(params_to_store, ensure_ascii=False),
        submitted_by=submitted_by,
    )
    
    db.add(task)
    await db.commit()
    await db.refresh(task)
    
    # 更新使用量
    await update_daily_usage(db, account.id, tokens_needed)
    
    return {
        "ok": True,
        "task_id": task_id,
        "account_id": account.id,
        "account_name": account.name,
        "status": "queued",
        "generation_type": generation_type,
        "estimated_tokens": tokens_needed
    }


@router.get("/video/{task_id}")
async def get_video_status(
    task_id: str,
    user: dict = Depends(get_api_user),
    db: AsyncSession = Depends(get_db)
):
    """获取视频任务状态"""
    result = await db.execute(
        select(Task).options(selectinload(Task.account)).where(Task.task_id == task_id)
    )
    task = result.scalar_one_or_none()
    
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    if task.task_type != "video":
        raise HTTPException(status_code=400, detail="该任务不是视频任务")
    
    # 同步状态
    if task.status in ["queued", "running"]:
        await sync_task_status(task, db)
    
    return TaskStatusResponse(
        ok=True,
        task_id=task.task_id,
        task_type=task.task_type,
        status=task.status,
        account_id=task.account_id,
        account_name=task.account.name if task.account else None,
        result_url=task.result_url,
        last_frame_url=task.last_frame_url,
        token_usage=task.token_usage,
        error_message=task.error_message,
        created_at=task.created_at.isoformat() if task.created_at else None,
        updated_at=task.updated_at.isoformat() if task.updated_at else None,
    )


# ======================== 图片生成 API ========================

@router.post("/image/generate")
async def generate_image(
    request: ImageGenerateRequest,
    user: dict = Depends(get_api_user),
    db: AsyncSession = Depends(get_db)
):
    """
    创建图片生成任务
    
    - 支持纯文生图、单图参考、多图融合
    - 不指定 account_id 时自动选择剩余额度最高的账户
    """
    settings = get_settings()
    
    # 选择账户
    account = await select_best_account(db, "image", request.account_id)
    
    # 检查剩余额度
    used = await get_daily_image_usage(db, account.id)
    
    if request.sequential_image_generation == "auto":
        estimated_count = request.max_images
    else:
        estimated_count = request.count
    
    remaining = settings.daily_image_limit - used
    
    if estimated_count > remaining:
        raise HTTPException(
            status_code=400,
            detail=f"账户 '{account.name}' 额度不足，需要 {estimated_count} 张，剩余 {remaining} 张"
        )
    
    # 处理参考图片
    final_images = list(request.images) if request.images else []
    
    # 确定生成类型
    has_images = len(final_images) > 0
    if has_images:
        generation_type = "multi_image" if len(final_images) > 1 else "image_to_image"
    else:
        generation_type = "text_to_image"
    
    # 验证参考图片数量
    if final_images and len(final_images) > 14:
        raise HTTPException(status_code=400, detail="参考图片最多14张")
    
    # 处理尺寸
    if request.size == "2K":
        size = SIZE_MAP_2K.get(request.ratio, "2048x2048")
    elif request.size == "4K":
        size = SIZE_MAP_4K.get(request.ratio, "4096x4096")
    else:
        size = request.size  # 直接使用像素值
    
    # 生成任务ID
    task_id = f"img-{uuid.uuid4().hex[:16]}"
    
    # 保存参考图片到本地
    saved_ref_paths = []
    if has_images:
        settings.ensure_volcano_ref_dir()
        task_dir = os.path.join(settings.volcano_ref_images_dir, task_id)
        for idx, img_data in enumerate(final_images):
            try:
                filepath = save_ref_image(img_data, task_dir, idx)
                saved_ref_paths.append(filepath)
            except Exception as e:
                pass  # 忽略保存错误
    
    # 构建 API 请求
    api_request = {
        "model": account.image_model_id,
        "prompt": request.prompt,
        "size": size,
        "watermark": request.watermark,
        "response_format": "url",
    }
    
    if request.optimize_prompt:
        api_request["optimize_prompt_options"] = {"mode": "standard"}
    
    if has_images:
        if len(final_images) == 1:
            api_request["image"] = final_images[0]
        else:
            api_request["image"] = final_images
    
    if request.sequential_image_generation == "auto":
        api_request["sequential_image_generation"] = "auto"
        api_request["sequential_image_generation_options"] = {
            "max_images": request.max_images
        }
    else:
        api_request["sequential_image_generation"] = "disabled"
    
    # 保存任务到数据库
    params_to_store = {
        "model": account.image_model_id,
        "prompt": request.prompt,
        "size": size,
        "watermark": request.watermark,
        "sequential_image_generation": request.sequential_image_generation,
        "ref_image_count": len(final_images),
        "ref_image_paths": saved_ref_paths,
    }
    
    submitted_by = "admin" if user.get("role") == "admin" else f"guest_{user.get('guest_id', '')}"
    
    task = Task(
        task_id=task_id,
        account_id=account.id,
        task_type="image",
        status="running",
        generation_type=generation_type,
        params=json.dumps(params_to_store, ensure_ascii=False),
        image_count=request.max_images if request.sequential_image_generation == "auto" else 1,
        submitted_by=submitted_by,
    )
    
    db.add(task)
    await db.commit()
    await db.refresh(task)
    
    # 启动后台任务处理
    start_image_task(task_id, api_request, account.api_key, account.id)
    
    return {
        "ok": True,
        "task_id": task_id,
        "account_id": account.id,
        "account_name": account.name,
        "status": "running",
        "generation_type": generation_type,
        "estimated_count": estimated_count
    }


@router.get("/image/{task_id}")
async def get_image_status(
    task_id: str,
    user: dict = Depends(get_api_user),
    db: AsyncSession = Depends(get_db)
):
    """获取图片任务状态"""
    result = await db.execute(
        select(Task).options(selectinload(Task.account)).where(Task.task_id == task_id)
    )
    task = result.scalar_one_or_none()
    
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    if task.task_type != "image":
        raise HTTPException(status_code=400, detail="该任务不是图片任务")
    
    # 解析结果 URLs
    result_urls = None
    if task.result_urls:
        try:
            result_urls = json.loads(task.result_urls)
        except:
            pass
    
    return TaskStatusResponse(
        ok=True,
        task_id=task.task_id,
        task_type=task.task_type,
        status=task.status,
        account_id=task.account_id,
        account_name=task.account.name if task.account else None,
        result_urls=result_urls,
        image_count=task.image_count,
        error_message=task.error_message,
        created_at=task.created_at.isoformat() if task.created_at else None,
        updated_at=task.updated_at.isoformat() if task.updated_at else None,
    )


# ======================== Banana 生图 API ========================

@router.post("/banana/generate")
async def generate_banana_image(
    request: BananaGenerateRequest,
    user: dict = Depends(get_api_user),
    db: AsyncSession = Depends(get_db)
):
    """
    创建 Banana (Gemini) 图片生成任务
    
    - 不指定 account_id 时自动选择有 Banana 配置的账户
    """
    settings = get_settings()
    
    # 选择账户
    account = await select_best_account(db, "banana", request.account_id)
    
    # 检查剩余额度
    used = await get_daily_image_usage(db, account.id)
    remaining = settings.daily_image_limit - used
    
    if remaining <= 0:
        raise HTTPException(
            status_code=400,
            detail=f"账户 '{account.name}' 今日图片额度已用尽"
        )
    
    # 处理参考图片
    final_images = list(request.images) if request.images else []
    
    # 确定生成类型
    has_images = len(final_images) > 0
    if has_images:
        if len(final_images) > 1:
            generation_type = "multi_image"
        else:
            generation_type = "image_to_image"
    else:
        generation_type = "text_to_image"
    
    # 生成任务ID
    task_id = f"banana-{uuid.uuid4().hex[:16]}"
    
    # 保存参考图片到本地
    saved_ref_paths = []
    if final_images:
        settings.ensure_banana_dir()
        task_dir = os.path.join(settings.banana_images_dir, task_id)
        for idx, img_data in enumerate(final_images):
            try:
                filepath = save_banana_ref_image(img_data, task_dir, idx)
                saved_ref_paths.append(filepath)
            except Exception as e:
                pass
    
    # 构建 Gemini API 请求
    parts = []
    
    # 添加参考图片
    for img_data in final_images:
        if img_data.startswith("data:"):
            base64_start = img_data.find(",") + 1
            img_base64 = img_data[base64_start:]
            mime_type = img_data[5:img_data.find(";")]
        else:
            img_base64 = img_data
            mime_type = "image/png"
        
        parts.append({
            "inline_data": {
                "mime_type": mime_type,
                "data": img_base64
            }
        })
    
    # 添加提示词
    parts.append({"text": request.prompt})
    
    # 确定尺寸
    aspect_ratio = request.aspect_ratio
    
    api_request = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "responseModalities": ["TEXT", "IMAGE"],
        }
    }
    

    # 保存任务到数据库
    params_to_store = {
        "prompt": request.prompt,
        "aspect_ratio": aspect_ratio,
        "resolution": request.resolution,
        "ref_image_count": len(final_images),
        "ref_image_paths": saved_ref_paths,
    }
    
    submitted_by = "admin" if user.get("role") == "admin" else f"guest_{user.get('guest_id', '')}"
    
    task = Task(
        task_id=task_id,
        account_id=account.id,
        task_type="banana_image",
        status="running",
        generation_type=generation_type,
        params=json.dumps(params_to_store, ensure_ascii=False),
        conversation_history=None,  # 不再保存对话历史
        submitted_by=submitted_by,
    )
    
    db.add(task)
    await db.commit()
    await db.refresh(task)
    
    # 启动后台任务处理
    start_banana_background_task(
        task_id,
        api_request,
        account.banana_base_url,
        account.banana_api_key,
        account.banana_model_name or "gemini-3-pro-image-preview",
        account.id
    )
    
    return {
        "ok": True,
        "task_id": task_id,
        "account_id": account.id,
        "account_name": account.name,
        "status": "running"
    }


@router.get("/banana/{task_id}")
async def get_banana_status(
    task_id: str,
    user: dict = Depends(get_api_user),
    db: AsyncSession = Depends(get_db)
):
    """获取 Banana 任务状态"""
    result = await db.execute(
        select(Task).options(selectinload(Task.account)).where(Task.task_id == task_id)
    )
    task = result.scalar_one_or_none()
    
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    if task.task_type != "banana_image":
        raise HTTPException(status_code=400, detail="该任务不是 Banana 任务")
    
    # 解析结果 URLs，将本地路径转换为可访问的 URL
    result_urls = None
    if task.result_urls:
        try:
            raw_results = json.loads(task.result_urls)
            result_urls = []
            for item in raw_results:
                if isinstance(item, dict) and "path" in item:
                    # 从路径中提取文件名
                    import os
                    filename = os.path.basename(item["path"])
                    # 构建可访问的 URL
                    url = f"/api/banana/images/file/{task.task_id}/{filename}"
                    result_urls.append({
                        "url": url,
                        "index": item.get("index", 0),
                        "local_path": item["path"]
                    })
                else:
                    result_urls.append(item)
        except:
            pass
    
    return TaskStatusResponse(
        ok=True,
        task_id=task.task_id,
        task_type=task.task_type,
        status=task.status,
        account_id=task.account_id,
        account_name=task.account.name if task.account else None,
        result_urls=result_urls,
        image_count=task.image_count,
        error_message=task.error_message,
        created_at=task.created_at.isoformat() if task.created_at else None,
        updated_at=task.updated_at.isoformat() if task.updated_at else None,
    )


@router.post("/banana/{task_id}/continue")
async def continue_banana_image(
    task_id: str,
    request: BananaContinueRequest,
    user: dict = Depends(get_api_user),
    db: AsyncSession = Depends(get_db)
):
    """
    Banana 多轮对话
    
    继续修改已生成的图片 - 从 result_urls/params 重构对话历史
    """
    settings = get_settings()
    
    # 获取任务
    result = await db.execute(
        select(Task).options(selectinload(Task.account)).where(Task.task_id == task_id)
    )
    original_task = result.scalar_one_or_none()
    
    if not original_task:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    if original_task.task_type != "banana_image":
        raise HTTPException(status_code=400, detail="该任务不是 Banana 任务")
    
    if original_task.status != "succeeded":
        raise HTTPException(status_code=400, detail="任务未完成，无法继续对话")
    
    account = original_task.account
    if not account or not account.banana_api_key:
        raise HTTPException(status_code=400, detail="账户 Banana 配置无效")
    
    # 从 result_urls 和 params 重构对话历史
    # 遍历任务链，收集所有历史对话
    task_chain = []
    current_task = original_task
    
    while current_task:
        task_chain.insert(0, current_task)  # 插入到开头，保持时间顺序
        
        # 检查是否有父任务
        try:
            params = json.loads(current_task.params or "{}")
            parent_task_id = params.get("parent_task_id")
            if parent_task_id:
                result = await db.execute(
                    select(Task).where(Task.task_id == parent_task_id)
                )
                current_task = result.scalar_one_or_none()
            else:
                current_task = None
        except:
            current_task = None
    
    # 构建 Gemini API 的 contents 数组
    contents = []
    
    for task_item in task_chain:
        try:
            params = json.loads(task_item.params or "{}")
            prompt = params.get("prompt", "")
            ref_image_paths = params.get("ref_image_paths", [])
            
            # 用户消息: 提示词 + 参考图
            user_parts = []
            if prompt:
                user_parts.append({"text": prompt})
            
            # 添加参考图 (如果有)
            for ref_path in ref_image_paths:
                if os.path.exists(ref_path):
                    with open(ref_path, "rb") as f:
                        img_data = base64.b64encode(f.read()).decode()
                    user_parts.append({
                        "inlineData": {
                            "mimeType": "image/png",
                            "data": img_data
                        }
                    })
            
            if user_parts:
                contents.append({"role": "user", "parts": user_parts})
            
            # 模型响应: 生成的图片
            result_urls = json.loads(task_item.result_urls or "[]")
            model_parts = []
            
            for result_item in result_urls:
                result_path = result_item.get("path", "")
                if result_path and os.path.exists(result_path):
                    with open(result_path, "rb") as f:
                        img_data = base64.b64encode(f.read()).decode()
                    model_parts.append({
                        "inlineData": {
                            "mimeType": "image/png",
                            "data": img_data
                        }
                    })
            
            if model_parts:
                contents.append({"role": "model", "parts": model_parts})
                
        except Exception as e:
            continue
    
    # 添加新的用户请求
    contents.append({
        "role": "user",
        "parts": [{"text": request.prompt}]
    })
    
    # 构建 API 请求
    api_request = {
        "contents": contents,
        "generationConfig": {
            "responseModalities": ["TEXT", "IMAGE"],
        }
    }
    
    # 创建新任务记录
    new_task_id = f"banana-{uuid.uuid4().hex[:16]}"
    
    submitted_by = original_task.submitted_by or ("admin" if user.get("role") == "admin" else f"guest_{user.get('guest_id', '')}")
    
    new_task = Task(
        task_id=new_task_id,
        account_id=account.id,
        task_type="banana_image",
        status="running",
        generation_type="continue",
        params=json.dumps({
            "prompt": request.prompt,
            "parent_task_id": task_id
        }, ensure_ascii=False),
        conversation_history=None,
        submitted_by=submitted_by,
    )
    
    db.add(new_task)
    await db.commit()
    await db.refresh(new_task)
    
    # 启动后台任务
    start_banana_background_task(
        new_task_id,
        api_request,
        account.banana_base_url,
        account.banana_api_key,
        account.banana_model_name or "gemini-3-pro-image-preview",
        account.id
    )
    
    return {
        "ok": True,
        "task_id": new_task_id,
        "status": "running",
        "message": "继续对话已提交"
    }



# ======================== 账户查询 API ========================

@router.get("/accounts")
async def list_accounts_with_quota(
    user: dict = Depends(get_api_user),
    db: AsyncSession = Depends(get_db)
):
    """获取可用账户及剩余额度"""
    accounts = await get_accounts_with_quota(db)
    
    return AccountQuotaResponse(
        ok=True,
        accounts=accounts
    )
