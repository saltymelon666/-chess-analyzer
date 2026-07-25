# 棋盘研究所项目状态

更新时间：2026-07-24

本文档是继续开发时的首要状态来源。内容只依据当前代码、Git 状态、自动化测试、仓库内质量报告和部署配置；无法从这些材料确认的内容均标为“待确认”。

## 1. 当前快照

- 当前分支：`main`
- 当前提交：`022a5bf`（`Prioritize professional chess analysis focus (#5)`）
- `main` 与 `origin/main` 当前指向同一提交。
- 当前工作区不是干净状态，存在尚未提交的专业分析展示、棋盘坐标修复与 Phase 1—3 分析链改造。
- 根目录 `index.html` 与发布目录 `docs/index.html` 当前内容一致。
- 本地自动化测试：`173 passed, 1 warning`。
- Render 与 GitHub Pages 的当前线上运行状态：待确认。仓库只证明部署配置存在，不能证明此刻生产服务可用。

### 当前未提交的业务相关修改

已修改：

- `app/ai_explainer.py`
- `app/api.py`
- `app/engine.py`
- `app/game_review.py`
- `app/models.py`
- `app/professional_analysis.py`
- `app/professional_refs.py`
- `app/professional_validation.py`
- `index.html`
- `docs/index.html`
- `scripts/run_professional_quality_suite.py`
- `tests/test_api.py`
- `tests/test_engine.py`
- `tests/test_explanation_guard.py`
- `tests/test_game_review.py`
- `tests/test_professional_analysis.py`

新增但尚未跟踪：

- `app/chess_facts.py`
- `app/strategic_plans.py`
- `app/threat_analysis.py`
- `tests/test_chess_facts.py`
- `tests/test_strategic_plans.py`
- `tests/test_threat_analysis.py`
- `tests/test_professional_frontend.py`

`docs/professional-analysis-focus-report.md` 被 Git 标记为已修改，但当前没有可见文本差异，可能是换行符差异；在确认前不要覆盖或提交。

工作区还有多份未跟踪的历史审查文档、演示文件和辅助脚本。它们不属于当前功能修改，不得在未确认用途前批量删除、提交或覆盖。`docs/PROJECT_STATUS.md` 是后续状态判断的规范入口。

## 2. 项目核心目标与 MVP 范围

“棋盘研究所”是面向 4—12 岁儿童的国际象棋复盘网站。用户导入或粘贴 PGN，系统使用 Stockfish 生成权威棋盘事实与候选路线，并按需使用 DeepSeek 生成中文解释。

当前 MVP 范围：

- 导入、解析和浏览 PGN。
- 显示棋盘、完整回合记录以及当前半回合。
- 使用服务端 Stockfish 分析当前局面和整盘棋。
- 对每步棋给出评价、质量等级、最佳着和参考变化。
- 通过 `python-chess` 校验棋子、格子、SAN/UCI、吃子、将军、将杀、易位和升变事实。
- 生成儿童化走法解释，并在 DeepSeek 不可用时保留 Stockfish 结果。
- 为选定走法生成结构化“专业棋局分析”，包含事实引用、严格校验、纠错重试、缓存和安全回退。
- 按相关性与重要性筛选弱点、王安全、威胁和路线事件，避免把所有底层事实机械展示。
- 提供统一的响应式“棋盘研究所”视觉界面。

当前不属于已实现 MVP 的内容：

- 用户账号、云端棋局库、社交或课程系统。
- 新模型训练。
- 专业分析后的独立“儿童化翻译第二阶段”。该方向讨论过，但尚未实施。
- 可靠 ECO/开局库识别。页面有示例开局，但后端没有可确认的 ECO 数据源；不能自由补写开局名称。

## 3. 当前技术架构

### 前端

- 技术：单页 `HTML/CSS/JavaScript`，使用本地 `chess.js`。
- 开发入口：`index.html`
- GitHub Pages 发布入口：`docs/index.html`
- 运行时后端地址：
  - 本地从 `http://localhost:8080` 打开时使用 `http://localhost:8000`
  - 线上地址由 `runtime-config.js` / `docs/runtime-config.js` 的 `window.CHESS_API_BASE_URL` 提供
- 静态资源：`assets/`；发布副本在 `docs/assets/`
- 两个 HTML 入口必须同步修改并在提交前验证一致。

重要限制：不要直接用 `file:///.../index.html` 验证后端。当前本地识别和 CORS 配置只允许 `http://localhost:8080` 或 `http://127.0.0.1:8080` 等已配置来源。

