# LAS 影像 3D 查询与视觉定位

本仓库采用 **DDD 定边界、SDD 管变更、TDD 驱动实现** 的渐进式工程流程。

## 权威信息源

- 领域与架构：`docs/`
- 限界上下文及当前代码映射：`contexts/`
- 特性规格包：`specs/<feature-id>/`
- 旧需求入口：`spec/`（只作兼容索引，不再作为权威规格）
- 自动化测试：当前位于 `api/tests/`、`services/tests/`，跨上下文测试位于 `tests/`

## 🚨 强制流程门禁（每次变更前必须确认）

> **这是硬约束，不是建议。违反门禁 = 流程 bug，必须报告。**

### 动手前必须回答的三个问题

1. **变更包在哪？**
   - 非琐碎改动（≥10 行或跨文件）→ `specs/<feature-id>/` 八件套必须存在
   - 如果没有 → **先建变更包，再动手**
   - 如果已有但脱节 → **先更新 spec 对齐代码，再动手**

2. **当前 TDD 阶段？**
   - 从 `testlist.md` 选一个场景 → 写失败测试（Red）→ 跑 → 确认红 → 最小实现（Green）→ 跑 → 确认绿
   - **禁止一次写一堆测试**
   - **禁止跳过 Red 直接写实现**

3. **门禁跑了吗？**
   - 每次改动后：`./scripts/run-all-tests.sh fast`
   - 涉及 specs/ 时：`./scripts/validate-specs.sh`
   - 涉及 docs/ 时：`./scripts/drift-check.sh`

### 违规处理

| 违规 | 处置 |
|---|---|
| 没有变更包就改了代码 | 停下，补建 specs/，更新 testlist 让已实现的场景标记为 `[x]` |
| 跳过 Red 直接写实现 | 删除实现，先写失败测试，再重新实现 |
| 一次改了多个不相关文件 | 拆成多个 commit，每个对应一个 testlist 场景 |
| 改了代码没跑测试 | 停下，跑测试，修到全绿再继续 |

## 变更流程

1. 在 `specs/<feature-id>/` 创建或更新 `spec.md`、`clarify.md`、`plan.md`、`tasks.md`、`checklist.md`、`testlist.md`、`risks.md`、`decisions.md`。
2. 先运行 `./scripts/validate-specs.sh`，确保规格包完整。
3. 从 `testlist.md` 选择一个场景，按 Red → Green → Refactor 推进；一次只实现一个可验证行为。
4. 领域术语或边界变化时，同步更新 `docs/ubiquitous-language.md`、`docs/context-map.md` 与对应 `contexts/*/README.md`。
5. 完成前运行 `./scripts/run-all-tests.sh fast` 和 `./scripts/drift-check.sh`，并更新任务、清单与追踪状态。

## 实现约束

- 业务依赖方向为：接口层 → 应用编排 → 领域规则；基础设施通过适配接口接入。
- 新业务能力按 `contexts/` 中的边界归属，不再新增横向的通用 `service` 大目录。
- 领域规则不得只写在路由或数据 DTO 中；需要由实体、值对象、策略或领域服务表达。
- API、数据库、文件系统、模型权重和第三方算法属于边界适配，不得反向污染领域概念。
- `las/`、`query_images/`、`projections/`、`logs/` 中的运行产物不进入版本控制。
- 不覆盖工作区中与当前任务无关的未提交改动。

## Agent 与 Git

- **orchestrator**：扫描 `specs/`、拆分任务并串联编码 → 测试 → PR。
- **git_auto**：仅在用户授权 Git 操作时使用。
- `dev → test` 和 `test → main` 等受保护分支操作必须遵循对应规格中的发布约束，不得擅自执行。

---

## Bug 扩散排查（强制）

**定位并修复一个 bug 后，必须在项目里扩散检索同类问题、一并修，再宣告完成。** 单点修复留下的同模式 bug 是线上事故高频来源。

**触发判定**（任一命中即必扩散）：可被模式化（拼错字段名 / 漏 await / 未捕获异常类型 / 错配 key / 过期 API）、来自共享逻辑（公共工具·基类·装饰器·ACL）、配置或常量类（环境变量名·端口·路径·魔法字符串）、依赖升级 / 上游接口变更。纯一次性、与其他模块无共享的业务错误可豁免，但结论里**必须明说**「已确认不属于可扩散模式」。

**排查步骤：**

```bash
git diff HEAD~1 HEAD -- <fixed-files>        # 1. 抽「问题指纹」
rg -n '<problem-pattern>' contexts/          # 2. 各上下文 grep 同模式
rg -n '<problem-pattern>' docs/ specs/       # 3. SDD/文档里也查，防文档误导未来开发
```

