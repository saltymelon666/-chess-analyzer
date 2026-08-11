# 专业棋局分析质量与性能报告

生成模型：`deepseek-v4-flash`。本报告不包含 API Key、Authorization 请求头或任何密钥内容。

## game1 基线问题

旧版复杂局面输入为 80,620 Token，首次响应 75,136ms；两次原始输出均失败并使用安全回退。失败类型包括：

- 第一次：`candidateLines[*].firstMove / continuationPhases[*].moves` 出现不属于三条 Stockfish 路线的 `Bxh7+`、`Qxc3`、`Qxh2+`；`playedMoveAnalysis.positiveEffects` 把实战走法写成未验证吃子；`weaknesses.white[*].evidenceRefs` 引用了错误一方；`mainDanger` 缺少来源格和目标格，且描述了事实包中不存在的将军；正文 2,259 字，超过复杂局面上限。
- 第二次：多个 `evidenceRefs` 使用不存在的引用；候选路线中出现 `Bxe7`、`Bxh7+`、`Qxh2+`、`Rxb7`、`Rxh6`；`weaknesses.white[*].evidenceRefs` 黑白说反；再次描述不存在的将军；正文 2,260 字。
- 安全回退曾把结果 FEN 的一段 `p7` 误判成棋盘格；现已停止把结果 FEN写入正文，只保留结构化结果事实。

## 优化方案

- 候选路线只返回 `lineRef`；完整 PV 只返回已有 `plyRefs`，SAN、UCI、格子、棋子与结果局面由后端填充。
- 提示词不发送 FEN；仅发送 ChessFactPackage 版本/来源清单、去重棋子与事实引用、实战走法引用和三条最多 10 半回合的已验证路线；不发送 legalMoves、positionAfter、重复 evidence 字典、调试字段或整盘历史。
- 保持严格校验：未知 ID、事实包外格子、路线外 SAN、任何 UCI、黑白颠倒、缺证据结论仍会拒绝。
- 分别记录 DeepSeek 网络、校验和后处理耗时；保留缓存。

## 15 局面结果

| 局面 | 复杂度 | 首次通过 | 重试 | 回退 | 输入Token | 输出Token | 首次耗时ms | 最大危险有证据 | 双方计划有证据 | 三条路线有效 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| opening-1 | normal | 是 | 否 | 否 | 6906 | 1796 | 16963 | 是 | 是 | 是 |
| opening-2 | normal | 是 | 否 | 否 | 6112 | 1543 | 14733 | 是 | 是 | 是 |
| opening-3 | normal | 是 | 否 | 否 | 7574 | 1737 | 16002 | 是 | 是 | 是 |
| tactic-1 | complex | 是 | 否 | 否 | 8424 | 1756 | 17822 | 是 | 是 | 是 |
| tactic-2 | complex | 是 | 否 | 否 | 8704 | 2220 | 19766 | 是 | 是 | 是 |
| tactic-3 | complex | 是 | 否 | 否 | 8231 | 1713 | 17701 | 是 | 是 | 是 |
| king-attack-1 | complex | 是 | 否 | 否 | 7326 | 1874 | 20109 | 是 | 是 | 是 |
| king-attack-2 | complex | 是 | 否 | 否 | 7698 | 1575 | 16656 | 是 | 是 | 是 |
| king-attack-3 | complex | 是 | 否 | 否 | 5796 | 1635 | 15078 | 是 | 是 | 是 |
| center-1 | complex | 是 | 否 | 否 | 7744 | 1641 | 15844 | 是 | 是 | 是 |
| center-2 | complex | 是 | 否 | 否 | 7131 | 1655 | 15852 | 是 | 是 | 是 |
| closed-1 | normal | 是 | 否 | 否 | 5458 | 1410 | 12960 | 是 | 是 | 是 |
| closed-2 | complex | 是 | 否 | 否 | 5854 | 1642 | 17210 | 是 | 是 | 是 |
| simplify-1 | complex | 是 | 否 | 否 | 7694 | 1475 | 14415 | 是 | 是 | 是 |
| simplify-2 | complex | 是 | 否 | 否 | 6506 | 1572 | 16453 | 是 | 是 | 是 |

## 汇总

- 首次校验通过：15/15（100.0%）
- 最终严格校验通过：15/15（100.0%）
- 安全回退：0/15（0.0%）
- 缓存响应：410ms

