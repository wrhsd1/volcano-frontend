"""
Banana (Gemini) 图片生成 API 路由
使用 Gemini 3 Pro Image Preview 模型
支持多轮对话修改，本地图片存储
"""

import json
import uuid
import asyncio
import threading
import base64
import os
import shutil
from datetime import datetime, timedelta, timezone
from typing import Optional, List
from pathlib import Path
from fastapi import APIRouter, HTTPException, Depends, Query
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
from .upload import get_base64_from_file_id, delete_file_by_id
from ..config import get_settings

router = APIRouter(prefix="/api/banana", tags=["Banana生图"])

# 日志
logger = logging.getLogger(__name__)

# 北京时区
BEIJING_TZ = timezone(timedelta(hours=8))


# ======================== 请求/响应模型 ========================

class BananaImageCreateRequest(BaseModel):
    """创建图片生成任务请求"""
    account_id: int
    prompt: str  # 提示词，必填
    
    # 参考图片 (可选，最多14张，base64格式)
    images: Optional[List[str]] = None
    file_ids: Optional[List[str]] = None  # 服务端预上传的文件ID数组
    
    # 尺寸设置
    aspect_ratio: str = "1:1"  # "1:1","2:3","3:2","3:4","4:3","4:5","5:4","9:16","16:9","21:9"
    resolution: str = "1K"  # "1K", "2K", "4K"


class BananaContinueRequest(BaseModel):
    """多轮修改请求"""
    prompt: str  # 修改指令


class BananaTaskResponse(BaseModel):
    """Banana任务响应"""
    id: int
    task_id: str
    account_id: int
    account_name: Optional[str]
    task_type: str
    status: str
    generation_type: Optional[str]
    result_urls: Optional[str]  # JSON array of local file paths
    image_count: Optional[int]
    error_message: Optional[str]
    conversation_history: Optional[str]  # JSON array
    created_at: Optional[str]
    updated_at: Optional[str]


class BananaListResponse(BaseModel):
    """任务列表响应"""
    ok: bool
    tasks: List[BananaTaskResponse]
    total: int


class BananaUsageResponse(BaseModel):
    """用量响应"""
    model_name: str
    images_last_5h: int
    total_requests: int


class BananaStorageResponse(BaseModel):
    """存储空间响应"""
    size_bytes: int
    size_display: str
    file_count: int


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


def save_base64_image(base64_data: str, task_dir: str, index: int) -> str:
    """保存 base64 图片到本地，返回文件路径"""
    # 创建任务目录
    Path(task_dir).mkdir(parents=True, exist_ok=True)
    
    # 解码 base64
    image_data = base64.b64decode(base64_data)
    
    # 生成文件名
    filename = f"image_{index}_{datetime.now().strftime('%H%M%S')}.png"
    filepath = os.path.join(task_dir, filename)
    
    # 写入文件
    with open(filepath, 'wb') as f:
        f.write(image_data)
    
    return filepath


def save_banana_ref_image(base64_data: str, task_dir: str, index: int) -> str:
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


# ======================== 后台任务处理 ========================

def run_async_task(coro):
    """在新的事件循环中运行异步任务"""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(coro)
    finally:
        loop.close()


def start_banana_background_task(task_id: str, api_request: dict, base_url: str, api_key: str, model_name: str, account_id: int, conversation_history: list):
    """启动后台线程执行图片生成"""
    thread = threading.Thread(
        target=run_async_task,
        args=(process_banana_task(task_id, api_request, base_url, api_key, model_name, account_id, conversation_history),)
    )
    thread.daemon = True
    thread.start()


