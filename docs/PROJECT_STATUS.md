# 棋盘研究所项目状态

更新时间：2026-08-10

本文档是继续开发时的首要状态来源。内容只依据当前代码、Git 状态、自动化测试、仓库内质量报告和部署配置；无法从这些材料确认的内容均标为“待确认”。

## 1. 当前快照

- 当前分支：`codex/latest-frontend`
- 当前提交：`91f9c67`
- 当前工作区不是干净状态，存在尚未提交的“完整删除关键棋子栏目”和Phase 6A修改。
- 根目录 `index.html` 与发布目录 `docs/index.html` 当前内容一致。
- “小兵研究员说”主模块固定保留并始终显示；前端展示“双方子力与局面”“白方计划/黑方计划”和Stockfish三条研究路线。路线的“第一步作用”由程序根据已验证首着生成，不再展示DeepSeek自由编写的直接目的；候选路线内部吃子/子力变化事件卡不展示。质量标记为`?!`、`?`或`??`的实战着会额外展示完整验证路线和最终战术结果，正常着法不展开。双方是否已经易位、易位权及易位历史相关句子不在局面描述中展示；棋书原评、弱点、潜在威胁和王的安全栏目仍不展示。
- 本地自动化测试：`291 passed, 2 warnings`（2026-08-06，路线首着作用硬事实保护后的完整回归）。
- 15局面真实质量套件：最终严格校验`15/15`，首次通过`14/15`，`center-1`因DeepSeek传输中断进入程序安全回退；三条路线有效`15/15`。
- Render 与 GitHub Pages 的当前线上运行状态：已验证。合并提交 `76d1ccc` 后，GitHub Pages 返回 200；Render `/api/health` 返回 200，Stockfish 可用、DeepSeek 已配置，开局与残局 lookup 接口均返回 200。

### 前端首次加载性能优化

- 101 张生产图片已从 PNG 转为 WebP，并同步到 `assets/` 与 `docs/assets/`；单份资源总量由 10.07 MiB 降至 2.05 MiB，减少 79.7%，当前最大的 WebP 为 489.4 KiB。
- `chess.js` 已改为用户首次导入 PGN 或分析局面时通过 `pgn-runtime.js` 动态加载；Stockfish WASM 保持按需路径，不进入首次页面请求。
- Fast 4G（1.6 Mbps、150 ms RTT）、冷缓存、桌面 1365×768、5 次中位数：首次传输 3811.1 KiB → 762.0 KiB，DOMContentLoaded 1330.1 ms → 740.2 ms，load 19795.2 ms → 4131.3 ms，LCP 17844 ms → 1980 ms。
- 桌面、平板、手机浏览器冒烟检查均通过：无横向溢出、无控制台错误，`chess.js` 首屏不加载，导入示例 PGN 后按需加载并正确恢复 12 着。
- 完整自动化回归：`337 passed, 2 warnings`；警告为既有 Starlette 废弃提示及本机 `.pytest_cache` 写权限提示。
- 15 局面真实 Stockfish + DeepSeek 质量套件：首次通过 `15/15`，最终严格通过 `15/15`，重试 `0/15`，安全回退 `0/15`；输入 Token `5,458—8,704`，首次网络响应 `12.041—16.611` 秒，缓存响应 `674ms`。结果写入被忽略的 `work/frontend-performance/`，未覆盖仓库历史质量报告。

### Phase 6D / 6E / 6F 最新进展

- 两轮人工棋书局面审核已经完成；正式样本库为30题，另有2题排除。
- 30题真实 Phase 6D 基线全部完成，运行错误0；基线程序主导主题识别为18/30。
- Phase 6E 已接入双攻、牵制、串击、战术性牺牲、第七横线活动和路线作用域信号；无DeepSeek结构回归为28/30。
- Professional安全模板从候选路线生成全局直接危险的旁路已经删除；相关基线问题由10题降为0。
- 8个受影响真实局面中，程序主题和Professional最终文本均为8/8；无证据主动权为0。
- Analysis Report 已读取 Position Interpretation；在同一批8题真实事实包上，程序安全报告主题命中由2/8提升到8/8。
- Phase 6F 已实现有界 Prepared Threat Ignore Test；CC-022 的 `Nd5` 经 3 个中性忽略着验证，最小评价损失为 2.38 兵。
- Phase 6F 已实现条件化杀王深度升级；30题中只有 BB-02 触发并确认 `Qf1+` 为 M3，没有新增将杀误报。
- 30题结构回归为30/30；30题真实全链完成30/30，运行错误0，无当前威胁作用域误写和无证据主动权。
- 当前工作已到人工审核节点：`docs/research/phase6f-final-human-review-boards.html` 用于审核主次、棋理与自然度。
- Phase 6E完整报告：`docs/research/chess-analysis-phase6e-validation-report.md`；Phase 6F完整报告：`docs/research/chess-analysis-phase6f-validation-report.md`。
- 当前样本只批准用于离线评测、回归与规则校准，不是DeepSeek训练数据，也没有进入生产Prompt。

### Phase 7A 棋书自动抽取与检索可行性

- 已完成30题逐题留一检索实验；查询侧不读取当前题的人类答案。普通混合检索Top-3主题族为63.3%，但跨棋书仅20.0%，低于跨书随机基线42.9%，当前30题不能直接作为生产RAG。
- 已在`work/research_books/`下载4本Project Gutenberg研究副本；该目录被`.gitignore`排除，不进入产品或提交范围。
- 已为《Chess and Checkers: The Way to Mastership》实现纯文本棋盘自动抽取：检测102个棋盘编号，75个恢复棋子，73个有效棋盘，42个同时具备有效棋盘、行棋方和相邻原始解释。
- 用现有12个CC正式局面做解析盲对照：棋盘12/12完全一致，行棋方12/12一致。
- 已为《The Blue Book of Chess》实现PGN原注释自动抽取：85盘有效棋局、228个去重合法注释局面，保存走前FEN、着法、走后FEN和原始注释；85个空解析分段被自动忽略，1盘带PGN错误并记录。
- 现有6个BB正式局面中4个可按走前棋盘和标注着精确找回；另2个因原书注释挂载边界不同而不强行匹配。
- 两本书合计得到约270个程序可恢复的原始棋书条目；其中只有具备明确题解范围和可验证主题的子集可进入检索，不能把数量直接当作270条标准答案。
- 把42个自动条目用于棋盘外形检索后，Top-3主题提示为90.0%，但随机基线已达86.3%，证明仅凭棋盘外形和原文常见词没有有效增益。
- Phase 7A结论：棋书自动抽取可行，当前检索不可接入生成链。下一步必须扩展不同作者的同主题覆盖，并为自动条目运行Stockfish、ThreatPackage与结构化评价来源后再测试。
- 报告：`docs/research/chess-analysis-phase7a-retrieval-feasibility-report.md`、`docs/research/phase7a-cc-extraction-validation-report.md`、`docs/research/phase7a-bb-extraction-validation-report.md`、`docs/research/phase7a-book-corpus-retrieval-report.md`。

