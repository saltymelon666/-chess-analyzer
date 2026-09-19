---
name: 棋盘研究所
description: 用双角色研究搭档，把引擎事实讲成学习者能复用的棋理。
colors:
  laboratory-green: "#173C36"
  mint-surface: "#DDEFEA"
  action-yellow: "#F5BF48"
  action-ink: "#6F4A00"
  warm-paper: "#F4F0E7"
  paper-panel: "#FFFCF5"
  paper-soft: "#F7F2E8"
  divider: "#D8D0C2"
  quiet-text: "#6C746F"
  success: "#2F8B76"
  danger: "#C85F5F"
typography:
  display:
    fontFamily: "Microsoft YaHei UI, PingFang SC, Noto Sans SC, system-ui, sans-serif"
    fontSize: "clamp(42px, 4.8vw, 66px)"
    fontWeight: 700
    lineHeight: 1.1
    letterSpacing: "-0.035em"
  headline:
    fontFamily: "Microsoft YaHei UI, PingFang SC, Noto Sans SC, system-ui, sans-serif"
    fontSize: "clamp(29px, 2.5vw, 38px)"
    fontWeight: 700
    lineHeight: 1.18
    letterSpacing: "-0.025em"
  body:
    fontFamily: "Segoe UI, Microsoft YaHei, system-ui, sans-serif"
    fontSize: "16px"
    fontWeight: 400
    lineHeight: 1.8
  label:
    fontFamily: "Segoe UI, Microsoft YaHei, system-ui, sans-serif"
    fontSize: "13px"
    fontWeight: 800
    lineHeight: 1.4
rounded:
  sm: "10px"
  md: "14px"
  lg: "18px"
spacing:
  xs: "8px"
  sm: "12px"
  md: "18px"
  lg: "24px"
  xl: "36px"
components:
  button-primary:
    backgroundColor: "{colors.action-yellow}"
    textColor: "{colors.laboratory-green}"
    rounded: "{rounded.sm}"
    padding: "13px 25px"
  button-secondary:
    backgroundColor: "{colors.paper-panel}"
    textColor: "{colors.laboratory-green}"
    rounded: "{rounded.sm}"
    padding: "9px 16px"
  card:
    backgroundColor: "{colors.paper-panel}"
    textColor: "{colors.laboratory-green}"
    rounded: "{rounded.lg}"
    padding: "24px"
---

# Design System: 棋盘研究所

## Overview

**Creative North Star: "双人棋局研究台"**

界面像一张温暖、可信的棋局研究桌：纯暖米白纸面负责承载内容，深墨绿负责建立专业的棋盘语境，黄色只在行动与关键发现上出现。双角色画面承担品牌识别，但不叠加姓名、角色分工或解释性标签。

营销首页可以更有角色感和留白，分析台则回到清晰、稳重、棋盘优先的操作界面。两者共享同一配色、字体性格、圆角和柔和景深，因此从介绍页进入分析台时仍属于同一个产品世界。

**Key Characteristics:**

- 暖米白纸面与深墨绿工作区形成稳定主对比。
- 双角色画面与真实棋盘共同承担产品识别，不附加角色文字标签。
- 黄色集中用于主行动、步骤编号和关键提示。
- 标题使用稳重的中文正体，正文保持清楚克制。
- 手机端单列重排，棋盘与主行动不产生横向溢出。

## Colors

主色不是通用科技蓝，而是“研究桌上的墨绿、纸张和标记笔”组合。

### Primary

- **实验室墨绿**：用于品牌文字、棋盘容器、首页角色背景与高权重信息。
- **行动黄**：用于“开始复盘”“导入并显示”等唯一主行动，以及有限的关键标记。

### Secondary

- **薄荷研究面**：用于次级按钮、状态背景和已验证信息的轻量分组。

### Neutral

- **暖纸背景**：全局底色，保持长时间阅读舒适。
- **纸面白与柔纸色**：区分主容器、输入区和次级面板。
- **温灰分隔线**：建立边界，不制造厚重卡片框。
- **安静正文灰绿**：承载辅助说明，同时保持与墨绿体系一致。

### Named Rules

**The Yellow Means Action Rule.** 黄色优先留给主行动和真正关键的棋局提示，不用于大面积铺底。

**The Chess Facts Stay Green Rule.** 与棋盘事实、引擎状态和已验证信息相关的高权重文字使用墨绿或薄荷色系，不借用危险红色制造紧张感。

## Typography

**Display Font:** Microsoft YaHei UI / PingFang SC / Noto Sans SC
**Body Font:** Segoe UI / Microsoft YaHei（后备为系统无衬线）
**Label/Mono Font:** 棋谱输入使用 Consolas / SFMono-Regular

