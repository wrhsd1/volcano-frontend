"""
配置管理
"""

import os
from pathlib import Path
from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """应用配置"""
    
    model_config = {
        "extra": "ignore",  # 允许额外的环境变量（如 GUEST_PASSWORD1, GUEST_PASSWORD2 等）
        "env_file": ".env",
        "env_file_encoding": "utf-8",
    }
    
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
    
    # 每日 Token 额度 (视频生成)
    daily_token_limit: int = 1_800_000
    
    # 每日图片生成额度
    daily_image_limit: int = 20
    
    @property
    def database_url(self) -> str:
        """SQLite 数据库 URL"""
        return f"sqlite+aiosqlite:///{self.data_dir}/volcano.db"
    
    @property
    def banana_images_dir(self) -> str:
        """Banana 图片存储目录"""
        return f"{self.data_dir}/banana_images"
    
    @property
    def volcano_ref_images_dir(self) -> str:
        """火山图片参考图存储目录"""
        return f"{self.data_dir}/volcano_ref_images"
    
    def ensure_data_dir(self):
        """确保数据目录存在"""
        Path(self.data_dir).mkdir(parents=True, exist_ok=True)
    
    def ensure_banana_dir(self):
        """确保 Banana 图片目录存在"""
        Path(self.banana_images_dir).mkdir(parents=True, exist_ok=True)
    
    def ensure_volcano_ref_dir(self):
        """确保火山参考图片目录存在"""
        Path(self.volcano_ref_images_dir).mkdir(parents=True, exist_ok=True)
    
    @property
    def volcano_video_frames_dir(self) -> str:
        """火山视频首尾帧图片存储目录"""
        return f"{self.data_dir}/volcano_video_frames"
    
    def ensure_volcano_video_frames_dir(self):
        """确保火山视频帧目录存在"""
        Path(self.volcano_video_frames_dir).mkdir(parents=True, exist_ok=True)
    
    @property
    def temp_uploads_dir(self) -> str:
        """临时上传文件目录"""
        return f"{self.data_dir}/temp_uploads"
    
    def ensure_temp_uploads_dir(self):
        """确保临时上传目录存在"""
        Path(self.temp_uploads_dir).mkdir(parents=True, exist_ok=True)


    @property
    def guest_passwords(self) -> dict:
        """
        解析 GUEST_PASSWORD1, GUEST_PASSWORD2... 环境变量
        返回 {guest_id: password} 字典，例如 {"1": "pwd1", "2": "pwd2"}
        同时检查 os.environ 和 .env 文件
        """
        result = {}
        
        # 从 os.environ 读取
        for key, value in os.environ.items():
            if key.startswith("GUEST_PASSWORD") and value:
                suffix = key.replace("GUEST_PASSWORD", "")
                if suffix:
                    result[suffix] = value
        
        # 从 .env 文件读取（补充 os.environ 中没有的）
        env_path = Path(".env")
        if env_path.exists():
            try:
                with open(env_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            key, _, value = line.partition("=")
                            key = key.strip()
                            value = value.strip().strip('"').strip("'")
                            if key.startswith("GUEST_PASSWORD") and value:
                                suffix = key.replace("GUEST_PASSWORD", "")
                                if suffix and suffix not in result:
                                    result[suffix] = value
            except Exception:
                pass
        
        return result


@lru_cache()
def get_settings() -> Settings:
    """获取配置单例"""
    return Settings()
