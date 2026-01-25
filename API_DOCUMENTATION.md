# 火山前端对外 API 文档

## 概述

本 API 提供视频生成、图片生成和 Banana (Gemini) 生图功能的外部访问接口。

**Base URL**: `http://your-server:8000/api/v1`

## 认证

所有 API 请求需要携带 API Key。支持两种方式:

### 方式一: X-API-Key Header (推荐)

```
X-API-Key: your-password
```

### 方式二: Authorization Header

```
Authorization: Bearer your-password
```

**API Key 说明**:
- 使用 `.env` 中配置的 `ACCESS_PASSWORD` 作为管理员 API Key
- 使用 `GUEST_PASSWORD1`, `GUEST_PASSWORD2` 等作为访客 API Key

---

## 账户管理

### 获取可用账户及额度

获取所有可用账户及其剩余额度信息。

```
GET /api/v1/accounts
```

**响应示例**:

```json
{
  "ok": true,
  "accounts": [
    {
      "id": 1,
      "name": "主账户",
      "has_video": true,
      "has_image": true,
      "has_banana": true,
      "video_daily_limit": 1800000,
      "video_used_tokens": 500000,
      "video_remaining_tokens": 1300000,
      "image_daily_limit": 20,
      "image_used": 5,
      "image_remaining": 15
    }
  ]
}
```

---

## 视频生成

### 创建视频生成任务

支持文生视频、首帧图生视频、首尾帧图生视频三种模式。

```
POST /api/v1/video/generate
```

**请求参数**:

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `prompt` | string | 条件必填 | - | 提示词 (文生视频模式必填) |
| `account_id` | int | 否 | null | 账户ID，不指定则自动选择剩余额度最高的账户 |
| `first_frame_base64` | string | 否 | null | 首帧图片 (Base64 或 data URL) |
| `last_frame_base64` | string | 否 | null | 尾帧图片 (Base64 或 data URL) |
| `first_frame_url` | string | 否 | null | 首帧图片 URL |
| `last_frame_url` | string | 否 | null | 尾帧图片 URL |
| `ratio` | string | 否 | "16:9" | 画面比例: 16:9, 4:3, 1:1, 3:4, 9:16, 21:9 |
| `resolution` | string | 否 | "720p" | 分辨率: 480p, 720p |
| `duration` | int | 否 | 5 | 时长: 5 或 10 秒 |
| `generate_audio` | bool | 否 | true | 是否生成音频 |
| `seed` | int | 否 | -1 | 随机种子 (-1 为随机) |
| `watermark` | bool | 否 | false | 是否添加水印 |
| `camera_fixed` | bool | 否 | false | 是否固定镜头 |

**请求示例**:

```bash
curl -X POST "http://localhost:8000/api/v1/video/generate" \
  -H "X-API-Key: your-password" \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "一只猫在草地上奔跑",
    "ratio": "16:9",
    "resolution": "720p",
    "duration": 5,
    "generate_audio": true
  }'
```

**响应示例**:

```json
{
  "ok": true,
  "task_id": "cgt-xxxxxxxxxxxx",
  "account_id": 1,
  "account_name": "主账户",
  "status": "queued",
  "generation_type": "text_to_video",
  "estimated_tokens": 207360
}
```

### 查询视频任务状态

```
GET /api/v1/video/{task_id}
```

**响应示例**:

```json
{
  "ok": true,
  "task_id": "cgt-xxxxxxxxxxxx",
  "task_type": "video",
  "status": "succeeded",
  "account_id": 1,
  "account_name": "主账户",
  "result_url": "https://xxx.volces.com/video.mp4",
  "last_frame_url": "https://xxx.volces.com/last_frame.png",
  "token_usage": 207360,
  "created_at": "2024-01-15T10:30:00",
  "updated_at": "2024-01-15T10:35:00"
}
```

**任务状态说明**:

