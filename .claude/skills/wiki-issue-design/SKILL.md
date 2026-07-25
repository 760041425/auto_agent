---
name: wiki-issue-design
description: 「需求设计讨论」全流程技能（通用研发流，跨 git 项目复用；典型项目：示例项目）——从模糊想法到一份可开发的 GitHub Issue，走「挖掘 → 分析过滤 → 原型方案设计 → 评审 → 变更追踪」五阶段闭环，全程以多轮 AskUserQuestion ASK 模式与用户对齐，所有产出物落在 `docs/issue/<NNN>-<日期>-<feature>/` 目录。当用户说「我们创建一个需求」「更新用户需求」「设计一个需求」「讨论需求」「/wiki-issue-design」「wiki-issue-design」「需求设计」「梳理需求」「做需求方案」「写 PRD」「需求评审」时触发。区别于 `/wiki-issue-dev`（直接开发关 issue），本技能只负责「想清楚 + 设计 + 落 Issue」，不写业务代码；收尾串联 `/wiki-issue-dev` 进入开发。严格遵循项目根 `CLAUDE.md` 的 SoT 文档约束，Web 原型必读项目 UI 规范（典型 `docs/rules/web-ui-spec.md`），需求争议/优先级/方案决策一律走 AskUserQuestion，禁止替用户拍板。
---

<!-- wiki-common-rule:v1 -->
## 0. 通用交互与汇报规约（全技能通用 · 优先级高于下文具体步骤的表述习惯）

**语言**：面向用户的所有叙述、标题、汇报一律用**简体中文**（代码 / 命令 / 路径 / 标识符 / 引用的英文报错原文除外）。即使 subagent、工具或 CI 返回英文或日文，回写给用户时必须翻成简体中文，全程不得切换语种。

**收尾汇报**：本技能每跑完一个阶段或全部完成时，**必须**：
1. 用 Markdown **表格**汇报结果（典型列：步骤 / 事项、状态、产物或证据链接、备注）；
2. 表格后另起「**下一步建议**」小节，给出 **1–3 条可直接执行**的动作——可复制的 shell / `gh` 命令，或 `/wiki-*` 串联指令；禁止只写「可以继续优化」这类空话。
<!-- /wiki-common-rule:v1 -->

<!-- project-profile:v1 -->
## 项目环境档案（跨项目复用 · 由 _shared/project-profile.md 提供）

本技能所有环境值（线上地址 `PROD_URL`、仓库 `GIT_REPO`、测试 namespace `DEPLOY_NAMESPACE`、
kubeconfig `KUBECONFIG_PATH` 等）一律从**当前项目根 `CLAUDE.md` 的《项目环境档案》小节**读取，
不在本技能写死。读取约定、完整变量字典、以及「项目缺档案时先引导用户补最小档案」的前置流程见
`~/.claude/skills/_shared/project-profile.md`。**主流程动手前先执行该文件 §3 的缺档案前置检查。**

```bash
# === 解析项目《环境档案》（见 ~/.claude/skills/_shared/project-profile.md §3）===
# 执行下列命令前，先读「当前项目根 CLAUDE.md」的 <!-- project-profile:v1 --> 表，把以下变量代入实际值
# （本项目即 your-org/your-repo / staging / ~/.kube/config / https://app.example.com / 10.0.0.10）。
# 若项目无该档案，先按 _shared/project-profile.md §3 用 AskUserQuestion 引导用户补最小档案再继续。
PROJECT_ROOT=$(git rev-parse --show-toplevel)
GIT_REPO="<档案 GIT_REPO>"               # 例 your-org/your-repo
DEPLOY_NS="<档案 DEPLOY_NAMESPACE>"       # 例 staging
KUBECONFIG_PATH="<档案 KUBECONFIG_PATH>"  # 例 ~/.kube/config
PROD_URL="<档案 PROD_URL>"                # 例 https://app.example.com
INTERNAL_SLB="<档案 INTERNAL_SLB>"        # 例 10.0.0.10（可选）
```
<!-- /project-profile:v1 -->

<!-- parallel-default-rule:v1 -->
## 0. 并行化默认规则（全技能通用）

**当本技能识别出任务可拆为 ≥5 个相互独立的子单元**（典型：竞品逐个调研、多个用户画像访谈、多份原型方案各自渲染等），**优先**走以下骨架：

1. 用一个前台 subagent 完成调研 + 拆分，输出 5–30 个独立工作单元。
2. 对每个单元用 `Agent` 工具发起 subagent（调研类用 `Explore` / `general-purpose`，纯读不写不必 worktree）。
3. 在一条消息里一次性发起全部 subagent（多 Agent 工具调用并列），最大化并行度。
4. 协调者只负责：渲染进度表 → 收结论 → 汇总。

**红线**：
- 产出物目录 `docs/issue/...` 在 git 仓库内；阶段 1–3 写文档时**不需要** worktree 隔离，在主检出工作区顺序写即可。**唯一例外**：阶段 4.1.5「设计文档 + `spec.md` 入库」要 push main，**必须**在临时 worktree 上提交（绝不在主检出 checkout/commit/push，避免成为多 session 互斥点）。
- 任何需要**用户决策**的环节（ASK 模式）**必须由主协调者亲自串行执行**，绝不下放给后台 subagent。
- 单元 <5 或强依赖 → 直接顺序。

> ⚠️ **本技能是「人在环路（human-in-the-loop）」设计技能**：核心价值在每阶段与用户多轮对齐。主流程**强制顺序**，并行仅可用于「阶段 1 竞品/用户调研」「阶段 3 多方案原型渲染」这类纯调研/纯生成的内部子任务，且产出汇总后仍要回到主流程让用户拍板。

---

## 1. 技能定位与边界

