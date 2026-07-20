# 专业棋局分析质量与性能报告

生成模型：`deepseek-v4-flash`。本报告不包含 API Key、Authorization 请求头或任何密钥内容。

## game1 基线问题

旧版复杂局面输入为 80,620 Token，首次响应 75,136ms；两次原始输出均失败并使用安全回退。失败类型包括：

- 第一次：`candidateLines[*].firstMove / continuationPhases[*].moves` 出现不属于三条 Stockfish 路线的 `Bxh7+`、`Qxc3`、`Qxh2+`；`playedMoveAnalysis.positiveEffects` 把实战走法写成未验证吃子；`weaknesses.white[*].evidenceRefs` 引用了错误一方；`mainDanger` 缺少来源格和目标格，且描述了事实包中不存在的将军；正文 2,259 字，超过复杂局面上限。
- 第二次：多个 `evidenceRefs` 使用不存在的 `centipawnLoss:103` 和 `fact:move-1-after:key:pv_key_piece:black:c7`；候选路线中出现 `Bxe7`、`Bxh7+`、`Qxh2+`、`Rxb7`、`Rxh6`；`keyPieces[*]` 声称存在局面前 FEN 中没有的 `white_bishop@g5`；`weaknesses.white[*].evidenceRefs` 黑白说反；再次描述不存在的将军；正文 2,260 字。
- 安全回退曾把结果 FEN 的一段 `p7` 误判成棋盘格；现已停止把结果 FEN写入正文，只保留结构化结果事实。

## 优化方案

- 棋子改用固定的 `keyPieces.white.pieceRef` / `keyPieces.black.pieceRef`。
- 候选路线只返回 `lineRef`；完整 PV 只返回已有 `plyRefs`，SAN、UCI、格子、棋子与结果局面由后端填充。
- 提示词仅发送一个当前 FEN、去重棋子/事实目录、实战走法和三条最多 10 半回合的路线；不发送 legalMoves、positionAfter、重复 evidence 字典、调试字段或整盘历史。
- 保持严格校验：未知 ID、事实包外格子、路线外 SAN、任何 UCI、黑白颠倒、缺证据结论仍会拒绝。
- 分别记录 DeepSeek 网络、校验和后处理耗时；保留缓存。

## 15 局面结果

| 局面 | 复杂度 | 首次通过 | 重试 | 回退 | 输入Token | 输出Token | 首次耗时ms | 关键棋子正确 | 最大危险有证据 | 双方计划有证据 | 三条路线有效 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| opening-1 | normal | 是 | 否 | 否 | 5159 | 1416 | 14243 | 是 | 是 | 是 | 是 |
| opening-2 | normal | 是 | 否 | 否 | 4526 | 1452 | 13547 | 是 | 是 | 是 | 是 |
| opening-3 | normal | 是 | 否 | 否 | 6009 | 1440 | 11416 | 是 | 是 | 是 | 是 |
| tactic-1 | complex | 是 | 否 | 否 | 6264 | 1648 | 15092 | 是 | 是 | 是 | 是 |
| tactic-2 | complex | 是 | 否 | 否 | 6217 | 1459 | 13641 | 是 | 是 | 是 | 是 |
| tactic-3 | complex | 是 | 否 | 否 | 6726 | 1671 | 21144 | 是 | 是 | 是 | 是 |
| king-attack-1 | complex | 是 | 否 | 否 | 5802 | 1465 | 12900 | 是 | 是 | 是 | 是 |
| king-attack-2 | complex | 是 | 否 | 否 | 6009 | 1388 | 14040 | 是 | 是 | 是 | 是 |
| king-attack-3 | complex | 是 | 否 | 否 | 4457 | 1646 | 17170 | 是 | 是 | 是 | 是 |
| center-1 | complex | 是 | 否 | 否 | 6224 | 1648 | 29139 | 是 | 是 | 是 | 是 |
| center-2 | complex | 是 | 否 | 否 | 5943 | 1530 | 15340 | 是 | 是 | 是 | 是 |
| closed-1 | normal | 是 | 否 | 否 | 4595 | 1343 | 11876 | 是 | 是 | 是 | 是 |
| closed-2 | complex | 是 | 否 | 否 | 5045 | 1650 | 14271 | 是 | 是 | 是 | 是 |
| simplify-1 | complex | 是 | 否 | 否 | 6199 | 1421 | 17289 | 是 | 是 | 是 | 是 |
| simplify-2 | complex | 是 | 否 | 否 | 4979 | 1287 | 11301 | 是 | 是 | 是 | 是 |

## 汇总

- 首次校验通过：15/15（100.0%）
- 最终严格校验通过：15/15（100.0%）
- 安全回退：0/15（0.0%）
- 缓存响应：3ms

## 分析重点筛选验收