| 状态 | 说明 |
|------|------|
| `queued` | 排队中 |
| `running` | 生成中 |
| `succeeded` | 生成成功 |
| `failed` | 生成失败 |

---

## 图片生成

### 创建图片生成任务

支持纯文生图、单图参考、多图融合三种模式。

```
POST /api/v1/image/generate
```

**请求参数**:

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `prompt` | string | 是 | - | 提示词 |
| `account_id` | int | 否 | null | 账户ID，不指定则自动选择 |
| `images` | array | 否 | null | 参考图片数组 (Base64 或 URL)，最多14张 |
| `size` | string | 否 | "2K" | 尺寸: 2K, 4K, 或具体像素如 "2048x2048" |
| `ratio` | string | 否 | "1:1" | 比例 (当 size 为 2K/4K 时生效) |
| `count` | int | 否 | 1 | 生成数量 (1-9，非组图模式) |
| `sequential_image_generation` | string | 否 | "disabled" | 组图模式: "auto" / "disabled" |
| `max_images` | int | 否 | 4 | 组图最大数量 (1-15，组图模式生效) |
| `optimize_prompt` | bool | 否 | true | 是否优化提示词 |
| `watermark` | bool | 否 | false | 是否添加水印 |

**请求示例**:

```bash
curl -X POST "http://localhost:8000/api/v1/image/generate" \
  -H "X-API-Key: your-password" \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "一只可爱的猫咪",
    "size": "2K",
    "ratio": "1:1",
    "count": 1
  }'
```

**响应示例**:

```json
{
  "ok": true,
  "task_id": "img-xxxxxxxxxxxx",
  "account_id": 1,
  "account_name": "主账户",
  "status": "running",
  "generation_type": "text_to_image",
  "estimated_count": 1
}
```

### 查询图片任务状态

```
GET /api/v1/image/{task_id}
```

**响应示例**:

```json
{
  "ok": true,
  "task_id": "img-xxxxxxxxxxxx",
  "task_type": "image",
  "status": "succeeded",
  "account_id": 1,
  "account_name": "主账户",
  "result_urls": [
    {
      "url": "https://xxx.volces.com/image1.png",
      "size": "2048x2048"
    }
  ],
  "image_count": 1,
  "created_at": "2024-01-15T10:30:00",
  "updated_at": "2024-01-15T10:31:00"
}
```

---

## Banana 生图 (Gemini)

### 创建 Banana 生图任务

使用 Gemini 模型生成图片，支持多轮对话修改。

```
POST /api/v1/banana/generate
```

**请求参数**:

| 参数 | 类型 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `prompt` | string | 是 | - | 提示词 |
| `account_id` | int | 否 | null | 账户ID，不指定则自动选择 |
| `images` | array | 否 | null | 参考图片数组 (Base64) |
| `aspect_ratio` | string | 否 | "1:1" | 画面比例 |
| `resolution` | string | 否 | "1K" | 分辨率: 1K, 2K |

**请求示例**:

```bash
curl -X POST "http://localhost:8000/api/v1/banana/generate" \
  -H "X-API-Key: your-password" \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "一只穿着西装的猫咪",
    "aspect_ratio": "1:1"
  }'
```

**响应示例**:

```json
{
  "ok": true,
  "task_id": "banana-xxxxxxxxxxxx",
  "account_id": 1,
  "account_name": "主账户",
  "status": "running"
}
```

### 查询 Banana 任务状态

```
GET /api/v1/banana/{task_id}
```

**响应示例**:

```json
{
  "ok": true,
  "task_id": "banana-xxxxxxxxxxxx",
  "task_type": "banana_image",
  "status": "succeeded",
  "account_id": 1,
  "account_name": "banana1",
  "result_urls": [
    {
      "url": "/api/banana/images/file/banana-xxxxxxxxxxxx/image_0_123456.png",
      "index": 0,
      "local_path": "D:\\data\\banana\\banana-xxxxxxxxxxxx\\image_0_123456.png"
    }
  ],
  "image_count": 1,
  "created_at": "2024-01-15T10:30:00",
  "updated_at": "2024-01-15T10:31:00"
}
```