| 维度 | 说明 |
|---|---|
| **做什么** | 把一个模糊需求「想清楚、设计完、对齐好」，最终落成一份高质量、可直接派给开发的 GitHub Issue + 一套设计文档 + 一套**完整的 spec 七件套**（`spec.md` / `plan.md` / `tasks.md` / `testlist.md` / `clarify.md` / `decisions.md` / `checklist.md` 全部 design 阶段产齐，见阶段 4.1.3） |
| **不做什么** | ❌ 不写业务代码 ❌ 不跑 K8s 实测 ❌ 不合**业务代码**到 main。那是 `/wiki-issue-dev` / `/wiki-bug-fix` 的事。⚠️ 但 **`<需求目录>` 设计文档是 SoT，必须 docs-only 入库到 main**（见阶段 4.1.5），否则 Issue 链接死链 + 下游 dev 在干净 worktree 里读不到 PRD。✅ 阶段 4.1.3 的**完整 spec 七件套**同样随设计文档一起入库 main（design 一次性把七件套写完整完善，dev 步骤 ② 仅做「校验 + 增量微调」全部七件套，**不再从零新建任何一件**） |
| **唯一产出根目录** | `docs/issue/<NNN>-<YYYYMMDD>-<feature-slug>/`（下称 `<需求目录>`） |
| **决策原则** | 凡「真伪/优先级/方案/边界」有歧义 → **必走 `AskUserQuestion`**，禁止 Claude 替用户拍板 |
| **SoT 约束** | 遵循项目根 `CLAUDE.md`；Web 原型设计**必读**项目 UI 规范（典型 `docs/rules/web-ui-spec.md`，配色/字体/间距/antd+Tailwind 组件） |
| **收尾** | 末尾独立一行输出 `/wiki-issue-dev 请基于 <需求目录>/PRD.md 进入开发闭环` 串联指令 |

**需求目录命名规约**：
- `<NNN>` = 三位零填充递增序号（扫 `docs/issue/` 下已有目录取 max+1，无则 `001`）
- `<YYYYMMDD>` = 当天日期（见会话顶部 currentDate，如 `20260601`）
- `<feature-slug>` = 简短英文/拼音连字符 slug（如 `frame-extract-preview`）
- 示例：`docs/issue/001-20260601-frame-extract-preview/`

---

## 阶段 1 · 需求挖掘 (Elicitation)

**目标**：搞清楚用户和业务的底层痛点，而不是表面诉求。

### 1.1 建需求目录
- 扫 `docs/issue/` 取下一个序号，`mkdir -p docs/issue/<NNN>-<YYYYMMDD>-<feature-slug>/`。
- 在目录内建占位 `README.md`（写：需求一句话标题 + 创建日期 + 当前阶段进度勾选表）。

> ### 🔴 调研硬约束（强制联网 + 近 2 年信息，缺一不合格）
>
> 需求挖掘**禁止只凭模型记忆/常识脑补**。阶段 1 必须真实发起**联网检索**（`WebSearch` / `WebFetch`），且：
> - **强制至少 1 轮 `WebSearch`**：用户调研 + 竞品调研各至少 1 次真实联网检索（不是"我了解到…"，要有真实搜索动作 + 可点击来源 URL）。
> - **信息时效：只取近 2 年（≥2024 年至今；当前 2026-06）**。检索词显式带年份/`2025`/`2026`/`latest`；命中更老的资料要么弃用、要么标注"过时，仅作背景"。技术/竞品/行业基准 2 年前的数据基本失真，不许当现状引用。
> - **每条引用必带 `[来源标题](URL) · 发布/更新日期`**，日期早于 2024 的不计入"现状证据"。
> - 联网失败（脱网/被墙）→ 不许静默跳过：在 backlog 显式标注「本条未能联网核实，待补」，并提示用户（参考 [[public_network_dev_fallback]] 公网兜底）。

### 1.2 用户调研（强制联网 + 近 2 年）
- 用 `Explore` / `general-purpose` subagent + **`WebSearch`/`WebFetch`（强制真实发起）**，调研**目标用户对这类功能的真实感受/吐槽**（社区、论坛、issue 区、用户访谈记录、近期评测）。
- 检索词带时间限定（如 `<功能关键词> 2025 体验 吐槽` / `<product> review 2026`），**只采信近 2 年内容**，引用标注来源 URL + 日期。
- 同步检索本项目内已有线索：`grep`/`Glob` 扫 `docs/`、`web/specs`、`algo/specs`、历史 `docs/issue/`，看是否已有相关需求/抱怨/half-done。

### 1.3 竞品 & 数据分析（强制联网 + 近 2 年 + ≥3 竞品）
- **强制联网**调研 **≥3 个竞品/同类产品近 2 年怎么做这件事**（功能形态、交互、定价、口碑、近期更新/版本说明）；竞品的能力以**其最近 2 年的版本/文档/发布说明为准**，不许引用停更或过时形态。
- 引入可量化数据时同样限定近 2 年（使用频次、转化、行业基准、市场份额），每个数字带来源 + 统计时间。
- 竞品调研可并行：每个竞品一个 subagent（各自带 `WebSearch`），结果汇总成对比表，**表内每行带「信息日期」列**。

### 1.4 多轮 ASK 获取需求信息（强制）
- 用 `AskUserQuestion` 与用户多轮交互，至少澄清：**谁用 / 解决什么痛点 / 触发场景 / 期望结果 / 不要什么（反需求）**。
- 一轮问不清就继续问，直到痛点收敛。每轮都把用户回答回写进 Backlog。

### 1.5 产出物 → `<需求目录>/01-backlog.md`
- 原始需求清单（Backlog）：每条含「需求描述 / 来源（用户原话 or 调研出处）/ 关联竞品做法 / 初步价值假设」。
- 末尾附「用户调研纪要」「竞品对比表」两节作为证据：
  - **「调研来源」小节强制列出本轮所有联网检索的引用**，每条 `[标题](URL) · 发布/更新日期`，**日期须在近 2 年内（≥2024）**；竞品对比表含「信息日期」列。
  - 凡未能联网核实的条目，显式标注「未联网核实，待补」，不得伪装成已核实。

---

## 阶段 2 · 需求分析与过滤 (Analysis)

**目标**：检索整体项目情况，鉴别需求真伪，确定哪些做、哪些不做。

