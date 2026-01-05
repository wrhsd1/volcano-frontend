"""
文件上传 API 路由
支持图片上传、进度跟踪、临时文件管理
"""

import os
import uuid
import base64
import asyncio
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends, UploadFile, File, BackgroundTasks
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..auth import get_current_user
from ..config import get_settings

router = APIRouter(prefix="/api/upload", tags=["文件上传"])


class UploadResponse(BaseModel):
    """上传响应"""
    ok: bool
    file_id: str
    filename: str
    size: int


class FileInfoResponse(BaseModel):
    """文件信息响应"""
    file_id: str
    filename: str
    size: int
    created_at: str
    base64_data: Optional[str] = None  # 可选的 base64 数据


# ======================== 辅助函数 ========================

def get_file_path(file_id: str) -> str:
    """获取文件完整路径"""
    settings = get_settings()
    return os.path.join(settings.temp_uploads_dir, file_id)


def get_file_meta_path(file_id: str) -> str:
    """获取文件元数据路径"""
    return get_file_path(file_id) + ".meta"


def save_file_meta(file_id: str, filename: str, size: int):
    """保存文件元数据"""
    meta_path = get_file_meta_path(file_id)
    with open(meta_path, 'w', encoding='utf-8') as f:
        f.write(f"{filename}\n{size}\n{datetime.utcnow().isoformat()}")


def read_file_meta(file_id: str) -> tuple:
    """读取文件元数据"""
    meta_path = get_file_meta_path(file_id)
    if not os.path.exists(meta_path):
        return None, None, None
    with open(meta_path, 'r', encoding='utf-8') as f:
        lines = f.read().strip().split('\n')
        if len(lines) >= 3:
            return lines[0], int(lines[1]), lines[2]
        return None, None, None


def file_to_base64(file_path: str) -> str:
    """将文件转换为 base64 数据 URL"""
    with open(file_path, 'rb') as f:
        data = f.read()
    
    # 检测 MIME 类型
    mime_type = "image/png"
    if data[:3] == b'\xff\xd8\xff':
        mime_type = "image/jpeg"
    elif data[:4] == b'\x89PNG':
        mime_type = "image/png"
    elif data[:6] in (b'GIF87a', b'GIF89a'):
        mime_type = "image/gif"
    elif data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        mime_type = "image/webp"
    
    b64 = base64.b64encode(data).decode('utf-8')
    return f"data:{mime_type};base64,{b64}"


async def cleanup_old_files():
    """清理超过 24 小时的临时文件"""
    settings = get_settings()
    upload_dir = settings.temp_uploads_dir
    
    if not os.path.exists(upload_dir):
        return
    
    cutoff_time = datetime.utcnow() - timedelta(hours=24)
    
    for filename in os.listdir(upload_dir):
        if filename.endswith('.meta'):
            continue
        
        file_path = os.path.join(upload_dir, filename)
        meta_path = file_path + ".meta"
        
        # 读取元数据获取创建时间
        if os.path.exists(meta_path):
            _, _, created_str = read_file_meta(filename)
            if created_str:
                try:
                    created_at = datetime.fromisoformat(created_str)
                    if created_at < cutoff_time:
                        # 删除文件和元数据
                        if os.path.exists(file_path):
                            os.remove(file_path)
                        if os.path.exists(meta_path):
                            os.remove(meta_path)
                except:
                    pass
        else:
            # 没有元数据的文件，使用文件修改时间
            try:
                mtime = datetime.fromtimestamp(os.path.getmtime(file_path))
                if mtime < cutoff_time:
                    os.remove(file_path)
            except:
                pass


# ======================== API 端点 ========================

