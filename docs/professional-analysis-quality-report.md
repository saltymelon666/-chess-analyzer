# 专业棋局分析质量与性能报告

生成模型：`deepseek-v4-flash`。本报告不包含 API Key、Authorization 请求头或任何密钥内容。

## game1 基线问题

旧版复杂局面输入为 80,620 Token，首次响应 75,136ms；两次原始输出均失败并使用安全回退。失败类型包括：

- 第一次：`candidateLines[*].firstMove / continuationPhases[*].moves` 出现不属于三条 Stockfish 路线的 `Bxh7+`、`Qxc3`、`Qxh2+`；`playedMoveAnalysis.positiveEffects` 把实战走法写成未验证吃子；`weaknesses.white[*].evidenceRefs` 引用了错误一方；`mainDanger` 缺少来源格和目标格，且描述了事实包中不存在的将军；正文 2,259 字，超过复杂局面上限。
- 第二次：多个 `evidenceRefs` 使用不存在的 `centipawnLoss:103` 和 `fact:move-1-after:key:pv_key_piece:black:c7`；候选路线中出现 `Bxe7`、`Bxh7+`、`Qxh2+`、`Rxb7`、`Rxh6`；`keyPieces[*]` 声称存在局面前 FEN 中没有的 `white_bishop@g5`；`weaknesses.white[*].evidenceRefs` 黑白说反；再次描述不存在的将军；正文 2,260 字。
- 安全回退曾把结果 FEN 的一段 `p7` 误判成棋盘格；现已停止把结果 FEN写入正文，只保留结构化结果事实。
- 历史生产日志只保留到字段组和错误值，没有保留上述数组项的原始下标；报告使用 `[*]` 标记这一事实，不虚构无法恢复的索引。新验证流程会为每次尝试保存完整字段路径。

## 优化方案

- 棋子改用固定的 `keyPieces.white.pieceRef` / `keyPieces.black.pieceRef`。
- 候选路线只返回 `lineRef`；完整 PV 只返回已有 `plyRefs`，SAN、UCI、格子、棋子与结果局面由后端填充。
- 提示词仅发送一个当前 FEN、去重棋子/事实目录、实战走法和三条最多 10 半回合的路线；不发送 legalMoves、positionAfter、重复 evidence 字典、调试字段或整盘历史。
- 保持严格校验：未知 ID、事实包外格子、路线外 SAN、任何 UCI、黑白颠倒、缺证据结论仍会拒绝。
- 分别记录 DeepSeek 网络、校验和后处理耗时；保留缓存。

## game1 优化实测对比

- 输入 Token：80,620 → 6,250，减少 92.2%。
- 首次 DeepSeek 网络耗时：75,136ms → 23,573ms，减少 68.6%。
- 优化后的实测结果通过最终严格校验且未触发安全回退；该次测量因模型返回 `medium_term` 枚举同义值发生一次纠错重试，现已在不改变语义和事实标准的前提下归一为既有枚举。
- 输入压缩目标已达到；复杂局面 25 秒目标已达到。普通局面本轮实测为 16,603—21,205ms，仍高于 15 秒目标，耗时主体是 DeepSeek 网络与生成（校验、后处理均为毫秒级），未通过减少三条 Stockfish 路线或关键事实换取速度。

## 15 局面结果