### Phase 7B / 7C 棋书数据库与棋理规则

- Phase 7B 已把三本完成棋盘恢复的公版棋书整理为本地SQLite研究库：共326题、323个不同棋盘；CC 42题、BB 228题、CF 56题。数据库位于被忽略的`work/research_books/phase7b-book-corpus.sqlite3`，不会随代码提交。
- 已登记8本公版候选棋书；另外5本因图片棋盘、旧记谱法或来源筛选尚未达到可靠自动恢复标准，没有为了扩充数量强行导入。
- Phase 7C 新增92条证据有界棋理规则：44条棋盘精确规则、22条棋盘启发式规则、12条Stockfish路线规则、14条MultiPV规则。
- 326题FEN离线标注共生成12,116条信号，命中54种规则；依赖路线或MultiPV的规则在没有引擎证据时保持不命中。
- 30题跨棋书盲检中，棋盘外形、规则、固定混合检索的Top-1主题族分别为53.3%、30.0%、33.3%；随机基线为37.3%。规则Top-3为73.3%，只比随机71.0%高2.4个百分点。
- 已新增第一版`PositionFactorRanker`，按规则可靠度、作用域和语料出现率把每题压缩为最多2个主导因素和3个辅助因素；326题平均输出4.98个因素。
- 显著性规则检索Top-1提升到46.7%，但仍低于棋盘外形53.3%；显著性混合为36.7%。当前仍否决接入DeepSeek。
- 真正缺少的部分已经收窄为Stockfish评价敏感度与因果验证：需要证明某因素是否导致评价变化、是否在多条路线中迫使对手应对，再更新重要性分数。
- 报告：`docs/research/chess-analysis-phase7b-book-corpus-report.md`、`docs/research/chess-analysis-phase7c-reasoning-rules-report.md`；数据：`docs/research/phase7c-book-rule-enrichment-summary.json`、`docs/research/phase7c-rule-retrieval-blind-results.json`。

### Phase 7D Stockfish评价敏感度与因果验证

- 已新增`EvaluationSensitivityAnalyzer`，为排序因素附加首选路线支持、稳定MultiPV覆盖、评价敏感度和`confirmed/supporting/unproven`状态；MultiPV分差不会被直接解释为静态主题的因果证明。
- 326个棋书局面使用Stockfish Depth 8、Threads 1、Hash 32 MB、MultiPV 3完成离线标注：错误0，威胁53个、计划323个、引擎支持规则信号372个。
- 1,629个候选因素中确认42个、支持59个、未证明1,528个；多数普通信号没有被升级为因果结论。
- 为消除低深度哈希状态影响，语料和查询都改成每个局面独立启动引擎；30题盲检连续两次指标完全一致。
- 最终因果检索Top-1/Top-3为40.0%/73.3%，随机为37.3%/71.0%，棋盘外形为53.3%/70.0%；固定棋盘+因果混合为33.3%/70.0%。生产门禁仍不通过。
- 已生成10题最小人工审核页`docs/research/phase7d-causal-human-review-boards.html`，用于区分自动粗标签错误与检索真实错误。完成这10题前不继续调权重，也不接入DeepSeek。
- 审核页中的30段候选棋书评注、主题名称、因果状态和书名信息已默认中文显示；英文原文折叠保留用于溯源。
- 项目负责人已完成10题人工审核：8题“有帮助”、2题“部分有帮助”、0题“无关”。这10题在自动精确主题族指标中全部属于Top-1失败，证明该指标存在严重假阴性，不能再作为主要检索成功标准。
- 审核纠正两处内容边界：`CF-AUTO-21`旧式记谱应译为`Bxf7+`；`BB-AUTO-G014-P043`的`...Bd6+`属于假设变化内部事件，不是根局面立即合法着。中文页已经修正并明确作用域。
- 审核结果归档为`docs/research/phase7d-causal-human-review-results.json`，裁决为`docs/research/phase7d-human-review-adjudication.json`。下一步主要评价目标改为“可迁移思路有用性”，精确主题族仅作诊断。
- 报告：`docs/research/chess-analysis-phase7d-evaluation-sensitivity-report.md`；原始结果：`docs/research/phase7d-engine-evidence-summary.json`、`docs/research/phase7d-causal-retrieval-blind-results.json`。

### Phase 7E 棋书上下文安全迁移 A/B

- 已新增棋书案例安全迁移包：只允许 DeepSeek借用类比局面的观察角度和思考顺序，禁止迁移源局面的棋子、格子、着法、评价、结果、威胁和主动权。
- `ProfessionalAnalysisService`和`NarrativeGenerator`只增加可选棋书上下文参数；现有调用默认不传，当前生产API行为不变。
- 原有硬事实、路线、颜色、引用和主动权门禁全部保留；书例ID不得成为当前局面证据引用。
- 选取6个未参与Phase 7D人工审核的留出局面，使用相同事实包、相同模型和温度完成真实DeepSeek A/B。两组均为4题通过校验、2题安全回退，运行错误0。
- 已生成3:3平衡且隐藏答案的可视棋盘盲审页`docs/research/phase7e-deepseek-book-ab-review-boards.html`。人工答案未进入Prompt；当前停在6题人工审核门禁，完成前不接入生产链。
- Phase 7E人工盲审已完成：基线胜1、棋书上下文胜0、相同3、两边都不安全2；平均棋理相关性同为2.33，棋书上下文自然度由3.00降至2.33。生产门禁未通过，棋书上下文保持关闭。
- 原始结果：`docs/research/phase7e-deepseek-book-ab-results.json`；人工裁决：`docs/research/phase7e-deepseek-book-ab-adjudication.json`；阶段报告：`docs/research/chess-analysis-phase7e-book-context-ab-report.md`。

### Phase 7F 分析目标选择

- `PositionInterpretationPackage`已增加程序控制的局面阶段和首要分析目标，覆盖胜势兑现、攻势兑现、残局计划、动态平衡、强制战术、着法质量解释和普通战略改善。
- 王位置、易位权和易位历史未知不再自动成为王安全战略主题；候选路线内部战术不再自动成为当前评价来源。
- 6个人工反馈局面的结构目标已经分别收敛为：CS-01胜势兑现、CS-04残局计划、BB-04强制战术、CC-029攻势兑现、CF-03着法质量解释、CF-06动态平衡。
- 同6题真实DeepSeek A/B已完成：修改前2题安全回退，修改后3题安全回退。自动观察存在局部改善但回退增加。
- 本轮没有使用棋书上下文，人工答案没有进入Prompt，生产API尚未启用新棋书检索。
- 项目负责人随后终止这类逐题主观A/B审核；Phase 7F页面保留为研究证据，但不再等待人工导出，也不作为生产门禁。