覆盖：错误标识符本身、近义写法（驼峰/下划线、单复数）、被替换前的旧 API 名。

**处理纪律：** 找到同类 → **一并在本次改动里修**，不留 TODO、不拆 PR（除非超范围且用户同意，此时开 issue 记录）；跨多 spec 按 §变更流程同步每个 spec 的 SDD + 单测；**报告必带「扩散结论」段**（grep 了哪些模式、覆盖哪些目录、找到几处、是否全修，没找到也写「已扩散排查，无同类问题」）；不扩大战场——顺手发现的无关问题用 issue 或 `spawn_task` 记录，不塞当前 PR。

---

## CI/CD 红线纪律

> 根治「判 FALSE-RED / 无关红 → 开 follow-up 推给下一个人」的历史复发模式。

- **谁发现谁负责**：任一 session 在 push / 合并 / 验收 / 例行查看中，发现 CI 或 CD（含 `ci`、`cd`、`pr-quality`、`spec-gate`）变红，**就由这个 session 在当回合负责把它处理到绿，或确证中和**。不得视而不见，不得以「这不是我引入的」推责离场。
- **根因与自己无关也要积极解决**：红的根因即便完全不是本次改动引入（历史遗留 / 他人 commit / 门守卫自身 bug / 环境抖动），发现者仍须当回合推进——能修代码就修代码，是门/守卫的 bug 就修门，是抖动就 rerun 并确认稳定后再走。
- **禁止另起 follow-up 拖延**：**不允许**用「开个 follow-up issue / 转需求轨 / 留给晨间 acceptance / 标 parked」把当前这条红丢给下一个人。Follow-up 只能承载**与当前红无关的增量改进**，绝不能用于绕开当前这条红本身。
- **「FALSE-RED 误报」不是免责，是举证 + 修门的义务**：若判定红是误报，必须当回合①给出可复核证据证明误报，②修掉让门不再误红。当回合确实无法修门时，**至少把状态恢复成绿**（rerun / `git revert` 引入红的 commit）再交接，并在 Issue/PR 留证据；只写一句「FALSE-RED 跳过」就走 = 违规。
- **唯一例外**：当回合无论如何到不了绿（依赖外部不可达资源 / 需他人权限 / 须用户拍板），必须用 `AskUserQuestion` 显式暴露阻塞点请人决策，而**不是**静默开 follow-up 离场。

---

## Issue 终态覆盖（项目级，优先于通用 wiki-bug-fix 技能）

> 本节覆盖通用 `wiki-bug-fix` 技能中所有「关闭 Issue / `gh issue close`」步骤。本项目问题单的最终关闭权保留给人工验收负责人。

- **禁止代理直接关闭**：处理已有问题单时，Issue 必须保持 `OPEN`；PR 标题和正文不得使用 `close/fixes/resolves #N` 等自动关闭关键字，也不得执行 `gh issue close`。
- **先复现再分流**：基于最新 `origin/main`（或对应基线分支）和对应的测试环境，按原始复现路径重新验证，不得仅凭旧评论、历史 CI 或读代码推断「已经解决」。
  - **当前版本已经解决**：确认原问题无法复现且实际行为符合 Spec 后，不做无效代码修改；在 Issue 评论中写明环境、部署 SHA、复现步骤、结果和 Spec 依据。
  - **当前版本仍未解决**：继续完成 `wiki-bug-fix` 的 RCA、TDD 红绿、扩散排查、Review、合入、CI/CD 与 AFTER 真验；不得省略质量门。
- **统一转人工验收**：两条分支技术处理完成后，都必须按顺序：
  1. 将问题单所在的当前 GitHub Project 的 `Status` 字段更新为 `已完成`（注意：`未完成` 是工作进行中的状态，**不得**用作终态），不得用同名 label 冒充 Project 状态；
  2. 将 Issue 负责人 / assignee 转交给 `pangjf03_onewo`；
  3. 回读确认 `Status=已完成`、assignee 包含 `pangjf03_onewo` 且 Issue 仍为 `OPEN`；
  4. 附言「技术处理完成，已转 pangjf03_onewo 人工验收」并释放自动化认领（gh 操作即可，如 `gh issue edit <N> --remove-assignee "<bot>"`）。
- **终态写入失败就是阻塞**：若问题单未关联 GitHub Project、缺少 `已完成` 选项、当前令牌无 Project 写权限，或 `pangjf03_onewo` 无法被分配，必须保持 Issue 打开，用 gh 留痕并显式报告阻塞；不得擅自创建状态 / 标签、扩大权限或改用关闭 Issue 绕过。