async def process_banana_task(task_id: str, api_request: dict, base_url: str, api_key: str, model_name: str, account_id: int, conversation_history: list):
    """后台处理 Banana 图片生成任务"""
    settings = get_settings()
    
    logger.info(f"[Banana任务 {task_id}] 开始处理...")
    
    # 创建独立的数据库会话
    engine = create_async_engine(settings.database_url, echo=False)
    async_session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    
    async with async_session_maker() as db:
        try:
            # 调用 Gemini API
            api_url = f"{base_url}/v1beta/models/{model_name}:generateContent"
            
            logger.info(f"[Banana任务 {task_id}] 调用 Gemini API: {api_url}")
            
            async with httpx.AsyncClient(timeout=180.0) as client:
                resp = await client.post(
                    api_url,
                    json=api_request,
                    headers={
                        "Content-Type": "application/json",
                        "x-goog-api-key": api_key
                    }
                )
                
                if resp.status_code != 200:
                    error_detail = resp.text
                    try:
                        error_json = resp.json()
                        if "error" in error_json:
                            error_detail = error_json["error"].get("message", resp.text)
                    except:
                        pass
                    
                    logger.error(f"[Banana任务 {task_id}] API错误: {error_detail}")
                    
                    # 更新任务状态为失败
                    result = await db.execute(select(Task).where(Task.task_id == task_id))
                    task = result.scalar_one_or_none()
                    if task:
                        task.status = "failed"
                        task.error_message = f"Gemini API错误: {error_detail}"
                        task.updated_at = datetime.utcnow()
                        await db.commit()
                    return
                
                data = resp.json()
            
            logger.info(f"[Banana任务 {task_id}] API返回成功，解析结果...")
            
            # 解析响应 - 提取图片
            result_paths = []
            image_count = 0
            task_dir = os.path.join(settings.banana_images_dir, task_id)
            
            candidates = data.get("candidates", [])
            if candidates:
                content = candidates[0].get("content", {})
                parts = content.get("parts", [])
                
                # 更新对话历史
                model_response_parts = []
                
                for i, part in enumerate(parts):
                    if "inlineData" in part:
                        inline_data = part["inlineData"]
                        if inline_data.get("mimeType", "").startswith("image/"):
                            # 保存图片
                            image_base64 = inline_data.get("data", "")
                            if image_base64:
                                filepath = save_base64_image(image_base64, task_dir, image_count)
                                result_paths.append({"path": filepath, "index": image_count})
                                image_count += 1
                                model_response_parts.append({"type": "image", "path": filepath})
                    elif "text" in part:
                        text = part.get("text", "")
                        if text and not part.get("thought"):  # 排除思考过程
                            model_response_parts.append({"type": "text", "content": text})
                
                # 添加模型响应到对话历史
                if model_response_parts:
                    conversation_history.append({
                        "role": "model",
                        "parts": model_response_parts
                    })
            
            # 更新任务状态为成功
            result = await db.execute(select(Task).where(Task.task_id == task_id))
            task = result.scalar_one_or_none()
            if task:
                task.status = "succeeded"
                task.result_urls = json.dumps(result_paths, ensure_ascii=False)
                task.image_count = image_count
                task.conversation_history = json.dumps(conversation_history, ensure_ascii=False)
                task.updated_at = datetime.utcnow()
                await db.commit()
                
                logger.info(f"[Banana任务 {task_id}] 完成，生成了 {image_count} 张图片")
            else:
                logger.error(f"[Banana任务 {task_id}] 未找到任务记录")
                
        except httpx.RequestError as e:
            logger.error(f"[Banana任务 {task_id}] 网络错误: {str(e)}")
            result = await db.execute(select(Task).where(Task.task_id == task_id))
            task = result.scalar_one_or_none()
            if task:
                task.status = "failed"
                task.error_message = f"请求Gemini API失败: {str(e)}"
                task.updated_at = datetime.utcnow()
                await db.commit()
        except Exception as e:
            logger.error(f"[Banana任务 {task_id}] 处理异常: {str(e)}")
            result = await db.execute(select(Task).where(Task.task_id == task_id))
            task = result.scalar_one_or_none()
            if task:
                task.status = "failed"
                task.error_message = f"处理失败: {str(e)}"
                task.updated_at = datetime.utcnow()
                await db.commit()
    
    await engine.dispose()