### Phase 7G 棋书原局面真值层

- 项目负责人决定：棋书原文直接作为其完全相同源局面的标准人类解释，不再进行逐题主观A/B审核。
- 新增`BookGroundTruthRepository`；精确匹配同时要求棋子摆放、行棋方、易位权和吃过路兵状态一致，半回合与完整回合计数忽略。
- 正式真值集包含326条原评、325个不同完整局面状态：BB 228、CC 42、CF 56；157,536个原评字符。
- 228条书中标注着全部通过python-chess合法性验证，非法标注着0条。
- 相似局面迁移被明确关闭；无精确命中时返回空结果，不把相似棋书原文当成正确答案。
- 56条CF数据保留`legal_board_fen_audit_pending`来源状态；按当前决策进入标准集，但不伪装成已人工逐图核验。
- 真值数据：`docs/research/phase7g-book-ground-truth-dataset.json`；清单：`docs/research/phase7g-book-ground-truth-manifest.json`；报告：`docs/research/chess-analysis-phase7g-book-ground-truth-report.md`。
- 当前只建立了可追溯真值层，尚未提交、部署或接入生产API。

### Phase 7H 棋书真值自动基准与最小接入

- 326条棋书原评精确查回为326/326；228条书中标注着全部合法。
- 程序任一候选因素覆盖为101/112（90.18%），但第一主题仅54/112（48.21%），首要分析目标52/112（46.43%）；当前真正薄弱点是重要性排序。
- `/api/professional-analysis`已增加`bookReferences`，仅在棋子、行棋方、易位权和吃过路兵状态完全一致时返回棋书原评。
- 前端将“棋书原评（精确局面）”优先显示；DeepSeek不读取或重写原文，DeepSeek失败也不影响已命中原评。
- 研究SQLite缺失时自动读取正式Phase 7G JSON真值集；未命中仍返回空结果，不进行相似局面迁移。
- 完整测试：`262 passed, 2 warnings`；未提交、未推送、未部署。
- 报告：`docs/research/chess-analysis-phase7h-book-ground-truth-benchmark-report.md`；结果：`docs/research/phase7h-book-ground-truth-benchmark-results.json`、`docs/research/phase7h-book-ground-truth-benchmark-summary.json`。

### Phase 7I 公版棋书全自动扩容

- 通过Classic Chess公开annotated-book API导入9本公版经典棋书，处理457盘带注释棋局。
- 新增4,695条原始棋书评注，下载错误0，SAN/UCI、行棋方、回合定位和python-chess合法性错误0。
- 正式真值集从326条扩大到5,021条，覆盖4,848个完整局面状态，原评总字符数920,603。
- 运行时已切换到`phase7i-book-ground-truth-dataset.json`，研究SQLite与正式JSON合并去重，并建立完整局面键索引。
- 棋书原文仍不进入DeepSeek Prompt，不迁移到相似局面，不覆盖python-chess和Stockfish事实。
- 完整测试：`267 passed, 2 warnings`；页面内联JavaScript语法通过，`git diff --check`无内容错误。
- 报告：`docs/research/chess-analysis-phase7i-public-domain-book-expansion-report.md`；清单：`docs/research/phase7i-book-ground-truth-manifest.json`。

### Phase 8A 开局路径与残局来源扩展

- 已从 Lichess `chess-openings` CC0 数据建立独立开局路径目录：3,810 条全部通过 `python-chess` 逐着合法性验证，拒绝 0 条；覆盖 500 个 ECO 编码、149 个开局家族，最长 36 个半回合。
- 每条开局记录保留完整体系名、变例层级、PGN、SAN、UCI、终点 FEN、完整局面键、父分支和转置关联字段；DeepSeek 不参与开局命名和走法恢复。
- 当前 5,021 条棋书原评中按现有阶段规则归为残局的只有 542 条，七子及以下只有 38 条，残局覆盖确实不足。
- 已审计并下载研究副本：`Chess Openings Ancient and Modern`、`Synopsis of Chess Openings`、`Chess Studies; or, Endings of Games`、`Chess: Theory and Practice`；均为旧式记谱或图像棋盘，尚未通过 FEN/行棋方/解答合法性门禁，不强行进入正式真值库。
- Project Gutenberg 三子与四子将杀数据分别有 580 和 551,739 个合法 FEN，但全部为已经将杀的终局，只适合终局识别测试，不作为解释知识数量灌入。
- 开局目录仍是离线知识数据，尚未接入生产 API、前端或 DeepSeek Prompt；残局下一步需要独立真值结构并用 Syzygy 校验七子及以下结论。
- 产物：`docs/research/phase8a-opening-path-catalog.json`、`docs/research/phase8a-opening-path-manifest.json`、`docs/research/chess-analysis-phase8a-opening-endgame-corpus-report.md`。

### Phase 8B 开局只读识别层

- 新增 `OpeningKnowledgeRepository` 与 `POST /api/opening-lookup`，支持使用 PGN、精确 FEN 或二者一致性校验查询开局身份。
- 返回 ECO、完整体系名、家族、变例路径、标准 PGN/SAN/UCI、匹配深度和后续命名分支；匹配类型区分完全路线、最长命名前缀、转置局面、精确 FEN和未命中。
- PGN 主线和每一步合法性由 `python-chess` 验证；PGN终点与同时提交的FEN不一致时返回422。
- 开局接口不调用Stockfish或DeepSeek，不判断路线好坏，不把历史开局路线表述为当前最佳着或优势来源。
- 2.5 MB压缩运行目录位于`app/data/opening-path-catalog.json`，会随Docker中的`app/`复制进入部署环境，不依赖本地研究目录。
- 阶段报告：`docs/research/chess-analysis-phase8b-opening-lookup-report.md`。

### Phase 8C Kling/Horwitz 残局图盘恢复门禁

