# 抽派生 · Design System

> 本文件是 ParoWEB 的视觉契约。现有页面（抽派生主站）在 `app/static/app.css` 中已有
> 一套稳定的隐式系统；本文件把它提取成 token，之后新增页面/组件必须引用这里的
> 变量与模式。2026-10-21 为群友入口页首次建立，入口路径后改为 `/roomlist`。

## 1. Atmosphere & Identity

**温暖的纸张工作台。** 米色纸面、奶油色卡片、细线分隔，像一本被认真排版过的同人
小册子；信息密度可以高，但表面始终安静。签名是**纸张 + 圆角卡片 + 衬线标题**：
标题用宋体/思源宋体，正文用 Noto Sans SC；卡片用 22px 圆角和一层很轻的暖色阴影，
而不是冷硬的边框。交互色只借彰人橙 `#ff7722` 与冬弥蓝 `#0077dd` 点到为止。

## 2. Color

### Palette（来自 `app.css :root`）

| Role | Token | Value | Usage |
| --- | --- | --- | --- |
| 页面底色 | `--paper` | `#f7f1e9` | body |
| 下沉底色 | `--paper-deep` | `#efe6da` | 分段控件底、输入区 |
| 卡片 | `--card` | `#fffdf9` | 主卡片、输入框 |
| 卡片下沉 | `--card-deep` | `#faf5ee` | 次级卡片、收起区 |
| 主文字 | `--ink` | `#292826` | 标题、主按钮底 |
| 次文字 | `--muted` | `#716e68` | 正文说明 |
| 弱文字 | `--dim` | `#8a7e70` | 辅助说明 |
| 更弱 | `--faint` | `#a89684` | 标签、页脚 |
| 最弱 | `--fainter` | `#b39b85` | 占位、隐藏入口 |
| 线 | `--line` | `#ddd2c4` | 卡片/输入边框 |
| 软线 | `--line-soft` | `#e3d8c9` | 分隔线 |
| 彰人橙 | `--akito` | `#ff7722` | 彰人语义、错误/强调的暖色 |
| 冬弥蓝 | `--toya` | `#0077dd` | 冬弥语义、信息/上传交互 |
| 危险 | `--danger` | `#ae382f` | 错误文字、危险按钮 |
| 危险线 | `--danger-line` | `#d8aaa5` | 错误边框 |

### Rules
- 交互强调只用 `--akito` / `--toya`，其余表面用纸色与墨色。
- 不新增品牌色；MySekai 页沿用 `--toya` 作为上传/焦点色。
- 正文与背景对比必须 ≥ 4.5:1；`--faint` 只用于辅助信息。

## 3. Typography

### Scale

| Level | Size | Weight | Line Height | Usage |
| --- | --- | --- | --- | --- |
| Hero | 52px（移动 27px） | 700 | 1.18 | 首页大标题（衬线） |
| Page title | 34px（移动 26px） | 700 | 1.25 | 子页面标题（衬线） |
| Section | 26px | 700 | 1.3 | 区块标题（衬线） |
| Card title | 18px | 700 | 1.4 | 卡片标题 |
| Body | 15px / 14px | 400 | 1.6 | 正文 |
| Small | 13px / 12.5px | 400 | 1.5 | 辅助说明 |
| Caption | 11px / 12px | 500–700 | 1.4 | 标签、eyebrow |
| Overline | 11px | 700 | 1.4 | `.eyebrow`，字距 .25em，大写 |

### Font Stack
- Sans：`"Noto Sans SC", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", system-ui, -apple-system, "Segoe UI", sans-serif`
- Serif：`"Source Han Serif CN", "Noto Serif SC", "Songti SC", "STSong", "SimSun", Georgia, serif`
- Numerals：`"Inter", "Segoe UI", system-ui, sans-serif`

### Rules
- 最多两套字族（sans + serif）；数字用 `--font-num`。
- 标题用 serif，按钮/正文用 sans。
- 正文不小于 12px；主要正文 14–15px。

## 4. Spacing & Layout

### Base Unit
4px。常用值：4 / 8 / 10 / 12 / 16 / 18 / 20 / 22 / 24 / 26 / 28 / 32 / 40 / 44 / 48。

### Grid
- 内容宽：`min(100% - 48px, 1280px)`；移动端 `min(100% - 40px, 720px)`。
- 顶栏高 88px（移动 68px）。
- 断点：1199 / 1023 / 899 / 759 / 560。
- 子页面主内容：`padding: 40px 0 80px`；移动端 24px 顶部。

### Rules
- 卡片内边距桌面 26px、移动 20px；卡片圆角 `--radius-card`（22px），移动 18px。
- 页面级间距用 24/32/40；控件内间距用 8/10/12。
- 不引入任意 px；新增间距先映射到 4px 基准。