### 2.1 评估与排序
- 逐条评估 **价值 / 技术可行性 / 商业 ROI**（结合阶段 1 调研 + 本项目代码现状）。
- 用 **KANO 模型**（基本型/期望型/兴奋型/无差异/反向）+ **P0/P1/P2** 双维度排序，输出表格。
- 标出「**建议不做**」的需求并写明理由（伪需求/ROI 过低/超出边界）。

### 2.2 多轮 ASK 确认优先级（强制）
- 用 `AskUserQuestion` 让用户对「做/不做」「P0/P1/P2」「KANO 归类」逐项拍板。
- 有分歧的条目必须问到用户明确选择，不许 Claude 默认决定。

### 2.3 产出物 → `<需求目录>/02-analysis.md`
- 明确划分优先级的**核心功能列表**（含 KANO + P0/P1/P2 + 做/不做裁决 + 用户确认记录）。

---

## 阶段 3 · 原型与方案设计 (Design)

**目标**：把抽象需求具象化，输出逻辑细节。

### 3.1 业务流程 & 状态机
- 梳理业务流程图（Flowchart）与状态机；优先复用 `/wiki-draw-diagram` 产出 HTML+SVG 图。
- 落 `<需求目录>/03-flowchart.html`（或 `.md` 内嵌 mermaid）。

### 3.2 PRD 初稿
- 编写产品需求文档（PRD）：**功能边界 / 核心规则 / 正常流 / 异常分支 / 边界条件 / 验收标准**。
- 落 `<需求目录>/PRD.md`（这是后续 `/wiki-issue-dev` 的输入，务必可执行、可验收）。
- **验收标准必须带 AC-ID + 可勾选 + 可测**（统一追溯标准见 [`_shared/test-traceability-and-assets.md`](../_shared/test-traceability-and-assets.md) §1），供 dev/acceptance 端到端继承，禁止下游重定义：
  ```markdown
  ## 验收标准
  - [ ] AC-<spec>-01 <一句可测断言，如"上传 >2GB 文件提示超限并拒绝">
  - [ ] AC-<spec>-02 ...
  ```
  > AC 是"可勾选可测断言"，不是模糊目标（"体验好"）；前端需求的 AC 必须能在浏览器里验（对应 [`_shared/frontend-browser-testing.md`](../_shared/frontend-browser-testing.md)）。

### 3.3 Web 变更 → 3 套原型方案（强制用户决策）
> 仅当需求涉及前端界面变更时执行；纯后端/算法需求跳过本节。
- **必读** `docs/rules/web-ui-spec.md`，原型严格遵循项目 UI 规范（配色/字体/间距/antd+Tailwind 组件），并参考 `docs/product/ui-mockups/` 既有 mockup 风格。
- 用 HTML 各出 **3 个不同设计方案**（布局/交互各异），落 `<需求目录>/prototypes/v1.html` `v2.html` `v3.html`。
- **用浏览器打开**给用户看：优先用 `open` 命令打开本地 HTML，或 playwright/Claude-in-Chrome 截图对比。
- 三方案各写一句「设计取舍说明」。

### 3.4 多轮 ASK 决策最终方案（强制）
- 用 `AskUserQuestion`（可带 `preview` 展示各方案要点）让用户在 3 套方案中选定 / 组合。
- 把决策结果回写 PRD，更新选中原型为 `<需求目录>/prototypes/final.html`。

### 3.5 产出物 → 最终版 PRD + 排期表
- 更新 `<需求目录>/PRD.md` 为最终版。
- 落 `<需求目录>/04-schedule.md`：粗排期表（里程碑 / 预估工时 / 依赖项 / 上线计划）。

---

## 阶段 4 · 需求评审 (Review)

**目标**：从工程视角评审需求，创建 / 完善 spec 轻骨架，落成 GitHub Issue。

### 4.1 开发视角影响评估（表格）
- 从开发视角评估该需求对系统的影响，**用表格**展示，维度至少含：

| 维度 | 评估内容 |
|---|---|
| 功能影响 | 涉及哪些模块/接口/页面/DB 表 |
| 性能影响 | 是否引入慢查询/大数据量/高并发风险 |
| 成本影响 | 存储/算力/第三方依赖/人力工时 |
| 风险与依赖 | 上下游依赖、扩散影响面（参 CLAUDE.md §7 扩散排查）、回滚方案 |
| 工时预估 | Estimation（人天）+ 建议上线计划 |

- 评估证据尽量带 `file:line` 锚点，禁止脑补。
- **本表的「功能影响 / 风险与依赖」要在 4.1.3 同步落进 `spec.md` 的 §3 范围 / §8 风险与缓解 两节**，避免影响评估只活在 Issue body 不进 SoT。

### 4.1.3 spec 创建 / 完善（**完整七件套**，**在 4.1.5 入库之前 / 建 Issue 之前**）

