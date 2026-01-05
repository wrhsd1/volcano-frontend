"""
任务管理 API 路由
"""

import json
import os
import base64
import uuid
import shutil
from pathlib import Path
from datetime import datetime
from typing import Optional, List
from fastapi import APIRouter, HTTPException, Depends, Query
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from sqlalchemy.orm import selectinload
from pydantic import BaseModel
import httpx

from ..auth import get_current_user
from ..database import get_db, Task, Account
from .accounts import get_daily_usage, update_daily_usage
from .upload import get_base64_from_file_id, delete_file_by_id
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
    first_frame_file_id: Optional[str] = None  # 服务端预上传的文件ID
    last_frame_file_id: Optional[str] = None   # 服务端预上传的文件ID
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
    task_type: str  # video / image
    status: str
    generation_type: Optional[str]
    params: Optional[str]  # JSON string with request params
    result_url: Optional[str]
    last_frame_url: Optional[str]
    result_urls: Optional[str]  # JSON array for images
    image_count: Optional[int]
    token_usage: Optional[int]
    error_message: Optional[str]
    submitted_by: Optional[str]  # 提交者标识
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


def save_frame_image(base64_data: str, task_dir: str, filename: str) -> str:
    """保存帧图片到本地，返回文件路径"""
    Path(task_dir).mkdir(parents=True, exist_ok=True)
    
    # 处理 data:image/xxx;base64, 前缀
    if base64_data.startswith("data:"):
        base64_start = base64_data.find(",") + 1
        img_base64 = base64_data[base64_start:]
    else:
        img_base64 = base64_data
    
    image_data = base64.b64decode(img_base64)
    filepath = os.path.join(task_dir, filename)
    
    with open(filepath, 'wb') as f:
        f.write(image_data)
    
    return filepath


