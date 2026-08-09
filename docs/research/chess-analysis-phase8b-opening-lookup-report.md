# Chess Analysis Phase 8B Opening Lookup Report

## 1. 结果

Phase 8A 的 3,810 条开局路径已经进入后端只读识别层。

- 新接口：`POST /api/opening-lookup`。
- 输入：PGN、FEN，或同时提供二者进行终点一致性验证。
- 输出：ECO、完整开局名称、家族名称、变例层级、标准 PGN、SAN、UCI、匹配深度和后续命名分支。
- 匹配类型：完全路线、已命名路线前缀、转置局面、精确 FEN、未命中。
- 目录随 `app/` 一同进入 Docker，不依赖 `docs/research/` 或本地 `work/` 目录。

## 2. 事实边界

开局识别完全由程序完成：

1. PGN 由 `python-chess` 解析。
2. 主线每一步转换为 UCI 并检查合法性。
3. 优先查找完整路径；如果实战已经走出目录，返回最长已命名前缀。
4. 不同走子次序到达同一完整局面时，按完整局面键识别为转置。
5. PGN 与同时提供的 FEN 不一致时直接返回 422。

该接口不进行以下推断：

- 不判断开局好坏。
- 不把历史主线称为当前最佳着。
- 不根据 ECO 推断某方有优势或主动权。
- 不调用 DeepSeek 改写开局名称或补写变例。
- 不调用 Stockfish，因此不增加引擎分析耗时。

## 3. 文件

- `app/opening_knowledge.py`
- `app/data/opening-path-catalog.json`
- `app/api.py`
- `scripts/build_phase8a_opening_catalog.py`
- `tests/test_opening_knowledge.py`
- `tests/test_phase8a_opening_catalog.py`

## 4. 接口示例

请求：

```json
{
  "pgn": "1. e4 e5 2. Nf3 Nc6 3. Bb5"
}
```

响应只返回目录中实际存在的开局身份与路线字段。策略解释仍应由当前棋盘事实、Stockfish 和后续解释层共同完成。

## 5. 残局下一步

残局不能照搬开局的路径识别结构。下一阶段应建立独立 `EndgameKnowledgeRepository`：

- 精确局面键和行棋方；
- 子力类型；
- 书中胜和负结论；
- Syzygy 校验后的 WDL/DTZ；
- 合法关键着和解答路线；
- 棋书解释与来源；
- 书中结论和残局库冲突标志。

第一批应优先处理 Kling/Horwitz 的两百多个分类残局。图片棋盘未恢复、行棋方不明确或解答着不合法的条目不得进入正式库。