# ======================== API 端点 ========================

@router.post("/images", response_model=BananaTaskResponse)
async def create_banana_image(
    request: BananaImageCreateRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """创建 Banana 图片生成任务"""
    settings = get_settings()
    settings.ensure_banana_dir()
    
    # 获取账户
    result = await db.execute(select(Account).where(Account.id == request.account_id))
    account = result.scalar_one_or_none()
    
    if not account:
        raise HTTPException(status_code=404, detail="账户不存在")
    
    if not account.is_active:
        raise HTTPException(status_code=400, detail="账户已禁用")
    
    if not account.banana_base_url or not account.banana_api_key:
        raise HTTPException(status_code=400, detail="该账户未配置 Banana API")
    
    # 验证参考图片数量
    if request.images and len(request.images) > 14:
        raise HTTPException(status_code=400, detail="参考图片最多14张")
    
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
            generation_type = "multi_image"
        else:
            generation_type = "image_to_image"
    else:
        generation_type = "text_to_image"
    
    # 构建 Gemini API 请求
    parts = [{"text": request.prompt}]
    
    # 添加参考图片
    if final_images:
        for img_data in final_images:
            # 处理 base64 数据
            if img_data.startswith("data:"):
                # 移除 data:image/xxx;base64, 前缀
                base64_start = img_data.find(",") + 1
                img_base64 = img_data[base64_start:]
                mime_type = img_data[5:img_data.find(";")]
            else:
                img_base64 = img_data
                mime_type = "image/png"
            
            parts.append({
                "inlineData": {
                    "mimeType": mime_type,
                    "data": img_base64
                }
            })
    
    api_request = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "responseModalities": ["TEXT", "IMAGE"],
            "imageConfig": {
                "aspectRatio": request.aspect_ratio,
                "imageSize": request.resolution
            }
        }
    }
    
    # 初始化对话历史
    conversation_history = [{
        "role": "user",
        "parts": [{"type": "text", "content": request.prompt}]
    }]
    
    # 如果有参考图片，也记录到历史
    if final_images:
        conversation_history[0]["parts"].append({
            "type": "images",
            "count": len(final_images)
        })
    
    # 生成本地任务ID
    task_id = f"banana-{uuid.uuid4().hex[:16]}"
    
    # 保存参考图片到本地 (新增)
    saved_ref_paths = []
    if final_images:
        task_dir = os.path.join(settings.banana_images_dir, task_id)
        for idx, img_data in enumerate(final_images):
            try:
                filepath = save_banana_ref_image(img_data, task_dir, idx)
                saved_ref_paths.append(filepath)
            except Exception as e:
                logger.warning(f"保存Banana参考图片失败: {e}")
    
    # 确定提交者标识
    submitted_by = "admin" if user.get("role") == "admin" else f"guest_{user.get('guest_id', '')}"
    
    # 创建任务记录
    task = Task(
        task_id=task_id,
        account_id=account.id,
        task_type="banana_image",
        status="running",
        generation_type=generation_type,
        params=json.dumps({
            "prompt": request.prompt,
            "aspect_ratio": request.aspect_ratio,
            "resolution": request.resolution,
            "image_count": len(final_images),
            "ref_image_paths": saved_ref_paths  # 新增: 保存参考图路径
        }, ensure_ascii=False),
        conversation_history=json.dumps(conversation_history, ensure_ascii=False),
        submitted_by=submitted_by,
    )
    
    db.add(task)
    await db.commit()
    await db.refresh(task)
    
    logger.info(f"创建 Banana 任务: {task_id}")
    
    # 启动后台处理
    model_name = account.banana_model_name or "gemini-3-pro-image-preview"
    start_banana_background_task(
        task_id, api_request, 
        account.banana_base_url, account.banana_api_key, model_name,
        account.id, conversation_history
    )
    
    # 清理使用完毕的临时上传文件
    for file_id in uploaded_file_ids:
        try:
            delete_file_by_id(file_id)
        except:
            pass  # 忽略清理错误
    
    return BananaTaskResponse(
        id=task.id,
        task_id=task.task_id,
        account_id=task.account_id,
        account_name=account.name,
        task_type=task.task_type,
        status=task.status,
        generation_type=task.generation_type,
        result_urls=task.result_urls,
        image_count=task.image_count,
        error_message=task.error_message,
        conversation_history=task.conversation_history,
        created_at=task.created_at.isoformat() if task.created_at else None,
        updated_at=task.updated_at.isoformat() if task.updated_at else None,
    )


