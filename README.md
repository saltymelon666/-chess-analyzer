# 棋盘研究所：AI 国际象棋复盘 MVP

这是一个前后端分离的儿童国际象棋复盘网页。用户粘贴 PGN 后，服务端使用 Stockfish 18 分析棋局，并按需调用 DeepSeek，将引擎事实转换成简单、具体、鼓励性的中文解释。DeepSeek API Key 只保存在服务端环境变量中，不会发送到浏览器。

## 当前能力

- 粘贴并解析 PGN，浏览棋局中的任意一步
- 使用服务端 Stockfish 18 分析当前局面
- 对整盘棋逐步计算走棋质量：最佳着、好棋、不精确、错误、败着或常规着
- 正确按当前走棋一方计算损失，单独处理将杀分数
- 通过 python-chess 确认每步棋子、起终点、SAN/UCI、吃子、将军、将杀、易位和升变事实
- 按合法走法数、候选差距、强制变化、评价波动和参战棋子等指标区分简单、普通和复杂局面
- 对 DeepSeek 输出校验格子、走法、棋子颜色和特殊走法；失败时纠错一次，再失败则使用保守模板
- 在走法列表显示质量符号，并在棋盘高亮实战走法和最佳走法箭头
- 走法记录按完整回合显示为“回合 / 白方 / 黑方”，每个单方走棋保持独立 ply 索引
- 点击某一步时按需生成儿童化解释；相同解释在服务端和浏览器中缓存
- DeepSeek 不可用时仍展示 Stockfish 等级、分数变化和推荐走法
- 响应式“棋盘研究所”界面，支持桌面、平板和手机

“精彩着”需要可靠的唯一解、战术或弃子证据。当前 MVP 宁可少标记，暂不根据“普通最佳着”自动产生精彩着。

## 项目结构

```text
app/                 FastAPI 后端
  api.py             API 路由与分析缓存
  engine.py          Stockfish 18 服务
  game_review.py     PGN 逐步分析编排
  quality.py         走棋质量阈值和判定
  complexity.py      局面复杂度和解释长度配置
  ai_explainer.py    DeepSeek 解释服务
  config.py          环境配置
  models.py          请求和响应模型
docs/                GitHub Pages 发布目录
tests/               后端自动化测试
index.html           前端开发入口
requirements.txt     Python 运行依赖
.env.example         环境变量示例
```

## 本地启动

需要 Python 3.11+。Windows 开发时确保项目根目录存在可执行的 `stockfish.exe`；Linux 部署时让 `STOCKFISH_PATH` 指向 Linux Stockfish 二进制文件。

1. 创建虚拟环境并安装依赖：

   ```powershell
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```

2. 创建本地配置并填写新的 DeepSeek Key：

   ```powershell
   Copy-Item .env.example .env
   ```

3. 启动后端：

   ```powershell
   uvicorn app.api:app --host 127.0.0.1 --port 8000 --reload
   ```

4. 另开终端启动前端：

   ```powershell
   py -m http.server 8080
   ```

5. 访问 `http://localhost:8080`。API 文档位于 `http://localhost:8000/docs`。

## API

### 健康检查

```http
GET /api/health
```

### 分析当前局面（保留的原有接口）

```http
POST /api/review
Content-Type: application/json

{
  "fen": "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
}
```

### 分析整盘棋的每一步

```http
POST /api/game-review
Content-Type: application/json

{
  "pgn": "1. e4 e5 2. Nf3 Nc6"
}
```

响应包含 `analysis_id` 和逐步 Stockfish 事实。每步包括走棋前后评价、实战着、最佳着、实战后的对手第一选择、已验证 PV、centipawn loss、质量等级、复杂度以及将杀信息。所有具体走法都会由 python-chess 再次确认合法性并转换为权威 SAN。

### 按需解释某一步

```http
POST /api/move-explanation
Content-Type: application/json

{
  "analysis_id": "整盘分析返回的 ID",
  "move_index": 3
}
```