> spec（spec 目录见项目《环境档案》`SPEC_DIRS`，例：`web/specs/` 或 `algo/specs/`）是项目的**工程 SoT**。本步在生成正式 Issue 前，必须把需求落成一套**完整的 spec 七件套**——参考项目现存最高质量 spec（示例项目 如 `web/specs/017-data-augmentation/spec.md` ~190 行、`web/specs/024-model-weights-management/spec.md` ~150 行、`web/specs/018-robot-lingshi-ingestion/spec.md` ~130 行）的形态，**绝不输出四段轻骨架**。让 Issue 标题的 `SPEC-XX`、正文的 spec 链接都指向真实存在 + 内容完整的文件，并给下游 `/wiki-issue-dev` 步骤 ② 一个**七件套全齐**的可直接增量的起点。
>
> **职责边界（与 dev 步骤 ② 协调，已升级为 design 一次性产齐 · 用户拍板）**：本步必须一次性产出 / 完善**全部七件套**到同一个 `SPEC_PATH`（如 `web/specs/<id>-<name>/` 或 `algo/specs/<id>-<name>/`）：
>
> | 文件 | 用途 | 质量门 |
> |---|---|---|
> | `spec.md` | SoT 主体，11 段必写（详见 4.1.3.2） | **≥ 100 行**（典型 130–200），frontmatter 9 字段齐 |
> | `plan.md` | 技术方案：数据模型 / API 设计 / 模块拆分 / 集成点 / 部署拓扑 | **≥ 80 行**，覆盖范围内全部模块 |
> | `tasks.md` | 任务拆解清单（T-01..T-NN，每条带预估工时 + 依赖 + AC 映射） | **≥ 10 条**，覆盖 In Scope 全部范围 |
> | `testlist.md` | 测试用例清单（继承 PRD AC-ID，每条列「层级 / 文件 / 断言要点 / 在哪验」） | 用例数 **≥ PRD AC 总数 × 1.5**，覆盖 P0/P1/P2 各级 |
> | `clarify.md` | 需求澄清记录：阶段 1.4 / 2.2 / 3.4 全部 `AskUserQuestion` 问答原文 + 拍板结论 | **至少含 3 轮 ASK 原文**（挖掘 / 优先级 / 方案选定） |
> | `decisions.md` | 关键技术决策（ADR 风格：背景 / 选项 / 选择 / 理由 / 取舍） | **≥ 3 条 ADR**，每条带时间 + 负责人 |
> | `checklist.md` | 开发自检清单：Pre-merge / 扩散排查 / K8s 部署 / 安全 / 性能 多维 checkbox | **≥ 20 条**可勾选条目 |
>
> 七件套全部产齐后随 `<需求目录>` 一起入库 main（4.1.5），dev 步骤 ② 对七件套统一做「校验 + 增量微调」，**绝不再从零新建任何一件**；遇代码现状与文档偏离按增量更新走，并在对应文件追加 Changelog。
>
> **整体质量门**：任一文件不达标 → 整体不达标，必须补齐后才能进 4.1.5 入库。**绝不允许只产 `spec.md` 一个文件**。

#### 4.1.3.1 判定 spec 路径（增量 or 新建）
- 扫已有 spec 决定归属：`ls web/specs/ algo/specs/ | sort -V`。
- **增量到已有 spec**（需求归属某已存在编号）→ `Edit` 该 `spec.md`，在「目标 / 范围 / 验收判据 / 用户故事 / Changelog」相应段落补本需求；末尾 `## Changelog` 必加一行带 Issue 链接的变更说明。增量时**不得删除既有段落**，仅追加 / 扩展。
- **新建 spec**（无明显归属）→ `Write` 到 `web/specs/<新编号-kebab-name>/spec.md` 或 `algo/specs/<新编号-kebab-name>/spec.md`。新编号规则与 dev 对齐：取 `sort -V` 末位按现有命名风格递增（数字 / 数字+字母后缀）。
- 归属拿不准（多 spec 都沾边 / 无明显落点）→ 走 `AskUserQuestion` 让用户拍板，禁止 Claude 默认。

#### 4.1.3.2 `spec.md` 完善骨架（11 段必写，对齐现存高质量 spec）

**只产出 `spec.md` 一个文件**；内容**全部从已定稿的 PRD / 阶段 1–3 调研产物提炼**，不重定义、不脑补。强制 11 段如下：

```markdown
---
spec: <NNN>
title: <spec 标题（中文）>
status: proposed                          # proposed → in_progress → done → archived
issue: <建 Issue 后由 4.2 回填，如 https://github.com/<GIT_REPO>/issues/NNN；此刻先留 TODO>
design: docs/issue/<NNN>-<YYYYMMDD>-<feature-slug>/PRD.md
owner: <bounded context 名 / 责任团队，如 sample-production-context>
created: <YYYY-MM-DD>
updated: <YYYY-MM-DD>
depends_on:
  - <前置 spec 编号-名>                   # 至少 1 条
related:
  - ../../CLAUDE.md
  - <相关 spec 路径>
  - <关联 algo spec 路径（若涉算法）>
  - <关联 BC / decision / 竞品 wiki 路径>
---

# Spec <NNN>:<标题>

**上下文**:<bounded-context 名 + 链接>
**优先级**:P0 / P1 / P2（与 PRD 一致）
**阶段**:WXX-WYY（起止周次 + 目标完工日期）
**前置 Spec**:[Spec NNN <名>](../<NNN-name>/spec.md)
**相关 Spec**:<横向相关 spec>
**算法侧 SDD**:[algo/specs/NNN-...](../../../algo/specs/<NNN-name>/spec.md)（若涉算法）
**跟踪 Issue**:<建 Issue 后回填 #num>

---

## 1. 背景与动机

### 1.1 业务痛点
> 必须**量化**(表格 + 数字 + 数据来源)；禁止空泛形容词。最少 2 行痛点,每行含「现状量化」+「根因」两列。

| 痛点 | 现状量化 | 根因 |
|---|---|---|
| <一句话痛点> | <带数字的现状,如「FN 率 8.7%(spec-007a 评估基线)」> | <为什么会这样> |
| ... | ... | ... |

### 1.2 现状能力 gap
> 引用现有 spec/代码锚点说明「目前已覆盖什么」+「缺什么」;算法工程师 / 用户当前的**临时绕行方案**也要点名(typically 3 条),凸显 ROI。

1. <临时方案 1,如「离线在自己电脑跑 X 然后压 zip 上传」(无 lineage / 无去重 / 无审核)>
2. <临时方案 2>
3. <临时方案 3>

### 1.3 为什么独立成 <模块 / spec / 二级菜单>
> 解释「为什么要独立 spec / 独立 BC / 独立菜单组」,从工作流形态、技术栈、上下游解耦三个角度论证。若是子 spec(如 `024b-…`)则说明与父 spec 的边界。

---

## 2. 目标

> 用 **G1-Gn 编号 + 「验证方式」列**表格,每个目标必须能映射到下游 testlist 的具体用例编号(本节先占位,如 `testlist 用例 #4-7`)。

| # | 目标 | 验证方式 |
|---|------|---------|
| G1 | <可独立验证的目标 1> | <浏览器实测 / 单测 / E2E / K8s 真跑> |
| G2 | ... | ... |

---

## 3. 范围

### 3.1 In Scope(M1, <X 周>)

