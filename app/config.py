"""
配置管理
"""

import os
from pathlib import Path
from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """应用配置"""
    
    # 数据目录
    data_dir: str = "./data"
    
    # 访问密码
    access_password: str = "changeme"
    
    # JWT 配置
    jwt_secret: str = "your-secret-key-change-this"
    jwt_algorithm: str = "HS256"
    jwt_expire_hours: int = 24 * 7  # 7天
    
    # 调试模式
    debug: bool = False
    
    # 每日 Token 额度
    daily_token_limit: int = 1_800_000
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
    
    @property
    def database_url(self) -> str:
        """SQLite 数据库 URL"""
        return f"sqlite+aiosqlite:///{self.data_dir}/volcano.db"
    
    def ensure_data_dir(self):
        """确保数据目录存在"""
        Path(self.data_dir).mkdir(parents=True, exist_ok=True)


@lru_cache()
def get_settings() -> Settings:
    """获取配置单例"""
    return Settings()