| 局面 | 修改前弱点 | 修改后弱点 | 过滤仅未保护 | 过滤无关王安全 | 移入候选路线 | 过滤普通PV吃子 | 最终展示栏目 |
|---|---:|---:|---:|---:|---:|---:|---|
| opening-1 | 2 | 0 | 4 | 8 | 2 | 7 | positionAssessment、keyPieces、plans、playedMoveAnalysis、candidateLines、comparison |
| opening-2 | 2 | 0 | 5 | 8 | 0 | 6 | positionAssessment、keyPieces、plans、playedMoveAnalysis、candidateLines、comparison |
| opening-3 | 2 | 0 | 5 | 8 | 6 | 2 | positionAssessment、keyPieces、plans、playedMoveAnalysis、candidateLines、comparison |
| tactic-1 | 2 | 1 | 3 | 8 | 6 | 7 | positionAssessment、threats、weaknesses、keyPieces、plans、playedMoveAnalysis、candidateLines、comparison |
| tactic-2 | 2 | 0 | 3 | 7 | 5 | 2 | positionAssessment、threats、kingSafety、keyPieces、plans、playedMoveAnalysis、candidateLines、comparison |
| tactic-3 | 2 | 0 | 7 | 8 | 5 | 1 | positionAssessment、threats、kingSafety、keyPieces、plans、playedMoveAnalysis、candidateLines、comparison |
| king-attack-1 | 2 | 1 | 7 | 9 | 3 | 2 | positionAssessment、threats、kingSafety、weaknesses、keyPieces、plans、playedMoveAnalysis、candidateLines、comparison |
| king-attack-2 | 2 | 0 | 2 | 7 | 4 | 3 | positionAssessment、threats、kingSafety、keyPieces、plans、playedMoveAnalysis、candidateLines、comparison |
| king-attack-3 | 2 | 0 | 4 | 7 | 0 | 2 | positionAssessment、threats、kingSafety、keyPieces、plans、playedMoveAnalysis、candidateLines、comparison |
| center-1 | 2 | 0 | 3 | 7 | 6 | 8 | positionAssessment、threats、kingSafety、keyPieces、plans、playedMoveAnalysis、candidateLines、comparison |
| center-2 | 1 | 0 | 1 | 8 | 6 | 6 | positionAssessment、keyPieces、plans、playedMoveAnalysis、candidateLines、comparison |
| closed-1 | 2 | 0 | 4 | 8 | 0 | 4 | positionAssessment、keyPieces、plans、playedMoveAnalysis、candidateLines、comparison |
| closed-2 | 2 | 0 | 4 | 8 | 2 | 2 | positionAssessment、threats、keyPieces、plans、playedMoveAnalysis、candidateLines、comparison |
| simplify-1 | 2 | 0 | 4 | 8 | 6 | 5 | positionAssessment、threats、keyPieces、plans、playedMoveAnalysis、candidateLines、comparison |
| simplify-2 | 2 | 0 | 6 | 8 | 3 | 6 | positionAssessment、keyPieces、plans、playedMoveAnalysis、candidateLines、comparison |

- 修改前显示弱点：29 项；修改后：2 项。
- 被过滤的“仅未保护”事实：62 项。
- 被过滤的无关王安全描述：117 项。
- 从全局威胁归入候选路线内部：54 项。
- 被过滤的普通PV吃子：63 项。
- 固定物质差栏目：已移除；物质事实仍保留在底层事实包和严格校验上下文。
- g5马、a7兵、h4象、b7象类无意义弱点：未再显示。

## 三个修改前后对比

### opening-1

- 修改前弱点：白车(h1)当前没有本方棋子保护；黑车(h8)当前没有本方棋子保护
- 修改后弱点：无
- 修改前全局威胁：候选路线1第5个半回合cxd5包含吃子
- 修改后全局威胁：无
- 路线内部事件：路线1：候选路线1第7个半回合Qa4+包含将军；路线2：候选路线2中的Nxd4直接吃掉价值至少3分的棋子
- 最终栏目：positionAssessment、keyPieces、plans、playedMoveAnalysis、candidateLines、comparison
### tactic-2

- 修改前弱点：白马(g5)当前没有本方棋子保护；黑兵(a7)当前没有本方棋子保护
- 修改后弱点：无
- 修改前全局威胁：候选路线1第1个半回合Rxf7+包含吃子、将军
- 修改后全局威胁：white方当前可以走Rxf7+；white方当前可以走Rg8+
- 路线内部事件：路线1：候选路线1中的Nxf7直接吃掉价值至少3分的棋子；路线1：候选路线1中的Qxf7直接吃掉价值至少3分的棋子；路线2：候选路线2第2个半回合Qc1+包含将军；路线3：候选路线3中的Nxd8直接吃掉价值至少3分的棋子；路线3：候选路线3中的Nxf7直接吃掉价值至少3分的棋子
- 最终栏目：positionAssessment、threats、kingSafety、keyPieces、plans、playedMoveAnalysis、candidateLines、comparison
### closed-2

- 修改前弱点：白象(h4)当前没有本方棋子保护；黑象(b7)当前没有本方棋子保护
- 修改后弱点：无
- 修改前全局威胁：候选路线1第4个半回合gxh4包含吃子
- 修改后全局威胁：white方当前可以走Bxe7
- 路线内部事件：路线1：候选路线1中的Nxb7直接吃掉价值至少3分的棋子；路线1：候选路线1中的gxh4直接吃掉价值至少3分的棋子
- 最终栏目：positionAssessment、threats、keyPieces、plans、playedMoveAnalysis、candidateLines、comparison

## 原始输出校验明细

- 15 个局面均无原始输出校验错误。

## 安全字面量归一化

以下项目不会被放行或返回给前端；后端先替换为“该格/该路线着法”，再执行完整严格校验：

- `tactic-1` attempt 1 `plans.white[0].evidenceRefs`：已移除不支持white方计划的引用：f:18
- `center-1` attempt 1 `candidateLines[1].directPurpose`：已移除路线外SAN：Bg4
- `closed-2` attempt 1 `plans.white[1].explanation`：已移除事实包外格子：d5
- `closed-2` attempt 1 `plans.white[1].explanation`：已移除事实包外格子：e5
- `closed-2` attempt 1 `playedMoveAnalysis.positiveEffects[1]`：已移除事实包外格子：c4