**Character:** 大标题使用克制、清楚的中文正体，避免斜体和营销海报感；正文和数据保持熟悉的中文系统无衬线，提高分析阅读效率。

### Hierarchy

- **Display**（700，流体字号，舒展行高）：只用于首页核心价值标题。
- **Headline**（700，轻度紧字距）：用于分析台的任务标题和重要报告标题。
- **Body**（400，宽松行高）：用于产品解释和教练正文，段落宽度受容器控制。
- **Label**（800，小字号）：用于步骤、状态和辅助说明，不使用全大写制造噪声。
- **Chess notation**（等宽）：只用于 PGN、走法与测量数据，不作为品牌装饰字体。

### Named Rules

**The Clear Chinese Type Rule.** 首页与分析台标题统一使用高可读中文正体；长篇教练解释继续使用高可读正文体。

## Layout

页面采用两段式路径：首页先说明价值并展示 P1/P2，分析台再进入真实操作。桌面首页为文字与角色图左右分屏；分析台以棋盘为中心，在左右安排导入与分析状态。主容器宽度约 1260–1420px，常用间距来自 8、12、18、24、36px 节奏。

980px 以下逐步转为单列；560px 以下使用 10px 页面边距、单列信息结构和全宽主按钮。移动端品牌名称保持单行，P1/P2 在首屏 CTA 后立即出现，页面级 `scrollWidth` 必须等于 `clientWidth`。

## Elevation & Depth

背景只使用纯暖米白，不叠加点阵、渐变或装饰光斑。深度仅用于分析台必要的层级，以低对比柔和阴影表达；首页导航和主容器保持平面。

### Shadow Vocabulary

- **Ambient panel** (`0 8px 22px rgba(47,57,50,.055)`): 分析台主要面板和报告容器。
- **Raised workspace** (`0 30px 66px rgba(91,70,43,.16)`): 桌面分析区的大型纸面容器。
- **Focus halo** (`0 0 0 4px rgba(245,191,72,.14)`): 输入控件聚焦状态，不作为静态装饰。

### Named Rules

**The Soft Evidence Rule.** 所有实际景深必须带模糊；硬偏移阴影不属于棋盘研究所。

## Shapes

主容器使用克制的 18px 圆角，内部控件使用 10–14px；小型状态胶囊可以使用全圆角。角色图保持自身有机轮廓，棋盘与纸面保持清晰矩形结构，不用几何蒙版替代角色素材。

## Components

### Buttons

- **Shape:** 紧凑圆角矩形（9–10px），移动端主行动可全宽。
- **Primary:** 行动黄背景、墨绿文字，首页 CTA 使用更大的 13px × 25px 内边距。
- **Hover / Focus:** 轻微上浮；键盘焦点使用清晰黄色外环。
- **Secondary:** 暖白或薄荷背景，边框与文字权重低于主行动。

### Cards / Containers

- **Corner Style:** 主卡 18px，内部卡 10–14px。
- **Background:** 暖白为主，薄荷和柔纸色只承担语义分组。
- **Shadow Strategy:** 使用环境软阴影；同一卡片不同时依赖粗边框和重阴影。
- **Internal Padding:** 手机约 14–18px，桌面约 22–24px。

### Inputs / Fields

- **Style:** 柔纸色底、1px 温灰边框、10px 圆角。
- **Focus:** 边框切换为行动黄并出现低透明度焦点环。
- **PGN field:** 等宽字体保留棋谱结构；普通文本输入沿用正文体。

### Navigation

首页和分析台都使用暖白导航容器。品牌图标、中文名和英文副标形成固定组合；手机端隐藏英文副标，并将登录、复习与返回操作换行排列，不能挤压中文品牌名。

### P1/P2 Mascot Pair

双角色图用于首页核心叙事，深墨绿背景与黄色帽子共同衔接品牌色。手机端在主 CTA 后直接展示裁切完整的双角色；画面上不叠加 P1/P2 名称或角色说明文字。

## Do's and Don'ts

### Do:

- **Do** 让棋盘在分析台始终拥有最高视觉优先级。
- **Do** 让双角色画面承担品牌识别，不叠加姓名或角色说明文字。
- **Do** 在手机端按任务顺序单列重排，并检查真实 390px 横向溢出。
- **Do** 保持首页和分析台在配色、字体、圆角与影调上的连续性。

### Don't:

- **Don't** 把黄色铺满大面积内容区，或让多个主按钮互相争夺注意力。
- **Don't** 使用硬偏移阴影、装饰性玻璃拟态或通用科技蓝破坏纸面研究氛围。
- **Don't** 在页面背景叠加点阵、渐变、光斑或装饰纹理。
- **Don't** 用表情符号代替正式图标或 P1/P2 品牌资产。
- **Don't** 为每张卡片分别播放相同入场动画；页面切换只保留一次整体过渡。