@router.post("", response_model=UploadResponse)
async def upload_file(
    file: UploadFile = File(...),
    background_tasks: BackgroundTasks = None,
    user: dict = Depends(get_current_user)
):
    """
    上传文件
    
    使用 multipart/form-data 上传，支持大文件流式接收
    返回 file_id 用于后续引用
    """
    settings = get_settings()
    settings.ensure_temp_uploads_dir()
    
    # 验证文件类型
    content_type = file.content_type or ""
    if not content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="仅支持图片文件")
    
    # 生成唯一文件ID
    file_ext = Path(file.filename).suffix if file.filename else ".png"
    file_id = f"upload-{uuid.uuid4().hex[:16]}{file_ext}"
    
    file_path = get_file_path(file_id)
    
    # 流式写入文件
    total_size = 0
    try:
        with open(file_path, 'wb') as f:
            while chunk := await file.read(1024 * 1024):  # 每次读取 1MB
                f.write(chunk)
                total_size += len(chunk)
    except Exception as e:
        # 清理失败的文件
        if os.path.exists(file_path):
            os.remove(file_path)
        raise HTTPException(status_code=500, detail=f"文件保存失败: {str(e)}")
    
    # 保存元数据
    save_file_meta(file_id, file.filename or "unknown", total_size)
    
    # 后台清理旧文件
    if background_tasks:
        background_tasks.add_task(cleanup_old_files)
    
    return UploadResponse(
        ok=True,
        file_id=file_id,
        filename=file.filename or "unknown",
        size=total_size
    )


@router.get("/{file_id}", response_model=FileInfoResponse)
async def get_file_info(
    file_id: str,
    include_base64: bool = False,
    user: dict = Depends(get_current_user)
):
    """获取文件信息，可选返回 base64 数据"""
    # 安全检查
    if ".." in file_id or "/" in file_id or "\\" in file_id:
        raise HTTPException(status_code=400, detail="非法文件ID")
    
    file_path = get_file_path(file_id)
    
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="文件不存在")
    
    filename, size, created_at = read_file_meta(file_id)
    
    response = FileInfoResponse(
        file_id=file_id,
        filename=filename or file_id,
        size=size or os.path.getsize(file_path),
        created_at=created_at or datetime.utcnow().isoformat()
    )
    
    if include_base64:
        response.base64_data = file_to_base64(file_path)
    
    return response


@router.get("/{file_id}/preview")
async def get_file_preview(file_id: str):
    """
    获取文件预览（无需认证，供 img 标签使用）
    """
    # 安全检查
    if ".." in file_id or "/" in file_id or "\\" in file_id:
        raise HTTPException(status_code=400, detail="非法文件ID")
    
    file_path = get_file_path(file_id)
    
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="文件不存在")
    
    # 确定媒体类型
    ext = Path(file_id).suffix.lower()
    media_types = {
        '.jpg': 'image/jpeg',
        '.jpeg': 'image/jpeg',
        '.png': 'image/png',
        '.gif': 'image/gif',
        '.webp': 'image/webp'
    }
    media_type = media_types.get(ext, 'image/png')
    
    return FileResponse(file_path, media_type=media_type)


@router.delete("/{file_id}")
async def delete_file(
    file_id: str,
    user: dict = Depends(get_current_user)
):
    """删除已上传的文件"""
    # 安全检查
    if ".." in file_id or "/" in file_id or "\\" in file_id:
        raise HTTPException(status_code=400, detail="非法文件ID")
    
    file_path = get_file_path(file_id)
    meta_path = get_file_meta_path(file_id)
    
    deleted = False
    
    if os.path.exists(file_path):
        os.remove(file_path)
        deleted = True
    
    if os.path.exists(meta_path):
        os.remove(meta_path)
    
    if not deleted:
        raise HTTPException(status_code=404, detail="文件不存在")
    
    return {"ok": True, "message": "文件已删除"}


# ======================== 工具函数（供其他模块调用）========================

def get_base64_from_file_id(file_id: str) -> str:
    """
    从 file_id 获取 base64 数据
    如果成功返回 base64 数据，失败返回 None
    """
    file_path = get_file_path(file_id)
    if os.path.exists(file_path):
        return file_to_base64(file_path)
    return None


def delete_file_by_id(file_id: str):
    """
    删除文件（任务创建成功后调用）
    """
    file_path = get_file_path(file_id)
    meta_path = get_file_meta_path(file_id)
    
    if os.path.exists(file_path):
        os.remove(file_path)
    if os.path.exists(meta_path):
        os.remove(meta_path)