@router.post("/images/{task_id}/continue", response_model=BananaTaskResponse)
async def continue_banana_image(
    task_id: str,
    request: BananaContinueRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """多轮修改 Banana 图片"""
    settings = get_settings()
    
    # 获取原任务
    result = await db.execute(
        select(Task).options(selectinload(Task.account)).where(Task.task_id == task_id)
    )
    original_task = result.scalar_one_or_none()
    
    if not original_task:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    if original_task.task_type != "banana_image":
        raise HTTPException(status_code=400, detail="该任务不是 Banana 图片任务")
    
    if original_task.status != "succeeded":
        raise HTTPException(status_code=400, detail="只能对已完成的任务进行修改")
    
    account = original_task.account
    if not account or not account.banana_base_url or not account.banana_api_key:
        raise HTTPException(status_code=400, detail="账户 Banana API 配置无效")
    
    # 解析对话历史
    conversation_history = json.loads(original_task.conversation_history or "[]")
    
    # 构建多轮请求 - 包含完整对话历史
    contents = []
    
    for turn in conversation_history:
        role = turn.get("role", "user")
        parts = []
        
        for part in turn.get("parts", []):
            if part.get("type") == "text":
                parts.append({"text": part.get("content", "")})
            elif part.get("type") == "image":
                # 读取本地图片并转为 base64
                image_path = part.get("path", "")
                if os.path.exists(image_path):
                    with open(image_path, "rb") as f:
                        img_data = base64.b64encode(f.read()).decode()
                    parts.append({
                        "inlineData": {
                            "mimeType": "image/png",
                            "data": img_data
                        }
                    })
        
        if parts:
            contents.append({"role": role, "parts": parts})
    
    # 添加新的用户请求
    contents.append({
        "role": "user",
        "parts": [{"text": request.prompt}]
    })
    
    # 更新对话历史
    conversation_history.append({
        "role": "user",
        "parts": [{"type": "text", "content": request.prompt}]
    })
    
    api_request = {
        "contents": contents,
        "generationConfig": {
            "responseModalities": ["TEXT", "IMAGE"]
        }
    }
    
    # 创建新任务记录
    new_task_id = f"banana-{uuid.uuid4().hex[:16]}"
    
    # 确定提交者标识 (继承原任务的提交者，或使用当前用户)
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
        conversation_history=json.dumps(conversation_history, ensure_ascii=False),
        submitted_by=submitted_by,
    )
    
    db.add(new_task)
    await db.commit()
    await db.refresh(new_task)
    
    logger.info(f"创建 Banana 多轮修改任务: {new_task_id} (基于 {task_id})")
    
    # 启动后台处理
    model_name = account.banana_model_name or "gemini-3-pro-image-preview"
    start_banana_background_task(
        new_task_id, api_request,
        account.banana_base_url, account.banana_api_key, model_name,
        account.id, conversation_history
    )
    
    return BananaTaskResponse(
        id=new_task.id,
        task_id=new_task.task_id,
        account_id=new_task.account_id,
        account_name=account.name,
        task_type=new_task.task_type,
        status=new_task.status,
        generation_type=new_task.generation_type,
        result_urls=new_task.result_urls,
        image_count=new_task.image_count,
        error_message=new_task.error_message,
        conversation_history=new_task.conversation_history,
        created_at=new_task.created_at.isoformat() if new_task.created_at else None,
        updated_at=new_task.updated_at.isoformat() if new_task.updated_at else None,
    )