> 必须**层级化**(典型分组:菜单与路由 → 各前端页面 → 后端 API → 数据模型 → 上游集成 → 测试 → K8s 部署);每一组列出**具体文件 / 端点 / 表名**,不许"做 XX 模块"这种空话。

#### 3.1.1 菜单与路由
- 在 [`web/ui/src/data/nav-config.ts`](../../../web/ui/src/data/nav-config.ts) 的 `/<父菜单>` 模块下新增 `<本 spec slug>`:
```
/<父菜单>
├── <既有兄弟项>
└── <新增项 ← 本 spec>
    ├── /<父>/<子1>     <一句话功能>
    └── /<父>/<子2>     <一句话功能>
```
- **菜单 parity 守门**:同步加在 [`web/backend/src/common/rbac/menu_registry.py`](../../backend/src/common/rbac/menu_registry.py),避免双 SoT drift([[menu_registry_dual_sot_drift]] 已踩过)。

#### 3.1.2 <前端页面 1> `/<路由>`
- **列表 / 详情 / 操作**:列出每个页面的列 / 筛选项 / 操作按钮(参考 PRD 已选定的 final.html 原型)。

#### 3.1.3 <后端 API>(<X 个 endpoint,统一前缀 `/api/<prefix>`>)
- `<METHOD> /api/<prefix>` — <用途>
- ...

#### 3.1.4 <数据层>
- 新建 ORM `web/backend/src/models/<name>.py`
- 新建 migration `<NNN>_<name>.py`(含表 / 索引 / 约束)

#### 3.1.5 <上游 / 兄弟 spec 集成>
- **<上游 spec>**:[Spec NNN](../<NNN-name>/spec.md) 新增 <字段 / 筛选项>,详情抽屉新增 <时间线节点>
- ...

#### 3.1.6 <测试>
- 后端单测 `web/tests/api/test_<name>.py` ≥ N 例
- 前端单测 `web/ui/src/pages/<…>/__tests__/` ≥ N 例

#### 3.1.7 K8s 部署(若涉及)
- 测试环境 K8s namespace（见项目《环境档案》DEPLOY_NAMESPACE）,N 个 Deployment + N 个 Service + N 个 ConfigMap。

### 3.2 Out of Scope(移到 M2 / M3 / backlog)
- ❌ <明确不做的 1>(<为什么不做 / 移到哪>)
- ❌ <明确不做的 2>
- ...

---

## 4. 用户故事

| ID | 角色 | 故事 |
|---|---|---|
| US-01 | <算法工程师 / 标注员 / 数据 curator / ops / 平台管理员> | <作为 X,我想 Y,以便 Z> |
| US-02 | ... | ... |

> 至少 3 条,角色不重复;每条对应一个真实的端到端使用闭环,不是 CRUD 操作清单。

---

## 5. 验收判据(关 Issue 时逐条勾选)

> **AC-ID 必须继承 PRD**(阶段 3.2 已分配的 `AC-<spec>-NN`):design ↔ `spec.md` ↔ 下游 testlist ↔ acceptance 全程同一套 ID,**禁止在此重编号**。
> 分 P0(必过)/ P1(应过)/ P2(可选) 三级;每条带「在哪验」(浏览器 / 后端单测 / E2E / 测试环境 K8s 实跑,namespace 见《环境档案》DEPLOY_NAMESPACE)。

### 5.1 P0(必过)
- [ ] **AC-<spec>-01** <一句可测断言> — <在哪验>
- [ ] **AC-<spec>-02** ...

### 5.2 P1(应过)
- [ ] **AC-<spec>-P1-01** ...

### 5.3 P2(可选)
- [ ] **AC-<spec>-P2-01** ...

---

## 6. 非目标 / 边界澄清

> 与 §3.2 Out of Scope 互补:这里写**容易被误解为本 spec 范围、但实际不做**的边界,逐条点名相关 spec / 历史决策,防 scope creep。

- 不替代 <既有能力 X>(归属 [Spec NNN](../<NNN-name>/spec.md))
- 不引入新 UI 库(沿用 AntD + Tailwind + lucide,符合 [`docs/rules/web-ui-spec.md`](../../../docs/rules/web-ui-spec.md))
- 不做 <能力 Y>(M2 范围,等 <前置依赖> 收敛)
- ...

---

## 7. 数据模型(若涉及)

> 若涉表结构变更:列出新增 / 修改的表 + 关键列 + 索引 + 约束。**细节落 plan.md §数据模型**,本节只占位 + 链接,避免与 plan 双 SoT drift。

- 新增 N 张表(详见 [plan.md §数据模型](plan.md))
- 关键约束:`<sha256 UNIQUE>` / `<status enum CHECK>` / ...

---

## 8. 风险与缓解

| 风险 | 缓解 |
|---|---|
| <技术 / 业务 / 运维风险 1> | <具体缓解措施 + 复用 helper / 范式锚点> |
| <风险 2> | ... |

> 至少 3 条;典型来源:依赖外部服务、性能瓶颈、数据迁移、跨 BC 联动、踩过的 anti-pattern(参 [`docs/practices/antipatterns.md`](../../../docs/practices/antipatterns.md))。

---

## 9. Mockups(若涉前端)

> 列阶段 3.3 产出的 3 套原型方案 + 最终选定方案(`prototypes/final.html`),给下游 dev 直接参考。

| 方案 | 文件 | 风格 | 用途 |
|---|---|---|---|
| A · <风格名> | [<路径>](../../../docs/issue/<…>/prototypes/v1.html) | <一句风格描述> | 本期落地依据 / 二期参考 / power-user 入口 |
| B · ... | ... | ... | ... |
| C · ... | ... | ... | ... |

---

## 10. 关联文档 / 反向链接

> 出链 ≥ 5,入链 ≥ 1,覆盖以下维度:

