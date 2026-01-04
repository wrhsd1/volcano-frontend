"""
数据库模型和连接管理
使用 SQLAlchemy + SQLite
"""

from datetime import datetime, date
from typing import Optional
from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, Date, ForeignKey, Text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase, relationship

from .config import get_settings


class Base(DeclarativeBase):
    """SQLAlchemy 基类"""
    pass


class Account(Base):
    """账户配置表"""
    __tablename__ = "accounts"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    
    # 视频生成端点ID (如 ep-20251229122405-zxz8f)
    video_model_id = Column(String(200), nullable=True)
    # 图片生成端点ID (如 ep-20251229122405-abc12)
    image_model_id = Column(String(200), nullable=True)
    
    # Banana (Gemini) API 配置
    banana_base_url = Column(String(500), nullable=True)  # 如 https://generativelanguage.googleapis.com
    banana_api_key = Column(String(500), nullable=True)
    banana_model_name = Column(String(100), nullable=True, default="gemini-3-pro-image-preview")
    
    api_key = Column(String(500), nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # 关联
    tasks = relationship("Task", back_populates="account", cascade="all, delete-orphan")
    daily_usages = relationship("DailyUsage", back_populates="account", cascade="all, delete-orphan")
    
    def to_dict(self, include_sensitive: bool = False):
        """转换为字典"""
        result = {
            "id": self.id,
            "name": self.name,
            "video_model_id": self.video_model_id,
            "image_model_id": self.image_model_id,
            "banana_base_url": self.banana_base_url,
            "banana_model_name": self.banana_model_name,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
        if include_sensitive:
            result["api_key"] = self.api_key
            result["banana_api_key"] = self.banana_api_key
        return result


class DailyUsage(Base):
    """每日使用记录表"""
    __tablename__ = "daily_usages"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    usage_date = Column(Date, nullable=False)  # 北京时间日期
    used_tokens = Column(Integer, default=0)   # 视频 token 使用量
    used_images = Column(Integer, default=0)   # 图片生成数量
    
    # 关联
    account = relationship("Account", back_populates="daily_usages")
    
    def to_dict(self):
        return {
            "id": self.id,
            "account_id": self.account_id,
            "usage_date": self.usage_date.isoformat() if self.usage_date else None,
            "used_tokens": self.used_tokens,
            "used_images": self.used_images,
        }


class Task(Base):
    """任务表"""
    __tablename__ = "tasks"
    
    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String(100), unique=True, nullable=False)  # 火山返回的 cgt-xxx 或自定义 img-xxx
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=False)
    
    # 任务类型: video / image
    task_type = Column(String(20), default="video")
    
    # 状态: queued/running/succeeded/failed/cancelled/expired
    status = Column(String(20), default="queued")
    
    # 生成类型: 
    # 视频: text_to_video / first_frame / first_last_frame
    # 图片: text_to_image / image_to_image / multi_image
    generation_type = Column(String(30), nullable=True)
    
    # 参数 (JSON)
    params = Column(Text, nullable=True)
    
    # 视频结果
    result_url = Column(String(1000), nullable=True)
    last_frame_url = Column(String(1000), nullable=True)
    
    # 图片结果 (JSON数组，存储多张图片的URL)
    result_urls = Column(Text, nullable=True)
    
    # 图片生成数量
    image_count = Column(Integer, nullable=True)
    
    token_usage = Column(Integer, nullable=True)
    error_message = Column(Text, nullable=True)
    
    # Banana 多轮对话历史 (JSON数组)
    conversation_history = Column(Text, nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    
    # 关联
    account = relationship("Account", back_populates="tasks")
    
    def to_dict(self):
        """转换为字典"""
        return {
            "id": self.id,
            "task_id": self.task_id,
            "account_id": self.account_id,
            "account_name": self.account.name if self.account else None,
            "task_type": self.task_type,
            "status": self.status,
            "generation_type": self.generation_type,
            "params": self.params,
            "result_url": self.result_url,
            "last_frame_url": self.last_frame_url,
            "result_urls": self.result_urls,
            "image_count": self.image_count,
            "token_usage": self.token_usage,
            "error_message": self.error_message,
            "conversation_history": self.conversation_history,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


# 数据库引擎和会话
_engine = None
_async_session = None


async def init_db():
    """初始化数据库"""
    global _engine, _async_session
    
    settings = get_settings()
    settings.ensure_data_dir()
    
    _engine = create_async_engine(
        settings.database_url,
        echo=settings.debug,
    )
    
    _async_session = async_sessionmaker(
        _engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    
    # 创建表
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    return _engine


async def get_db() -> AsyncSession:
    """获取数据库会话 (FastAPI 依赖)"""
    global _async_session
    if _async_session is None:
        await init_db()
    
    async with _async_session() as session:
        yield session


async def close_db():
    """关闭数据库连接"""
    global _engine
    if _engine:
        await _engine.dispose()