### 后端

- 框架：FastAPI
- `app/api.py`：API 路由、服务初始化和内存缓存
- `app/config.py`：`.env` 与环境变量读取
- `app/engine.py`：Stockfish 子进程和多路线分析
- `app/game_review.py`：PGN 逐步分析、合法走法事实和路线构建
- `app/quality.py`：走法质量与损失判定
- `app/complexity.py`：简单、普通、复杂局面分类
- `app/position_facts.py`：子力、王安全、兵形、威胁和关键棋子事实
- `app/analysis_focus.py`：相关性、重要性评分和动态栏目选择
- `app/ai_explainer.py`：儿童化 DeepSeek 解释与校验
- `app/professional_analysis.py`：专业分析调用、Token 控制、缓存、重试和安全回退
- `app/professional_refs.py`：事实 ID、棋子 ID、路线 ID 与 ply ID 的引用解析
- `app/professional_validation.py`：专业输出的结构、事实、路线、证据和长度校验
- `app/models.py`：Pydantic 请求和响应模型

当前 API：

- `GET /api/health`
- `POST /api/review`
- `POST /api/game-review`
- `POST /api/move-explanation`
- `POST /api/move-facts`
- `POST /api/professional-analysis`
- `POST /api/analysis-report`

### 分析与数据流

```text
PGN
→ python-chess 解析
→ Stockfish 三条候选路线
→ 棋盘事实包
→ 专业分析重点筛选
→ 紧凑引用式 DeepSeek 请求
→ 严格校验
→ 必要时纠错重试
→ 安全回退或通过后的结构化结果
→ 前端动态栏目
```

### 部署

- 前端：`docs/` 用于 GitHub Pages。
- 后端：根目录 `Dockerfile` 与 `render.yaml` 用于 Render。
- Render 配置为自动部署、`/api/health` 健康检查、Linux Stockfish 路径 `/usr/local/bin/stockfish`。
- 当前配置模型：`deepseek-v4-flash`
- 当前配置 base URL：`https://api.deepseek.com`
- API Key 只允许存在于本地 `.env` 或 Render 环境变量；不得读取、打印或提交真实值。

## 4. 已完成并验证的内容

### 已合并到 `main`

根据最近提交和跟踪文件，以下能力已进入 `main`：

- PR #2：专业棋局分析严格化。
- PR #3：复杂安全回退长度修复。
- PR #4：专业分析引用化、Token 压缩、性能优化、缓存和 15 局面验证集。
- PR #5：专业分析重点筛选与动态栏目。
- PGN 解析、联盟棋谱文件导入、逐步走棋质量、Stockfish 三路线、儿童解释和 DeepSeek 安全集成。
- 专业分析使用 `pieceRef`、`lineRef`、`plyRefs` 和 `evidenceRefs`，后端填入实际棋子、格子、走法和 PV。
- 无意义的“仅未保护”弱点、无现实危险的王安全、固定物质差栏目和普通 PV 吃子已被重点筛选器过滤或归入对应路线。

### 已合并版本的质量数据

来源：`docs/professional-analysis-quality-report.md`、`docs/professional-analysis-focus-report.md` 和 `docs/professional-analysis-quality-results.json`。

- 固定验证局面：15 个。
- 首次严格校验通过：15/15。
- 最终严格校验通过：15/15。
- 安全回退：0/15。
- 输入 Token：4,457—6,726。
- 首次网络响应：11.3—29.1 秒。
- 缓存响应：3ms。
- 修改前弱点 29 项，重点筛选后 2 项。
- 过滤“仅未保护”事实 62 项。
- 过滤无关王安全描述 117 项。
- 移入候选路线内部事件 54 项。
- 过滤普通 PV 吃子 63 项。

这些数字是 2026-07-20 对已合并 PR #4/#5 的验证结果，不代表当前未提交工作区修改已经重新完成相同的真实 DeepSeek 验证。

### 当前工作区修改已验证的部分

当前本地修改实现了：

- 将“小兵研究员说”和专业分析合并为一个默认展开区域。
- 删除旧简略说明、重复的专业分析外层和展开/收起按钮。
- 放大路线标题、战略标签、首着、评价和路线正文。
- 动态隐藏空栏目。
- 前端正文使用自动高度并显式取消省略、行数限制和隐藏溢出。
- 将内部棋子变量名转换为自然中文名称。
- 增加完整句检查；残句优先用已验证事实重建，无法重建时整条删除。
- 长度控制只允许在完整句或完整分句边界压缩，不再从字符中间硬切。

