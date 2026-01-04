"""
火山视频生成前端 - FastAPI 应用入口
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from .database import init_db, close_db
from .routers import auth, accounts, tasks, images, banana_images


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时初始化数据库
    await init_db()
    yield
    # 关闭时清理
    await close_db()


app = FastAPI(
    title="火山视频/图片生成前端",
    description="基于火山方舟 Seedance 1.5 Pro / Seedream 4.5 API 的视频和图片生成前端",
    version="2.0.0",
    lifespan=lifespan
)

# 注册路由
app.include_router(auth.router)
app.include_router(accounts.router)
app.include_router(tasks.router)
app.include_router(images.router)
app.include_router(banana_images.router)

# 静态文件
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def index():
    """返回主页"""
    return FileResponse("static/index.html")


@app.get("/api/health")
async def health():
    """健康检查"""
    return {"ok": True, "status": "healthy"}