- 已下载并拆分1851年 `Chess Studies; or, Endings of Games` EPUB：共185张JPEG，其中绝大多数是单独裁切的残局棋盘。
- 使用MIT许可的本地 `fenshot` ONNX模型进行离线棋盘识别；针对原书斜线底纹和装饰外框增加了多边界候选及置信度仲裁，图片不上传外部服务。
- 185张中183张生成了书本边界候选；严格门禁要求双王完整、棋盘至少对一个行棋方合法、最低格置信度不低于0.70、平均置信度不低于0.90。
- 只有16张通过严格门禁；其余169张全部拒绝进入正式残局库。第一题已正确恢复为黑王d8、黑兵d7、白王d6、白兵d2。
- 已生成16题左右对照人工审核页`docs/research/phase8c-kling-endgame-review-boards.html`；左侧为原书棋盘，右侧为Unicode棋子恢复棋盘，只审核棋子格子、颜色和种类，不审核行棋方、胜负或解答。
- 本轮人工审核通过后，审核过的同版式棋子图可作为模板继续恢复剩余低置信度图；未经审核的数据不会进入`EndgameKnowledgeRepository`。
- 第一批16题审核已导出：13题按页面显示通过，KH-005、KH-012、KH-016被标为错误。裁决确认前两类根因分别为错误旋转180度和候选择优漏子；三题修正后均通过python-chess合法棋盘检查。
- 已保留每张图的20个边界候选，并用第一批审核结果校准本书固定版式的棋盘边界选择。16题逐题留一恢复为16/16，但样本仍小，只作为进入下一人工门禁的依据。
- 183张有候选的图片中，校准后43张具备双王且至少对一个行棋方合法；排除第一批后，12张通过第二批平均置信度、候选分差和合法性门禁。
- 第二批审核页为`docs/research/phase8c-kling-round2-review-boards.html`；完成前不恢复解答、不写入正式残局知识库。
- 第二批12题已全部通过人工棋盘审核；两轮共28个棋盘均已裁决。
- 图注与结果恢复后，20题同时具备明确行棋方、原书结论和七子表一致性，并保留合法关键首着；3题超过七子，仅由Stockfish Depth 20/MultiPV 3支持；4题缺少行棋方或完整目标；KH-007原书称白先胜但Syzygy精确判和。
- 新增只读`EndgameKnowledgeRepository`与`POST /api/endgame-lookup`。运行时只导出20个`exact_verified`局面，必须棋子摆放与行棋方完全一致；不返回相似局面、冲突题、缺失题或仅有普通引擎支持的题。
- 残局查询不调用Stockfish或DeepSeek，尚未接入专业分析Prompt或前端。完整研究数据为`docs/research/phase8c-kling-endgame-dataset.json`，运行时数据为`app/data/endgame-knowledge.json`。

### Phase 8D 多棋书七子残局扩充

- 从现有棋书真值集筛出38个七子以内候选，并逐题使用Lichess Syzygy重新验证胜和负、距离字段和关键首着。
- 33个候选通过准确性与教学内容门禁，5个源范围冲突、书库冲突或无有效教学内容的候选保留在研究审计中但不进入产品查询。
- 与Phase 8C的20题合并后，`app/data/endgame-knowledge.json`现有53个精确残局局面，来源扩展到Kling/Horwitz及另外8本棋书。
- 运行时结构升级为多来源记录；每条可独立保存书名、作者、年份、定位、来源链接、表库关键着和书中着。旧版单来源数据仍兼容。
- 棋书原评仍只绑定完全相同局面；胜和负及程序关键着由Syzygy负责，DeepSeek不参与硬事实生成。
- 《残局教科书》已列为下一优先来源；当前尚未取得实际电子文件，取得后使用同一门禁导入，不从非授权网页抓取正文。
- 报告：`docs/research/chess-analysis-phase8d-endgame-expansion-report.md`；审计：`docs/research/phase8d-endgame-expansion-audit.json`。

### Phase 8E 开局人类解释层

- 使用通过官方MD5校验的Wikimedia Wikibooks快照提取《Chess Opening Theory》；发现3,010个页面，2,948个页面标题可恢复为合法走法路径。
- 清除空页、纯谱表、模板、引用和导航后保留1,896段有实质内容的人类开局解释。
- 现有3,810条Lichess开局目录全部能命中同路径或最深祖先路径解释，覆盖149个家族；801条为完全同路径解释，平均匹配深度6.19个半回合。
- `/api/opening-lookup`新增可选`humanExplanation`，包含原文、匹配深度、来源页面、固定修订号、CC BY-SA/GFDL许可和署名。
- Lichess CC0名称/路径与Wikibooks人类解释保持独立；解释不提供当前评价或最佳着，也尚未进入DeepSeek Prompt或前端。
- 报告：`docs/research/chess-analysis-phase8e-opening-human-explanations-report.md`；数据：`docs/research/phase8e-opening-explanations.json`；运行时：`app/data/opening-explanations.json`。

### Phase 8E Wikibooks 开局解释研究数据

- 工作区现有未提交的Wikibooks `Chess Opening Theory`英文解释数据：60条合法走法路径解释，按最长前缀覆盖3,578/3,810条开局目录记录（93.91%）和135个开局家族。
- 每条解释保留页面标题、URL、修订ID、许可与署名；`OpeningKnowledgeRepository`只按已验证UCI路径精确或最长前缀查找，不让文本参与开局命名。
- 英文原文只描述其来源路径，不是当前Stockfish评价、最佳着、当前威胁或当前计划的事实来源；当前“双方子力与局面”不直接展示或发送这些原文。
- 运行时数据为`app/data/opening-explanations.json`；构建脚本与门禁测试分别为`scripts/build_phase8e_opening_explanations.py`和`tests/test_phase8e_opening_explanations.py`。

### Phase 8F 开局识别接入“双方子力与局面”

- 专业分析会使用当前选中节点及此前全部已验证UCI走法查询`OpeningKnowledgeRepository`；开局身份、ECO和变例完全由程序确定，DeepSeek不参与命名。
- 展示采用保守门禁：至少匹配4个半回合；最长前缀匹配还必须覆盖当前序列至少75%，且最多偏离2个半回合。浅层随机前缀不会触发开局卡片。
- 开局卡片嵌入现有“双方子力与局面”，不新增独立开局模块；未命中、低置信度或缺少受控中文资料时保持原有界面和生成逻辑。
- 当前受控中文资料覆盖意大利开局、西班牙开局、西西里防御、法兰西防御、卡罗-康防御、后翼弃兵、英国式开局和王印度防御；其他数据库名称不会为了扩大显示率而自由生成说明。
- 已确认开局作为`confirmedOpening`传入专业分析Prompt；Prompt禁止DeepSeek重新判断名称、补写变例，或把开局常见思路直接升级为当前局面的事实、计划、评价或威胁。
- 根目录`index.html`与发布版`docs/index.html`保持同步；开局卡片在手机布局下改为单列，避免横向溢出。
- 完整单元测试为`327 passed, 2 warnings`；15局面真实DeepSeek质量套件首次通过与最终严格通过均为15/15、安全回退0/15；另有1个携带“意大利开局 · 吉奥科钢琴变化”的真实请求首次通过且严格校验错误为0。

### 当前未提交的业务相关修改

已修改：