本地验证：

- `136 passed, 1 warning`
- 根目录与 `docs/` 的 HTML 内容一致。
- `git diff --check` 没有空白错误，只有 Windows 换行提示。

测试警告来自 FastAPI/Starlette 对当前 `httpx` 测试客户端组合的弃用提示，不是测试失败。

## 5. 当前正在处理的任务

当前工作停留在“专业分析统一卡片与文字完整性修复”的 P0 本地人工验收完成阶段。

2026-07-24 已完成：

1. 对当前未提交 diff 做最终人工代码审查。
2. 使用仓库固定棋局 `tests/fixtures/game1_selected_position.pgn` 做真实本地 UI 验收，DeepSeek 专业分析正常返回。
3. 在 `1440×1000` 桌面视口和 `390×844` 手机视口检查统一卡片、完整句、内部变量名、文字截断、路线字号、控制台错误和横向溢出。
4. 修复嵌在句子中间的“依据……可判断……”判断过程未被前端清理的问题。
5. 修复候选路线标题行在桌面窄栏中产生 15px 内部溢出的问题。
6. 修复“没有直接威胁”仍可能作为“最大危险”栏目展示的边界情况。

P0 验收结果：

- 页面只出现一个“小兵研究员说”区域，专业分析默认完整显示。
- 旧简略说明和展开/收起按钮未出现。
- 未发现“当前局面为意大。”“黑象(e7)正。”等残句。
- 未发现 `white_b`、`black_pawn`、evidenceRefs、“查看证据”“事实依据”“判断依据”等用户不可见内容。
- 三条 Stockfish 路线正常显示；桌面路线标题 28px、正文 20px，手机路线标题 22px、正文 16px。
- 桌面和手机页面均无横向溢出，专业正文无 CSS 截断，控制台和页面脚本无新增错误。
- 本次只验证了一个固定局面，不能替代 15 局面真实质量套件。

### 2026-07-24 棋盘坐标与 PGN 方向修复

- 已确认旧前端坐标换算在白方视角时上下颠倒，在黑方行棋时又只翻转横轴，导致棋盘格与 PGN 的 `a1`—`h8` 坐标不一致。
- 棋盘现固定为标准白方视角：白方在下，`a1` 在左下，`h8` 在右上；行棋方变化不再自动翻转棋盘。
- 棋子、实战走法高亮和 Stockfish 推荐箭头统一使用同一套标准坐标换算。
- 棋盘左侧显示 `8`—`1`，底部显示 `a`—`h`，开发版 `index.html` 与发布版 `docs/index.html` 已同步。
- 已在 `1440×1000` 桌面视口和 `390×844` 手机视口检查坐标顺序、初始棋子位置、控制台错误与横向溢出；均通过。
- 新增棋盘前端回归测试；完整自动化测试结果为 `136 passed, 1 warning`。

### 2026-07-24 Phase 1：统一事实驱动 AI 链路

- 新增 `app/chess_facts.py`，统一 `ChessFactPackage 1.0`、白方视角评价、实际走法、最佳走法、候选路线、程序事件及各字段来源。
- FEN 只保留在服务端事实包中用于 `python-chess` 校验；三条 DeepSeek 路径的模型可见载荷均不包含 FEN、完整 PGN、Stockfish 日志或未验证路线。
- Stockfish PV 任意一步非法、SAN/UCI 不一致或最终 FEN 不一致时整条路线失败，不再保留合法前缀；`verified=false` 路线不会进入 DeepSeek 载荷。
- `/api/review` 已改为事实包、DeepSeek JSON、ID/棋步/事件校验、一次纠错和程序模板回退，前端请求与响应入口保持不变。
- `/api/move-explanation` 与 `/api/professional-analysis` 已接入同一事实协议；网络失败或未配置 DeepSeek 时使用经过原有验证器检查的安全回退。
- 专业缓存键加入 `factPackageVersion`，避免读取旧协议缓存。
- 完整自动化测试：`137 passed, 1 warning`。
- 15 局面真实 DeepSeek 套件：首次通过 `15/15`，最终通过 `15/15`，重试 `0/15`，安全回退 `0/15`；输入 Token `4,936—7,502`，首次网络响应 `16.0—24.5` 秒，缓存 `4ms`。
- 本阶段未修改威胁分析、战略计划、知识库或 Agent 架构。

### 2026-07-24 Phase 2：程序化动态威胁分析层