def get_video_storage_size(path: str) -> tuple:
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
    
    if not account.video_model_id:
        raise HTTPException(status_code=400, detail="该账户未配置视频生成端点ID")
    
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
    # 如果传入了 file_id，需要转换为 base64
    first_frame_base64 = request.first_frame_base64
    last_frame_base64 = request.last_frame_base64
    uploaded_file_ids = []  # 记录使用的临时文件，任务成功后清理
    
    if request.first_frame_file_id:
        b64 = get_base64_from_file_id(request.first_frame_file_id)
        if b64:
            first_frame_base64 = b64
            uploaded_file_ids.append(request.first_frame_file_id)
        else:
            raise HTTPException(status_code=400, detail="首帧图片文件不存在或已过期")
    
    if request.last_frame_file_id:
        b64 = get_base64_from_file_id(request.last_frame_file_id)
        if b64:
            last_frame_base64 = b64
            uploaded_file_ids.append(request.last_frame_file_id)
        else:
            raise HTTPException(status_code=400, detail="尾帧图片文件不存在或已过期")
    
    has_first_frame = bool(first_frame_base64 or request.first_frame_url)
    has_last_frame = bool(last_frame_base64 or request.last_frame_url)
    
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
        # 生成本地任务ID用于保存帧图片
        local_task_id = f"vid-{uuid.uuid4().hex[:16]}"
        
        # 保存首帧/尾帧到本地（如果是base64）
        saved_frame_paths = {}
        if first_frame_base64:
            settings.ensure_volcano_video_frames_dir()
            task_dir = os.path.join(settings.volcano_video_frames_dir, local_task_id)
            first_path = save_frame_image(first_frame_base64, task_dir, "first_frame.png")
            saved_frame_paths["first_frame"] = first_path
        
        if last_frame_base64:
            settings.ensure_volcano_video_frames_dir()
            task_dir = os.path.join(settings.volcano_video_frames_dir, local_task_id)
            last_path = save_frame_image(last_frame_base64, task_dir, "last_frame.png")
            saved_frame_paths["last_frame"] = last_path
        
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
            first_url = request.first_frame_url or first_frame_base64
            img_obj = {
                "type": "image_url",
                "image_url": {"url": first_url}
            }
            if has_last_frame:
                img_obj["role"] = "first_frame"
            content.append(img_obj)
        
        # 添加尾帧图片
        if has_last_frame:
            last_url = request.last_frame_url or last_frame_base64
            content.append({
                "type": "image_url",
                "image_url": {"url": last_url},
                "role": "last_frame"
            })
        
        # 构建请求体
        api_request = {
            "model": account.video_model_id,
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
        
        # 如果有保存的帧图片，重命名目录到正式 task_id
        if saved_frame_paths:
            old_dir = os.path.join(settings.volcano_video_frames_dir, local_task_id)
            new_dir = os.path.join(settings.volcano_video_frames_dir, task_id)
            if os.path.exists(old_dir):
                os.rename(old_dir, new_dir)
                # 更新路径
                for key in saved_frame_paths:
                    saved_frame_paths[key] = saved_frame_paths[key].replace(local_task_id, task_id)
        
        # 为数据库存储创建不含 base64 的 params
        params_to_store = {
            "model": account.video_model_id,
            "generate_audio": request.generate_audio,
            "prompt": request.prompt,
            "ratio": request.ratio,
            "resolution": request.resolution,
            "duration": request.duration,
            "frame_paths": saved_frame_paths,  # 存储本地路径
            "first_frame_url": request.first_frame_url,  # URL 保留
            "last_frame_url": request.last_frame_url,
        }
        
        # 确定提交者标识
        submitted_by = "admin" if user.get("role") == "admin" else f"guest_{user.get('guest_id', '')}"
        
        # 保存任务到数据库
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
        await update_daily_usage(db, account.id, tokens_per_video)
        
        created_tasks.append(TaskResponse(**task.to_dict()))
    
    # 清理使用完毕的临时上传文件
    for file_id in uploaded_file_ids:
        try:
            delete_file_by_id(file_id)
        except:
            pass  # 忽略清理错误
    
    return created_tasks


@router.get("", response_model=TaskListResponse)
async def list_tasks(
    account_id: Optional[int] = Query(None, description="按账户筛选"),
    status: Optional[str] = Query(None, description="按状态筛选"),
    task_type: Optional[str] = Query(None, description="按任务类型筛选 (video/image)"),
    limit: int = Query(50, le=100),
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """列出任务（访客只能看到自己的任务）"""
    query = select(Task).options(selectinload(Task.account)).order_by(desc(Task.created_at))
    
    # 访客只能看到自己提交的任务
    if user.get("role") == "guest":
        guest_tag = f"guest_{user.get('guest_id', '')}"
        query = query.where(Task.submitted_by == guest_tag)
    
    if account_id is not None:
        query = query.where(Task.account_id == account_id)
    if status is not None:
        query = query.where(Task.status == status)
    if task_type is not None:
        query = query.where(Task.task_type == task_type)
    
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
    
    # 如果任务未完成，从火山 API 同步状态 (仅视频任务需要同步)
    if task.status in ["queued", "running"] and task.task_type == "video":
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
    
    # 仅视频任务需要同步
    if task.task_type == "video":
        await sync_task_status(task, db)
    
    return TaskResponse(**task.to_dict())


@router.delete("/{task_id}")
async def delete_task(
    task_id: str,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """删除任务（访客只能删除自己的任务）"""
    result = await db.execute(
        select(Task).options(selectinload(Task.account)).where(Task.task_id == task_id)
    )
    task = result.scalar_one_or_none()
    
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    
    # 访客只能删除自己的任务
    if user.get("role") == "guest":
        guest_tag = f"guest_{user.get('guest_id', '')}"
        if task.submitted_by != guest_tag:
            raise HTTPException(status_code=403, detail="无权删除此任务")
    
    # 本地删除
    await db.delete(task)
    await db.commit()
    
    return {"ok": True, "message": "任务已删除"}


# ======================== 辅助函数 ========================

async def sync_task_status(task: Task, db: AsyncSession):
    """从火山 API 同步任务状态 (仅用于视频任务)"""
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


@router.get("/video/frame/{task_id}/{filename}")
async def get_video_frame_file(
    task_id: str,
    filename: str
):
    """获取视频帧图片文件 (无需认证，供前端img标签使用)"""
    settings = get_settings()
    
    # 安全检查
    if ".." in task_id or ".." in filename or "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="非法路径")
    
    filepath = os.path.join(settings.volcano_video_frames_dir, task_id, filename)
    
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="图片不存在")
    
    return FileResponse(filepath, media_type="image/png")


@router.get("/video/storage/info")
async def get_video_storage(
    user: dict = Depends(get_current_user)
):
    """获取视频帧存储空间占用"""
    settings = get_settings()
    
    size_bytes, file_count = get_video_storage_size(settings.volcano_video_frames_dir)
    
    return {
        "size_bytes": size_bytes,
        "size_display": format_size(size_bytes),
        "file_count": file_count
    }


@router.post("/video/storage/cleanup")
async def cleanup_video_storage(
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """清理所有视频帧存储"""
    settings = get_settings()
    
    # 获取清理前的大小
    size_before, count_before = get_video_storage_size(settings.volcano_video_frames_dir)
    
    # 删除整个目录并重建
    if os.path.exists(settings.volcano_video_frames_dir):
        shutil.rmtree(settings.volcano_video_frames_dir)
        Path(settings.volcano_video_frames_dir).mkdir(parents=True, exist_ok=True)
    
    # 更新数据库中的任务，清空 frame_paths
    result = await db.execute(
        select(Task).where(Task.task_type == "video")
    )
    tasks = result.scalars().all()
    
    for task in tasks:
        if task.params:
            try:
                params = json.loads(task.params)
                if "frame_paths" in params:
                    params["frame_paths"] = {}
                    task.params = json.dumps(params, ensure_ascii=False)
            except:
                pass
    
    await db.commit()
    
    return {
        "ok": True,
        "message": f"已清理 {count_before} 个文件，释放 {format_size(size_before)} 空间"
    }