## 分析重点筛选验收

| 局面 | 修改前弱点 | 修改后弱点 | 过滤仅未保护 | 过滤无关王安全 | 移入候选路线 | 过滤普通PV吃子 | 最终展示栏目 |
|---|---:|---:|---:|---:|---:|---:|---|
| opening-1 | 2 | 0 | 4 | 8 | 2 | 7 | positionAssessment、playedMoveAnalysis、plans、candidateLines、comparison |
| opening-2 | 2 | 0 | 5 | 8 | 0 | 6 | positionAssessment、playedMoveAnalysis、plans、candidateLines、comparison |
| opening-3 | 2 | 0 | 5 | 8 | 6 | 2 | positionAssessment、playedMoveAnalysis、plans、candidateLines、comparison |
| tactic-1 | 2 | 1 | 3 | 8 | 6 | 7 | positionAssessment、threats、weaknesses、playedMoveAnalysis、plans、candidateLines、comparison |
| tactic-2 | 2 | 0 | 3 | 7 | 5 | 2 | positionAssessment、threats、kingSafety、playedMoveAnalysis、plans、candidateLines、comparison |
| tactic-3 | 2 | 0 | 7 | 8 | 5 | 1 | positionAssessment、threats、kingSafety、playedMoveAnalysis、plans、candidateLines、comparison |
| king-attack-1 | 2 | 1 | 7 | 9 | 3 | 2 | positionAssessment、threats、kingSafety、weaknesses、playedMoveAnalysis、plans、candidateLines、comparison |
| king-attack-2 | 2 | 0 | 2 | 7 | 4 | 3 | positionAssessment、threats、kingSafety、playedMoveAnalysis、plans、candidateLines、comparison |
| king-attack-3 | 2 | 0 | 4 | 7 | 0 | 2 | positionAssessment、threats、kingSafety、playedMoveAnalysis、plans、candidateLines、comparison |
| center-1 | 2 | 0 | 3 | 7 | 6 | 8 | positionAssessment、threats、kingSafety、playedMoveAnalysis、plans、candidateLines、comparison |
| center-2 | 1 | 0 | 1 | 8 | 6 | 6 | positionAssessment、playedMoveAnalysis、plans、candidateLines、comparison |
| closed-1 | 2 | 0 | 4 | 8 | 0 | 4 | positionAssessment、playedMoveAnalysis、plans、candidateLines、comparison |
| closed-2 | 2 | 0 | 4 | 8 | 2 | 2 | positionAssessment、threats、playedMoveAnalysis、plans、candidateLines、comparison |
| simplify-1 | 2 | 0 | 4 | 8 | 6 | 5 | positionAssessment、threats、playedMoveAnalysis、plans、candidateLines、comparison |
| simplify-2 | 2 | 0 | 6 | 8 | 3 | 6 | positionAssessment、playedMoveAnalysis、plans、candidateLines、comparison |

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
- 修改前全局威胁：Qa4+ 后，白后同时攻击 黑兵（d4）、黑兵（a7）、黑王（e8）。
- 修改后全局威胁：无
- 路线内部事件：路线1：候选路线1第7个半回合Qa4+包含将军；路线2：候选路线2中的Nxd4直接吃掉价值至少3分的棋子
- 最终栏目：positionAssessment、playedMoveAnalysis、plans、candidateLines、comparison
### tactic-2

- 修改前弱点：白马(g5)当前没有本方棋子保护；黑兵(a7)当前没有本方棋子保护
- 修改后弱点：无
- 修改前全局威胁：Rxf7+ 后，白车同时攻击 黑兵（a7）、黑王（g7）。
- 修改后全局威胁：white方当前可以走Rxf7+；white方当前可以走Rg8+
- 路线内部事件：路线1：候选路线1中的Nxf7直接吃掉价值至少3分的棋子；路线1：候选路线1中的Qxf7直接吃掉价值至少3分的棋子；路线2：候选路线2第2个半回合Qc1+包含将军；路线3：候选路线3中的Nxd8直接吃掉价值至少3分的棋子；路线3：候选路线3中的Nxf7直接吃掉价值至少3分的棋子
- 最终栏目：positionAssessment、threats、kingSafety、playedMoveAnalysis、plans、candidateLines、comparison
### closed-2