- `app/api.py`
- `app/analysis_focus.py`
- `app/analysis_report.py`
- `app/models.py`
- `app/opening_knowledge.py`
- `app/position_facts.py`
- `app/professional_analysis.py`
- `app/professional_refs.py`
- `app/professional_validation.py`
- `index.html`
- `docs/index.html`
- `docs/PROJECT_STATUS.md`
- `scripts/build_professional_validation_set.py`
- `scripts/run_professional_quality_suite.py`
- `tests/fixtures/professional_validation_positions.json`
- `tests/test_position_facts.py`
- `tests/test_opening_knowledge.py`
- `tests/test_professional_analysis.py`
- `tests/test_professional_frontend.py`
- `tests/test_professional_quality_set.py`

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
- `app/position_facts.py`：子力、王安全、兵形和威胁事实
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

### 2026-07-26：完整删除关键棋子功能

- 已从 `PositionFacts`、专业分析公开模型、DeepSeek 草稿 Schema、引用目录、Prompt、resolver、validator、fallback、长度裁剪、质量脚本和测试数据中删除关键棋子字段及生成逻辑。
- `/api/professional-analysis` 的 `analysis` 不再返回 `keyPieces`；Prompt 版本升级为 `professional-v10-without-key-pieces`，避免旧内存缓存与新协议混用。
- 根目录与发布目录前端已删除专用读取、转换和栏目渲染；剩余主要顺序为当前局面、最大危险/威胁、实战走法、双方计划和候选路线，不新增替代栏目。
- 完整自动化测试为 `174 passed, 2 warnings`；内联 JavaScript 语法、页面同步、真实 PGN 导入/整盘分析/回放和专业分析接口浏览器流程均通过。
- 15 局面真实 DeepSeek 套件：首次通过 `15/15`，最终通过 `15/15`，重试 `0/15`，安全回退 `0/15`；输入 Token `3,569—6,261`，首次网络响应 `10.142—18.801` 秒，缓存 `6ms`。本次结果写入系统临时目录，没有覆盖仓库内历史质量报告。
- Stockfish、ChessFactPackage、Threat Analysis、Strategic Plan、AnalysisReport、候选路线、局面分析和实战走法分析均保留；未提交、未部署。

### 2026-07-30：Phase 5B 棋书局面解释试验

- 完成三本 Project Gutenberg 棋书的来源、可抽取内容和境外权利边界核验；它们只被确认为美国公版来源，中国及其他产品发行地区的商业再利用权仍需正式复核。
- 抽取 20 个内部研究局面：`Chess Strategy` 8 个 ASCII 棋盘图、`Chess Fundamentals` 6 个旧式描述记谱局面、`The Blue Book of Chess` 6 个 PGN 注释局面。
- 20 个 FEN 与引用走法均通过 `python-chess` 合法性检查；使用 Stockfish 18、深度 16、MultiPV 3 复核评价与候选着。
- 初步裁决为：10 个高度一致、7 个有条件一致、3 个明确分歧。该结果证明棋书适合作为人类解释模式来源，但不能未经引擎和人工裁决直接作为当前局面的事实真值。
- 研究材料保存在 `docs/research/phase5b-source-inventory.md`、`docs/research/phase5b-pilot-findings.md` 和 `docs/research/phase5b-pilot-positions.json`。
- 本阶段没有修改 `app/`、前端、API、Prompt 或测试，没有实现知识库、RAG、Agent，也没有调用 DeepSeek 生成金标准。

### 2026-07-30：Phase 5C 古今棋书候选样本扩展

- 新核对 6 本古今棋书；从 4 本书中恢复 18 个候选局面，其中古典教材 7 个、现代出版社官方样章 11 个。
- 18 个 FEN、行棋方和标注 SAN/UCI 均通过 `python-chess` 合法性检查，覆盖评价来源、战术威胁、王安全、兵结构、子力协调、空间、开放线、关键计划、最差棋子和长期弱点 10 个受控维度。
- Stockfish 18 初筛改为“100,000 节点发现候选，再对每个根着分别使用 250,000 节点复核”，避免直接比较 MultiPV 与单着搜索造成的资源分配误差。
- 初筛结果为 12 个方向一致、4 个需要收窄强度或唯一性措辞、2 个存在引擎张力；全部保持 `reviewStatus: pending`，没有把引擎结果冒充人工裁决。
- Philidor 1803 年古式记谱和旧易位说明、Franklin K. Young 的抽象战略理论暂不自动转成局面样本，避免猜测 FEN 或错误配对理论。
- 研究材料保存在 `docs/research/phase5c-expanded-source-inventory.md`、`docs/research/phase5c-expanded-findings.md` 和 `docs/research/phase5c-candidate-positions.json`。
- 本阶段只新增研究文档和候选数据，没有修改 `app/`、前端、API、Prompt、测试或运行时行为，也没有建设知识库、RAG、Agent 或模型训练流程。

### 2026-07-30：Phase 5C 第一轮人工审核

- 项目负责人完成 18 个棋书候选局面的第一轮人工审核：15 个通过，第 4、5、6 题（`PCT-01`、`PCT-03`、`GRO-01`）要求深入分析。
- 三个待深入局面使用 Stockfish 18、Threads 1、Hash 256 MB，对每个指定根着分别运行 3,000,000 节点，避免不同着法获得不同搜索预算。
- 深入结果：`PCT-01` 的棋理方向成立，但 `...Qe7` 只比最佳防守约差 0.26 兵；`PCT-03` 的 `...Qb8` 约损失 0.61 兵，属于战略不精确但“严重错误”措辞偏重；`GRO-01` 的更深结果支持 `Na3` 略强于实战 `Ra2`，两着均保持明显胜势。
- 项目负责人已经接受三题的深入分析建议：`PCT-01` 和 `PCT-03` 收窄错误强度后通过，`GRO-01` 按“Na3 可能更强”通过；本批 18 个局面全部完成审核。
- 人工审核记录保存在 `docs/research/phase5c-human-review-results.json`，深入分析保存在 `docs/research/phase5c-deep-analysis-456.md`。
- 本轮仍只修改研究资料，没有修改业务代码或运行时行为。

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
- 栏目动态出现，可能包含：当前局面、最大危险、双方计划、弱点、王安全、实战走法和三条 Stockfish 路线。
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

## 9. 仅讨论过但尚未实施的想法

- 在专业分析之后增加独立儿童化翻译流程。
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

## 12. 2026-07-31 Phase 5D 棋书局面基线评测

Phase 5D 已完成评测、诊断和架构建议，未修改业务代码。

评测输入：

- `docs/research/phase5c-candidate-positions.json`
- `docs/research/phase5c-human-review-results.json`
- `docs/research/phase5c-deep-analysis-456.md`
- 18 个局面均已由项目负责人完成最终审核。