**注意**: `result_urls` 中的 `url` 是相对路径，需要拼接服务器地址使用，例如:
```
http://localhost:8000/api/banana/images/file/banana-xxxxxxxxxxxx/image_0_123456.png
```

### Banana 多轮对话

继续修改已生成的图片。

```
POST /api/v1/banana/{task_id}/continue
```

**请求参数**:

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `prompt` | string | 是 | 修改指令 |

**请求示例**:

```bash
curl -X POST "http://localhost:8000/api/v1/banana/banana-xxxx/continue" \
  -H "X-API-Key: your-password" \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "把猫咪的领带改成红色"
  }'
```

---

## 错误响应

所有 API 在出错时返回统一的错误格式:

```json
{
  "detail": "错误信息描述"
}
```

**常见错误码**:

| HTTP 状态码 | 说明 |
|-------------|------|
| 400 | 请求参数错误或业务错误 (如额度不足) |
| 401 | 认证失败 (API Key 无效) |
| 404 | 资源不存在 (如任务不存在) |
| 500 | 服务器内部错误 |

---

## 使用示例

### Python

```python
import requests

API_BASE = "http://localhost:8000/api/v1"
API_KEY = "your-password"

headers = {
    "X-API-Key": API_KEY,
    "Content-Type": "application/json"
}

# 创建视频任务
response = requests.post(
    f"{API_BASE}/video/generate",
    headers=headers,
    json={
        "prompt": "一只猫在草地上奔跑",
        "ratio": "16:9",
        "resolution": "720p",
        "duration": 5
    }
)

result = response.json()
task_id = result["task_id"]
print(f"任务已创建: {task_id}")

# 轮询任务状态
import time
while True:
    status_response = requests.get(
        f"{API_BASE}/video/{task_id}",
        headers=headers
    )
    status = status_response.json()
    
    if status["status"] == "succeeded":
        print(f"视频生成成功: {status['result_url']}")
        break
    elif status["status"] == "failed":
        print(f"视频生成失败: {status['error_message']}")
        break
    else:
        print(f"状态: {status['status']}")
        time.sleep(5)
```

### JavaScript

```javascript
const API_BASE = "http://localhost:8000/api/v1";
const API_KEY = "your-password";

async function generateVideo() {
  const response = await fetch(`${API_BASE}/video/generate`, {
    method: "POST",
    headers: {
      "X-API-Key": API_KEY,
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      prompt: "一只猫在草地上奔跑",
      ratio: "16:9",
      resolution: "720p",
      duration: 5
    })
  });
  
  const result = await response.json();
  console.log("任务已创建:", result.task_id);
  
  // 轮询任务状态
  while (true) {
    const statusResponse = await fetch(
      `${API_BASE}/video/${result.task_id}`,
      { headers: { "X-API-Key": API_KEY } }
    );
    const status = await statusResponse.json();
    
    if (status.status === "succeeded") {
      console.log("视频生成成功:", status.result_url);
      break;
    } else if (status.status === "failed") {
      console.log("视频生成失败:", status.error_message);
      break;
    }
    
    await new Promise(r => setTimeout(r, 5000));
  }
}

generateVideo();
```

---

## Token 消耗计算

视频生成的 Token 消耗公式:

```
tokens = width × height × fps × duration / 1024
```

其中 fps 固定为 24。

**分辨率像素值参考**:

| 分辨率 | 16:9 | 1:1 | 9:16 |
|--------|------|-----|------|
| 480p | 864×496 | 640×640 | 496×864 |
| 720p | 1280×720 | 960×960 | 720×1280 |

**价格**:
- 有声视频: ¥0.016/千tokens
- 无声视频: ¥0.008/千tokens
- 图片: ¥0.25/张