## 5. Components

### Site Header
- **Structure**: `.site-header > .container.site-header__inner > .brand + nav`
- **Spacing**: min-height 88px（移动 68px）; container。
- **States**: brand hover 保持墨色；active nav 用 `--card` 底 + `--line` 边。
- **Accessibility**: `<header>` landmark；brand 是 `<a href="/">`。

### Control Card
- **Structure**: `.control-card` 包 `control-card__group` / `control-card__legend`。
- **Variants**: 表单卡、结果卡；padding 26px（移动 20px）。
- **Depth**: `1px solid var(--line)` + `--shadow-card`。

### Primary / Secondary Button
- Primary: 墨色底、卡片色字、min-height 54px、radius 14px、字距 .16em、100% 宽。
- Secondary: 透明底、`--line` 边、min-height 46px、radius 12px。
- **States**: hover `opacity: .9`; disabled `opacity: .5; cursor: wait`; focus-visible 全局 3px 橙环。
- **Motion**: opacity .15s ease。

### Segmented Control
- 3 列（两列变体 `--two`）；底 `--paper-deep`，选中项 `--card` + 轻阴影。
- 键盘：原生 radio + `:focus-visible` 环。

### Text Input
- 1px `--line`、radius 12px、`--card` 底、min-height 46px、padding 0 14px、15px 字。
- Focus: `--toya` 边 + `0 0 0 3px rgba(0,119,221,.12)`。
- Error: `--danger` 边 + `--danger-line` 辅助。

### Drop Zone
- 虚线 `--line`、radius 16px、`--card-deep` 底、padding 26px、居中。
- Hover/dragover: `--toya` 边 + `#eef6fd` 底。
- Focus-visible: 3px 蓝环。
- 内部：图标（SVG）、主提示 14px、副提示 12px `--faint`。

### Result Card
- `--card` 底、`--line` 边、radius 18px、overflow hidden。
- Figcaption：左侧标题（13px 700），右侧下载链接（12px `--toya`）。
- 图片：`display:block; width:100%; height:auto;` 带 width/height 属性防 CLS。

### Profile Strip
- 44px 圆角头像 + 昵称（16px 700）+ ID（12px `--faint`）+ 推送状态 pill。
- 头像缺省时用 `--paper-deep` 方块 + 首字。

### Push Badge
- pill，12px；`is-ok` 用绿（`#2f7d4f`，新增语义色）、`is-warn` 用 `--faint`、
  `is-error` 用 `--danger`。

## 6. Motion & Interaction

| Type | Duration | Easing | Usage |
| --- | --- | --- | --- |
| Micro | 150ms | ease | hover / 按钮透明度 |
| Standard | 200ms | ease | 面板显隐、分段切换 |
| Emphasis | 300ms | cubic-bezier(.16,1,.3,1) | 结果区进入 |
| Error shake | 320ms | ease-in-out | 密码错误 |

- 只动画 `transform` / `opacity`；不动画布局属性。
- 所有交互元素有 hover / active / focus-visible。
- `prefers-reduced-motion: reduce` 时关闭非必要动画（现有 `app.css` 已有此规则）。

## 7. Depth & Surface

**Mixed**：卡片用 1px 暖色边 + 很轻的暖阴影；浮层/弹窗用 `--shadow-float`。
- `--shadow-card`: `0 14px 36px -6px rgba(66, 48, 32, .1)`
- `--shadow-float`: `0 14px 30px -8px rgba(66, 48, 32, .16)`
- 不用冷色阴影；不用大面积玻璃拟态（底部导航的 `backdrop-filter` 是既有例外）。

## 8. Accessibility Constraints & Accepted Debt

### Constraints
- WCAG 2.2 AA：正文 ≥ 4.5:1，大字号 ≥ 3:1；所有交互可键盘到达；可见 focus。
- `prefers-reduced-motion` 关闭抖动/入场动画。
- 表单 label 与 input 显式关联；状态用 `role="status"` + `aria-live`。
- 结果图片带 `alt`；上传区是可点击的 `<button>`，支持拖拽与键盘。

### Accepted Debt
| Item | Location | Why accepted | Owner / Exit |
| --- | --- | --- | --- |
| 现有 `app.css` 中存在少量非 token 的 px/颜色（历史样式） | `app/static/app.css` | 本次只新增页面，不做全站重构 | 后续统一 token 化 |
| 主站 CSP 允许一个 inline style hash | `app/main.py` | 既有页面需要 | 迁移到外部 CSS 后移除 |
| 结果图以 data URL 返回 | `/roomlist` | 本地/群友工具，避免额外的临时存储 | 若并发上升改为结果 ID + 短期 URL |