真实运行：

- Stockfish 18，Depth 10，Threads 1，Hash 32 MB，MultiPV 3。
- 当前配置模型 `deepseek-v4-flash`。
- 18/18 局面完成 PositionFacts、ThreatAnalyzer、StrategicPlanAnalyzer、Professional Analysis、Analysis Report 和 DeepSeek 两条文本链。
- 运行错误 0。
- Professional Analysis 首次校验失败后重试 2 题，最终安全回退 0 题。
- Analysis Report 安全回退 4 题。
- 棋书原文和人工答案没有进入模型 Prompt。

质量结论：

- 严格棋理通过 0/18。
- 核心棋理较完整复现 3/18，部分触及 10/18，遗漏或方向相反 5/18。
- 战略主题遗漏 15 题，最终文本棋盘/走法事实错误 13 题，战术判断错误 7 题，错误计划 7 题，无证据主动权判断 2 题。
- 当前真正缺少的是位于事实/路线与 DeepSeek 之间的程序化“局面解释层”，而不是知识库、RAG、训练或 Agent。

Phase 5D 文件：

- 正式规则：`docs/research/phase5d-evaluation-rubric.json`
- 原始真实输出：`docs/research/phase5d-baseline-raw-results.json`
- 紧凑证据：`docs/research/phase5d-compact-evidence.json`
- 逐题裁决：`docs/research/phase5d-adjudication.json`
- 最终报告：`docs/research/chess-analysis-phase5d-baseline-report.md`
- 可重复运行脚本：`scripts/run_phase5d_baseline.py`
- 证据压缩脚本：`scripts/summarize_phase5d_results.py`

Phase 6 建议优先级：

1. 隔离当前威胁与 PV 内部事件。
2. 新增最小 `Position Interpretation Package`，输出评价来源、主题优先级、计划因果、措辞边界和禁止推断。
3. 物质、王位置、易位、是否首选等字段改用程序受控措辞。
4. 为“主动权”增加动态证据门禁。
5. 再补弱格、空间、预防、交换目的、最差棋子目标和计划时机检测。

本阶段只提出 `app/position_interpretation.py` 的建议输入输出结构，没有创建或修改该业务模块。

## 13. 2026-07-31 Phase 6A 威胁作用域与硬事实保护

Phase 6A 已完成最小业务代码修改，未提交、推送或部署。

已实现：

- `current_direct_threat`、`prepared_threat`、`route_event`三类威胁作用域。
- PV后续普通吃子、将军、交换和升变不再自动成为当前全局威胁。
- 准备型威胁必须有明确准备着、执行着在忽略应对后仍合法，并通过Ignore Test。
- 物质差、王位置、易位边界、评价方向、走法质量及首选一致性改由程序模板控制。
- Stockfish分数不再直接推出主动权；主动权需要当前动态威胁和至少两条强制应对路线。
- 事实不足时initiative为`unknown`，DeepSeek不得输出主动权结论。

验证结果：

- 完整pytest：`186 passed, 2 warnings`。
- 18题真实套件：18/18完成，运行错误0。
- 严格通过：2/18；Phase 5D基线为0/18。
- 棋盘事实错误：13→0。
- 战术判断错误：7→2。
- 无证据主动权：2→0。
- 战略主题遗漏：15→15。
- 错误计划：7→7。
- Analysis Report安全回退：4→15。
- CC-035、CC-036、GEL-03的PV内部吃子均只保留为`route_event`。

Phase 6A文件：

- 最终报告：`docs/research/chess-analysis-phase6a-validation-report.md`
- 原始真实输出：`docs/research/phase6a-validation-raw-results.json`
- 紧凑证据：`docs/research/phase6a-compact-evidence.json`
- 逐题裁决：`docs/research/phase6a-adjudication.json`

下一阶段可以进入Phase 6B，但范围应限定为战略主题识别和计划门禁。不要增加知识库、RAG、训练或Agent；不要继续用Prompt承担程序尚未识别的棋理。

## 14. 2026-08-02 Phase 6D 人类局面样本候选与可视审核

- 已整理 32 个不与 Phase 5C 既有 18 题重复的候选局面，保存在 `docs/research/phase6d-human-case-library-candidates.json`。
- 32 个 FEN 均通过合法性检查；全部保持 `pending_human_review`，尚未作为事实真值、Prompt 示例或训练数据使用。
- 已生成 `docs/research/phase6d-human-case-review-boards.html`，使用项目现有透明棋子图片直接显示棋盘，并支持着法高亮、来源筛选、通过/修改/删除、审核备注和 JSON 导出。
- 页面生成脚本为 `scripts/build_phase6d_board_review_page.py`；本步骤没有修改业务分析链、Prompt、模型或 API。
- 第一轮人工审核已导出并归档到 `docs/research/phase6d-human-review-results.json`：20 题通过、9 题需要修改、2 题删除、1 题未审核（`BB-05`）。
- 审核处理清单见 `docs/research/phase6d-human-review-summary.md`。需要修改、删除和未审核的局面尚未进入正式样本库。
- 第二轮处理已完成：20题归入 `phase6d-approved-human-cases.json`，2题保持删除，10题进入 `phase6d-round2-review-candidates.json`。
- 对CS-06、CF-03、CF-06、BB-01、BB-05、BB-06、CC-037使用Stockfish 18执行同预算深搜：候选发现250,000节点，每个候选根着单独3,000,000节点。
- 关键收窄：CF-03的Nd7仅约损失0.46兵；CF-06的Rb1仅约损失0.14兵；CC-037的h6仅约损失0.19兵；BB-06的Qe7约损失1.69兵；BB-05的Bb3比次选约好1.60兵。
- 第二轮可视页面为 `docs/research/phase6d-round2-review-boards.html`，只含10个待复核局面；完整说明见 `phase6d-round2-analysis-report.md`。

## 15. 2026-08-04 Phase 7B 多棋书人工评注检索库

