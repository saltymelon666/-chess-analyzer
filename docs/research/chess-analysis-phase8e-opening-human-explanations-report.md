# Chess Analysis Phase 8E Opening Human Explanations Report

## 1. 结果

- 使用 Wikimedia 官方 Wikibooks 快照提取《Chess Opening Theory》的人类开局解释。
- 快照 MD5 与官方清单一致：`819fb69769a1a20ff840981f8454ca92`。
- 发现3,010个开局理论页面，其中2,948个页面标题可以恢复为合法走法路径。
- 清除空页面、纯谱表、模板、引用和导航后，保留1,896段有实质内容的解释。
- 现有3,810条Lichess开局路径全部能命中至少一段同路径或祖先路径解释，覆盖149个开局家族。
- 801条目录路径有完全同路径解释；其余使用当前走法路径上最深的祖先解释。
- 平均解释匹配深度为6.19个半回合，中位数为6个半回合。

## 2. 解释内容

保留的原始人类说明通常覆盖：

- 开局着法的直接目的；
- 双方对中心的处理；
- 常见兵突破；
- 子力发展位置；
- 王翼或后翼的典型计划；
- 常见转置和主要分支。

这些解释不提供当前Stockfish分数，也不证明某一步在当前局面中是最佳着。

## 3. 查询方式

`POST /api/opening-lookup`的返回结果新增`humanExplanation`：

- `text`：Wikibooks原始人类解释；
- `matchedPly`：解释实际对应的路径深度；
- `pageTitle`和`pageUrl`：来源页面；
- `revisionId`：固定修订号；
- `license`和`attribution`：CC BY-SA/GFDL许可与署名。

查询优先返回当前实战路径上最深的有效解释。FEN转置查询使用开局目录选中的标准路径查找解释。

## 4. 许可与边界

- 来源文本按Wikibooks的CC BY-SA 4.0/GFDL条件保存，并逐条保留页面、修订号和署名。
- Lichess的CC0开局名称/路径与Wikibooks解释保持为两个独立数据层。
- 当前运行时返回英文原文，没有伪装成程序事实，也没有自动进入DeepSeek Prompt。
- 若前端需要中文，应由独立翻译/表达层处理，并继续展示来源和许可信息。

## 5. 产物

- 运行时解释库：`app/data/opening-explanations.json`
- 研究数据：`docs/research/phase8e-opening-explanations.json`
- 清单：`docs/research/phase8e-opening-explanations-manifest.json`
- 构建脚本：`scripts/build_phase8e_opening_explanations.py`
- 查询实现：`app/opening_knowledge.py`
- 测试：`tests/test_opening_knowledge.py`、`tests/test_phase8e_opening_explanations.py`