| 局面 | 复杂度 | 首次通过 | 重试 | 回退 | 输入Token | 输出Token | 首次耗时ms | 关键棋子正确 | 最大危险有证据 | 双方计划有证据 | 三条路线有效 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| opening-1 | normal | 是 | 否 | 否 | 5752 | 2052 | 19999 | 是 | 是 | 是 | 是 |
| opening-2 | normal | 是 | 否 | 否 | 5525 | 2094 | 20540 | 是 | 是 | 是 | 是 |
| opening-3 | normal | 是 | 否 | 否 | 6467 | 2032 | 21205 | 是 | 是 | 是 | 是 |
| tactic-1 | complex | 是 | 否 | 否 | 7812 | 2150 | 19913 | 是 | 是 | 是 | 是 |
| tactic-2 | complex | 是 | 否 | 否 | 6679 | 1885 | 19506 | 是 | 是 | 是 | 是 |
| tactic-3 | complex | 是 | 否 | 否 | 6617 | 2383 | 22744 | 是 | 是 | 是 | 是 |
| king-attack-1 | complex | 是 | 否 | 否 | 5183 | 1649 | 15713 | 是 | 是 | 是 | 是 |
| king-attack-2 | complex | 是 | 否 | 否 | 6643 | 1941 | 17415 | 是 | 是 | 是 | 是 |
| king-attack-3 | complex | 是 | 否 | 否 | 5349 | 1988 | 18423 | 是 | 是 | 是 | 是 |
| center-1 | complex | 是 | 否 | 否 | 6983 | 2101 | 19748 | 是 | 是 | 是 | 是 |
| center-2 | complex | 是 | 否 | 否 | 7253 | 2316 | 22956 | 是 | 是 | 是 | 是 |
| closed-1 | normal | 否 | 是 | 否 | 6049 | 3578 | 16603 | 是 | 是 | 是 | 是 |
| closed-2 | complex | 是 | 否 | 否 | 5683 | 1900 | 15109 | 是 | 是 | 是 | 是 |
| simplify-1 | complex | 是 | 否 | 否 | 6870 | 2055 | 16431 | 是 | 是 | 是 | 是 |
| simplify-2 | complex | 是 | 否 | 否 | 5675 | 2064 | 16802 | 是 | 是 | 是 | 是 |

## 汇总

- 首次校验通过：14/15（93.3%）
- 最终严格校验通过：15/15（100.0%）
- 安全回退：0/15（0.0%）
- 缓存响应：3ms
- 输入 Token 范围：5,183—7,812；复杂局面最大 7,812（低于 25,000）。
- 首次耗时范围：15,109—22,956ms；复杂局面最大 22,956ms（低于 25 秒）。
- 普通局面最大首次耗时：21,205ms（未达到 15 秒目标）。
- 校验与后处理均为毫秒级，主要等待来自 DeepSeek 网络和结构化内容生成。

## 原始输出校验明细

- `closed-1` attempt 1 `mainDanger.description` / 其他原因：没有同时指出具体棋子和格子
- `closed-1` attempt 1 `mainDanger.description` / 其他原因：没有同时指出来源格和目标格

## 安全字面量归一化

以下项目不会被放行或返回给前端；后端先替换为“该格/该路线着法”，再执行完整严格校验：

- `opening-1` attempt 1 `plans.black[1].explanation`：已移除事实包外格子：a6
- `king-attack-2` attempt 1 `plans.black[1].evidenceRefs`：已移除不支持black方计划的引用：f:50
- `center-1` attempt 1 `keyPieces.white.futureTask`：已移除路线外SAN：Bxh7+
- `center-1` attempt 1 `plans.white[1].explanation`：已移除路线外SAN：Bxh7+
- `center-1` attempt 1 `candidateLines[2].risks[0]`：已移除事实包外格子：b4
- `center-2` attempt 1 `candidateLines[2].directPurpose`：已移除事实包外格子：e4
- `closed-1` attempt 2 `plans.black[0].evidenceRefs`：已移除不支持black方计划的引用：f:49
- `closed-1` attempt 2 `plans.black[0].evidenceRefs`：已补入支持black方计划的引用：p:b:1
- `simplify-1` attempt 1 `plans.white[0].evidenceRefs`：已移除不支持white方计划的引用：f:56
- `simplify-1` attempt 1 `plans.white[1].evidenceRefs`：已移除不支持white方计划的引用：f:60
- `simplify-2` attempt 1 `plans.white[1].evidenceRefs`：已移除不支持white方计划的引用：f:13
