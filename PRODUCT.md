# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

面向中国用户的中文国际象棋学习者。用户会导入自己的 PGN 棋谱，希望看懂关键失误，并把值得重复训练的局面留下来复习。

## Product Purpose

“棋盘研究所”把整盘棋谱转成可理解的中文复盘，并帮助用户通过再次走棋巩固关键局面。成功意味着用户不只看到引擎答案，还能在之后独立走出正确着法。

## Positioning

棋盘事实、走法、评价和推荐路线由 `python-chess`、Stockfish 或已验证事实包提供，再用自然中文解释；复习训练直接复用这些已验证局面，不让生成式模型成为棋盘事实来源。

## Operating Context

用户先在网页导入 PGN、查看逐步复盘，将 `?!`、`?`、`??` 局面加入复习；随后进入独立的复习训练页面，在棋盘上走出正确着法并安排下一次复习。

## Capabilities and Constraints

- 保持现有原生 HTML、CSS、JavaScript 技术结构，不引入前端框架。
- 保持 PGN 解析、棋盘交互、Stockfish、DeepSeek、缓存、API 和部署链路。
- 复习数据当前保存在同一浏览器的 `localStorage`，不伪装成已登录或已云同步。
- 开发版页面与 `docs/` 发布版页面保持一致。

## Brand Commitments

- 产品名称为“棋盘研究所”。
- 中文表达自然、具体、适合中国用户，不展示内部变量名、证据 ID 或模型判断过程。
- 保持现有网站的暖米白、薄荷绿、黄色重点色、圆角与棋盘研究氛围。

## Evidence on Hand

- 现有分析页面：`index.html` 与 `docs/index.html`。
- 棋盘和棋子资产：`assets/chess/` 与 `docs/assets/chess/`。
- 当前复习记录包含走前 FEN、实战着、Stockfish 推荐着、错误等级和解释文本。
- 当前没有用户账号或跨设备同步证据，未来页面不得暗示这些能力已经存在。

## Product Principles

- 先保证棋盘事实正确，再追求解释自然。
- 分析和训练分层：分析页负责发现问题，复习页负责实际走棋。
- 复习答案默认隐藏，用户必须在棋盘上完成正确着法。
- 优先沿用用户已经熟悉的页面结构和视觉语言。