- 修改前弱点：白象(h4)当前没有本方棋子保护；黑象(b7)当前没有本方棋子保护
- 修改后弱点：无
- 修改前全局威胁：c5 后，黑兵同时攻击 白兵（b4）、白马（d4）。
- 修改后全局威胁：white方当前可以走Bxe7
- 路线内部事件：路线1：候选路线1中的Nxb7直接吃掉价值至少3分的棋子；路线1：候选路线1中的gxh4直接吃掉价值至少3分的棋子
- 最终栏目：positionAssessment、threats、playedMoveAnalysis、plans、candidateLines、comparison

## 原始输出校验明细

- 15 个局面均无原始输出校验错误。

## 安全字面量归一化

以下项目不会被放行或返回给前端；后端先替换为“该格/该路线着法”，再执行完整严格校验：

- `opening-2` attempt 1 `playedMoveAnalysis.continuationPhases[0].explanation`：已整句重建越界的程序专属结论
- `opening-3` attempt 1 `playedMoveAnalysis.continuationPhases[0].explanation`：已整句重建越界的程序专属结论
- `tactic-1` attempt 1 `candidateLines[1].advantages[0]`：已整句重建越界的程序专属结论
- `tactic-2` attempt 1 `mainDanger.description`：已整句重建越界的程序专属结论
- `tactic-2` attempt 1 `playedMoveAnalysis.continuationPhases[0].explanation`：已整句重建越界的程序专属结论
- `tactic-2` attempt 1 `candidateLines[0].advantages[0]`：已整句重建越界的程序专属结论
- `tactic-2` attempt 1 `candidateLines[0].continuationPhases[0].explanation`：已整句重建越界的程序专属结论
- `tactic-2` attempt 1 `candidateLines[2].advantages[0]`：已整句重建越界的程序专属结论
- `tactic-2` attempt 1 `candidateLines[2].continuationPhases[0].explanation`：已整句重建越界的程序专属结论
- `tactic-2` attempt 1 `comparison.whyFirstLineIsBest`：已整句重建越界的程序专属结论
- `tactic-3` attempt 1 `candidateLines[1].risks[0]`：已整句重建越界的程序专属结论
- `king-attack-1` attempt 1 `mainDanger.description`：已整句重建越界的程序专属结论
- `king-attack-1` attempt 1 `playedMoveAnalysis.intention`：已整句重建越界的程序专属结论
- `king-attack-1` attempt 1 `candidateLines[0].continuationPhases[0].explanation`：已整句重建越界的程序专属结论
- `king-attack-1` attempt 1 `candidateLines[1].continuationPhases[0].explanation`：已整句重建越界的程序专属结论
- `king-attack-1` attempt 1 `candidateLines[2].continuationPhases[0].explanation`：已整句重建越界的程序专属结论
- `king-attack-2` attempt 1 `playedMoveAnalysis.continuationPhases[0].explanation`：已整句重建越界的程序专属结论
- `king-attack-2` attempt 1 `candidateLines[0].risks[0]`：已整句重建越界的程序专属结论
- `king-attack-3` attempt 1 `playedMoveAnalysis.continuationPhases[0].explanation`：已整句重建越界的程序专属结论
- `center-2` attempt 1 `playedMoveAnalysis.continuationPhases[0].explanation`：已整句重建越界的程序专属结论
- `center-2` attempt 1 `candidateLines[1].risks[1]`：已整句重建越界的程序专属结论
- `closed-1` attempt 1 `comparison.mainDifference`：已整句重建越界的程序专属结论
- `closed-1` attempt 1 `comparison.whyFirstLineIsBest`：已整句重建越界的程序专属结论
- `simplify-1` attempt 1 `playedMoveAnalysis.positiveEffects[1]`：已整句重建越界的程序专属结论
- `simplify-1` attempt 1 `candidateLines[0].advantages[0]`：已整句重建越界的程序专属结论
- `simplify-1` attempt 1 `candidateLines[0].continuationPhases[0].explanation`：已整句重建越界的程序专属结论
- `simplify-1` attempt 1 `candidateLines[1].continuationPhases[0].explanation`：已整句重建越界的程序专属结论
- `simplify-2` attempt 1 `candidateLines[0].advantages[1]`：已整句重建越界的程序专属结论
- `simplify-2` attempt 1 `candidateLines[0].continuationPhases[0].explanation`：已整句重建越界的程序专属结论