- 项目负责人明确将第二步调整为多棋书人工评注检索库；该决定替代此前“本阶段不建知识库/RAG”的研究边界，但仍不允许棋书文字覆盖当前局面的程序事实和Stockfish证据。
- 已登记并下载8本Project Gutenberg美国公版棋书；原始全文和图像只保存在被忽略的`work/research_books/`，不进入Git或生产包。
- 已建立离线SQLite语料库`work/research_books/phase7b-book-corpus.sqlite3`：326条人工评注局面、323个不同棋盘；BB 228、CC 42、CF 56，FTS5全文索引可用。
- 新增《Chess Fundamentals》自动抽取：61个FEN辅助索引条目中57个通过python-chess合法性检查，最终56个同时具有稳定原文范围并进入数据库；FEN逐图盲对照仍待完成。
- 已下载《Chess Strategy》167张原始棋盘并测试MIT许可的书籍棋盘识别器；仅1张达到严格可靠阈值、11张满足双方各一王，低置信结果全部排除，没有为了扩量放松合法性门禁。
- 新增离线`BookCorpusRetriever`，按子力、兵线、王区、阶段、行棋方、棋子粗区域和程序主题返回最多5条原评；Prompt载荷带来源、长度上限和“不得复制棋子、走法、评价”的硬边界。
- 检索接口尚未接入DeepSeek生产链。下一步必须先完成50–100条可验证棋理规则，再为326条语料补Stockfish/规则/威胁作用域标签并做排除同盘、排除同书的盲检索评测。
- 完整pytest：`218 passed, 2 warnings`；新增5个抽取、数据库和检索测试全部通过。
- 完整报告：`docs/research/chess-analysis-phase7b-book-corpus-report.md`；语料清单：`docs/research/phase7b-book-corpus-manifest.json`。

## 16. 2026-08-05 Phase 7J 棋理重要性排序层

- 已为 Phase 7I 的 5,021 条棋书原评建立离线主题标签；1,700 条能提取明确主题，3,321 条短评、纯变例或无明确主题的原评保持 `unknown`，不进入主题准确率分母。
- 数据按整本书划分：校准集 2,703 条、盲测集 2,318 条；同一本书不会同时进入两边。
- 已对 4,848 个唯一局面运行 Stockfish 18 Depth 8 MultiPV 3、事实、威胁、棋理规则、战略计划、因果排序和当前 PositionInterpretation；另完成 4,843 个不同书中着法后局面的评价，运行错误 0。
- 新增实验版 `PositionImportanceRanker`，只重排可靠程序信号，输出第一主题、辅助主题、证据、可信度和禁止结论；棋书原文、相似局面文本和 DeepSeek 输出都不是排序器输入。
- 整书盲测修改前 PositionFactorRanker 为 Top-1 35.45%、Top-3 76.37%；实验排序器校准后为 Top-1 44.63%、Top-3 76.86%。
- 错误计划、评价强度越界、无证据主动权和 PV 路线事件升级均为 0；事实、路线和主动权门禁没有放松。
- 盲测未达到 Top-1 70%、Top-3 90% 的生产门槛，因此排序器保持离线，未接入 PositionInterpretation、API 或 DeepSeek Prompt。
- 完整 pytest：`276 passed, 2 warnings`。本阶段未训练 DeepSeek、未使用 Agent、未提交、未推送、未部署。
- 报告：`docs/research/chess-analysis-phase7j-importance-ranking-report.md`；结果：`docs/research/phase7j-importance-ranking-results.json`；摘要：`docs/research/phase7j-importance-ranking-summary.json`。

## 17. 2026-08-06 Phase 7K 结构化棋书视角选择先导实验

- 新增两段式棋书视角选择：独立DeepSeek调用可以读取最多5条候选原评，但只能输出案例ID、五类主题、固定原因码和可信度；最终指导包不含原文、来源FEN、棋步、评价、胜负结论或案例ID。
- 精确局面库仍保留全部5,021条原评；相似视角选择只使用1,700条具有明确可追溯主题的案例，3,321条主题不明确原评不强行参与相似迁移。
- 对Phase 7J旧盲测书籍进行五主题各10题、共50题真实DeepSeek诊断；候选只来自校准书籍，同书泄漏0，精确同盘候选0。由于旧盲测结果已公开，本轮仅为诊断，不是新的生产门禁。
- 当前PositionImportanceRanker为Top-1 26%、Top-3 76%；检索第一案例为20%/34%；DeepSeek选择器为24%/56%；程序第一主题加DeepSeek辅助主题为26%/78%。
- DeepSeek修正4个程序第一主题错误，同时破坏5个原本正确结果，净效果为负；2题纠错重试后仍违反“主题必须由所选案例支持”，均被安全拒绝。
- 选择输出保持枚举化；来源原文、棋步和评价进入最终指导包均为0。没有接入PositionInterpretation、API或正式DeepSeek Prompt。
- 完整pytest：`282 passed, 2 warnings`。未提交、未推送、未部署。
- 报告：`docs/research/chess-analysis-phase7k-structured-book-perspective-pilot-report.md`；结果：`docs/research/phase7k-perspective-selector-diagnostic-results.json`。

## 18. 2026-08-06 Phase 7L 主题级因果重要性

- 新增`ThemeCausalImportanceAnalyzer`，按主题聚合全部MultiPV覆盖、稳定路线覆盖、替代路线评价损失、静态主题根着后持续性和当前直接威胁证据；输出`confirmed/supporting/unproven`、因果分数和禁止结论。
- 修正两组主题语义映射错误：`evaluation.tactical/king_safety/structure/activity_source`不再统一归入转换主题；最差棋子、开放线、中心突破、王安全等`plan.*`规则按真实主题归类。主动权门禁和跨主题预防规则不参与主题排序。
- 复用Phase 7J的4,848个真实Stockfish证据包运行5,021条记录，错误0；不调用DeepSeek，不读取棋书原文作为排序输入。
- 旧盲测诊断中，Phase 7J排序为Top-1 44.63%、Top-3 76.86%；修正映射的未校准排序为35.84%/77.73%；主题因果分数直接排序为27.54%/70.80%。
- 修正映射后王安全、兵结构、子力活动的第一主题命中分别提高到15.95%、45.21%、51.53%，但强制战术从81.29%降到44.52%；说明主题更均衡但仍不会稳定选择作者第一重点。
- 因果证据共799个confirmed、14,902个supporting、2,105个unproven。该层适合未来控制结论强度，不适合直接替换第一主题。
- 旧盲测已经公开，本轮不具备新生产门禁资格；未接入PositionInterpretation、API或正式Prompt。
- 完整pytest：`287 passed, 2 warnings`。未提交、未推送、未部署。
- 报告：`docs/research/chess-analysis-phase7l-theme-causal-importance-report.md`；结果：`docs/research/phase7l-theme-causal-benchmark-results.json`；摘要：`docs/research/phase7l-theme-causal-benchmark-summary.json`。

## 19. 2026-08-06 MVP专业分析稳定化

