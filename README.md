# 火山视频生成前端

基于火山方舟 Seedance 1.5 Pro API 的视频生成前端应用。

## 功能特性

- 多账户管理
- 支持文生视频、首帧图生视频、首尾帧图生视频
- Token 消耗和价格实时预估
- 任务队列管理

## 安装

```bash
pip install -r requirements.txt
```

## 配置

1. 复制 `.env.example` 为 `.env`
2. 修改 `ACCESS_PASSWORD` 为您的访问密码
3. 修改 `JWT_SECRET` 为随机字符串

## 运行

```bash
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

访问 http://localhost:8000

## 添加账户

1. 登录后进入设置页面
2. 点击"添加账户"
3. 填写名称、Model ID (如 ep-xxx) 和 API Key
