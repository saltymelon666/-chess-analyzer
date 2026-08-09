# Chess Analysis Phase 8A Opening and Endgame Corpus Expansion Report

## 1. 结论

可以继续扩展，但必须把“开局路径库”“棋书原评库”和“残局真值层”分开，不能继续把所有材料混成一个相似局面文本库。

- 开局：已建立第一版结构化开局路径目录，收录 3,810 条开局与变例，覆盖全部 500 个 ECO 编码和 149 个开局家族。
- 中局与完整对局：继续保留现有 5,021 条棋书原评，只对完全相同的源局面负责。
- 残局：现有 5,021 条中只有 542 条被当前阶段判定规则归为残局，七子及以下只有 38 条，覆盖明显不足。
- 准确性边界：增加残局书可以改善“人类怎样解释”，但七子及以下的胜和负真值应由残局库验证，不能只相信旧书文字或 Stockfish 浅层分数。

## 2. 已完成的开局扩展

来源为 Lichess `chess-openings` CC0 数据。它提供 ECO、英文开局名称和标准 PGN 路径，并明确支持按命名局面反向识别和常见转置处理。

本轮导入结果：

- 输入 3,810 条，合法路径 3,810 条，拒绝 0 条。
- 500 个 ECO 编码。
- 149 个开局家族。
- 最长路径 36 个半回合。
- 每条记录包含：ECO、完整体系名、开局家族、变例层级、PGN、SAN、UCI、终点 FEN、完整局面键、父分支和转置关联字段。
- 所有谱着均由 `python-chess` 从初始局面逐着验证，DeepSeek 不参与命名或走法恢复。

产物：

- `docs/research/phase8a-opening-path-catalog.json`
- `docs/research/phase8a-opening-path-manifest.json`
- `scripts/build_phase8a_opening_catalog.py`
- `tests/test_phase8a_opening_catalog.py`

当前目录仍是离线知识数据，尚未接入生产 API 或前端，也没有进入 DeepSeek Prompt。

## 3. 棋书来源审计

### P0：可直接作为结构骨架

1. Lichess `chess-openings`
   - 用途：现代 ECO、体系名、变例名、标准 PGN/UCI 路径。
   - 权利：CC0。
   - 状态：已导入并完成合法性验证。

### P1：优先恢复的公版开局书

1. Edward Freeborough、Charles Ranken，*Chess Openings Ancient and Modern*
   - 内容：按开局和变例组织的大量历史路线，并讨论转置与开局原则。
   - 来源：Internet Archive 全文 OCR 和 EPUB。
   - 风险：旧式描述记谱与表格 OCR，必须逐着转换并通过合法性验证。
2. William Cook，*Synopsis of Chess Openings*
   - 内容：开局分支表和历史变例。
   - 来源：Internet Archive 全文 OCR。
   - 风险：旧式描述记谱、表格列可能错位；只能把成功还原的合法路线入库。
3. Howard Staunton、Robert B. Wormald，*Chess: Theory and Practice*（1876）
   - 内容：开局分析和残局专章。
   - 来源：Internet Archive 全文 OCR 和 EPUB。
   - 风险：部分棋盘只存在于图像，文字 OCR 不能单独恢复完整状态。

### P1：优先恢复的公版残局书

1. Josef Kling、Bernhard Horwitz，*Chess Studies; or, Endings of Games*（1851）
   - 内容：两百多个残局研究，按兵残局、象兵、马象、车兵、后兵等子力类别组织。
   - 来源：Internet Archive 全文 OCR 和 EPUB。
   - 状态：正文和目录可提取；棋盘图没有完整进入 OCR，必须先恢复 FEN。
2. Howard Staunton、Robert B. Wormald，*Chess: Theory and Practice*（1876）
   - 内容：残局规则、例题和解答，可补充基本残局解释。
   - 状态：与上项相同，先做图盘恢复，再校验行棋方与解答着。
3. Josef Kling，*The Chess Euclid*（1849）
   - 内容：200 个棋题和残局。
   - 价值：适合作为战术性残局和将杀题补充，不应替代理论残局主库。

### 不应作为主要残局知识的材料

Project Gutenberg 的 *Checkmates for Three Pieces* 和 *Checkmates for Four Pieces* 分别包含 580 和 551,739 个可解析合法 FEN，但这些局面全部已经将杀。它们适合测试终局识别，不包含“怎样赢、为什么赢”的过程，因此不应为了追求数量而灌入解释知识库。

## 4. 建议的数据结构

### 开局路径记录

```json
{
  "eco": "B90",
  "familyName": "Sicilian Defense",
  "variationPath": ["Najdorf Variation", "English Attack"],
  "sanMoves": ["e4", "c5"],
  "uciMoves": ["e2e4", "c7c5"],
  "terminalFen": "...",
  "parentOpeningIds": [],
  "transpositionOpeningIds": [],
  "sourceRef": "..."
}
```

识别时应同时支持：

- 按实战着法前缀匹配最长命名路线；
- 按完整局面键识别转置；
- 返回“家族 → 变例 → 子变例”的完整名称路径；
- 不把历史棋书对某一路线的旧评价当作当前 Stockfish 评价。

### 残局精确记录

```json
{
  "fen": "...",
  "sideToMove": "white",
  "materialClass": "KRPKR",
  "bookResult": "draw",
  "verifiedResult": "draw",
  "verificationSource": "syzygy",
  "keyMoves": [{"san": "...", "uci": "..."}],
  "solutionLine": [],
  "themes": ["opposition", "rook-behind-passed-pawn"],
  "referenceExplanation": "...",
  "sourceRef": "...",
  "conflictStatus": "none"
}
```

## 5. 残局入库硬门禁

每个残局必须依次通过：

1. 来源和权利边界登记。
2. 棋盘图恢复为 FEN。
3. 明确行棋方；无法确认时不得进入真值库。
4. `python-chess` 检查棋盘和每一步解答合法性。
5. 七子及以下使用 Syzygy 校验 WDL/DTZ；书中结论冲突时保留原评，但标记冲突，程序真值以残局库为准。
6. 八子以上使用 Stockfish 深度验证候选路线，但不得把单一分数伪装成理论胜和负。
7. 棋书解释只绑定精确源局面，不把原文迁移到相似残局。

## 6. 下一步优先级

1. P0：把已生成的 3,810 条开局路径接到后端只读识别层，先返回 ECO、体系名和最长匹配谱着，不接 DeepSeek。
2. P0：为残局增加独立的 `EndgameKnowledgeRepository` 和 Syzygy 验证字段。
3. P1：优先恢复 Kling/Horwitz 的两百多个残局图，逐题完成 FEN、行棋方和解答合法性验证。
4. P1：解析 *Chess Openings Ancient and Modern* 与 *Synopsis of Chess Openings*；只收录成功转换并合法的旧式记谱路线。
5. P2：再扩充公版大师对局评注；当前瓶颈不是一般中局评注数量，而是结构化开局和精确残局。

## 7. 不做的事情

- 不把 55 万个已经将杀的 FEN 当成“残局分析能力”。
- 不让 DeepSeek 猜开局名称、恢复旧式谱着或判断残局理论结果。
- 不把扫描 OCR 的残句直接写入正式库。
- 不因书中结论权威而跳过合法性、行棋方和残局库校验。
- 不把相似局面的棋书原文直接用于当前局面结论。