- 当前工作从继续扩展研究模块切换到最小可用产品收敛；Phase 7J、7K、7L实验模块仍未接入生产API或DeepSeek Prompt。
- 专业分析新增程序专属结论整句保护：DeepSeek在自由文本中重写物质、王位置、易位、评价方向、走法质量、首选一致性或主动权时，违规整句会被删除或用中性完整句重建；严格校验规则没有放宽。
- 专业分析缓存版本已更新，避免旧安全回退继续命中；真实质量套件可以从项目根目录直接运行。
- 15局面真实Stockfish + DeepSeek套件由首次通过5/15、安全回退8/15，改善为首次通过15/15、重试0、安全回退0；最终严格通过仍为15/15。
- 本轮记录29处整句边界保护；三条路线、最大危险证据、双方计划证据均为15/15有效，最终校验错误0。
- 完整pytest：`288 passed, 2 warnings`；`/api/health`返回200，Stockfish可用、DeepSeek配置有效；开发版与发布版前端规范化内容一致。
- 当前可以进入5—10盘页面体验验收。提交、推送和部署仍需项目负责人另行授权。
- 报告：`docs/research/chess-analysis-mvp-readiness-report.md`；真实结果：`docs/professional-analysis-quality-results.json`、`docs/professional-analysis-quality-report.md`。
- 按产品负责人最终页面审核意见，开发版和发布版继续保留“小兵研究员说”专业分析面板及`/api/professional-analysis`调用，并删除面板内“当前局面分析”摘要卡片；其余展示范围以后续“结论模式”规则为准。
- 后续页面审核将专业分析前端进一步收敛为“结论模式”：只显示明确威胁、双方计划、王安全、值得关注的弱点，以及精确命中的棋书原评；不显示最大危险推导、实战走法分析过程、候选路线展开、PV、评价变化说明、过程性后果和原始JSON。无有效结论时整个专业面板自动隐藏；后端完整分析结果与严格校验不变。

## 20. 2026-08-10 7天公测版

- 当前产品本来没有登录或注册门槛；页面现已明确标注“公测体验版 · 免费使用”，并同时支持粘贴PGN和选择本地`.pgn`文件，完整Stockfish与DeepSeek流程不降级。
- 新增匿名Analytics：浏览器用`localStorage`保存随机`visitor_id`；后端SQLite记录访客首次/最后访问、设备摘要、来源，以及`page_view`、`upload_pgn`、`analysis_start`、`analysis_complete`事件。没有新增个人信息字段，也不持久化IP。
- 新增`analysis_logs`，记录PGN长度与哈希、走子数、Stockfish/DeepSeek/总耗时、Prompt/Completion/总Token和成功/失败状态；Analytics写入失败不会中断核心棋局分析。
- 新增`GET /api/admin/statistics`，生产环境通过`X-Admin-Key`访问；返回当日访客、上传、分析、成功率、平均耗时、Token和可选成本估算。成本单价通过环境变量配置，默认不猜测价格。
- 新增仅针对异常流量的内存保护和DeepSeek全局突发保护；前端不展示IP、Token或成本限制。棋谱上限统一为100个完整回合（200个半回合），Stockfish深度、MultiPV、分析局面与DeepSeek Prompt未降低或修改。
- 完整pytest：`336 passed, 2 warnings`；两个前端内联JavaScript语法通过；开发版与发布版SHA-256一致；实际启动FastAPI后`/api/health`、`/api/event`和`/api/admin/statistics`分别返回200、202、200；Edge无头浏览器的桌面、平板、手机视口均无横向溢出、控制台错误或资源加载错误。
- 15局面真实Stockfish + DeepSeek质量套件已重跑：首次校验通过15/15、最终严格通过15/15、安全回退0/15、三条路线15/15有效，缓存响应312ms；正式结果和报告已刷新。
- 当前SQLite路径可通过`ANALYTICS_DB_PATH`配置。Render生产环境如需跨重启保留完整7天数据，部署前仍需确认该路径位于持久卷；当前仓库没有擅自升级付费实例或挂载付费磁盘。
- 公测能力现已在首页显式展示为“无需注册、完整分析、免费公测”三项，并提供匿名数据说明；通过`file:///`打开时会显示文件预览警告并停止后端健康检查，提示改用`http://localhost:8080`。HTTP桌面、平板、手机视口均显示新说明且无横向溢出或控制台错误。

## 21. 2026-08-11 独立商业化运营后台

- 新增独立`admin.html`与发布副本`docs/admin.html`；产品首页没有后台入口，也不展示Token、成本、异常保护或生产配置。管理员密钥只保存在当前标签页的`sessionStorage`，不会写入页面或仓库。
- 新增`GET /api/admin/dashboard`，支持按日期读取访问人数、页面访问、上传成功/失败、分析次数、转化率、成功率、Stockfish/DeepSeek耗时、Prompt/Completion/总Token、估算成本、最近50条匿名分析日志、保护规则和生产配置状态。
- 原`GET /api/admin/statistics`保持兼容并增加日期参数；生产环境的两个管理员接口均继续通过`X-Admin-Key`保护。
- 为降低7天公测固定成本，Analytics存储已支持本地SQLite与生产Postgres双后端；Render Blueprint保持免费Web实例并新增免费`pawnlab-analytics` Postgres，通过`ANALYTICS_DATABASE_URL`连接。管理员密钥自动生成，DeepSeek V4 Flash成本估算单价按2026-08-11官方价格配置为输入`$0.14/百万Token`、输出`$0.28/百万Token`。
- 后台专项测试`23 passed`；完整pytest`341 passed, 2 warnings`。Edge真实浏览器桌面与手机均显示10个指标卡和4组保护规则，无横向溢出、控制台错误或产品页链接。两个后台页面内联JavaScript语法通过，Render Free Web + Free Postgres YAML解析通过。
- 15局面真实Stockfish + DeepSeek质量套件：首次通过15/15、最终严格通过15/15、安全回退0/15，缓存响应410ms；说明后台与统计改动没有降低正式分析质量。

## 22. 2026-08-11 免费公测版生产发布

- 免费公测版已通过PR #12合并到`main`，生产提交为`8a3a9365f4537ea8f94ca9607df4fd9d8a18b31d`；GitHub Pages构建与部署成功。
- `https://pawnlab.cn/`和`https://pawnlab.cn/admin.html`均返回200；产品页已包含匿名`visitor_id`与“公测体验版”，后台页为独立运营页面。
- Render生产`/api/health`返回200，Stockfish可用且DeepSeek配置有效；生产OpenAPI已包含`/api/event`、`/api/admin/dashboard`和`/api/admin/statistics`，后台无密钥访问返回401。
- `render.yaml`已配置免费Web实例与免费Postgres。生产数据库是否已经实际绑定`ANALYTICS_DATABASE_URL`仍需在Render控制台确认；在确认前，不能把跨重启持久化描述为已验证。
- 为绕过`pawnlab.cn/admin.html`曾被旧前端缓存替换成产品首页的问题，FastAPI生产后端现直接提供`/admin`与`/admin.html`运营入口；页面响应为`Cache-Control: no-store`并同源读取`/api/admin/dashboard`。`https://ai-chess-review-api.onrender.com/admin`已验证返回200、包含管理员密钥输入框且无产品首页内容。
