# AI 国际象棋复盘工具 MVP

这是一个前后端分离的网页 MVP。用户在网页粘贴 PGN 并选择局面；FastAPI 后端调用 Stockfish 18 分析，再由后端调用 DeepSeek 生成中文解释。DeepSeek API Key 只保存在服务器环境变量中，不会发送给浏览器。

## 当前能力

- 粘贴并解析 PGN
- 浏览棋局中的任意一步
- 服务端 Stockfish 18 分析当前局面
- 返回统一白方视角的评估、前三条候选着法和变化线
- 服务端 DeepSeek 中文解释
- DeepSeek 不可用时降级展示 Stockfish 结果
- 响应式网页展示

首版只分析当前选中的局面，暂不包含整盘失误扫描、账户、数据库或商业化功能。

## 项目结构

```text
app/                 FastAPI 后端
  api.py             API 路由
  engine.py          Stockfish 18 服务
  ai_explainer.py    DeepSeek 服务
  config.py          环境配置
  models.py          请求和响应模型
docs/                GitHub Pages 前端发布目录
tests/               后端自动化测试
index.html           前端开发入口
requirements.txt     Python 运行依赖
.env.example         环境变量示例
```

## 本地启动

需要 Python 3.11+，并确保项目根目录存在可运行的 `stockfish.exe`；Linux 部署时将 `STOCKFISH_PATH` 指向对应的 Linux Stockfish 二进制。

1. 创建虚拟环境并安装依赖：

   ```powershell
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```

2. 复制环境配置：

   ```powershell
   Copy-Item .env.example .env
   ```

3. 在 `.env` 中填写一个新生成的 `DEEPSEEK_API_KEY`。不要继续使用曾经出现在项目文件中的旧 Key。

4. 启动后端：

   ```powershell
   uvicorn app.api:app --host 127.0.0.1 --port 8000 --reload
   ```

5. 另开终端启动前端：

   ```powershell
   py -m http.server 8080
   ```

6. 访问 `http://localhost:8080`。API 文档位于 `http://localhost:8000/docs`。

## API

### 健康检查

```http
GET /api/health
```

### 分析当前局面

```http
POST /api/review
Content-Type: application/json

{
  "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
}
```

成功响应包含 `position`、`engine`、`explanation` 和可选的 `warning`。如果 DeepSeek 不可用，接口仍返回 Stockfish 结果，并通过 `warning` 说明降级原因。

## 测试

```powershell
pip install -r requirements-dev.txt
pytest -q
```

## 部署

- 前端可继续从 `docs/` 发布到 GitHub Pages。
- 后端使用根目录的 `Dockerfile` 部署到 Render，镜像会下载官方 Linux x64 Stockfish 18。
- `render.yaml` 已定义免费 Web Service、健康检查和非敏感环境变量。
- 在 Render 创建 Blueprint 时必须手动填写新的 `DEEPSEEK_API_KEY`；该值不会写入仓库。
- 发布前在 `docs/runtime-config.js` 中填写后端 HTTPS 地址，例如：

  ```javascript
  window.CHESS_API_BASE_URL = "https://api.example.com";
  ```

- 后端 `.env` 的 `ALLOWED_ORIGINS` 必须填写实际前端来源。
- 不要把 `.env`、DeepSeek Key 或本地 Stockfish 二进制提交到仓库。

### Render 部署步骤

1. 把本项目的运行文件推送到 GitHub。
2. 在 Render Dashboard 选择 **New > Blueprint**。
3. 连接仓库 `saltymelon666/-chess-analyzer`；Render 会读取 `render.yaml`。
4. 为 `DEEPSEEK_API_KEY` 填写新生成的服务端密钥。
5. 创建服务并等待 `/api/health` 通过。
6. 将 Render 提供的 `https://...onrender.com` 地址写入 `docs/runtime-config.js`。
7. 推送前端配置并用 GitHub Pages 地址完成端到端验收。

免费实例会在闲置后休眠，首次分析可能需要等待唤醒。正式对外时建议将 Render 实例升级为 Starter。

## 安全提醒

旧 `.env` 中的 DeepSeek Key 已经暴露在本地项目资料中，应立即在 DeepSeek 平台作废并生成新 Key。仅删除文件不能使旧 Key 失效。