重复请求同一个 `analysis_id + move_index` 会命中缓存，不再调用 DeepSeek。解释长度按 `simple` 50—100 字、`normal` 120—220 字、`complex` 250—500 字控制，对应 Token 上限分别为 300、550、1100。复杂解释以结构化 JSON 返回总体判断、当前局面、对手威胁、实战想法、问题、推荐着、2—4 步已验证变化和儿童提示；前端按小节展示。模型输出第一次事实或结构校验失败会纠错重试；第二次仍失败会返回由结构化事实组成的保守模板。若 DeepSeek 暂不可用，接口返回 `warning`，前端继续显示 Stockfish 结果。

## 主要配置

- `STOCKFISH_DEPTH`：单局面分析深度
- `GAME_ANALYSIS_DEPTH`：整盘逐步分析深度，默认 10
- `GAME_ANALYSIS_TIMEOUT_SECONDS`：整盘分析总超时，默认 240 秒
- `GAME_ANALYSIS_MAX_PLIES`：单盘最大半回合数，默认 160
- `DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL`、`DEEPSEEK_MODEL`
- `ALLOWED_ORIGINS`：允许访问后端的前端来源

### 微信与支付宝

支付页支持微信支付和支付宝。桌面微信订单显示站内生成的二维码，手机微信订单进入 H5 支付；支付宝根据设备进入电脑网站支付或手机网站支付。渠道回调必须通过平台签名验证，并与数据库订单的商户信息、订单号、人民币金额和成功状态一致后，才会开通会员。

- 公共配置：`PAYMENT_PUBLIC_ORIGIN`（可接收异步通知的 HTTPS 后端地址）、`PAYMENT_FRONTEND_ORIGIN`（支付完成后返回的正式网站地址）
- 微信：`WECHAT_PAY_MCH_ID`、`WECHAT_PAY_APP_ID`、`WECHAT_PAY_CERT_SERIAL`、`WECHAT_PAY_PRIVATE_KEY`、`WECHAT_PAY_API_V3_KEY`、`WECHAT_PAY_PLATFORM_PUBLIC_KEY`、`WECHAT_PAY_PLATFORM_SERIAL`
- 支付宝：`ALIPAY_APP_ID`、`ALIPAY_PRIVATE_KEY`、`ALIPAY_PUBLIC_KEY`、`ALIPAY_SELLER_ID`

私钥、API v3 Key 和平台公钥只放在部署环境变量中。商户平台还需要开通对应产品，并将支付域名、回调地址配置为实际生产域名；未完整配置的渠道会返回 503，不会生成假订单入口。生产环境关闭旧的内部 HMAC 测试回调。

### 个人收款码人工审核

商业验证阶段也可以使用个人微信或支付宝收款码。仓库默认使用 `assets/payment/` 下的站内收款码，也可通过 `MANUAL_WECHAT_QR_URL`、`MANUAL_ALIPAY_QR_URL` 覆盖。用户扫码付款并提交付款人手机号；手机号只用于人工核对，申请始终绑定当前登录 `user_id`，提交后不会自动开通会员。管理员在 `admin-payments.html` 使用 `ADMIN_STATISTICS_KEY` 登录，确认真实到账和金额后点击通过，后端才会激活相应月会员或年会员。

个人收款码无法由程序验证真实到账，可能受到支付平台经营收款规则限制，只适合作为人工核对方案。收款码图片不得包含在聊天记录或日志中；正式使用前需确认对应平台允许当前经营场景。

## 测试

```powershell
pip install -r requirements-dev.txt
pytest -q
```

## 部署

- 前端从 `docs/` 发布到 GitHub Pages。
- 后端通过根目录 `Dockerfile` 和 `render.yaml` 部署到 Render；镜像会安装 Linux Stockfish 18。
- 在 Render 中单独设置 `DEEPSEEK_API_KEY`，不要把 Key 写入仓库。
- `docs/runtime-config.js` 配置线上后端地址。
- Render 免费实例闲置后会休眠，首次分析可能需要等待唤醒。

## 安全提醒

不要提交 `.env`、DeepSeek Key 或本地 Stockfish 二进制文件。若 Key 曾出现在项目文件或聊天记录中，应立即在 DeepSeek 平台作废并重新生成。
