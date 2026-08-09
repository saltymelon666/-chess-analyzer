# Chess Analysis Phase 8D Endgame Knowledge Expansion Report

## 1. 结果

- 从现有公版/已授权棋书真值集筛出全部 38 个七子以内残局候选。
- 每个候选重新调用 Lichess Syzygy，验证胜和负、DTZ/DTM 和首个表库着法。
- 33 个候选通过准确性与教学内容门禁，5 个候选被隔离。
- 与 Phase 8C 的 20 个 Kling/Horwitz 精确残局合并后，运行时残局知识库共有 53 个精确局面。
- 所有记录只按完整棋子摆放和行棋方精确命中，不做相似局面原文迁移。

## 2. 新增来源覆盖

- *The Blue Book of Chess*
- *Chess and Checkers: The Way to Mastership*
- *Chess Fundamentals*
- *My Best Games of Chess 1908–1923*
- *Chess Strategy*
- *The International Chess Congress, St. Petersburg, 1909*
- *Morphy's Games of Chess*
- *The Modern Chess Instructor*

具体进入产品的条目以 `phase8d-endgame-expansion-audit.json` 中的
`admissionStatus=exact_verified` 为准。

## 3. 准确性边界

- 棋书文字只负责其精确源局面的人类解释。
- 胜、和、负及程序关键着由 Syzygy 决定。
- 书中着与表库着分别保存，不把书中着自动称为最佳着。
- 5 个隔离条目仍留在研究审计中，原因包括源文字与当前 FEN 的作用域冲突、
  棋书结论与表库冲突，以及注释没有可迁移的教学内容。
- DeepSeek 不参与棋盘恢复、残局真值或关键着选择。

## 4. 《残局教科书》接入方式

《残局教科书》/ *Dvoretsky's Endgame Manual* 已登记为下一优先来源。
取得合法电子文件后，按以下顺序处理：

1. 提取图号、棋盘、行棋方、书中结论、关键着和解释。
2. 用 `python-chess` 验证棋盘及所有着法合法性。
3. 七子以内使用 Syzygy；八子以上只保存 Stockfish 支持状态，不伪装成理论真值。
4. 原评绑定精确源局面，不作为相似局面的直接答案。
5. 仅通过门禁的记录进入 `EndgameKnowledgeRepository`。

当前尚未取得该书实际电子文件，因此本轮没有伪造或从非授权网页抓取其正文。

## 5. 产物

- 研究审计：`docs/research/phase8d-endgame-expansion-audit.json`
- 运行时知识库：`app/data/endgame-knowledge.json`
- 构建脚本：`scripts/build_phase8d_endgame_expansion.py`
- 查询层：`app/endgame_knowledge.py`
- 测试：`tests/test_endgame_knowledge.py`、`tests/test_phase8d_endgame_expansion.py`