- 新增 `app/threat_analysis.py` 与 `ThreatPackage 1.0`。每个威胁都包含程序来源、verified 路线证据、执行方、目标、支持走法、Ignore Test 结果和置信度。
- 第一阶段严格限制为五类：将杀威胁、战术吃子、稳定赢子、升变威胁和中心突破；普通无保护、仅被攻击、普通交换及无评价/路线支持的候选不会升级为威胁。
- `ChessFactPackage 1.0` 只增加默认空的 `threats` 列表，原 position/evaluation/move/route/event 字段与 FEN 隔离规则不变。
- `/api/review` 在事实包构建后、DeepSeek 前运行 `ThreatAnalyzer`，响应兼容地增加 `threats` 数组；未确认威胁时返回空数组。
- Ignore Test 最多选择 3 个非吃子、非将军、非升变、非目标处理的合法安静着；每次请求最多测试 1 个威胁，独立深度 8，总超时 12 秒。受将军时不存在合法“忽略”，不会伪造测试结果。
- DeepSeek 只能用 `threat_id` 返回解释；未知 ID、重复 ID、额外威胁类型、模型生成 SAN/UCI/格子或事实包外事件继续由统一 validator 拒绝。走法解释的旧 `opponentThreat` 展示字段改由后端根据通过校验的 threat explanation 回填。
- 专业分析缓存键加入 `ThreatPackage` 版本，避免旧提示载荷缓存与新协议混用。
- 完整自动化测试：`146 passed, 1 warning`，Phase 1 的 137 项全部保留，新增 9 项威胁层测试。
- 本地 Stockfish 深度 10 抽样：初始安静局面威胁层约 1ms、0 次额外分析；明确赢子局面威胁层约 379ms、1 次批量 Ignore Test。该数据只代表本机抽样，不是线上 SLA。
- 15 局面真实 DeepSeek 套件：首次通过 `15/15`，最终通过 `15/15`，重试 `0/15`，安全回退 `0/15`；输入 Token `4,941—7,507`，首次网络响应 `13.1—20.9` 秒，缓存 `4ms`。报告与原始结果保存在独立验证目录，没有覆盖仓库内既有质量报告。
- 本阶段未实现 Strategic Plan、知识库、RAG、Agent 框架或新的前端威胁展示。

### 2026-07-24 Phase 3：程序化战略计划分析层

- 新增 `app/strategic_plans.py` 与 `StrategicPlanPackage 1.0`。每个计划包含程序来源、执行方、计划类型、目标、支持走法、至少两条 verified 路线、结构证据和置信度。
- 第一阶段严格限制为八类：改善最差棋子、准备中心突破、占领开放线、激活车、改善王安全、攻击可利用弱兵、制造通路兵和有利简化；单条 PV、随机调动、评价方向冲突或结构证据不足时返回空计划。
- 计划识别比较多条 PV 的共同棋子、目标格、兵突破、攻击对象和结构转换，不要求完整走法字符串相同；路线评价差超过 100cp 或方向明显冲突时取消候选。
- `ChessFactPackage 1.0` 兼容地增加默认空的 `plans` 数组；`/api/review` 兼容地返回高置信度计划，未确认计划时返回空数组，前端本阶段没有新增展示改动。
- `/api/review`、走法解释和专业分析都在 DeepSeek 前运行同一个 `StrategicPlanAnalyzer`。DeepSeek 只允许按已有 `plan_id` 返回解释；未知或重复 ID、模型自造计划类型、路线外棋步及无证据事实会被统一 validator 拒绝。
- 专业分析旧 `plans` 生成入口在生产链上被禁用并保留模型兼容字段；最终双方计划由后端按 `plan_id` 合并。DeepSeek 失败时的安全回退也只使用高置信度程序计划，不再把单条 PV 首着包装成战略。
- 战略层完全复用已有 ChessFactPackage、PositionFacts、ThreatPackage 和 verified MultiPV，不新增 Stockfish 调用。合成三路线热身后基准约 `1.37ms/次`；该数据只代表本机抽样，不是线上 SLA。
- 完整自动化测试：`159 passed, 1 warning`，Phase 1/2 的 146 项全部保留，新增 13 项战略层和生产接入测试。
- 15 局面真实 DeepSeek 套件：首次通过 `14/15`，最终通过 `15/15`，重试 `1/15`，安全回退 `0/15`；输入 Token `5,153—7,530`，首次网络响应 `15.2—21.2` 秒。唯一重试来自既有候选路线 `strategyTags` 数量/枚举错误，纠错后通过；全部结果的计划证据和三条路线验证均通过。
- 本阶段未实现知识库、RAG、Agent 框架、自由棋理生成或新的前端计划展示。

