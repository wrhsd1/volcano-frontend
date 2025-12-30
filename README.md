# 火山内容生成前端

基于火山方舟 API 的视频和图片生成前端应用。

- **视频生成**: Seedance 1.5 Pro
- **图片生成**: Seedream 4.5

## 功能特性

### 视频生成
- 支持文生视频、首帧图生视频、首尾帧图生视频
- Token 消耗和价格实时预估
- 多种分辨率 (480p/720p) 和比例选择

### 图片生成
- 支持纯文生图、单图参考、多图融合
- 支持组图模式 (一次生成多张关联图片)
- 支持 2K/4K 智能尺寸和自定义像素尺寸
- 生成张数 1-9 张

### 通用功能
- 多账户管理 (每账户可配置视频/图片端点)
- 任务队列管理 (视频和图片任务统一管理)
- 每日配额追踪 (视频Token / 图片张数)

## 安装

```bash
pip install -r requirements.txt
```

## 配置

1. 复制 `.env.example` 为 `.env`
2. 修改 `ACCESS_PASSWORD` 为您的访问密码
3. 修改 `JWT_SECRET` 为随机字符串

## 数据库迁移

如果您是从旧版本升级，请运行迁移脚本：

```bash
python migrate_db.py
```

这会将旧的 `model_id` 迁移为 `video_model_id`，并添加新的字段。

## 运行

```bash
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

访问 http://localhost:8000

## 添加账户

1. 登录后进入设置页面
2. 点击"添加账户"
3. 填写：
   - 名称
   - 视频端点ID (可选，Seedance 1.5 Pro，如 `ep-xxx`)
   - 图片端点ID (可选，Seedream 4.5，如 `ep-yyy`)
   - API Key

至少需要填写一个端点ID。

## 价格说明

- **视频生成**
  - 无声: ¥0.008/千tokens
  - 有声: ¥0.016/千tokens
  
- **图片生成**
  - ¥0.25/张

## 注意事项

- 图片生成的链接有效期为 24 小时，请及时下载保存
- 每账户每日默认限制：180万 tokens (视频) / 20 张 (图片)
- 可在 `app/config.py` 或 `.env` 中修改限制