@router.get("/images", response_model=BananaListResponse)
async def list_banana_images(
    account_id: Optional[int] = Query(None, description="按账户筛选"),
    status: Optional[str] = Query(None, description="按状态筛选"),
    limit: int = Query(50, le=100),
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """列出 Banana 图片任务"""
    query = select(Task).options(selectinload(Task.account)).where(
        Task.task_type == "banana_image"
    ).order_by(desc(Task.created_at))
    
    if account_id is not None:
        query = query.where(Task.account_id == account_id)
    if status is not None:
        query = query.where(Task.status == status)
    
    query = query.limit(limit)
    
    result = await db.execute(query)
    tasks = result.scalars().all()
    
    return BananaListResponse(
        ok=True,
        tasks=[BananaTaskResponse(
            id=t.id,
            task_id=t.task_id,
            account_id=t.account_id,
            account_name=t.account.name if t.account else None,
            task_type=t.task_type,
            status=t.status,
            generation_type=t.generation_type,
            result_urls=t.result_urls,
            image_count=t.image_count,
            error_message=t.error_message,
            conversation_history=t.conversation_history,
            created_at=t.created_at.isoformat() if t.created_at else None,
            updated_at=t.updated_at.isoformat() if t.updated_at else None,
        ) for t in tasks],
        total=len(tasks)
    )


@router.get("/images/{task_id}", response_model=BananaTaskResponse)
async def get_banana_image(
    task_id: str,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """获取 Banana 任务详情"""
    result = await db.execute(
        select(Task).options(selectinload(Task.account)).where(Task.task_id == task_id)
    )
    task = result.scalar_one_or_none()
    
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    if task.task_type != "banana_image":
        raise HTTPException(status_code=400, detail="该任务不是 Banana 图片任务")
    
    return BananaTaskResponse(
        id=task.id,
        task_id=task.task_id,
        account_id=task.account_id,
        account_name=task.account.name if task.account else None,
        task_type=task.task_type,
        status=task.status,
        generation_type=task.generation_type,
        result_urls=task.result_urls,
        image_count=task.image_count,
        error_message=task.error_message,
        conversation_history=task.conversation_history,
        created_at=task.created_at.isoformat() if task.created_at else None,
        updated_at=task.updated_at.isoformat() if task.updated_at else None,
    )


@router.delete("/images/{task_id}")
async def delete_banana_image(
    task_id: str,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """删除 Banana 任务及其本地图片"""
    settings = get_settings()
    
    result = await db.execute(
        select(Task).where(Task.task_id == task_id)
    )
    task = result.scalar_one_or_none()
    
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    # 删除本地图片目录
    task_dir = os.path.join(settings.banana_images_dir, task_id)
    if os.path.exists(task_dir):
        shutil.rmtree(task_dir)
        logger.info(f"已删除 Banana 图片目录: {task_dir}")
    
    await db.delete(task)
    await db.commit()
    
    return {"ok": True, "message": "任务及图片已删除"}


@router.get("/images/file/{task_id}/{filename}")
async def get_banana_image_file(
    task_id: str,
    filename: str
):
    """获取本地图片文件 (无需认证，因为浏览器img/a标签无法发送auth header)"""
    settings = get_settings()
    
    # 安全检查：防止路径遍历攻击
    if ".." in task_id or ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="非法路径")
    
    filepath = os.path.join(settings.banana_images_dir, task_id, filename)
    
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="图片不存在")
    
    return FileResponse(filepath, media_type="image/png")


@router.get("/usage", response_model=BananaUsageResponse)
async def get_banana_usage(
    account_id: int = Query(..., description="账户ID"),
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """查询最近 5 小时内的生成数量"""
    # 获取账户
    result = await db.execute(select(Account).where(Account.id == account_id))
    account = result.scalar_one_or_none()
    
    if not account:
        raise HTTPException(status_code=404, detail="账户不存在")
    
    if not account.banana_base_url or not account.banana_api_key:
        raise HTTPException(status_code=400, detail="该账户未配置 Banana API")
    
    model_name = account.banana_model_name or "gemini-3-pro-image-preview"
    
    # 调用后端状态接口
    try:
        usage_url = f"{account.banana_base_url}/v0/management/usage"
        
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                usage_url,
                headers={
                    "Accept": "application/json",
                    "Authorization": f"Bearer {account.banana_api_key}"
                }
            )
            
            if resp.status_code != 200:
                # 如果后端不支持用量查询，返回本地统计
                logger.warning(f"用量查询失败: {resp.status_code}")
                
                # 从本地数据库统计
                five_hours_ago = datetime.utcnow() - timedelta(hours=5)
                local_result = await db.execute(
                    select(Task).where(
                        Task.account_id == account_id,
                        Task.task_type == "banana_image",
                        Task.status == "succeeded",
                        Task.created_at >= five_hours_ago
                    )
                )
                local_tasks = local_result.scalars().all()
                local_count = sum(t.image_count or 0 for t in local_tasks)
                
                return BananaUsageResponse(
                    model_name=model_name,
                    images_last_5h=local_count,
                    total_requests=len(local_tasks)
                )
            
            data = resp.json()
            
            # 解析响应，查找指定模型的用量
            images_last_5h = 0
            total_requests = 0
            
            usage = data.get("usage", {})
            apis = usage.get("apis", {})
            
            for api_id, api_data in apis.items():
                models = api_data.get("models", {})
                if model_name in models:
                    model_data = models[model_name]
                    total_requests = model_data.get("total_requests", 0)
                    
                    # 统计最近5小时的请求
                    details = model_data.get("details", [])
                    five_hours_ago = datetime.now(BEIJING_TZ) - timedelta(hours=5)
                    
                    for detail in details:
                        timestamp_str = detail.get("timestamp", "")
                        if timestamp_str:
                            try:
                                # 解析时间戳
                                ts = datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
                                if ts >= five_hours_ago and not detail.get("failed", False):
                                    images_last_5h += 1
                            except:
                                pass
            
            return BananaUsageResponse(
                model_name=model_name,
                images_last_5h=images_last_5h,
                total_requests=total_requests
            )
            
    except Exception as e:
        logger.error(f"查询用量失败: {e}")
        raise HTTPException(status_code=500, detail=f"查询用量失败: {str(e)}")


@router.get("/storage", response_model=BananaStorageResponse)
async def get_banana_storage(
    user: dict = Depends(get_current_user)
):
    """获取 Banana 图片存储空间占用"""
    settings = get_settings()
    
    size_bytes, file_count = get_storage_size(settings.banana_images_dir)
    
    return BananaStorageResponse(
        size_bytes=size_bytes,
        size_display=format_size(size_bytes),
        file_count=file_count
    )


@router.post("/storage/cleanup")
async def cleanup_banana_storage(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """清理所有 Banana 图片存储"""
    settings = get_settings()
    
    # 获取清理前的大小
    size_before, count_before = get_storage_size(settings.banana_images_dir)
    
    # 删除所有 Banana 任务的本地图片
    if os.path.exists(settings.banana_images_dir):
        shutil.rmtree(settings.banana_images_dir)
        Path(settings.banana_images_dir).mkdir(parents=True, exist_ok=True)
    
    # 更新数据库中的任务，清空 result_urls
    result = await db.execute(
        select(Task).where(Task.task_type == "banana_image")
    )
    tasks = result.scalars().all()
    
    for task in tasks:
        task.result_urls = None
    
    await db.commit()
    
    logger.info(f"已清理 Banana 存储: {count_before} 个文件, {format_size(size_before)}")
    
    return {
        "ok": True,
        "message": f"已清理 {count_before} 个文件，释放 {format_size(size_before)} 空间"
    }