- **上游**:[Spec NNN <名>](../<NNN-name>/spec.md)
- **下游**:[Spec NNN <名>](../<NNN-name>/spec.md)
- **配套算法 SDD**:[algo/specs/NNN-...](../../../algo/specs/<NNN-name>/spec.md)(若涉算法)
- **限界上下文**:[<bc 名>-context](../../../docs/practices/bounded-contexts/<bc-name>.md)
- **关键决策**:[TD-NNNN <名>](../../../docs/practices/decisions/TD-NNNN-<name>.md)
- **竞品调研**:[<竞品 X>](../../../docs/wiki/case-studies/<x>.md) / ...
- **核心概念**:[[../../docs/concepts/mlops/<name>]] / ...
- **规则**:[CLAUDE.md](../../CLAUDE.md) §<编号> <章节>
- **落地**:[plan.md](plan.md) / [decisions.md](decisions.md) / [tasks.md](tasks.md) / [testlist.md](testlist.md) / [checklist.md](checklist.md) / [clarify.md](clarify.md)(design 阶段一次性产齐,dev 步骤 ② 仅校验微调)

---

## 11. Changelog

- <YYYY-MM-DD>:创建 spec,对应 [Issue #<num>](https://github.com/<GIT_REPO>/issues/<num>) / 需求设计 `docs/issue/<NNN>-<YYYYMMDD>-<feature-slug>/`
```

#### 4.1.3.3 内容质量硬约束(交付前自查)

- **frontmatter 必填全 9 字段**(spec/title/status/issue/design/owner/created/updated/depends_on/related);**不许只有 issue + design 两行**。
- **§1.1 业务痛点表格必有数字**;若 PRD 调研未拿到量化数据,显式标注「(待补 — 联网检索未命中)」并在 Issue 正文同步标注,**不许编造**。
- **§2 目标 G1-Gn 必须编号 + 「验证方式」列**;数量与 PRD 目标对齐,不许漏。
- **§3.1 In Scope 必须层级化**(菜单/路由 → 页面 → API → 数据层 → 集成 → 测试 → K8s),每一组带**具体文件 / 端点 / 表名**;不许"做 XX 模块"这种空话。
- **§4 用户故事 ≥ 3 条**,角色不重复;每条对应一个端到端闭环。
- **§5 验收判据继承 PRD AC-ID**,**禁止重新分配**;分 P0/P1/P2 三级;每条带「在哪验」。
- **§8 风险与缓解 ≥ 3 条**;至少 1 条引用项目反模式知识库（典型 `docs/practices/antipatterns.md`）已踩过的 anti-pattern(若适用)。
- **§10 关联文档出链 ≥ 5**,入链 ≥ 1(被父 spec 或 `docs/index` 链入)。
- **行数 ≥ 100**,典型 130–200 行;少于 100 行视为不达标,补背景量化 / 范围层级化 / 用户故事 / 关联文档。
- **若涉前端**:§9 Mockups 必填,链接到 `docs/issue/<…>/prototypes/{v1,v2,v3,final}.html`。
- **若涉算法**:frontmatter `related` 必须含 `algo/specs/<…>/spec.md`;§元信息行有「算法侧 SDD」字段。

#### 4.1.3.4 产出物
- 新建 / 改动的 **完整七件套**全部落到同一个 `SPEC_PATH`(如 `web/specs/<id>-<name>/`):`spec.md` / `plan.md` / `tasks.md` / `testlist.md` / `clarify.md` / `decisions.md` / `checklist.md`,供 **4.1.5 一起入库**、**4.2 建 Issue 引用**、**收尾串联指令透传给 dev**。
- 同步在 `<需求目录>/README.md` 进度勾选表为每件加一行「`<file>` 已建于 `SPEC_PATH`,行数 N / 条目数 N」作为质量门可查证据(共 7 行)。
- **缺一件即整体不达标**,不得进 4.1.5 入库。

### 4.1.5 设计文档 + spec 七件套入库（docs-only push main，**强制，必须在建 Issue 之前**）

> 这是本技能此前缺失的一步。`<需求目录>` 与 4.1.3 的**完整 spec 七件套**都是 SoT，**不入库会导致：① Issue body 里的 `docs/issue/...` 链接在 GitHub 上 404；② 下游 `/wiki-issue-dev` 在基于 `origin/main` 的干净 worktree 里读不到 PRD / 七件套；③ Issue 标题的 `SPEC-XX` / spec 链接死链 + dev 步骤 ② 读不到可增量的 spec 起点**。纯文档零风险，走 CLAUDE.md §5.1 **direct 轨 docs-only** 直推。设计文档与 spec 七件套 **同一次 worktree 提交一起推**。

```bash
PROJECT_ROOT=$(git rev-parse --show-toplevel)              # 项目主检出（例：/Users/.../your-repo）
DESIGN_DIR="docs/issue/<NNN>-<YYYYMMDD>-<feature-slug>"     # = <需求目录>
SPEC_PATH="web/specs/<id>-<name>"                           # = 4.1.3 确定的 spec 路径（或 algo/specs/<id>-<name>）

# 多 session 安全：在临时 worktree 上提交，绝不在主检出 checkout/commit/push
TS="$(date +%Y%m%d-%H%M%S)-$$"
WT="${PROJECT_ROOT}/.claude/worktrees/design-commit-${TS}"
git -C "$PROJECT_ROOT" fetch origin main
git -C "$PROJECT_ROOT" worktree add "$WT" -b "wip/design-${TS}" origin/main

# 把设计产物 + spec 七件套整个目录拷进 worktree（均写在主检出工作区 → 同步过去）
# 注意：design 阶段一次性产齐七件套 → 整目录拷贝，确保 spec.md / plan.md / tasks.md / testlist.md / clarify.md / decisions.md / checklist.md 全部入库
mkdir -p "$WT/$(dirname "$DESIGN_DIR")" "$WT/$(dirname "$SPEC_PATH")"
cp -R "$PROJECT_ROOT/$DESIGN_DIR" "$WT/$(dirname "$DESIGN_DIR")/"
cp -R "$PROJECT_ROOT/$SPEC_PATH"  "$WT/$(dirname "$SPEC_PATH")/"   # 整个 spec 目录（七件套）

# 入库前自检：七件套全部存在 + 行数/条目不低于质量门
for f in spec.md plan.md tasks.md testlist.md clarify.md decisions.md checklist.md; do
  [ -f "$WT/$SPEC_PATH/$f" ] || { echo "❌ 七件套缺 $f，停下补齐"; exit 1; }
done

cd "$WT"
git add "$DESIGN_DIR/" "$SPEC_PATH/"                          # 设计目录 + 完整 spec 七件套
git commit -m "docs(issue): <feature> 需求设计（PRD + 调研 + 原型 + 排期 + spec 七件套）"

# rebase + retry 推 main（最多 3 次；冲突/3 次失败 → 停下报告用户，不 force）
for i in 1 2 3; do
  git push origin HEAD:main && break
  git fetch origin main && git rebase origin/main || { echo "❌ rebase 冲突，停下报告用户"; break; }
  [ "$i" = 3 ] && echo "❌ 3 次 push 被 reject，停下报告用户（禁 force）"
done

# 锁定已推 SHA，供 Issue/串联引用确认
DESIGN_SHA=$(git rev-parse origin/main)
cd "$PROJECT_ROOT" && git worktree remove "$WT" --force; git branch -D "wip/design-${TS}" 2>/dev/null || true
echo "✅ 设计文档已入库 origin/main @ ${DESIGN_SHA}：${DESIGN_DIR}"
```

> 后续若阶段 5「存量需求刷新」改了设计文档，**同样重跑本步**把更新推上去（保持远程 = SoT）。

### 4.2 创建 GitHub Issue

> **📛 Issue 标题命名规范（强制，全 wiki-issue-* 技能统一）**：所有 `gh issue create` 标题一律
> **`[类型][SPEC-XX][XX模块][XX功能]<一句话描述>`** —— 四段方括号紧挨 + 描述，方括号内无空格。
> - **类型** ∈ `需求 / 任务 / BUG / 优化 / 重构 / 文档 / 调研`（按 issue 性质选一个）
> - **SPEC-XX**：所属 spec 编号（如 `SPEC-018`）；跨 spec 基建 / 纯环境问题对不上 → 填 `SPEC-NA`
> - **XX模块**：业务模块（如 `抽帧`、`样本池`、`训练`）；对不上 → 填 `通用`
> - **XX功能**：具体功能点（如 `进度条`、`列表分页`）；对不上 → 填 `其他`
> - Follow-up 跟进 Issue：类型按性质选（多为 `任务`/`优化`），描述末尾加 `（Follow-up #N）`
> 例：`[需求][SPEC-024][权重库][基线训练]权重库选基线一键起 YOLOv8 训练`

- 用 `gh issue create` 在项目仓库建需求 Issue（**确认 4.1.5 已 push 成功后再建，链接才不是死链**）：
  - 标题：`[需求][SPEC-XX][XX模块][XX功能]<feature 简述>`（遵循上方命名规范；SPEC/模块/功能对不上填 `SPEC-NA`/`通用`/`其他`）
  - 正文：嵌入/链接 PRD 摘要、核心功能列表、影响评估表、`<需求目录>` 路径、**带 AC-ID 的验收标准清单**（dev 会复用本 Issue、继承这些 AC-ID，不重建 Issue/不重定义验收标准）。
  - **「开发/验证环境」段（强制，见 `_shared/project-profile.md`《环境路由约定》）**：开发类 Issue 一律写明「开发与验证环境 = K8s `DEV_NAMESPACE`（例：`dev`）」。**仅当**设计阶段已判定验收必须依赖 QA 数据态/存量数据时，先用 `AskUserQuestion` 向用户明示理由申请使用 `DEPLOY_NAMESPACE`（例：`staging`），**获批后**才可写「验证环境 = staging（已获人工授权：<日期>，理由：<一句话>）」；未获批一律写 `DEV_NAMESPACE` 并要求 acceptance 自带夹具数据。
  - 打 label（如 `enhancement`/`feature`），按情况指派里程碑。
- 把 Issue 链接回写 `<需求目录>/README.md` 与 `04-schedule.md`。
- **回填 spec 七件套**：建完 Issue 后，把 `${SPEC_PATH}/spec.md` frontmatter 的 `issue:` 字段填为 `#<num>`，并在 spec.md 顶部反链 Issue；同步把 Issue 链接补进 `plan.md` / `tasks.md` / `testlist.md` / `clarify.md` / `decisions.md` / `checklist.md` 顶部（若有引用占位）；该回填属设计文档更新，按 4.1.5 末注「重跑入库」把整个 spec 目录再推一次 main（`git add "$SPEC_PATH/"`）。

> **🚦 颗粒度闸（强制 · 决定能否入 auto-dev 队列）**
> 自动开发流水线（`/wiki-issue-dev-auto` / `night-dev`）的甜区是**单一、可独立 TDD、决策点少**的工作单元。Epic 级需求若直接打 `auto-dev-attended` 入队，会让后台流水线白跑最贵的深调研阶段（实测占整批 ~98% 浪费 token），最终只能停在需求门等人拆分。故建 Issue 时**必须先判颗粒度**：
> - **判为 Epic**（命中任一：多里程碑 / 跨多模块 / 粗估 ≥5 人天 / 标题含「启动…spec」「七件套」 / 自述含 `Phase N`/`Mn.x`）→ 该 Issue 打 **`epic`** 标签（**不打** `auto-dev-attended`），作为总纲；并**拆成 5–30 个可独立开发的子 Issue**（对齐阶段开篇「输出 5–30 个独立工作单元」），子 Issue 正文 `Part of #<epic>`，**只有子 Issue 才打 `auto-dev-attended` 入队**。
> - **判为单一工作单元**（单点功能/任务/bug，决策点 ≤2）→ 可直接打 `auto-dev-attended` 入队。
> - 拿不准 → 默认按单一工作单元（不打 `epic`），但在 Issue 正文标注「⚠️ 颗粒度待确认」，让人在环路 dev-auto 的需求门兜底。
> 与下游对齐：`dev-auto.workflow.js` 的 **Triage 段 Epic 颗粒度闸**会拦截漏判的 Epic 并 park 回此流程（双层防线）。

---

## 阶段 5 · 变更与追踪 (Traceability)

**目标**：确保产品不跑偏，且能应对变化。

- **存量需求刷新**：当用户说「更新用户需求」「需求变了」并指向一个已存在的 `<需求目录>` 时，**按阶段 1→5 重新跑一遍**，对该需求做覆盖式变更：
  - 不新建目录，在原目录内**增量更新**各产出物，并在 `README.md` 追加一条变更记录（日期 / 变更点 / 原因 / 受影响产出物）。
  - 若已有 GitHub Issue，用 `gh issue comment` 贴变更说明，必要时更新 Issue 正文。
- **追踪矩阵**：维护 `<需求目录>/README.md` 里的进度勾选表（阶段 1–5 完成状态 + Issue 链接 + 最终方案版本），作为需求的单一事实源（SoT）。

---

## 收尾输出（强制）

1. **产出一份 ~1000 字《工作过程总结》**，格式与七段骨架见统一 SoT：[`_shared/closing-summary.md`](../_shared/closing-summary.md)（**必读必执行**，四技能共用一份）。
   - 设计侧填充侧重见共享文件 §2 表对应行：「②总览」含需求名 / 需求目录 / 已建 Issue# / 是否涉前端；「③过程」走"挖掘 → 分析过滤 → 原型 → 评审 → 落 Issue"五阶段；「④质量门」多为"不涉及"（设计不跑测试），但列原型是否过 `web-ui-spec.md` UI 规范；「⑦下一步」首条即进入开发。
   - 原「五阶段进度 + 产出物清单 + Issue 链接」总表，作为「③过程分步」段的呈现形式。
2. 提示用户产出物目录位置。
3. **末尾独立一行**输出串联指令（供用户直接复制进入开发）：

```
/wiki-issue-dev 请基于 docs/issue/<NNN>-<YYYYMMDD>-<feature-slug>/PRD.md 进入开发闭环（已建 Issue #<num>，请复用、勿重建；继承 PRD 验收标准的 AC-ID；**完整 spec 七件套**已建于 <SPEC_PATH>/（spec.md / plan.md / tasks.md / testlist.md / clarify.md / decisions.md / checklist.md 全部 design 阶段产齐并已 push main），步骤 ② 请仅「校验 + 增量微调七件套」，**不要从零新建任何一件**，不要重写背景动机）
```

> 串联指令**必须带上已建的 Issue 编号**，让 dev 走"上游 Issue 复用探测"复用它（避免同需求两个 Issue，P2.6）；dev 据此继承 AC-ID 做 testlist（P2.4）。

---

## 自检清单（交付前逐项核对）

- [ ] 已在 `docs/issue/` 下用正确序号+日期+slug 建目录
- [ ] 阶段 1 **真实发起过联网检索**（`WebSearch`/`WebFetch`）：用户调研 + ≥3 竞品对比均带**可点击来源 URL + 发布日期**，且**信息均在近 2 年内（≥2024）**；过时资料已弃用或标注；未联网核实项已显式标注「待补」
- [ ] 阶段 1 多轮 ASK 澄清痛点
- [ ] 阶段 2 用 KANO + P0/P1/P2 排序，且做/不做与优先级**均由用户 ASK 确认**
- [ ] 阶段 3 有流程图 + 可验收 PRD；涉及 Web 则**读过 web-ui-spec.md** 且出 3 套 HTML 方案、浏览器打开、用户 ASK 选定
- [ ] 阶段 4.1.3 已建 / 完善**完整 spec 七件套**：`spec.md`（11 段必写齐：frontmatter / 元信息行 / 背景与动机（含量化痛点表） / 目标（G1-Gn 编号 + 验证方式列） / 范围（层级化 In/Out Scope，带具体文件 / 端点 / 表名） / 用户故事 ≥3 / 验收判据（**继承 PRD AC-ID**，P0/P1/P2 三级 + 在哪验） / 非目标边界澄清 / 数据模型 / 风险与缓解 ≥3 / Mockups（若涉前端） / 关联文档出链 ≥5 / Changelog）**≥ 100 行 + frontmatter 9 字段** + `plan.md` **≥ 80 行**（数据模型 / API / 模块拆分 / 集成 / 部署） + `tasks.md` **≥ 10 条**（T-01..T-NN + 工时 + 依赖 + AC 映射） + `testlist.md` **用例 ≥ AC × 1.5**（继承 AC-ID，含层级 / 文件 / 断言 / 在哪验） + `clarify.md` **含 3 轮 ASK 原文**（挖掘 / 优先级 / 方案选定） + `decisions.md` **≥ 3 条 ADR**（背景 / 选项 / 选择 / 理由 / 取舍） + `checklist.md` **≥ 20 条**可勾选条目（Pre-merge / 扩散 / K8s / 安全 / 性能）；七件套全部达标，**缺一即整体不达标**
- [ ] 阶段 4.1.5 **设计文档 + 完整 spec 七件套已 docs-only 入库 `origin/main`**（临时 worktree 提交 + push，已记 `DESIGN_SHA`，入库前已 `for f in spec.md plan.md tasks.md testlist.md clarify.md decisions.md checklist.md; do [ -f ... ]; done` 自检七件套齐全）——**必须在建 Issue 之前**，否则 Issue 链接 / SPEC-XX 死链 + 下游 dev 读不到 PRD / 七件套
- [ ] 阶段 4 有开发视角影响评估表 + 已 `gh issue create`（确认设计文档已 push 后再建）并回写链接
- [ ] 所有用户决策走的是 `AskUserQuestion`，没有 Claude 替用户拍板
- [ ] 存量需求走的是「原目录增量覆盖 + 变更记录」，没另起新目录
- [ ] 收尾产出了 ~1000 字《工作过程总结》（七段 + 表格 + 下一步建议，按 [`_shared/closing-summary.md`](../_shared/closing-summary.md)）
- [ ] 末尾输出了 `/wiki-issue-dev ...` 串联指令（带 Issue 编号 + 继承 AC-ID）