### 2026-07-24 Phase 4：专业复盘文本生成层

- 新增 `app/analysis_report.py` 与 `AnalysisReportPackage 1.0`，固定包含局面概况、关键一步、威胁、战略、verified 路线和总结六个章节。评价、优势方向、物质、王安全事实 ID、局面事实 ID、实际/最佳走法、威胁 ID、计划 ID 和路线均由程序写入。
- 新增 `app/narrative_generator.py`。Narrative DeepSeek 只接收去除 FEN、PGN、Stockfish 日志、未验证路线和路线 SAN/UCI 的报告载荷，只能补写章节文本及选择已有总结引用，不能发现事实、计算评价、生成棋步、威胁、战略或路线。
- 新增独立 `POST /api/analysis-report`，请求格式继续使用 `analysis_id` 与 `move_index`。旧 `/api/professional-analysis`、现有前端、儿童化解释、Stockfish、`ThreatAnalyzer` 和 `StrategicPlanAnalyzer` 均未修改。
- 新接口从缓存的 `MoveReview` 直接构建 `ChessFactPackage`、同步检测 `ThreatPackage`、构建 `StrategicPlanPackage` 和 verified routes，不新增 Stockfish 调用，也不调用旧 ProfessionalAnalysis LLM 路径。
- 每个未缓存报告请求最多执行一次 Narrative DeepSeek HTTP 请求；JSON、ID、棋步、评价方向、战略/威胁类型或总结引用校验失败后立即使用程序模板回退，不执行第二次纠错请求。缓存命中不调用 DeepSeek。
- 总结的 `source_refs` 必须至少引用已有 `move_error_id`、`threat_id`、`plan_id` 或 `route_id`；无引用、未知引用、重复引用或正文观点与引用类型不匹配时拒绝模型结果。
- 新增 `tests/test_analysis_report.py` 和新接口回归测试。完整自动化测试结果为 `173 passed, 1 warning`，Phase 1—3 的 159 项全部保留。
- 新增 `scripts/run_analysis_report_quality_suite.py`。15 局面真实 DeepSeek 套件结果：最终有效 `15/15`，单次调用约束 `15/15`，模型输出直接通过 `13/15`，严格校验后程序安全回退 `2/15`。两次回退分别来自评价方向说反、空计划包下生成战略观点；没有放松校验。
- 15 局面程序报告编排耗时中位数 `26.12ms`、最大 `51.29ms`，Narrative 网络耗时中位数 `4.463s`，输入 Token `2,046—2,488`，总 Token `2,393—3,087`。结果保存于 `docs/analysis-report-quality-results.json`，报告保存于 `docs/analysis-report-quality-report.md`。
- 本阶段没有实现儿童化、IP 角色、知识库、RAG、Agent 或模型训练，也没有迁移现有前端。

尚未完成：

1. 决定是否创建分支、提交和草稿 PR。
2. 合并后确认 GitHub Pages 与 Render 的实际部署状态。

不要把当前工作区修改描述为已经发布。

## 6. 已确认的产品与 UI 要求

### 产品原则

- 面向 4—12 岁儿童，但不能过度低幼。
- 棋盘是分析页视觉核心。
- DeepSeek 负责解释，不得创造棋子、格子、走法、评价或路线。
- 不得降低严格事实校验来提高通过率。
- 没有值得讲的内容时隐藏栏目，不为了填模板强行生成。
- 证据数据可以保留在后端，但最终用户报告不显示 evidenceRefs、事实依据、判断过程或证据数量。
- 不显示 `white_b`、`black_pawn` 等内部变量名。

### 视觉规范

- 风格：温暖、清爽、圆润的“小小棋盘研究所”。
- 主背景 `#FFF9EF`，卡片 `#FFFFFF`，主文字 `#292725`，次要文字 `#6F6A64`。
- 主按钮 `#F4B942`，辅助浅蓝 `#E8F3F2`，正确 `#55B987`，错误 `#E97878`，边框 `#E8DED1`。
- 使用大圆角、轻阴影和充足留白。
- 不使用暗色背景、霓虹、玻璃拟态或复杂动画。
- 桌面、平板和手机都不能横向溢出。

### 专业分析展示

- 只保留一个“小兵研究员说”区域，专业分析默认展示。
- 栏目动态出现，可能包含：当前局面、最大危险、关键棋子、双方计划、弱点、王安全、实战走法和三条 Stockfish 路线。
- 路线内部事件只能显示在对应路线中。
- 文本必须完整、自然、具体；不能显示“当前局面为意大。”“黑象(e7)正。”等残句。
- 无可靠 ECO/开局库来源时，不生成具体开局名称。
- 正文不得使用 `line-clamp`、省略号、固定高度或隐藏溢出来掩盖内容。

## 7. 已发现但尚未解决的问题

### 已确认

- 本地若从 `file:///` 打开页面，后端连接会失败。必须分别启动 8000/8080 服务并访问 `http://localhost:8080`。
- 本地后端和静态服务器不是持久服务，终端或运行会话结束后需要重新启动。
- 当前工作区有未提交和未跟踪文件，提交前必须精确选择文件，不能执行批量清理。
- `docs/professional-analysis-focus-report.md` 的 Git 修改标记没有可见文本 diff，原因待确认。
- Phase 3 的 15 局面真实 DeepSeek 原始结果和分批报告保存在独立验证目录，未覆盖仓库内既有质量报告。

### 待确认

- 当前 Render 生产服务是否仍在运行、是否会休眠、最新部署提交是什么。
- 当前 GitHub Pages 是否已经指向 `022a5bf`，以及是否包含本地未提交 UI 修改；按 Git 状态判断，本地修改尚未发布。
- `deepseek-v4-flash` 在当前生产账户下的实际可用性、延迟和费用。
- 截图对应同一盘棋、同一回合的棋理内容是否符合专业棋手预期；P0 已完成页面结构、完整性和响应式验收，但没有替代专业棋理人工评审。

## 8. 测试、性能与已知限制

### 每次修改后的基础测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

当前结果：173 项全部通过。

### 专业质量验证

- 固定局面：`tests/fixtures/professional_validation_positions.json`
- 自动测试：`tests/test_professional_quality_set.py`
- 质量脚本：`scripts/run_professional_quality_suite.py`
- 报告：`docs/professional-analysis-quality-report.md`
- 原始结果：`docs/professional-analysis-quality-results.json`

真实质量套件会调用 Stockfish 和 DeepSeek，不能用普通单元测试结果替代。

### 已知限制

- Render 免费实例可能冷启动，首次请求延迟不稳定。
- 质量报告中最慢网络响应为 29.1 秒，超过原定复杂局面 25 秒目标；报告说明该次包含网络传输重试。
- 专业分析缓存是服务端内存缓存，服务重启后失效。
- 游戏分析缓存同样为内存状态，前端持有的 `analysis_id` 在后端重启后不可继续使用。
- MVP 没有数据库、用户账户或持久化分析记录。
- 不存在可靠开局名称数据源。

## 9. 仅讨论过但尚未实施的想法

- 在专业分析之后增加独立儿童化翻译流程。
- 建立更完整的开局/ECO 名称识别。
- 用户账户、棋局历史、课程与训练计划。
- 将本地开发服务做成开机自动运行或桌面启动器。

这些内容不得在没有新需求和范围确认时自动开发。

## 10. 下一步建议

### P0：保护并验收当前工作区

已于 2026-07-24 完成。验收范围与结果记录在第 5 节。

### P1：重新验证质量

1. 运行 15 局面真实 DeepSeek 套件。
2. 确认首次通过率、最终通过率、回退率、Token 和耗时没有退化。
3. 对至少三个代表局面人工检查最终中文内容。

### P2：整理提交

1. 新建 `codex/` 前缀分支。
2. 只暂存本次相关文件；排除 `.env`、演示文件、旧辅助脚本和用途不明的未跟踪文件。
3. 创建草稿 PR，等待确认后再合并。

### P3：部署验收

1. 合并后检查 Render 与 GitHub Pages。
2. 检查 `/api/health`、Stockfish、DeepSeek 配置和 `/api/professional-analysis`。
3. 用固定 PGN 做生产冒烟测试，且不得记录密钥。

## 11. 压缩聊天记录后的继续指令

建议直接发送：

> 请先完整阅读根目录 AGENTS.md 和 docs/PROJECT_STATUS.md，然后检查当前 git status 与未提交 diff。继续“专业分析统一卡片与完整句修复”的 P1 质量验证：运行 15 局面真实 DeepSeek 套件，检查首次通过率、最终通过率、回退率、Token、耗时和至少三个代表局面的最终中文内容；不要提交、合并或部署。
