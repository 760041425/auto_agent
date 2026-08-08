---
name: wiki-issue-dev
description: Issue 全生命周期单流水线技能（通用研发流，跨 git 项目复用；典型项目：示例项目）。从「模糊需求」一口气走到「Issue 关闭 + 进展汇报」八步闭环：① 上下文澄清确认最终需求（争议必走 AskUserQuestion）→ ② 同步/新建 spec 文档（spec 目录见项目《环境档案》SPEC_DIRS）→ ③ gh 创建 GitHub Issue → ④ 单流水线开发（顺序、不并行、不中途暂停）→ ⑤ 在项目测试环境 K8s（namespace 见项目《环境档案》DEPLOY_NAMESPACE）跑实测（API/DB/Pod 日志三件套）→ ⑥ code review + 在 Issue 评论上贴关闭证据 → ⑦ gh issue close → ⑧ 串联 `/wiki-session-report` 输出非技术汇报。当用户说「/wiki-issue-dev」「wiki-issue-dev」「完成这个需求」「解决这个问题」「把这个需求做成 issue 跑完」「从需求到关 issue 一条龙」「需求闭环」「issue 全生命周期」时触发。严格遵循项目根 CLAUDE.md（调研 / Pre-merge / 双轨 Git / CI / 扩散 / K8s 等硬约束）；测试环境唯一项目指定的 K8s namespace，禁止用本机 docker / docker-compose 当测试环境。
---

<!-- wiki-common-rule:v1 -->
## 0. 通用交互与汇报规约（全技能通用 · 优先级高于下文具体步骤的表述习惯）

**语言**：面向用户的所有叙述、标题、汇报一律用**简体中文**（代码 / 命令 / 路径 / 标识符 / 引用的英文报错原文除外）。即使 subagent、工具或 CI 返回英文或日文，回写给用户时必须翻成简体中文，全程不得切换语种。

**收尾汇报**：本技能每跑完一个阶段或全部完成时，**必须**：
1. 用 Markdown **表格**汇报结果（典型列：步骤 / 事项、状态、产物或证据链接、备注）；
2. 表格后另起「**下一步建议**」小节，给出 **1–3 条可直接执行**的动作——可复制的 shell / `gh` 命令，或 `/wiki-*` 串联指令；禁止只写「可以继续优化」这类空话。
<!-- /wiki-common-rule:v1 -->

<!-- hardening-rules:v1 (skill-mining 2026-06-10) -->
## 硬化规约（数据挖掘驱动 · v1）
- **shell**：多行 shell 片段与 `source issue-claim.sh` 一律 `bash -c '...'` 显式执行；禁止依赖交互 shell（zsh 只读变量 status / `export -f` 不兼容 / 分词语义差异）。
- **gh**：写操作（comment / label / merge / api PATCH / close）必须 `_gh_retry 3 2 -- gh ...` 包裹（来源 wiki-issue-claim-lib，EOF/超时/5xx/限流自动退避重试）。
- **等待**：等 CI / 任务 / 部署一律 Bash `run_in_background: true` + Monitor/TaskOutput 收口；禁止 `sleep N && cat` 前台轮询（会被 harness 拦截）。
- **AskUserQuestion**：单次 ≤4 题、每题 2–4 个选项；>4 题拆多次背靠背连发；禁止发空 `questions`。
- **devlock**：**禁止用 MCP 阻塞式 `lock_acquire` 排队**——waiter 心跳绑在那条 MCP 长连接上，连接被杀（`-32000 Connection closed`）即断跳、60s 被 reap 成 EXPIRED 白排（2026-06-11 晨 #1570/#1589/#1555/#1607 四 session 连环实证）。排队等锁一律 `python3 ~/.claude/mcp/devlock/cli.py lock-acquire <资源> --session <id> --label "<skill #N>" --issue <n> --ttl 900 --wait 3600`（本地进程阻塞、幂等保号、自带 waiter 心跳）配 Bash `run_in_background` 收口；MCP 工具仅用于 `lock_try_acquire`/`lock_status`/`lock_release`/`lock_heartbeat` 等短调用；持锁者 ≥10 分钟无心跳先 `lock_reap` 再重试。🌗 锁面按 v6 车道（CLAUDE.md《环境路由规则》，2026-06-11 #1630 落地）：bug 道 `CI,CD,STAGING` / dev 道 `CI,CD,DEV`，对称分段释放；标题前缀=CD 车道选择器（dev 道禁用 `fix(` 前缀）。
<!-- /hardening-rules:v1 -->
<!-- issue-claim:v1 -->
## 0.7 GitHub Issue 处理留痕（全 wiki-issue-* / wiki-bug-fix 技能通用 · 由 wiki-issue-claim-lib 提供）

**目的**：让任何 session/任何机器都能从 GitHub 一行命令查出「这个 Issue 现在是不是被某个 Claude Code session 占着」，避免并行撞车（实战教训：[[dev_auto_skipped_claim_lock_collision]]）+ 防僵尸残留（[[dev_auto_crash_recovery_manual_merge_tail]]）。

**机制三件套**：
1. **`wip:claude-code` label** 作为「正在处理」唯一全网 SoT（`gh issue list --label wip:claude-code`）
2. **单条结构化 edit Comment**（marker `<!-- claude-code-claim:v1 -->`）携带 skill / 阶段 / session 短 hash / 时间 / PR；每次更新 **edit** 同一条，永不新增
3. **本机 `~/.claude/locks/issue-<N>.json`** 提供进程级互斥（flock）+ 离线查询

**隐私剔除**：comment 与 lock 中**绝不**暴露机器名 / 用户名 / 绝对路径 / IP / kubeconfig context / git 远程 URL；仅 skill / 阶段 / `sess_<8 位短 hash>` / 时间（UTC+8）/ PR 号。

**API**（source 后即可调用）：

```bash
# ====== 启动期必做：加载底座 + 注入两个上下文变量 ======
export SESSION_SHA8           # §0.6 中已生成的 8 位 hash
export SKILL_NAME="<本技能名>"  # 例: wiki-issue-dev / wiki-bug-fix / ...
source ~/.claude/skills/wiki-issue-claim-lib/scripts/issue-claim.sh

# ====== 6 个状态机 API ======
claim_issue        <ISSUE_NUM> [phase_initial="启动"]   # 拿到 ISSUE_NUM 后立刻 claim
advance_phase      <ISSUE_NUM> <phase_name> [pr_num]    # 每个步骤切换时调
heartbeat_issue    <ISSUE_NUM>                          # 长阶段每 30 min 兜底心跳
release_issue      <ISSUE_NUM> <reason>                 # 中途撤回（非关闭）
close_issue_claim  <ISSUE_NUM> [pr_num]                 # 真正 close issue 时调（release 的语义化别名）
park_issue         <ISSUE_NUM> <reason>                 # 卡住/等用户决策（label swap → parked:claude-code）
```

**钩子点位（本技能必须严格按此调用）**：

> ⚡ v5（2026-06-10 切主）：claim-lib 内核已切 devlock（MySQL 状态机 SoT，label 仅投影），**函数签名/退出码不变、本表照用**。两条新增红线：① 任何函数 rc=3 且 stderr 出现 `DEGRADED:park` = devlock 不可达 → 立即 park 当前工作并报告，**禁止退回裸 `gh issue edit --add/remove-label` 写状态标签**；② PR 标题/正文一律 `ref #N`、禁用 `close/closes/fixes #N` 关键字——关单顺序固定为「测试/验收通过 → `close_issue_claim`（内部 transition verified_closed）→ `gh issue close`」。

| 时机 | 调用 | 说明 |
|---|---|---|
| 拿到 `ISSUE_NUM` 后第一时间 | `claim_issue "$ISSUE_NUM" "<起步阶段名>"` | 退出码 2 = 已被他人占用 → **立刻 abort 报告用户**（禁止抢占）；接力场景走 devlock `handoff`（见 wiki-bug-fix §0.6.1）；退出码 0 才继续 |
| 每个步骤切换 | `advance_phase "$ISSUE_NUM" "<阶段中文名>" [$PR_NUM]` | 用本技能步骤号 + 中文短描述；**带 `$PR_NUM` 时自动把状态机转 `pr_open` 并绑定 PR**（开 PR 即出队列视野，防夜间重复发牌） |
| 长阶段（>30 min）内 | `heartbeat_issue "$ISSUE_NUM"` | 放循环 / 等待轮询里兜底，防 60 min 租约到期被服务端 reap 放回队列 |
| 验证通过、`gh issue close` **之前** | `close_issue_claim "$ISSUE_NUM" "$PR_NUM"` | 内部走 devlock `transition verified_closed`（关单唯一入口）+ 释放认领；成功后再 `gh issue close` |
| 中途撤回（非异常） | `release_issue "$ISSUE_NUM" "<reason 中文>"` | 自动把 issue 放回队列（requeue）；例如用户中断 / 主动让位 |
| 卡住等用户决策 | `park_issue "$ISSUE_NUM" "<reason 中文>"` | devlock 状态机转 `parked`（label 投影自动跟进）；夜间技能必走 |

**冲突处理（claim 返回非 0）**：

```bash
if ! claim_issue "$ISSUE_NUM" "<起步阶段>"; then
  rc=$?
  if [ "$rc" -eq 2 ]; then
    echo "❌ Issue #$ISSUE_NUM 已被其他 Claude Code session 处理（查询: gh issue view $ISSUE_NUM --comments）"
    echo "   推荐: /wiki-issue-status $ISSUE_NUM"
    exit 1
  else
    echo "⚠️ claim 失败 (rc=$rc)，可能 gh 未登录 / repo 取不到，请人工排查"
    exit 1
  fi
fi
```

**查询入口（用户视角）**：

```bash
/wiki-issue-status            # 列全网 wip + 全网 parked + 本机 lock
/wiki-issue-status <N>        # 看单个 issue 的 claim comment
/wiki-issue-status --mine     # 仅本机活跃 claim
/wiki-issue-status --reap     # 手动清理 ≥60 min 无心跳的僵尸
```

**兜底（cron 永不依赖本机）**：项目仓库 `.github/workflows/issue-claim-reap.yml` 每 30 min 跑一次，调用 `reap_stale_claims` 等价 Python/Shell 自动回收僵尸 claim。
<!-- /issue-claim:v1 -->

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

**当本技能识别出任务可拆为 ≥5 个相互独立的子单元**（典型：spec / 模块 / 文件夹 / Issue / 用例文件 / 镜像 / 子页面 / 子图等），**优先**走以下骨架，**不要**顺序硬扛：

1. 用一个前台 subagent 完成调研 + 拆分，输出 5–30 个独立工作单元（每个单元可单独完成、无相互依赖）。
2. 对每个单元用 `Agent` 工具发起后台 subagent，参数固定：
   - `isolation: "worktree"`（隔离的 git worktree，避免互踩）
   - `run_in_background: true`（并行执行，不阻塞协调者）
   - `subagent_type` 按任务类型选（默认 `general-purpose`）
3. 在一条消息里一次性发起全部 subagent（多 Agent 工具调用并列），最大化并行度。
4. 协调者只负责：渲染进度表 → 收 PR 链接 → 汇总状态。
5. 单元数 <5、或单元之间存在强依赖时，直接顺序处理，**不要**为并行而并行。

**红线**：
- 涉及非 git 仓库的目录（如 `~/.claude/skills/`、本机配置文件）→ 不能用 worktree，退回顺序模式。
- 单个单元预计 <2 分钟可完成 → 起 subagent 的开销得不偿失，直接做。
- 单元之间共享状态（同一文件、同一数据库行、同一端口）→ 强制顺序，不许并行。

> ⚠️ **本技能用户已明确要求「单流水线」**：8 个步骤共享同一份 Issue / spec / git 状态 / K8s 命名空间 / Issue 评论流，**强制全程顺序执行**，不许把任意两步拆给后台 subagent 并行跑。并行规则仅在「步骤 ④ 内部子任务足够独立（≥5 个互不依赖的代码模块/文件）时」才在该单步内局部启用，主流水线绝对顺序。

参考骨架：`/batch` slash command。

---

## 0.5 多 Session 隔离硬约束（每个 Issue 独占 worktree）

**前提**：用户经常同时开多个 Claude 会话各跑一个 `/wiki-issue-dev`。若大家都在项目主检出（`$PROJECT_ROOT`）上 `git checkout main && git merge` —— 互相覆盖、丢 commit、push reject、清理误删 —— **必然**发生。

**隔离模型**（强制）：

| 维度 | 规则 |
|---|---|
| **工作目录** | 每个 session 在 `.claude/worktrees/issue-gen-<session-id>/` 独占 worktree，**禁止**在主检出 `<é¡¹ç®æ ¹>/` 根目录上直接动 git |
| **分支命名** | 临时名 `wip/issue-gen-<session-id>`（步骤 ⓪ 起）→ 拿到 Issue 编号后重命名为 `feat/issue-<num>-<slug>`（步骤 ③ 末尾） |
| **拉新代码** | 步骤 ④ 合 main 前**必须** `git fetch origin && git rebase origin/main`，拿到其他 session 已推的 commit 再 push |
| **push 冲突** | 被 reject（远程比本地新）→ rebase + retry，**最多 3 次**；3 次失败 → 停下报告用户（可能存在 force-push / 历史改写，必须人工介入） |
| **rollout 竞态** | push 后立刻锁定 `$PROJ_PUSHED_SHA`，步骤 ⑤ 以它（非流动的 `origin/main`）为锚点。多 session 洪峰下本 SHA 的 build 常被并发取消 / 路径过滤不重建（[[cd_concurrency_cancel_pathfilter_deploy_gap]]）→ **不死等**，按 §5.2 deploy-gap 决策树用「后代绿 build + ancestor 校验 / 线上行为确证」判过；仅当确需本镜像新版且兜底也不成立时才回 ④ 重 push |
| **清理范围** | 步骤 ⑦ 只删 `.claude/worktrees/issue-gen-<本 session-id>` / `.claude/worktrees/issue-<本 ISSUE_NUM>-*` 和**本 session 创建的** `feat/issue-<本 ISSUE_NUM>-*` / `wip/issue-gen-<本 session-id>` 分支；其他 `.claude/worktrees/issue-gen-*` 和 `feat/issue-*-*` 一律**不碰** |
| **主检出禁动** | 项目主检出（`$PROJECT_ROOT`）**永远只用 `git fetch`**，从不在它上面 `checkout` / `merge` / `commit` / `push`，避免成为多 session 的共享互斥点 |

**生成 session-id**（步骤 ⓪ 第一件事）：

```bash
SESSION_ID="$(date +%Y%m%d-%H%M%S)-$$"     # 时间 + PID，全机器唯一
echo "$SESSION_ID"
```

之后整个流程把它作为本 session 的「身份证」，写到所有路径/分支名/Issue body 里，清理时严格 grep 比对。

---

## 0.6 Session 锁声明与冲突探测（启动第一步，强制；与 §0.5 配套）

**目的**：§0.5 通过 worktree + session-id 做"互不踩"，本节通过 **文件锁 + 同 (issue, spec) 探测** 防止"两个 session 都在跑同一个 issue + spec 三元组"。

执行顺序（步骤 ⓪ 起 worktree 之前 / 同步 / 之后立刻做）：

1. **生成 session-sha8**（与 §0.5 的 `SESSION_ID` 区分用途，作为锁文件命名空间）：
   ```bash
   SESSION_SHA8=$(echo "${SESSION_ID}-$(git config user.email)" | shasum | cut -c1-8)
   echo "本次 session-sha8 = $SESSION_SHA8"
   ```
2. **生成 target-id**（= `<issue-num>-<spec-id>`；步骤 ① ② 拿到 issue 编号 / spec id 后填实，未拿到前先用 `pending-pending`）：
   ```bash
   TARGET_ID="${ISSUE_NUM:-pending}-${SPEC_ID:-pending}"
   ```
3. **探测同 target 活锁**：
   ```bash
   mkdir -p .claude/locks
   ls .claude/locks/issuegen-${ISSUE_NUM:-pending}-${SPEC_ID:-pending}-*.lock 2>/dev/null
   ```
4. **命中已有锁** → 立即 `AskUserQuestion` 三选一：
   - **合并/续作**：放弃本次启动，让用户切到那个 session（避免两个 session 都在做同一需求）
   - **取消**：本次终止
   - **强制并行**：用户明确知晓冲突风险后继续（写入锁备注，冲突责任在用户）
5. **无命中** → 写自己的锁（步骤 ② ③ 拿到 SPEC_ID / ISSUE_NUM 后**重命名锁文件**把 `pending` 替换为真实编号）：
   ```bash
   cat > .claude/locks/issuegen-${ISSUE_NUM:-pending}-${SPEC_ID:-pending}-${SESSION_SHA8}.lock <<EOF
   {
     "session_sha8": "${SESSION_SHA8}",
     "session_id": "${SESSION_ID}",
     "target": "${TARGET_ID}",
     "worktree": "${PROJ_WORKTREE:-pending}",
     "started": "$(date -Iseconds)",
     "user": "$(git config user.email)"
   }
   EOF
   ```
6. **流程全部完成后**（步骤 ⑧ 输出 `/compact` 提示行之后）自动删锁：
   ```bash
   rm -f .claude/locks/issuegen-${ISSUE_NUM}-${SPEC_ID}-${SESSION_SHA8}.lock
   ```
7. **崩溃恢复**：若锁文件 `started` 距今 > 4 小时 → 视为僵尸锁，直接清理后继续。

> 📌 锁路径：仓库根 `.claude/locks/`，加入 `.gitignore`。本节与 §0.5 互补：§0.5 用 worktree 物理隔离，§0.6 用文件锁逻辑探测同需求多 session 启动。

---

## 0.7 分支命名硬约束（防多 session 撞名）

§0.5 §3 末尾的 `feat/issue-<num>-<slug>` 在多 session 同 issue 时会撞名。强制升级为：

```bash
SLUG="<kebab-slug>"
FEAT_BRANCH="feat/issue-${ISSUE_NUM}/${SESSION_SHA8}/${SLUG}"
git branch -m "$PROJ_TMP_BRANCH" "$FEAT_BRANCH"
export PROJ_FEAT_BRANCH="$FEAT_BRANCH"
```

> 例：`feat/issue-512/a3f8c2b1/training-progress-log`。两个 session 同时做 #512 也物理不可能撞名。
> 步骤 ④ push、步骤 ⑦ 清理按 `feat/issue-${ISSUE_NUM}/${SESSION_SHA8}/` 精确锁定。

---

## 0.8 研发资源锁（staging 串行 · 条件启用 + 优雅降级）

**目的**：多 session 并行时，「合 main → CD 滚 `staging` → 在 `staging` 验证」整段碰同一套共享资源（main 分支 / CI / CD / 单实例 `staging` namespace 的 PG·Redis·MinIO）。本节用中心化排队工具 **`devlock`**（MySQL FIFO 复合锁，MCP server，库 `claude_code_dev`，代码 `tools/devlock/`）让这段**全局串行、公平排队、零资源竞争**，取代各自为政的本机 mkdir/flock 锁。

**条件启用（硬约束 · 防污染通用性）**：本技能跨项目复用，**仅当**「`devlock` MCP 可达 **且** 当前项目《环境档案》已声明 `DEPLOY_NAMESPACE`」时启用资源锁；否则（MCP 不可用 / 非本项目 / 申请超时）**打一行 WARN 后跳过锁、回退现有行为，绝不阻断主流水线**。

**三个调用点**：

| 时机 | 调用 | 说明 |
|---|---|---|
| 步骤 ④ rebase 后、`git push HEAD:main` **前** | `python3 ~/.claude/mcp/devlock/cli.py lock-acquire CI,CD,DEV --session "$SESSION_ID" --label "wiki-issue-dev #$ISSUE_NUM" --issue $ISSUE_NUM --ttl 900 --wait 3600`（🌗 v6 开发道默认锁面；②.5 已授权 staging 验证 → 资源改 `CI,CD,STAGING`。Bash `run_in_background` 收口；**禁用 MCP 阻塞式 `lock_acquire` 排队**，waiter 随 MCP 连接死亡被 reap） | 拿到 `granted` 才 push；超时/降级 → WARN 跳过 |
| CI 构建绿（build-images 本 SHA success）后 | `cli.py lock-release $REQUEST_ID --resources CI` | **v6 分段释放**：CI 用完即还，下一位的合并+构建与本位的 CD/验证流水线化 |
| 本车道环境 rollout 确认（dev 道=dev）后 | `cli.py lock-release $REQUEST_ID --resources CD` | CD 用完即还；验证段**只持环境锁**（dev 道=DEV） |
| 步骤 ④→⑤ 期间 | 心跳由守护进程自动续租(60s/拍,见 §0.8 心跳守护);守护未起时退回每 5min `lock_heartbeat(request_id)` | 续租防被回收 |
| 步骤 ⑤ 验证结束（成败都走） | `lock_release(request_id)` | 全放余下资源，触发下一个排队 session 递补 |

**降级伪代码**：

```text
if devlock_available() and project_has_namespace():
    req = bash_bg('python3 ~/.claude/mcp/devlock/cli.py lock-acquire CI,CD,DEV --session $SESSION_ID --label "wiki-issue-dev #$ISSUE_NUM" --issue $ISSUE_NUM --ttl 900 --wait 3600')  # 🌗 v6 dev 道锁面（已授权 staging 则 CI,CD,STAGING）；cli 阻塞取锁，勿用 MCP lock_acquire 排队
    if not req.granted: WARN("devlock 超时，降级跳过资源锁"); req = None
else:
    WARN("devlock 不可用/非本项目，跳过资源锁"); req = None
# 持锁期间: 心跳由守护进程自动续租(60s/拍,见 §0.8 心跳守护);守护未起时退回每 5min: if req: lock_heartbeat(req.request_id)
# 步骤 ⑤ 结束(成败都走): if req: lock_release(req.request_id)
```

**🌗 双车道环境路由（v6 物理分道，2026-06-11 #1630/PR#1697 落地，与项目 CLAUDE.md《环境路由规则》对齐）**：项目声明了 `DEV_NAMESPACE`（例：`dev`）时，本技能属**开发道**——合并标题用 feat/chore/refactor（**禁 `fix(` 前缀**，标题前缀=CD 车道选择器），CD 自动**直滚 dev、不碰 staging**。锁面 = `CI,CD,DEV` 对称分段（见上表）：CI 绿放 CI → dev rollout 确认放 CD → 验证段只持 `DEV` → 终态全放。无需镜像复制（CD 直滚；CD 被并发取消时的兜底与 ancestor 核验三件套见 CLAUDE.md《环境路由规则》）。**使用 `staging` 验证须人工授权**（见步骤 ②.5 / ⑤.0）：已授权 = 锁面改 `CI,CD,STAGING`，合并仍走 dev 道标题（CD 滚 dev），验证段持 STAGING 锁内 `gh workflow run cd.yml -f namespace=staging`（image_tag 留空=最新 main per-SHA，必含本 commit）追平后再验。🚃 同班车搭车验证规约见 CLAUDE.md《环境路由规则》。项目未声明 `DEV_NAMESPACE` → 维持旧行为（取 `CI,CD,STAGING` 整段）。

**与 `wiki-issue-claim-lib` 的关系（正交并存）**：claim 锁 = **issue 维度**（`wip:claude-code` label 防两 session 取同一 Issue，§0.7 之外的 §0.7 留痕机制保留不变）；devlock = **资源维度**（防多 session 同时碰 `staging`）。两者互补，缺一不可。

> 跨机 20 session 生效需各机注册该 MCP 且连**同一共享 MySQL**（见 `tools/devlock/README.md`）；当前若指向 `localhost` 仅本机多 session 生效。

**🫀 心跳守护(P1 · 2026-06-10 起)**:`granted=true` 后**立刻**起结构化心跳守护,替代手动心跳(实测模型自觉心跳不可靠,TTL 已从 3600s 压到 900s):

```bash
CLAUDE_PID=$(p=$$; while [ "$p" -gt 1 ]; do case "$(ps -o comm= -p "$p")" in *[Cc]laude*) echo "$p"; break;; esac; p=$(ps -o ppid= -p "$p" | tr -d ' '); done)
if [ -n "$CLAUDE_PID" ]; then
  nohup bash ~/.claude/mcp/devlock/heartbeat-daemon.sh "$REQUEST_ID" --watch-pid "$CLAUDE_PID" \
    >> ~/.claude/mcp/devlock/heartbeat-daemon.log 2>&1 &
else
  echo "WARN: 探测不到 claude 进程,无守护——改为每 5min 手动 lock_heartbeat(TTL=900s,勿断跳)"
fi
```

守护每 60s 续租;session 崩溃 → 守护停跳 → ≤900s 被 reap(死窗 60min→15min);`lock_release` 后守护下一拍自动退出,无需 kill。`$REQUEST_ID` 换成 acquire 返回的 request_id。

---

# /wiki-issue-dev — 示例项目 需求到 Issue 关闭八步单流水线

## 1. 触发场景

- 用户给出**一个需求或一个待解决问题**，希望"一条龙跑到 Issue 关闭"。
- 用户说："完成这个需求"、"解决这个问题"、"从需求到关 issue 一条龙"、"需求闭环"、"/wiki-issue-dev"。
- 用户希望**不要中途反复确认**，只在「需求争议」和「破坏性动作」两处停下来问。

> 反例：
> - 只是写 prompt / 出任务书 → 用 [wiki-prompt-gen](../wiki-prompt-gen/SKILL.md)。
> - 已有 Issue + 实现，只是验收 → 用 [wiki-issue-acceptance](../wiki-issue-acceptance/SKILL.md)。
> - 只是合代码进 main → 用 [wiki-code-commit](../wiki-code-commit/SKILL.md)。
> - 只是 review 代码 ↔ spec 差异 → 用 [wiki-issue-review](../wiki-issue-review/SKILL.md)。

---

## 2. 核心硬约束（六条，缺一不合格）

来自项目根 `CLAUDE.md`（调研 / Pre-merge / 双轨 Git / CI / 扩散 / K8s 等小节）：

1. **需求争议必走 ASK**：步骤 ① 发现任何「目标模糊 / 范围不明 / 方案有分歧 / 缺关键事实」→ 一次性用 `AskUserQuestion` 问，**最多一轮**，用户答完直接进步骤 ②，不二次确认。
2. **Spec 是 SoT**：步骤 ② 必须落到 `web/specs/<id>/` 或 `algo/specs/<id>/`，不许只在 Issue body 里写需求。已有 spec → 增量更新 `spec.md`/`plan.md`/`decisions.md`；新 spec → 完整骨架（spec.md / plan.md / tasks.md / testlist.md）。
3. **单流水线 = 全程顺序，但顺序 ≠ 禁止上下文隔离**：八步之间禁止并行；步骤 ④ 内部子任务可在「≥5 个真正独立单元」时局部并行（按 §0 红线）。**例外（见 §2.2-C）**：步骤 ① 深度调研、步骤 ⑥ code review **必须**委托 **fresh-context、顺序阻塞式** subagent 执行（为上下文卫生，非并行提速；协调者仍顺序等其返回）。
4. **测试环境唯一项目指定的 K8s namespace（`DEPLOY_NAMESPACE`，例：`staging`）**：步骤 ⑤ 禁止 `ssh <DEBUG_HOST> "docker compose ..."`（排障机 `DEBUG_HOST`，例：`debug-host`），禁止本机 docker / docker-compose 当测试环境。
5. **关闭证据三件套缺一不可**：步骤 ⑦ 关 Issue 前必须凑齐 ① 静态检查全绿截图/输出、② `kubectl rollout status` ready、③ Post-push CI（build-images + cd）全绿；少一件禁止 close。**③ 的「cd 全绿」判据 = 已按 [`_shared/dev-acceptance-gate.md`](../_shared/dev-acceptance-gate.md) §5 把链式 CD Deploy run watch 到终态（`deploy-k8s` 滚成功 + `post-deploy-acceptance-gate` 门绿；或 `skipped`/ancestor 兜底成立），不是只看 build-images 绿**（见步骤 ④ 4.6.1）。
6. **不可中途暂停（仅三处合法停顿）**：除以下三处外，八步一气呵成，不逐步征求"是否继续"——① 步骤 ① **需求争议** ASK 一轮；② 步骤 ②.5 **技术方案审批门** ASK 一轮（见 §2.2-B，本次优化新增）；③ **破坏性动作**（force push / 删未合并分支 / revert）。

### 2.2 TOP-3 致命问题强化约束（本次优化新增，缺一不合格）

> 针对「单上下文焊死 / 假 TDD / 无方案审批门」三大致命问题的硬性补丁。三条与 §2 前 6 条同级，违反即本次闭环不合格。

**A. 真 TDD（红→绿→反验证），禁止 test-after**
- 步骤 ④ 每条子任务**先写测试 → 跑出红**（确认测试确实失败、且失败原因是"功能未实现"而非 import/语法错），**再写实现 → 跑出绿**。
- 实现通过后做**一次反验证**：临时注释/删掉本次实现的核心行 → 对应测试**必须重新变红** → 确认后还原。red→green→red 三相齐全，才算这条子任务的测试可信。
- **禁止**为迁就实现去改测试断言；**禁止** mock 跟着被测逻辑写成同样的错名（直堵 [[orm_field_typo_with_mock_aligned]]「mock 对齐 bug 永远绿」）。
- testlist 不只卡数量 ≥10，每条都必须能在"功能被破坏时失败"（即都经历过红相）。

**B. 技术方案审批门（步骤 ②.5，code 之前唯一强制 gate）**
- spec 写完（步骤 ②）后、建 Issue + 写码（步骤 ③④）之前，**必须**用 `AskUserQuestion` 把**技术方案**摆给用户确认一次：接口 / 数据模型 / 关键取舍，有分歧给 A/B 选项。
- 这是 explore→plan→**【人类批准】**→code 环里收益最高的一次打扰，凌驾"不可中途暂停"。用户拍板后才进步骤 ③，**不再**二次确认"是否开始"。
- 例外：纯 docs / ≤20 LOC hotfix / 配置改动等"方案无悬念"的改动，可一句话说明后跳过此门（须在 spec.md 注明"方案无悬念，跳过 ②.5"）。

**C. 关键阶段强制上下文隔离（解 lost-in-the-middle + 自审利益冲突）**
- 步骤 ① 的**深度代码调研**、步骤 ⑥ 的 **code review** 必须委托 **fresh-context subagent**（`Agent` 工具，**顺序、阻塞、不并行**，`run_in_background:false`，**不用** worktree），只把"结构化结论"回传主流水线。
- 目的是**上下文卫生**不是提速：主上下文跑到后半程（⑤⑥⑦）时不应被前半程的源码 diff / 日志塞满；review 用独立上下文还顺带消除"自己审自己"的利益冲突。
- 步骤 ④ 实现细节量大时，进步骤 ⑤ 前把实现过程**压缩成 ≤10 行摘要**（改了哪些文件 / 关键决策 / 遗留风险）交接，给后半程腾上下文。

### 2.1 跨 Session 防冲突四条（与 §0.6 §0.7 配套）

| # | 维度 | 要求 |
|---|---|---|
| 7 | Session 锁 | 启动前必须按 §0.6 写 `.claude/locks/issuegen-<issue-num>-<spec-id>-<session-sha8>.lock`；命中同 (issue, spec) 锁必须 `AskUserQuestion` 三选一；步骤 ⑧ 末尾必须 `rm` 自己的锁 |
| 8 | 分支命名 | 按 §0.7 用 `feat/issue-<num>/<session-sha8>/<slug>` 三段格式（含 session-sha8），物理不可能撞名 |
| 9 | Base 同步 | 步骤 ④ 任何 commit / push 前必须 `git fetch origin main && git rebase origin/main`（§0.5 已强制 rollout 竞态防护，本约束作为硬性提醒） |
| 10 | In-flight PR 查重 | 步骤 ④ PR 轨建 PR 前必须对每个计划改动的文件跑 `gh pr list --search "involves:@me state:open"` 查同文件 in-flight PR，命中 → **追加 commit 到那个 PR**，禁止新开重复 PR |

### 2.3 示例项目 已知环境坑速查框（高频必撞 · 开工即避，不必每次现查 memory）

> 以下几条在历史几乎**每个 issue 必撞**，已从 memory 提级为正文一等约束（详情仍见对应 `[[memory]]`）。先记住结论，别每次现查、现踩、现绕。

| 坑 | 现象 | 正确做法（正文权威） |
|---|---|---|
| **双轨判定以 hook 为准** | `scripts/git-track-classify.sh` 判 `direct`，但 `git push HEAD:main` 被 pre-push hook 拦截判 `pr`（脚本对 staged-but-uncommitted 会假判 direct，[[git_cached_removal_rebase_deletes_worktree]]） | **pre-push hook 是唯一权威判定**，classify 仅作预判；被 hook 拦即转 PR 轨，**不要反复重试 direct**（见 §4.4） |
| **本仓 PR 永远 0 check** | CI 仅在 push `main`/`test` 触发，PR 不触发任何 check；`gh pr merge --auto` 会**永久挂起**等不到的 check（[[dev_auto_openpr_ci_mismatch]]） | PR 轨**禁用 `--auto`**；本地静态检查全绿 + rebase 干净即**手动** `gh pr merge --squash`（见 §4.4） |
| **CD 并发取消 + deploy-gap** | 合并洪峰下本 session SHA 的 build-images 被后续 commit **并发取消、永不单独 build 完**；或本改动不在某镜像 build 路径过滤内 → 该镜像**永不重建**（[[cd_concurrency_cancel_pathfilter_deploy_gap]]） | **不死等本 SHA 独立 build**：用「后代绿 build + ancestor 校验 / 线上行为确证」判过；轮询设上限（见 §5.2 决策树） |
| **harness worktree 可能落后** | 调研时 grep 不到符号 / 文件不存在 / 心智模型对不上真代码——因 worktree base 落后 origin/main（#1109/#1122/#1113 均因此返工） | 步骤 ① **第一动作强制 `git fetch + rebase origin/main`** 再调研（见 §1.0） |

> ⚠️ 仓库规则**禁 force-push（任何分支）**：PR 轨 rebase 后**不能** force-push 更新已开 PR → 用 `gh pr update-branch` 或推新分支重开 PR（见 §4.4 PR 轨）。

### 2.4 轻量轨（小改动闭环裁剪 · 减少 follow-up 小修的过重流程）

> 历史多数 follow-up 是「≤20 LOC 纯后端/配置/测试、方案无悬念」的小改（如 3 行加 timezone、1 行日志字段、1 行 registry 对齐）。对它们走完整重流程（CI 死等 + 完整 review + 逐个 dispatch）ROI 极低。满足**全部**下列门槛即走轻量轨：

**轻量轨准入（全满足才行）**：
- [ ] 改动 ≤20 LOC 且**不命中 `web/ui/`**（前端必走完整轨 + 浏览器测试）
- [ ] 方案无悬念（②.5 可一句话跳过并在 spec 注明）
- [ ] 非鉴权 / 文件上传 / SQL / 反序列化 / 数据迁移等高风险面

**轻量轨裁剪（仅这几处放宽，其余八步不变）**：

| 步骤 | 完整轨 | 轻量轨 |
|---|---|---|
| ②.5 方案门 | 摆方案卡 + 可能 ASK | 一句话「方案无悬念」写进 spec，跳过 ASK |
| ⑤ K8s 实测 | 等本 SHA 滚到 + 跑 testlist | **优先 ancestor 兜底判过**（§5.2 决策树）：main HEAD 含本 commit + 后代 build 绿已部署 + 函数级/exec 校验；**不为它逐个 dispatch 死等** |
| ⑥ Review | 完整 12 步 fresh-context | 精简版 fresh-context（**仍独立上下文，不自审**） |

> 轻量轨**不放宽**：真 TDD 三相（§2.2-A）、关闭三件套（§2 约束 5）、扩散排查、清理后验证——一律照跑。轻量轨只裁「等待与重型 review 的墙钟成本」，**不裁「正确性证据」**。

---

## 3. 八步闭环总览（与用户 8 项需求 1:1 映射）

> **步骤 ⓪「起 worktree」** 是隔离前置，不算进用户八项需求，但**强制**执行（否则多 session 互相毁代码）。

| 步 | 动作 | 对应用户需求 | 主要工具 |
|---|---|---|---|
| **步骤 ⓪** | 起 Issue 独占 worktree + 临时分支 | 前置（多 session 隔离） | Bash(git worktree add) |
| **步骤 ①** | 上下文澄清 + 需求总结（深度调研走 fresh-context subagent） | 1️⃣ 总结需求，争议 ASK | Read / Grep / **Agent(Explore)** / AskUserQuestion |
| **步骤 ②** | 同步/新建 spec 文档（**在 worktree 内**） | 2️⃣ 更新或创建 spec | Read / Edit / Write |
| **步骤 ②.5** | 🆕 技术方案审批门（code 前唯一强制 gate） | 强化（plan-approval） | AskUserQuestion |
| **步骤 ③** | gh 创建 GitHub Issue + 分支重命名 | 3️⃣ GitHub 建 Issue | Bash(gh issue create / git branch -m) |
| **步骤 ④** | 单流水线开发（rebase + push，**不动主检出 main**） | 4️⃣ 单流水线完成开发 | Edit / Write / Bash |
| **步骤 ⑤** | 测试环境 K8s（`DEPLOY_NAMESPACE`）实测（锁定本 session SHA）；**前端需求加真实浏览器测试** | 5️⃣ 测试 | Bash(kubectl/curl/pytest) / **Playwright MCP / webapp-testing** |
| **步骤 ⑥** | Code review + Issue 评论关闭证据 | 6️⃣ Review + 评论 | Skill(wiki-code-review) / Bash(gh issue comment) |
| **步骤 ⑦** | 关闭 Issue + 严格限定清理本 session worktree/分支 | 7️⃣ 关 Issue | Bash(gh issue close / git worktree remove) |
| **步骤 ⑧** | 串联 `/wiki-session-report` | 8️⃣ 调用 session-report 总结 | Skill(wiki-session-report) |

---

## 4. 步骤逐条执行手册

### 步骤 ⓪ 起 Issue 独占 worktree + 临时分支（前置，强制）

> 目标：本 session 完全跑在 `.claude/worktrees/issue-gen-<session-id>/` 这个独占目录里，**永不**碰项目主检出（`$PROJECT_ROOT`）的工作树（只允许在它上面 `git fetch`）。

#### 0.1 生成 session-id 并起 worktree

```bash
PROJECT_ROOT=$(git rev-parse --show-toplevel)   # 项目主检出根；不写死任何绝对路径
PROJ_ROOT="$PROJECT_ROOT"                         # 下文沿用 PROJ_ROOT 变量名（=项目根），保持脚本一致
SESSION_ID="$(date +%Y%m%d-%H%M%S)-$$"
WORKTREE_PATH="${PROJ_ROOT}/.claude/worktrees/issue-gen-${SESSION_ID}"
TMP_BRANCH="wip/issue-gen-${SESSION_ID}"

# 主检出只 fetch，不动 working tree
git -C "$PROJ_ROOT" fetch origin main

# 起 worktree，新分支基于最新 origin/main
git -C "$PROJ_ROOT" worktree add "$WORKTREE_PATH" -b "$TMP_BRANCH" origin/main

cd "$WORKTREE_PATH"
echo "✅ Worktree: $WORKTREE_PATH"
echo "✅ Branch:   $TMP_BRANCH"
echo "✅ Session:  $SESSION_ID"

# 激活仓库 pre-push hooks（每个 clone 一次，幂等）
git config core.hooksPath scripts/hooks
```

> 已有同名 worktree 路径？说明本 session 之前异常退出，**先**在主检出跑 `git worktree prune` 清失效引用，再重新 `worktree add`；仍冲突则换 `SESSION_ID`（重新生成时间戳）。

#### 0.2 把 session-id 写进环境变量，传递给后续步骤

```bash
export PROJ_SESSION_ID="$SESSION_ID"
export PROJ_WORKTREE="$WORKTREE_PATH"
export PROJ_TMP_BRANCH="$TMP_BRANCH"
```

#### 0.3 步骤 ⓪ 通过判据

- [ ] `pwd` 输出 `.claude/worktrees/issue-gen-${SESSION_ID}`（不是主检出）
- [ ] `git branch --show-current` 输出 `wip/issue-gen-${SESSION_ID}`
- [ ] `git rev-parse HEAD == git rev-parse origin/main`（worktree 起点 = 最新 main）
- [ ] `core.hooksPath` 已设为 `scripts/hooks`（`git config core.hooksPath` 输出该路径）

> 后续步骤 ① ② ③ ④ ⑤ ⑥ ⑦ 全部在 `$PROJ_WORKTREE` 内执行；除步骤 ⓪ / ④ 末尾的 `git fetch origin` 外，绝不在主检出 `$PROJ_ROOT` 上动 git。

---

### 步骤 ① 上下文澄清 + 需求总结（对应 1️⃣）

#### 1.0 开工前强制同步 base（强制 · harness worktree 常落后，见 §2.3）

> 实战教训（#1109/#1122/#1113）：harness 提供的 worktree（或异常恢复的 worktree）其 HEAD 常**落后 origin/main**。不先同步就调研，会 grep 不到新符号 / 文件不存在 / 对着旧代码建错心智模型，白白返工。

```bash
cd "$PROJ_WORKTREE"
git fetch origin main
git rebase origin/main                          # 把 worktree 拉到最新真实代码再调研
git rev-parse HEAD; git rev-parse origin/main   # 两者应一致（或本地仅多出本 session commit）
```

> 调研（grep / Read / subagent）**必须在 rebase 之后**进行。任何「搜不到 / 对不上」**先回这一步确认 base 是不是旧的**，而不是先假设需求描述有误。

#### 1.1 拉上下文（先读，再问）

```bash
# 当前对话已给的事实：用户原文、引用文件、已有 spec
# 仓库现状：
cd "$PROJECT_ROOT"
ls $SPEC_DIRS | head -50                        # 已有 spec 编号清单（SPEC_DIRS 见《环境档案》，例：web/specs/ algo/specs/）
gh issue list --repo "$GIT_REPO" --state open --limit 30 \
  --json number,title,labels                   # 已有 Issue（避免重建）
```

读取与需求相关的：
- 上下文里用户**最近一段消息**（含截图、报错、日志）
- 已有 spec 的 `spec.md` / `plan.md`（若用户引用了某 spec 编号）
- 相关代码（grep 关键标识符确认现状）

> **深度调研走 fresh-context subagent（§2.2-C，强制）**：若需求需跨 ≥3 文件/模块摸清现状（不是看一两个文件就懂），**用 `Agent` 工具发一个顺序、阻塞、不并行的 subagent**（`subagent_type: "Explore"` 或 `general-purpose`，`run_in_background:false`，不用 worktree）去读，只回传"现状结论 + 关键文件:行号 + 风险点"的结构化摘要——**别让大量源码 diff 占满主上下文**，保后半程（⑤⑥⑦）判断质量。浅调研（1–2 个文件）直接读，不必起 subagent。

#### 1.2 写一段「最终需求总结」给用户看

```
## 我理解的需求

**目标**：<动词开头一句话>
**范围**：<量化：N 个接口 / X 个模块 / Y 个页面>
**归属 spec**：<web/specs/006a-... 或新建 algo/specs/0XX-...>
**预期产物**：<API / UI / 配置 / 文档>
**优先级**：<P0/P1/P2>
**验收判据**：<2-4 条可勾选>
```

#### 1.3 争议 → ASK 一次（最多一轮，4 个问题以内）

只问**推不出来的**：

| 维度 | 何时问 |
|---|---|
| 归属 spec（新建 or 增量） | 多个 spec 都沾边 / 无明显归属 |
| 范围切分（这次做哪几条） | 用户原文含"等"/"诸如此类"等模糊量词 |
| 方案选择（A 还是 B） | 两条路线代价/收益差异大 |
| 截止日期 / 优先级 | 推不出且影响排期 |

> 推得出的别问（已有 spec 编号、已有 Issue、已有判据）。用户答完**直接进步骤 ②**，不二次确认"是否开始"。

#### 1.4 步骤 ① 通过判据

- [ ] 「目标 / 范围 / 归属 spec / 验收判据」四项已落字（不是 TODO）
- [ ] 争议项已通过 ASK 收口（或确认无争议）
- [ ] 已确认不是 [wiki-prompt-gen](../wiki-prompt-gen/SKILL.md) / [wiki-issue-acceptance](../wiki-issue-acceptance/SKILL.md) 的场景

---

### 步骤 ② 同步/新建 spec 文档（对应 2️⃣）

#### 2.1 判定 spec 路径

| 情形 | 路径 | 动作 |
|---|---|---|
| 增量到已有 spec | `web/specs/<id>/` 或 `algo/specs/<id>/` | Edit 受影响的 `spec.md`/`plan.md`/`decisions.md`/`tasks.md` |
| 新建 spec | `web/specs/<新编号-kebab-name>/` 或 `algo/specs/<新编号-kebab-name>/` | Write 完整骨架 |

新编号规则：扫 `ls web/specs/ \| sort -V` 取最后一个，按现有命名风格递增（数字 / 数字+字母后缀 / kebab-name）。

#### 2.2 新 spec 骨架（最小集）

```
web/specs/<id>-<kebab-name>/
├── spec.md       # 目标 / 范围 / 验收判据 / 非目标
├── plan.md       # 接口设计 / 数据模型 / 关键决策
├── tasks.md      # 子任务清单（≤10 条，每条带 done 判据）
└── testlist.md   # 测试用例清单（≥10 条，覆盖 happy/edge/error/business）
```

每份 markdown 必须满足 CLAUDE.md §4.3 文档链接规则：
- 出链 ≥3（引用 CLAUDE.md / 关联 spec / 关联代码路径）
- 入链 ≥1（被父 spec / docs/index 链入）
- 无裸 URL（统一 `[文本](路径)` 或 `[[name]]` wiki 链接）

#### 2.2.1 验收标准追溯（AC-ID）+ 测试落点（统一 SoT，见 [`_shared/test-traceability-and-assets.md`](../_shared/test-traceability-and-assets.md)）

- **testlist.md 每条用例标注 AC-ID**（`AC-<spec>-<NN>`）：若上游来自 `wiki-issue-design`（有 `docs/issue/<NNN>/PRD.md`），**继承 PRD 验收判据的 AC-ID，不重定义**；无上游则在此首次分配。
- spec.md 的「验收判据」每条也写成 `- [ ] AC-<spec>-<NN> <可勾选断言>`，与 testlist 一一对应。
- 后续步骤 ④ 写的测试一律落 **canonical 位置**（`tests/acceptance/{unit,ac,browser}/` / `tests/api/`），**不再散放 `web/tests/`**；并更新 `tests/acceptance/ac/AC-STATUS.md` 追溯矩阵。详见共享文件 §2/§3。

#### 2.3 增量改 spec 时

- 在 `spec.md` 末尾追加「## Changelog」段，记本次 Issue 编号 + 一句话变更
- 同步 `tasks.md`（新增子任务）和 `testlist.md`（新增测试用例）
- 不要重写已存在段落，只做**增量 + 标注变更**

#### 2.4 步骤 ② 通过判据

- [ ] spec 路径已确定（已有 or 新建）
- [ ] spec.md 含「目标 / 范围 / 验收判据 / 非目标」四段
- [ ] tasks.md / testlist.md 已同步本次需求
- [ ] markdown 链接巡检过（`grep -nE '\[\[\|\]\(' <spec-files>`）

---

### 步骤 ②.5 技术方案审批门（plan-approval gate，本次优化新增，强制）

> 解决「无计划审批门 → 错误方案一路烧到 CD」。这是 code 之前**唯一**强制人类 gate，凌驾"不可中途暂停"（见 §2.2-B）。

#### 2.5.1 汇总技术方案（来自步骤 ② 的 plan.md，浓缩给用户）

把 spec 里的技术决策提炼成"用户 30 秒能看懂"的方案卡：

```
## 技术方案待确认

**改动面**：<后端 N 接口 / 前端 M 页面 / 数据模型 X 表>
**核心接口**：<METHOD /path → 入参/出参 一句话>
**数据模型**：<新增/改动的表或字段，含迁移策略>
**关键取舍**：<这次为什么选 A 不选 B，代价是什么>
**不做**：<明确排除项>
**影响半径**：<会动到的既有模块 / 是否有扩散风险>
**验证环境**：<默认 K8s `DEV_NAMESPACE`（示例项目=dev）；判 requiresQaData——验收是否必须读 QA 数据态/存量数据（新功能自带夹具=否）>
```

> **🔒 staging 人工授权（强制）**：上面「验证环境」判定为 requiresQaData=true（或有其他理由要用 `DEPLOY_NAMESPACE`）时，**必须**在本轮 AskUserQuestion 里单列一题明示理由申请授权（选项：批准用 staging / 改 dev+自建夹具 / 暂停）。**获批**才可路由 staging，并把「已获人工授权（日期+理由）」写进 spec 与 Issue 的「开发/验证环境」段；未获批一律 dev + 自建夹具。判定为 false 则不问、直接 dev。

#### 2.5.2 有分歧 → AskUserQuestion 给 A/B（最多一轮）

只问**方案级**分歧（不是需求级，需求级在步骤 ①）：

| 维度 | 何时问 |
|---|---|
| 数据模型走法 | 新表 vs 复用既有表加字段 |
| 接口风格 | 同步 vs 异步任务 / 一个聚合接口 vs 多个细接口 |
| 落点 | 改 web 还是 algo / 哪个 spec |
| 兼容策略 | 是否要 migration / feature flag / 灰度 |

> 与步骤 ① 的需求级 ASK 区分：① 问"做什么"，②.5 问"怎么做"。两处各最多一轮，用户拍板后不二次确认。

#### 2.5.3 例外：方案无悬念可跳过

纯 docs / ≤20 LOC hotfix / 配置改动 → 一句话说明后跳过本门，并在 `spec.md` 注明「方案无悬念，跳过 ②.5」。

#### 2.5.4 步骤 ②.5 通过判据

- [ ] 技术方案卡已摆给用户（改动面 / 接口 / 数据模型 / 取舍 / 不做 / 影响半径）
- [ ] 方案级分歧已 ASK 收口（或确认无悬念跳过并在 spec 注明）
- [ ] 用户已拍板，方可进步骤 ③（拍板后不再二次确认"是否开始"）

---

### 步骤 ③ 在 GitHub 创建 Issue（对应 3️⃣）

#### 3.1 切到对项目仓库有权限的 gh 账号

> 账号名按项目而定（示例项目 用 `zhaod39_example-corp`；其它项目用对 `GIT_REPO` 有写权限的账号），下面用 示例。

```bash
export GH_TOKEN="$(gh auth token --user zhaod39_example-corp)"     # 示例：个人账号 personal-account 对项目仓库（GIT_REPO）会 404
gh auth status                          # 确认 active 是对 GIT_REPO 有权限的账号
```

#### 3.1.5 上游 Issue 复用探测（防与 wiki-issue-design 重复建 Issue，P2.6）

> `wiki-issue-design` 串联进来时**已经建过 `[需求]` Issue**。dev 不能再建一个，否则同需求两个 Issue。

```bash
# 1) 若用户给了需求目录（docs/issue/<NNN>/），README 里通常已有 Issue 链接
ls docs/issue/ 2>/dev/null | tail -10
grep -rEoh 'issues/[0-9]+' docs/issue/<NNN>-*/README.md 2>/dev/null | head -1   # 上游 Issue 号

# 2) 或按标题搜既有未关 Issue（design 建的标题前缀 [需求]）
gh issue list --repo "$GIT_REPO" --state open --search "<feature 关键词> in:title" \
  --json number,title,labels
```

- **命中上游 Issue** → 复用它：`ISSUE_NUM=<上游号>`，跳过 3.2 创建，直接在该 Issue 评论里追加「进入开发」说明 + 关联 spec；继承其验收判据的 AC-ID（见 §2.2.1）。
- **未命中**（纯 dev 发起，无 design 上游）→ 走 3.2 正常创建。

#### 3.2 创建 Issue（仅"未命中上游 Issue"时执行）

> **📛 Issue 标题命名规范（强制，全 wiki-issue-* 技能统一）**：所有 `gh issue create` 标题一律
> **`[类型][SPEC-XX][XX模块][XX功能]<一句话描述>`** —— 四段方括号紧挨 + 描述，方括号内无空格。
> - **类型** ∈ `需求 / 任务 / BUG / 优化 / 重构 / 文档 / 调研`（dev 发起的开发任务多为 `任务`/`需求`）
> - **SPEC-XX**：所属 spec 编号（如 `SPEC-018`）；跨 spec 基建 / 纯环境问题对不上 → 填 `SPEC-NA`
> - **XX模块**：业务模块（如 `抽帧`、`样本池`、`训练`）；对不上 → 填 `通用`
> - **XX功能**：具体功能点（如 `进度条`、`列表分页`）；对不上 → 填 `其他`
> - Follow-up 跟进 Issue：类型按性质选（多为 `任务`/`优化`），描述末尾加 `（Follow-up #N）`
> 例：`[任务][SPEC-018][抽帧][列表分页]抽帧记录页消费后端 total 治分母错位`

```bash
SPEC_PATH="web/specs/<id>-<name>"       # 或 algo/specs/...
ISSUE_URL=$(gh issue create \
  --repo "$GIT_REPO" \
  --title "[<类型>][SPEC-XX][XX模块][XX功能]<一句话目标>" \
  --label "<feature|enhancement|bug>" \
  --body "$(cat <<EOF
## 背景
<2-3 句：为什么要做、谁提的、卡在哪>

## 目标
<动词开头一句话 + 量化范围>

## 范围
- 包含：<N 条具体项>
- 不包含：<明确排除项，避免范围蔓延>

## 验收判据
- [ ] <可勾选条目 1>
- [ ] <可勾选条目 2>
- [ ] <可勾选条目 3>

## 关联 Spec
- [${SPEC_PATH}/spec.md](${SPEC_PATH}/spec.md)
- [${SPEC_PATH}/plan.md](${SPEC_PATH}/plan.md)

## 开发/验证环境
开发与验证环境：K8s \`${VERIFY_NS}\`（开发类默认 = 项目《环境档案》DEV_NAMESPACE，例：\`dev\`，入口 \`${DEV_SLB:-10.0.0.20}\`）
<若 ②.5 已获人工授权用 staging，此处改写：K8s \`${DEPLOY_NS}\`（已获人工授权：<日期>，理由：<一句话>；入口 SLB \`${INTERNAL_SLB}\`）；未授权禁止填 staging>
<项目未声明 DEV_NAMESPACE → 写 \`${DEPLOY_NS}\` 旧行为，无需授权>

## 执行轨道
本 Issue 由 \`/wiki-issue-dev\` 单流水线驱动：spec → 开发 → K8s 实测 → review → 关 Issue → 进展汇报。
EOF
)")
ISSUE_NUM=$(echo "$ISSUE_URL" | grep -oE '[0-9]+$')
echo "✅ Created Issue #$ISSUE_NUM → $ISSUE_URL"
```

#### 3.3 在 spec 顶部反向挂上 Issue 链接

在 `spec.md` 顶部 frontmatter 或第一段加：

```markdown
> 跟踪 Issue：[#<num>](https://github.com/<GIT_REPO>/issues/<num>)
```

#### 3.4 临时分支重命名为正式 feature 分支

拿到 `$ISSUE_NUM` 后，把步骤 ⓪ 起的临时分支 `wip/issue-gen-${SESSION_ID}` 重命名为正式名，与 Issue 一一对应：

```bash
SLUG="<kebab-slug，按需求 3-5 词>"
FEAT_BRANCH="feat/issue-${ISSUE_NUM}-${SLUG}"
git branch -m "$PROJ_TMP_BRANCH" "$FEAT_BRANCH"
export PROJ_FEAT_BRANCH="$FEAT_BRANCH"
echo "✅ Branch renamed: $PROJ_TMP_BRANCH → $FEAT_BRANCH"
```

> worktree 目录路径不改（不需要 mv，git 自动识别）；只改分支名，方便步骤 ⑦ 清理时按 `feat/issue-${ISSUE_NUM}-*` 精确锁定。

#### 3.5 步骤 ③ 通过判据

- [ ] Issue 已建，URL 已记 `$ISSUE_URL` / 编号已记 `$ISSUE_NUM`
- [ ] Issue body 含「背景 / 目标 / 范围 / 验收判据 / 关联 Spec / 开发/验证环境」六段
- [ ] 「开发/验证环境」段：默认 `DEV_NAMESPACE`；若写了 staging，必须带「已获人工授权（日期+理由）」（来自 ②.5 AskUserQuestion，未获批不得填）
- [ ] spec.md 已反链 Issue
- [ ] 临时分支已重命名为 `feat/issue-${ISSUE_NUM}-<slug>`（`$PROJ_FEAT_BRANCH` 已 export）

---

### 步骤 ④ 单流水线完成 Issue 开发（对应 4️⃣）

> **「单流水线」= 顺序执行，禁止把开发任务拆给多个后台 subagent 并行跑**（用户已明确要求）。
> 例外：开发任务内部若有 ≥5 个真正独立的代码模块（互不引用、互不共享文件），可在「该步内」按 §0 红线启用局部并行；主流水线步骤 ① 到 ⑧ 之间永远顺序。

#### 4.1 确认仍在本 session 的独占 worktree 内

```bash
cd "$PROJ_WORKTREE"                                       # 强制回到本 session worktree
[ "$(git branch --show-current)" = "$PROJ_FEAT_BRANCH" ] || \
  { echo "❌ 不在 $PROJ_FEAT_BRANCH 上，禁止继续"; exit 1; }
pwd | grep -q "issue-gen-${PROJ_SESSION_ID}" || \
  { echo "❌ 不在本 session worktree，禁止继续"; exit 1; }
```

> feature 分支已由步骤 ⓪ + ③.4 准备好；步骤 ④ 不再 `git checkout -b`，避免把别的 session 在跑的分支顶掉。

#### 4.2 按 tasks.md 顺序实现（真 TDD：红 → 绿 → 反验证，见 §2.2-A）

对每条子任务，严格按 TDD 三相，**禁止 test-after**：

1. **🔴 红**：先写测试，**落 canonical 位置**（`tests/acceptance/unit/` L1 / `tests/acceptance/ac/` L2 AC 级 / `tests/api/` 通用 API / `tests/acceptance/browser/` L3；见 [`_shared/test-traceability-and-assets.md`](../_shared/test-traceability-and-assets.md) §2，**不要散放 `web/tests/`**）→ 跑 → **确认失败**，且失败原因是"功能未实现"（NotImplemented / 断言不符），不是 import/语法错。
   ```bash
   python -m pytest <new_test> -x 2>&1 | tail -20   # 期望看到 FAILED（功能未实现）
   ```
2. **🟢 绿**：再 Edit / Write 写实现 → 跑同一测试 → **变绿**。
3. **🔴 反验证**：临时注释掉本次实现的核心行 → 重跑测试 → **必须重新变红**（证明测试真的在测这个功能，直堵 [[orm_field_typo_with_mock_aligned]]「mock 对齐 bug 永远绿」）→ 确认后还原实现。
4. 改完 `git add <具体文件> && git commit -m "..."` —— **不要** `git add -A`；测试与实现可同一 commit，commit message 体现"先红后绿"。
5. Pre-merge 同步：受影响 Spec 的 `spec.md` / `plan.md` / `decisions.md` 一并提交（CLAUDE.md §4）。

> **禁止**为让测试过去改断言迁就实现；**禁止** mock 写成与被测逻辑同样的错字段名（那会让测试永远绿）。每条子任务的测试都必须经历过"红"。

#### 4.3 静态检查门禁（与 build-images.yml 逐字对齐）

按 [wiki-code-commit](../wiki-code-commit/SKILL.md) §4 跑：

```bash
CHANGED=$(git diff --name-only origin/main...HEAD)
echo "$CHANGED" | grep -q '^web/ui/'      && (cd web/ui && npm run typecheck && npm run lint)
echo "$CHANGED" | grep -q '^web/backend/' && ruff check --select F821 web/backend/src/ web/backend/tests/
echo "$CHANGED" | grep -qE '^algo/'       && (cd algo && uvx ruff check --select F821 src/ shared_kernel/ contexts/data-factory-context/ --exclude contexts/data-factory-context/tests/integration)
```

工具缺失 → 按 wiki-code-commit §4.3 先修工具再复跑；**不许跳过检查直接 push**。

#### 4.4 合代码到 main（双轨 + rebase + retry，**不动主检出**）

> **判轨以 pre-push hook 为唯一权威**（[[git_cached_removal_rebase_deletes_worktree]]）：`git-track-classify.sh` 对 staged-but-uncommitted 会**假判 `direct`**，只能当预判参考。正确顺序 = 先 `git commit` → 直接尝试 `git push HEAD:main`：hook 放行即 direct 轨；hook 拦截（输出 `track=pr` / 引用 CLAUDE.md §5.0 / TD-0025）即**立刻转 PR 轨，不要反复重试 direct**（历史 #1109/#1122/#1126 每次都在此空耗一来一回）。

```bash
bash scripts/git-track-classify.sh   # 仅预判参考；最终以 push 时 pre-push hook 判定为准
```

**通用前置：rebase 拿其他 session 已推的 commit**

```bash
git fetch origin main
git rebase origin/main              # 有冲突 → 解，禁止 --strategy=ours/theirs 一把梭
```

**🔒 资源锁（§0.8，条件启用 · 🌗 v6 双车道）**：rebase 完成后、下面真正 `git push HEAD:main` **之前**，若 `devlock` 可达且本项目有 `DEPLOY_NAMESPACE` → `python3 ~/.claude/mcp/devlock/cli.py lock-acquire CI,CD,DEV --session "$SESSION_ID" --label "wiki-issue-dev #$ISSUE_NUM" --issue $ISSUE_NUM --ttl 900 --wait 3600`（开发道默认锁面；②.5 已授权 staging → 资源改 `CI,CD,STAGING`。Bash `run_in_background`；勿用 MCP 阻塞式 `lock_acquire` 排队），拿到 `granted` 才继续 push；超时/不可用 → WARN 跳过（降级，不阻断）。心跳由守护进程自动续租(60s/拍,见 §0.8 心跳守护);守护未起时退回每 5min `lock_heartbeat(request_id)`。**释放按 v6 对称分段**：CI 构建绿放 CI（`cli.py lock-release $REQ --resources CI`）→ 本车道环境（dev 道=dev；授权道=staging dispatch 追平后）rollout 确认放 CD（`--resources CD`）→ 验证段只持环境锁 → 步骤 ⑤ 收口全放。合并标题前缀=车道选择器（dev 道禁 `fix(` 前缀）；改动不命中 build-images on.push.paths（纯 docs）→ 零 CI/CD 消耗可免锁直合（合后 `gh run list --commit` 验证零 run）。

**direct 轨（轻量直推，在 worktree 内完成，不切 main）：**

```bash
# 关键：从 feature 分支直接 push HEAD 到远程 main
# 不需要在本地 checkout main，避免与其他 session 争用主检出
push_to_main() {
  local attempt=$1
  echo "⏳ Push attempt $attempt/3 to origin main..."
  if git push origin "HEAD:main"; then
    echo "✅ Push succeeded on attempt $attempt"
    return 0
  fi
  echo "⚠️  Push rejected (likely remote moved). Re-fetch + rebase + retry."
  git fetch origin main
  git rebase origin/main || { echo "❌ Rebase conflict on attempt $attempt, 停下让用户处理"; return 2; }
  return 1
}

for i in 1 2 3; do
  push_to_main "$i" && break
  rc=$?
  [ "$rc" = 2 ] && exit 1
  [ "$i" = 3 ] && { echo "❌ 3 次 push 均被 reject，可能远程有 force-push / 历史改写，停下报告用户"; exit 1; }
done
```

> §5.1 适用场景：docs-only / 单文件 hotfix(≤20 LOC) / 配置 yml only(≤50 LOC) / 新建 TD。
> **不要** `git checkout main` —— 主检出的 working tree 可能正被别的 session / 用户终端占用；用 `git push HEAD:main` 直接把当前分支顶推到远程 main，本 worktree 始终留在 feature 分支上。

**pr 轨（≥2 个代码文件 / 任一 spec.md / cross-module）：**

> **建 PR 前先查同文件 in-flight PR（硬约束 10）**：
> ```bash
> PLANNED_FILES=( $(git diff --name-only origin/main...HEAD) )
> for FILE in "${PLANNED_FILES[@]}"; do
>   CONFLICT_PR=$(gh pr list --state open --search "involves:@me" --repo "$GIT_REPO" \
>     --json number,headRefName,files \
>     --jq ".[] | select(.files[].path == \"$FILE\") | .number" | head -1)
>   if [ -n "$CONFLICT_PR" ]; then
>     echo "⚠️ $FILE 已被 PR #$CONFLICT_PR 占用 → 应追加 commit 到该 PR 而非新开"
>   fi
> done
> ```
> 命中且属于本 issue 同语义改动 → 追加 commit 到那个 PR；属于不同 issue 但碰巧改同文件 → 跟那个 PR 的 owner 协调先后顺序，**不要新开重复 PR**。

```bash
git push -u origin "$PROJ_FEAT_BRANCH"                    # 推 feature 分支（PR 载体；hook 不拦 feature）
export GH_TOKEN="$(gh auth token --user zhaod39_example-corp)"
gh pr create --base main --head "$PROJ_FEAT_BRANCH" \
  --title "<scope>: <one-line summary> (close #${ISSUE_NUM})" \
  --body "$(cat <<EOF
## Summary
- closes #${ISSUE_NUM}
- <1-3 bullet points>

## Test plan
- [x] 本地静态检查全绿（tsc/eslint/ruff F821）—— 本仓 PR 不触发 CI，以本地为准
- [ ] 合 main 后 Post-push CI build-images + CD Deploy 全绿（见 §4.6）
EOF
)"
PR_NUM=$(gh pr view --json number -q .number)
# ❌ 禁用 --auto：本仓 PR 永远 0 check，--auto 会永久挂起（[[dev_auto_openpr_ci_mismatch]]）
# ✅ 本地静态检查全绿 + rebase 干净 → 手动 squash merge（_gh_retry 防 EOF/限流半途失败）
_gh_retry 3 2 -- gh pr merge "$PR_NUM" --squash --delete-branch
```

> **仓库禁 force-push（任何分支）**：PR 开出后若 origin/main 又前进、需 rebase——**不能** `git push --force` 更新本 PR 分支。两条合规路径：
> 1. 优先 `gh pr update-branch "$PR_NUM"`（GitHub 侧把 base 合进来，免本地 force-push）；
> 2. 不行则**推新 feature 分支重开 PR**（分支名末尾换新或加 `-r2` 后缀），关掉旧 PR（历史 #1122 即走此路）。
>
> rebase 时 `spec.md` Changelog 等冲突按 **union（双方条目都保留）** 解，不要丢任一方。

#### 4.5 锁定本 session 推上去的 SHA（防 rollout 竞态）

push 成功**立即**抓本 session 触发的 commit SHA，作为步骤 ⑤ 等 rollout 的锚点：

```bash
git fetch origin main
export PROJ_PUSHED_SHA=$(git rev-parse origin/main)
echo "✅ Locked SHA for this session: $PROJ_PUSHED_SHA"
```

> 若稍后有其他 session 又推了新 commit，`origin/main` 会被超过；步骤 ⑤ 会等到 K8s deploy image tag 出现 `$PROJ_PUSHED_SHA` 才开测——若被覆盖到永远不出现，需回步骤 ④ 重 rebase + push，刷新 `$PROJ_PUSHED_SHA`。

#### 4.6 Post-push CI 验证（强制，按 `$PROJ_PUSHED_SHA` 精确匹配）

```bash
export GH_TOKEN="$(gh auth token --user zhaod39_example-corp)"
gh run list --repo "$GIT_REPO" --commit "$PROJ_PUSHED_SHA" --limit 10 \
  --json databaseId,name,status,conclusion
RUN_ID=$(gh run list --repo "$GIT_REPO" --commit "$PROJ_PUSHED_SHA" --limit 1 --json databaseId -q '.[0].databaseId')
gh run watch "$RUN_ID" --repo "$GIT_REPO" --exit-status
```

- 空结果（改动全在 `build-images.yml` paths 白名单外，如纯 docs）→ 写入 Issue 评论时注明「未命中 paths，预期不触发」
- CI 红 → 在**当前 worktree** 修复（`git add` / `git commit` / 重走 4.4）；不要起新分支、不要换 worktree
- 环境抖动 `gh run rerun`；不可恢复先问用户是否 `git revert`

#### 4.6.1 build-images 绿后必须 watch 链式 CD Deploy run 到终态（强制 · build 绿 ≠ 部署成功）

> ⚠️ **`build-images` 绿只代表镜像造出来了，不代表滚上了环境**：`push main → build-images(CI) 绿 → 链式触发
> cd.yml（CD Deploy, workflow_run 事件）滚 K8s`，CD 里 `deploy-k8s`（kubectl set image + rollout status）成功后
> 才跑 `post-deploy-acceptance-gate`。真实故障：**CD 判红、无人 watch CD run 到终态 → 镜像一直没真正部署上去**，
> 而只看 build 绿就宣告完成会彻底漏掉这一段。

**硬规则**：build-images 绿后**不许停**——按 [`_shared/dev-acceptance-gate.md`](../_shared/dev-acceptance-gate.md) **§5**，用 `$PROJ_PUSHED_SHA`（非流动的 `origin/main`）为锚点定位链式 CD Deploy run（`--workflow cd.yml --commit "$PROJ_PUSHED_SHA" --event workflow_run`）并 `gh run watch` 到终态，再**分 `deploy-k8s` / `post-deploy-acceptance-gate` 两个 job 分别读结论**（别只看总 conclusion）：

- **`deploy-k8s` 失败 = 镜像没滚上去**（环境仍停在旧镜像）→ 按 §5③ 先看 Pod 归因，禁把代码问题当环境抖动无脑 rerun：
  - **A 类（本轮代码/清单引入**，Pod `CrashLoopBackOff`/`ImagePullBackOff`/启动即崩/readiness 挂）→ **回步骤 ④** 在当前 worktree 修好、重走 4.4→4.6，直到镜像真滚上、CD 绿；
  - **B 类（环境抖动/基建）** → `gh run rerun "$CD_RUN" --failed`；仍红且非代码 → 记录知会用户；
  - **C 类（并发顶替）** → 同 `cancelled`，走 5.2 ancestor 兜底，不算本轮失败。
- **`post-deploy-acceptance-gate` 门红**（镜像已滚但 L1+L2 全量回归门红）→ 按 [`_shared/dev-acceptance-gate.md`](../_shared/dev-acceptance-gate.md) **§2 `failure` 行**处置：本技能=**交互类**，**不放行**，回步骤 ④ 修到门绿或 `git revert`（凌驾 agent 自评）。
- **`success`**（deploy-k8s 滚成功 + gate 过）/ **`skipped`**（本 commit 无可部署模块）/ **`cancelled` 被后代 CD 顶替 + ancestor 兜底成立** → 部署面判过，进步骤 ⑤。

> 与 §5.2 deploy-gap 决策树 / ancestor 兜底**同源不重复**：本节负责「主动 watch CD run 到终态 + deploy-k8s/gate 分 job 归因」，5.2 Step C 负责「本 SHA build 被并发取消/路径过滤时的兜底判过」——两者衔接，勿各造轮子。**CD run 到 `success`/`skipped`/兜底成立之前不放 CD 段锁**（§5 ④ devlock 收口点，别在 build 绿就放 CD）。

#### 4.7 步骤 ④ 通过判据

- [ ] 始终在 `$PROJ_WORKTREE` 内、`$PROJ_FEAT_BRANCH` 上（每次 push 前自检过）
- [ ] tasks.md 每条子任务都已提交对应 commit
- [ ] 静态检查命中模块全绿（命令逐字对齐 build-images.yml）
- [ ] push main 前已 `git fetch + rebase origin/main`（rebase + retry 模式，未 force-push）
- [ ] `$PROJ_PUSHED_SHA` 已锁定（精确等于本 session 推上去的 commit）
- [ ] Post-push CI（build-images + cd）按 `$PROJ_PUSHED_SHA` 全绿 或 确认未命中 paths
- [ ] **build 绿后已 watch 链式 CD Deploy run 到终态**（`success`/`skipped`/ancestor 兜底放行；`deploy-k8s` 或 `post-deploy-acceptance-gate` failure 已按 [`_shared/dev-acceptance-gate.md`](../_shared/dev-acceptance-gate.md) §5 归因处置），**未在 build 绿就宣告部署完成**（见 4.6.1）

---

### 步骤 ⑤ 验证环境 K8s 实测（开发道默认 `DEV_NAMESPACE`，对应 5️⃣）

> **🔒 持锁验证（§0.8）**：心跳由守护进程自动续租(60s/拍,见 §0.8 心跳守护);守护未起时退回每 5min `lock_heartbeat(request_id)`；**本步结束（无论通过 / 失败 / 回 ④ 修）** 都必须释放仍持有的锁，让下一个排队 session 递补。降级（无锁）模式下本提示不适用。

#### 5.0 环境路由 + staging 人工授权门（强制，先于一切验证）

按项目 CLAUDE.md《环境路由规则》定 `VERIFY_NS`：

1. **开发道（默认，🌗 v6）**：项目声明了 `DEV_NAMESPACE`（例：`dev`）且 ②.5 未授权 staging → `VERIFY_NS=$DEV_NAMESPACE`。本 session 合并的 CD **直滚 dev**（feat/chore 标题=dev 道路由，staging 全程不动）：5.2 改为确认 **dev** rollout 含本 SHA（锚点仍 `$PROJ_PUSHED_SHA`，namespace 用 `$VERIFY_NS`）→ 放 CD（`cli.py lock-release $REQ --resources CD`，CI 已在构建绿时放）→ 验证段只持 `DEV` + **SHA ancestor 核验 + 入口打 `DEV_SLB`**（CLAUDE.md《环境路由规则》三件套；CD 被并发取消时兜底 `gh workflow run cd.yml -f namespace=dev`）。
2. **staging 道（仅限已获人工授权）**：②.5 已获批（spec/Issue 里有「已获人工授权」记录）→ `VERIFY_NS=$DEPLOY_NS`，锁面 `CI,CD,STAGING`；合并仍 dev 道标题（CD 滚 dev），持 STAGING 锁内 `gh workflow run cd.yml -f namespace=staging` 追平含本 commit 后再验。
3. **🔒 红线**：没有 ②.5 授权记录，**禁止**把 `VERIFY_NS` 定为 staging。中途才发现必须依赖 QA 数据态（或 dev 不可用：关键 pod 非 Running / `DEV_SLB` 不通 / `DEV` 锁 3 次拿不到）→ **现场 `AskUserQuestion` 补授权**（理由+选项：批准 staging / dev 自建夹具重试 / 暂停），获批才切换并把授权记录补进 Issue 评论；**绝不静默自动回退**。
4. 项目未声明 `DEV_NAMESPACE` → `VERIFY_NS=$DEPLOY_NS` 旧行为，本门跳过。

```bash
VERIFY_NS="${DEV_NS:-$DEPLOY_NS}"   # 开发道=DEV_NAMESPACE；已获人工授权或无双车道才允许 =DEPLOY_NS
```

#### 5.1 环境前置检查（红线：不通就停）

```bash
export KUBECONFIG="$KUBECONFIG_PATH"     # kubeconfig 路径见《环境档案》KUBECONFIG_PATH，例：~/.kube/config
nc -z -G 5 10.0.0.30 6443 && echo "VPN OK" \
  || { echo "❌ VPN 未连：提示用户连项目内网 VPN 或走 dev-public 兜底（[[public_network_dev_fallback]]）"; exit 1; }
kubectl auth whoami
kubectl get pods -n "$DEPLOY_NS" --no-headers | head -5    # CD 落点（镜像源），开发道也要可读
[ "$VERIFY_NS" != "$DEPLOY_NS" ] && kubectl get pods -n "$VERIFY_NS" --no-headers | head -5   # 开发道验证 ns 连通
```

#### 5.2 等 CD 把**本 session 代码**滚到测试 namespace（deploy-gap 兜底决策树）

> deployment 名见项目《环境档案》`KEY_DEPLOYMENTS`，以下用 示例（`web-bff` / `celery-worker` / `algo`）。
>
> ⚠️ **核心教训**（[[cd_concurrency_cancel_pathfilter_deploy_gap]]，历史每个 issue 几乎必撞）：多 session 合并洪峰下，**本 session SHA 的 build-images 常被后续 commit 并发取消、永远 build 不完**；或本改动不在某镜像的 build 路径过滤内、该镜像**永不重建**。死等本 SHA 会陷入「反复 dispatch + 无限后台轮询」的时间/token 黑洞——这是历史上最大的浪费源。**按下列决策树判过，不要死等本 SHA 单独 build 成功。**

```bash
SHORT_SHA=$(echo "$PROJ_PUSHED_SHA" | cut -c1-7)
```

**Step A：先判本改动该重建哪个镜像（决定是否存在路径过滤 deploy-gap）**

```bash
CHANGED=$(git -C "$PROJ_ROOT" diff --name-only "${PROJ_PUSHED_SHA}~1" "$PROJ_PUSHED_SHA")
echo "本次改动文件：$CHANGED"
# 对照 build-images.yml 各镜像 on.push.paths：若改动不在任一目标镜像 paths 内
#   → 该镜像不会因本 commit 重建（deploy-gap），直接跳 Step C 兜底，别空等 Step B
```

**Step B：等本 SHA 滚到（设上限，不无限轮询）**

```bash
kubectl get deploy web-bff celery-worker algo -n "$DEPLOY_NS" \
  -o custom-columns=NAME:.metadata.name,IMAGE:.spec.template.spec.containers[0].image
kubectl rollout status deploy/web-bff -n "$DEPLOY_NS" --timeout=10m
CURR_TAG=$(kubectl get deploy web-bff -n "$DEPLOY_NS" -o jsonpath='{.spec.template.spec.containers[0].image}')
echo "$CURR_TAG" | grep -q "$SHORT_SHA" && echo "✅ 已滚到本 session SHA，直接开测（5.3）"
```
- 轮询**最多 2–3 轮 / ~15min 上限**。命中 `$SHORT_SHA` → 直接 5.3 开测（最干净）。
- 超时仍没命中（被并发取消 / 路径过滤不重建）→ **不再 dispatch 死磕**，转 Step C 兜底。

**Step C：deploy-gap 兜底判过（三选一即可视为「部署面通过」）**

> **🚦 #1818 门前置闸（2026-06-15 焊接，必读 [_shared/dev-acceptance-gate.md](../_shared/dev-acceptance-gate.md)）**：ancestor 兜底（C-1/C-3 纯历史/补建证据、不真跑功能）**仅在 dev L1+L2 全量门绿时才允许**——先读 #1818 的 `post-deploy-acceptance-gate` job 结论：
> ```bash
> RUNID=$(gh run list --repo your-org/your-repo --workflow cd.yml --json databaseId,headSha \
>   --jq '[.[]|select(.headSha|startswith("'"$PROJ_PUSHED_SHA"'"))]|.[0].databaseId')
> [ -n "$RUNID" ] && gh run watch "$RUNID" --repo your-org/your-repo --exit-status >/dev/null 2>&1
> GATE=$(gh run view "$RUNID" --repo your-org/your-repo --json jobs \
>   --jq '.jobs[]|select(.name|startswith("Post-deploy acceptance gate"))|.conclusion' 2>/dev/null)
> ```
> - `GATE=success`（门绿）→ 回归维度权威通过，**可用 C-1/C-2/C-3 兜底判「部署面过」**；
> - `GATE=failure`（门红）→ **一票否决，不放行**：dev L1+L2 检出回归，必须修到门绿或 revert，禁止靠 ancestor 兜底判过；
> - `GATE=skipped`/空/查不到（#1818 未合/未跑）→ **不许纯 C-1 ancestor 判过**，必须走 5.3 跑 testlist.md 真验（或 C-2 线上行为确证）。

本 session 代码只要**真在 main 线性历史里**、且**某个含它的后代 commit 的绿 build 已部署**，功能就在线上跑——无需本 SHA 单独 build 成功。判过优先级 **B > C-2 > C-1**：

```bash
git -C "$PROJ_ROOT" fetch origin main --quiet

# C-1 ancestor 校验（纯历史证据）：本 commit 是当前部署镜像 SHA 的祖先
DEPLOYED_SHA=$(echo "$CURR_TAG" | grep -oE '[0-9a-f]{7,}' | head -1)
git -C "$PROJ_ROOT" merge-base --is-ancestor "$PROJ_PUSHED_SHA" "origin/main" \
  && echo "✅ 本改动在 main 历史" 
# 并确认部署镜像 SHA($DEPLOYED_SHA) 是本 commit 的后代、且该后代 build 是 success（gh run list --commit <后代SHA>）

# C-2 线上行为确证（最强证据，优先用）：直接观测本改动的行为
#   - 后端函数级：kubectl exec 进 pod 跑数据无关校验 / curl 端点看新字段
#   - 日志级：kubectl logs 抓本改动新增的日志字段
#   - bundle 级：curl 线上 bundle grep 本次新增字符串（前端 marker）

# C-3 主动补建（C-1/C-2 都不成立、且确需本镜像新版时）：
#   gh workflow run build-images.yml 强制全量 / kubectl set image 滚到含本代码的镜像
#   仍可能被并发取消 → 最多重试 2 次，别无限循环
```

> **如实标注**：在 acceptance.md / 关闭评论里写明部署面通过用的是哪条（B / C-1 / C-2 / C-3）。走 C 系时**必须**注明「本 SHA build 被并发取消，靠后代 build `<SHA>` 落地 + <行为确证方式>」，**不得谎报"本 SHA build 绿"**。
> 锚点**必须**用 `$PROJ_PUSHED_SHA`（本 session push 的 commit），不用流动的 `git rev-parse origin/main`（多 session 并行时它一直在动，可能已是别人的 commit）。

#### 5.3 跑 testlist.md 的用例（≥10 条）

> K8s LB 入口**按 5.0 选定的 `VERIFY_NS` 取**：开发道 = `DEV_SLB`（例：`10.0.0.20`，**禁打 10.0.0.10/app.example.com——打错环境=假结论**）；staging 道（已授权）= `INTERNAL_SLB`（例：`10.0.0.10`）。各服务 NodePort 以 示例（BFF `30115` / algo `30120`）。

```bash
ENTRY_IP=$([ "$VERIFY_NS" = "${DEV_NS:-}" ] && echo "$DEV_SLB" || echo "$INTERNAL_SLB")

# 单条 curl
curl -sf "http://${ENTRY_IP}:30115/<endpoint>" | python3 -m json.tool

# pytest 批量（指 K8s LB 入口）
BFF_BASE_URL=http://${ENTRY_IP}:30115 \
ALGO_BASE_URL=http://${ENTRY_IP}:30120 \
  python -m pytest tests/api/test_<spec>.py -v --tb=short 2>&1 | tail -80
```

数据落库 / Pod 日志 / 5xx 排查 → 参考 [wiki-issue-acceptance](../wiki-issue-acceptance/SKILL.md) §3.4 / §3.5（kubectl logs / psql / redis-cli / mc）。

#### 5.3.B 前端需求 → 强制真实浏览器测试（headless API 之外，必跑）

> headless（curl/pytest）只验后端返回，测不出 UI 真实交互。**完整标准（入口/工具/八维交互检查表/证据五件套/固化为回归/判过标准）见统一 SoT：[`_shared/frontend-browser-testing.md`](../_shared/frontend-browser-testing.md)，必读必执行**（dev/acceptance/bug-fix 共用一份，不在此重复）。

**判定**：

```bash
git diff --name-only origin/main...HEAD | grep -q '^web/ui/' \
  && echo "FRONTEND=1 → 按 _shared/frontend-browser-testing.md 强制执行" \
  || echo "FRONTEND=0 → 纯后端，跳过本节（仅 5.3 headless）"
```

**dev 侧最低交付**：命中前端 → 至少 1 条核心路径用真实浏览器跑通（Playwright MCP），覆盖共享文件 §5 八维、留 §6 证据、按 §7 固化为 `tests/acceptance/browser/*.spec.ts`。浏览器测出的 UI 问题与 headless 同级 = **P1** → 回步骤 ④ 修 → 重测（含硬刷新确认服务的是本 session SHA）。

#### 5.4 写测试结果到 `<spec>/acceptance.md`

| # | 测试用例 | 类别 | 测试方式 | 状态 | 备注 |
|---|---------|------|---------|------|------|
| TC-01 | ... | Happy path | headless(API) | ✅ / ❌ | 5xx 见 #问题1 |
| TC-0X | ...（前端） | UI 交互 | **browser(Playwright)** | ✅ / ❌ | 控制台无 error；截图见附 |

> 前端需求：acceptance.md 必须同时含 headless 行与 browser 行；browser 行附关键页截图链接。

#### 5.5 ❌ 用例 → 修 → 重新走步骤 ④ → 步骤 ⑤

循环到所有 TC 通过 + `kubectl rollout status` ready + Post-push CI 全绿。

#### 5.6 步骤 ⑤ 通过判据

- [ ] 线上 deploy 镜像 tag 含 `$PROJ_PUSHED_SHA` 短哈希（CD 已滚到**本 session** 推的 commit，未被其他 session 覆盖）；开发道还需 `VERIFY_NS` 镜像过 ancestor 核验
- [ ] **#1818 全量门（dev L1+L2 回归门）未红**（见 §5.2 Step C 门前置闸 / [_shared/dev-acceptance-gate.md](../_shared/dev-acceptance-gate.md)）：门绿=回归维度权威通过；门红=一票否决（修到绿或 revert）；门缺失=不许纯 ancestor 兜底、必须跑 testlist 真验
- [ ] 验证环境与 5.0 路由一致：开发道在 `DEV_NAMESPACE` 验、入口 `DEV_SLB`；若用了 staging，acceptance.md 里附「人工授权」记录（无授权记录的 staging 结果一律无效）
- [ ] testlist.md 的全部用例 100% 通过（写入 acceptance.md）
- [ ] **若改动命中 `web/ui/`**：按 [`_shared/frontend-browser-testing.md`](../_shared/frontend-browser-testing.md) 跑了真实浏览器测试，八维 + 证据五件套 + 控制台无 error；headless 与 browser 两类结果都写进 acceptance.md
- [ ] **测试资产已沉淀**：通过用例落 canonical 位置（`tests/acceptance/...`）+ `AC-STATUS.md` 追溯矩阵已更新且无 `none`、前端 AC 已到 `pass`（见 [`_shared/test-traceability-and-assets.md`](../_shared/test-traceability-and-assets.md) §5）
- [ ] 任何 P1 已修复并重测通过（无遗留 P1）

---

### 步骤 ⑥ Code review + Issue 评论关闭证据（对应 6️⃣）

#### 6.1 Code review（强制 fresh-context subagent，禁止本上下文自审，见 §2.2-C）

> 解决「自己审自己 + 上下文污染」：review **必须**在独立上下文做，不在已经写了一路代码的主上下文里自评。

- **统一走独立 subagent**：用 `Agent` 工具发一个**顺序、阻塞、不并行**的 review subagent（`subagent_type: "code-reviewer"` 或 `agent-code-review`，`run_in_background:false`，不用 worktree）。**喂料必须消歧义，防 subagent 读错 worktree 报假 P1（历史 #1074 真发生过：它读了不相关的 worktree 给出错误 P1）**：
  - prompt 里**显式写明本 worktree 绝对路径 `$PROJ_WORKTREE`**，并明确「只审这个路径下的改动，不要去别的 worktree / 检出」；
  - **直接把 `git -C "$PROJ_WORKTREE" diff origin/main...HEAD` 的 diff 文本贴进 prompt**（而非只说一句"看 diff"让它自己找仓库）；
  - 附相关 spec 路径；要求按 12 步 review 心法回传**结构化结论**（P1/P2 列表 + `file:line` 证据 + 改进建议），并**在结论开头声明它实际读取的路径**以便核对。
- **收到 P1 先核证据再动手**：按 `file:line` 回查真实代码确认 P1 属实（排除 subagent 路径串台 / 幻觉），确属真问题才回步骤 ④ 修。
- **重型**（≥200 LOC 或跨模块）→ 该 subagent 内部触发 [wiki-code-review](../wiki-code-review/SKILL.md) 完整 12 步，拿「改进计划」段。
- **轻量**（≤200 LOC 单模块）→ subagent 走精简版 12 步，但**仍在独立上下文**，不回退到主上下文内联自审。
- 安全敏感改动（鉴权 / 文件上传 / SQL / 反序列化）→ 额外串一个 `security-review` skill（独立上下文）。
- **Mock Boundary Audit（独立条目，示例项目 spec-031 QF-42）**：本轮改动含新增/修改的测试文件时，review subagent 须逐项过 `docs/rules/testing-spec.md` §4 checklist（新 mock 仅许外部三方 OBS/CVAT/GPU；自家契约 fixture 须 openapi.json 生成或真实响应录制；方言敏感断言打 `requires_pg`）。

发现 P1 → 回步骤 ④ 修 → 重走 ④ ⑤ ⑥；P2 → 创建 Follow-up Issue（见 6.3）。

#### 6.2 扩散排查（CLAUDE.md §7）

若本轮修过可模式化的 bug：

```bash
git diff origin/main~5 origin/main -- <fixed-files>    # 抽问题指纹
rg -n '<problem-pattern>' web/ algo/                   # 两模块扩散 grep
rg -n '<problem-pattern>' docs/ web/specs/ algo/specs/ # 文档里有没有抄了同样的错
```

找到同类 → 本次一并修；评论里写明「扩散 grep 模式、覆盖目录、修复 N 处」。

#### 6.3 在 Issue 上贴关闭证据评论

**三件套缺一不可**：

```bash
gh issue comment "$ISSUE_NUM" \
  --repo "$GIT_REPO" \
  --body "$(cat <<EOF
## ✅ 完成证据（准备关闭）

**完成日期**：$(date '+%Y-%m-%d')
**线上镜像 SHA**：\`<image-tag>\`
**本地 main commit**：\`$(git rev-parse origin/main | cut -c1-7)\`

### 关闭三件套

| 项 | 结果 |
|---|---|
| 静态检查 | <web tsc+eslint ✅ / algo ruff F821 ✅ / 或「无代码改动 N/A」> |
| K8s rollout | \`kubectl rollout status deploy/<name> -n ${DEPLOY_NS}\` ✅ ready |
| Post-push CI | build-images ✅ + cd ✅（或确认未命中 paths） |

### 测试结果

| # | 测试用例 | 类别 | 结果 |
|---|---------|------|------|
| TC-01 | ... | Happy path | ✅ |
| TC-02 | ... | Edge case | ✅ |
| ... | ... | ... | ✅ |

**通过：N / N（100%）**
**验收报告**：\`${SPEC_PATH}/acceptance.md\`

### Code Review 结论

- 模式：<轻量内联 / wiki-code-review 完整>
- P1：0（已全部修复，见 commit <sha-list>）
- P2：<N 个，已创建 Follow-up Issue #<num> / 无>

### 扩散排查

- grep 模式：\`<pattern>\` / 不属可扩散模式
- 覆盖目录：web/ algo/ docs/
- 修复同类：N 处 / 无同类问题

---

满足关闭三件套 + 验收 100% + Review P1 清零，可关闭。
EOF
)"
```

#### 6.4 P2 跟进 Issue（如有）

```bash
gh issue create \
  --repo "$GIT_REPO" \
  --title "[<类型>][SPEC-XX][XX模块][XX功能]<P2 一句话>（Follow-up #${ISSUE_NUM}）" \
  --body "来自 #${ISSUE_NUM} review 发现的 P2 优化项：

1. ...
2. ...

不阻塞 #${ISSUE_NUM}，本轮先关闭。" \
  --label "enhancement"
```

#### 6.5 步骤 ⑥ 通过判据

- [ ] Code review 已做（轻量 or wiki-code-review），P1 清零
- [ ] 扩散排查已做（grep 模式 + 覆盖目录 + 结论），结论写进评论
- [ ] 「关闭证据评论」已贴到 Issue（含三件套表 + 测试结果表 + Review 结论 + 扩散结论）
- [ ] P2 已建跟进 Issue（如有）

---

### 步骤 ⑦ 关闭 Issue（对应 7️⃣）

#### 7.1 关闭前最后一次自检（缺任一项禁止 close）

- [ ] 静态检查全绿
- [ ] `kubectl rollout status` ready
- [ ] Post-push CI 全绿（或确认未命中 paths）
- [ ] testlist.md 100% 通过
- [ ] P1 清零
- [ ] 关闭证据评论已贴

#### 7.2 关闭

```bash
_gh_retry 3 2 -- gh issue close "$ISSUE_NUM" \
  --repo "$GIT_REPO" \
  --comment "✅ 验收通过（$(date '+%Y-%m-%d')），关闭。关闭证据见上一条评论。"
```

#### 7.3 同步清理（**强校验 + 强清理 + 后验证**，严格限定本 session 产物）

**触发条件（强）**：本 session 代码已落 `origin/main` 且 `gh issue close` 已成功——此时必须**主动**清理，不留尾巴。

**清理范围 = 只本 session 产物**：

| 类别 | 命中规则 | 处置 |
|---|---|---|
| Worktree | 路径含 `issue-gen-${PROJ_SESSION_ID}` | `git worktree remove`（失败 → `--force`） |
| 本地分支 | 名称 == `$PROJ_FEAT_BRANCH`（含 `feat/issue-${ISSUE_NUM}-`） | `git branch -D`（前提：SHA 已在 origin/main） |
| 远程分支 | 仅 PR 轨；远程 `$PROJ_FEAT_BRANCH` 仍存在 | `gh api ... DELETE`（GitHub 通常 PR merge 后自动删） |
| 临时分支 | `wip/issue-gen-${PROJ_SESSION_ID}`（步骤 ③.4 已重命名走，理论上不该存在） | 若残留 → `git branch -D` |

**绝对禁止触碰**：其他 session 的 `.claude/worktrees/*`（含 `agent-*` / `funny-*` / `inspiring-*` / `issue-gen-<别的-id>`）、其他 session 的 `feat/issue-<别的-num>-*` 分支、历史遗留分支/worktree。

##### 7.3.A 强校验（不通过 → 拒绝清理，不静默兜底）

```bash
set -e
[ -n "$PROJ_PUSHED_SHA" ]   || { echo "❌ \$PROJ_PUSHED_SHA 未设，回步骤 ④.5 重锁定"; exit 1; }
[ -n "$PROJ_SESSION_ID" ]   || { echo "❌ \$PROJ_SESSION_ID 未设，无法精确锁定本 session 产物"; exit 1; }
[ -n "$PROJ_FEAT_BRANCH" ]  || { echo "❌ \$PROJ_FEAT_BRANCH 未设"; exit 1; }
[ -n "$PROJ_ROOT" ]         || PROJ_ROOT=$(git rev-parse --show-toplevel)   # 项目主检出根（=PROJECT_ROOT），不写死绝对路径

git -C "$PROJ_ROOT" fetch origin main --quiet
ORIGIN_MAIN_SHA=$(git -C "$PROJ_ROOT" rev-parse origin/main)
echo "📍 origin/main = $ORIGIN_MAIN_SHA"
echo "📍 本 session push = $PROJ_PUSHED_SHA"

# 核心校验：本 session 的 SHA 必须在 origin/main 历史里
# 对 direct 轨：$PROJ_PUSHED_SHA == origin/main HEAD 或其祖先（trivial 通过）
# 对 pr 轨：$PROJ_PUSHED_SHA == squash merge commit on main（通过；feature 分支的原 commits 不在 main 也无所谓，SHA 是真相）
if ! git -C "$PROJ_ROOT" merge-base --is-ancestor "$PROJ_PUSHED_SHA" "$ORIGIN_MAIN_SHA"; then
  echo "❌ \$PROJ_PUSHED_SHA 不在 origin/main 历史 → 代码未真正合进去，**拒绝清理**"
  echo "   排查："
  echo "   - 步骤 ④.4 push 是否被覆盖？回 ④ rebase + 重 push，刷新 \$PROJ_PUSHED_SHA 再来"
  echo "   - PR 是否真被 merge？\`gh pr view --json state\`"
  exit 1
fi
echo "✅ 本 session 代码已落 origin/main，强校验通过 → 可清理"
```

##### 7.3.B 离开 worktree（必须，否则 remove 失败）

```bash
ORIG_PWD=$(pwd)
cd "$PROJ_ROOT"                                              # 必须在主检出执行 worktree remove
echo "📍 cd → $PROJ_ROOT（离开 worktree 才能安全 remove）"
```

##### 7.3.C 删 worktree（常规 → --force 二段 fallback）

```bash
# 用 --porcelain 严格按 session-id 锁定（grep -F 精确匹配，禁通配）
WT_PATH=$(git worktree list --porcelain \
  | awk -v sid="$PROJ_SESSION_ID" '/^worktree / { if ($2 ~ ("issue-gen-" sid "$")) print $2 }' \
  | head -1)

if [ -z "$WT_PATH" ]; then
  echo "ℹ️  没找到匹配的 worktree（可能已被删过 / SESSION_ID 不对），跳过"
else
  echo "🧹 移除 worktree: $WT_PATH"
  if git worktree remove "$WT_PATH" 2>/dev/null; then
    echo "  ✅ 常规 remove 成功"
  else
    echo "  ⚠️  常规 remove 失败（可能有未跟踪文件 / lock），尝试 --force"
    git worktree remove --force "$WT_PATH" && echo "  ✅ --force 成功" \
      || { echo "  ❌ --force 仍失败，留给用户手动 \`rm -rf $WT_PATH && git worktree prune\`"; }
  fi
fi
git worktree prune --quiet                                  # 清理失效引用（含其他 session 已物理删但未 prune 的）
```

##### 7.3.D 删本地分支（用 -D 安全删，前提是 7.3.A 已证 SHA 在 main）

> **为什么用 `-D` 不用 `-d`**：PR 轨走 squash merge，原 feature 分支的 commits 在 main 里被合并成一个新 commit，`git branch -d` / `--merged` 会误判"未合并"导致永远删不掉。**只要 7.3.A 强校验过 `$PROJ_PUSHED_SHA` 在 main**，`-D` 就是安全的（代码不会丢）。

```bash
if git show-ref --quiet "refs/heads/$PROJ_FEAT_BRANCH"; then
  echo "🧹 删本地分支: $PROJ_FEAT_BRANCH"
  git branch -D "$PROJ_FEAT_BRANCH" && echo "  ✅ 已删"
else
  echo "ℹ️  本地分支 $PROJ_FEAT_BRANCH 不存在（可能已被删过），跳过"
fi

# 残留临时分支（理论上步骤 ③.4 已 rename 走，兜底清一下）
if git show-ref --quiet "refs/heads/wip/issue-gen-${PROJ_SESSION_ID}"; then
  echo "🧹 删残留临时分支: wip/issue-gen-${PROJ_SESSION_ID}"
  git branch -D "wip/issue-gen-${PROJ_SESSION_ID}"
fi
```

##### 7.3.E 删远程 feature 分支（仅 PR 轨需要；GitHub 通常 PR merge 后自动删）

```bash
export GH_TOKEN="$(gh auth token --user zhaod39_example-corp)" 2>/dev/null || true
if git ls-remote --heads origin "$PROJ_FEAT_BRANCH" 2>/dev/null | grep -q "$PROJ_FEAT_BRANCH"; then
  echo "🧹 删远程分支: origin/$PROJ_FEAT_BRANCH"
  gh api "/repos/${GIT_REPO}/git/refs/heads/${PROJ_FEAT_BRANCH}" -X DELETE 2>&1 \
    && echo "  ✅ 已删" \
    || echo "  ⚠️  删除失败（PR 可能已自动删，或权限问题），可在 GitHub UI 手删"
else
  echo "ℹ️  远程 $PROJ_FEAT_BRANCH 不存在（GitHub PR merge 后自动删了），跳过"
fi
```

##### 7.3.F 清理后验证（必跑，确认三个目标真的清掉了）

```bash
echo ""
echo "━━━━━━━━━━ 清理后状态验证 ━━━━━━━━━━"
PASS=true

# 1. Worktree
if git worktree list | grep -qF "issue-gen-${PROJ_SESSION_ID}"; then
  echo "❌ worktree 残留: $(git worktree list | grep "issue-gen-${PROJ_SESSION_ID}")"
  PASS=false
else
  echo "✅ worktree 已清"
fi

# 2. 本地分支
if git show-ref --quiet "refs/heads/$PROJ_FEAT_BRANCH"; then
  echo "❌ 本地分支 $PROJ_FEAT_BRANCH 残留"
  PASS=false
else
  echo "✅ 本地分支已清"
fi

# 3. 远程分支
if git ls-remote --heads origin "$PROJ_FEAT_BRANCH" 2>/dev/null | grep -q "$PROJ_FEAT_BRANCH"; then
  echo "❌ 远程 origin/$PROJ_FEAT_BRANCH 残留"
  PASS=false
else
  echo "✅ 远程分支已清（或本就被 GitHub 自动删）"
fi

# 4. 反向校验：未误伤其他 session
OTHER_WT_COUNT=$(git worktree list | grep -c "issue-gen-" || true)
echo "ℹ️  仍存在的其他 session worktree 数量: $OTHER_WT_COUNT（应保持 ≥0，本次只清了 1 个）"

if $PASS; then
  echo "━━━━━━━━━━ ✅ 清理完成 ━━━━━━━━━━"
else
  echo "━━━━━━━━━━ ⚠️  清理不完全，看上面 ❌ 项 ━━━━━━━━━━"
  exit 1
fi
```

##### 7.3.G 事后补救：如果本步骤被跳过 / 上次没清干净

用户可以**任何时候**在项目主检出根目录复跑下面这段一键脚本（self-contained，从 worktree 路径反推 session-id）：

```bash
# 用法：在项目主检出根目录下跑（cd 到项目根即可）
# 它会列出所有看似"已收口"的 issue-gen worktree，并对每个询问是否清
cd "$(git rev-parse --show-toplevel)"
for WT in $(git worktree list --porcelain | awk '/^worktree .*issue-gen-/ { print $2 }'); do
  echo ""
  echo "Worktree: $WT"
  BR=$(git -C "$WT" branch --show-current 2>/dev/null)
  echo "  Branch:  $BR"
  if [ -n "$BR" ] && git merge-base --is-ancestor "$BR" origin/main 2>/dev/null; then
    echo "  状态:    ✅ 已合 main（可安全清）"
    read -r -p "  清理? [y/N] " ans
    if [ "$ans" = "y" ]; then
      git worktree remove "$WT" || git worktree remove --force "$WT"
      git branch -D "$BR" 2>/dev/null || true
    fi
  else
    echo "  状态:    ⚠️  分支未在 origin/main → 可能本 session 还在跑 / 代码没合，跳过"
  fi
done
git worktree prune
```

> 注：这段是**手动补救**，主流程必须走 7.3.A → 7.3.F 全套；7.3.G 仅在异常退出未清时给用户兜底。

---

#### 7.4 步骤 ⑦ 通过判据（清理硬约束）

- [ ] **关单前置硬门（强制，凌驾「优雅降级」）**：`gh issue close` / `close_issue_claim` **之前**，dev 上 **quality-api L1+L2 全量真跑过且绿**——门绿 + **L2 非 `l2.noop`/非全 skipped（7806 真打有计数）** + L1 0 FAIL；门 `skipped`/`noop`/`absent` **不算通过**，须按 [`_shared/dev-acceptance-gate.md`](../_shared/dev-acceptance-gate.md) §4 **首选 `gh workflow run auto-test-L2-full.yml -f environment=dev` 触发集群内 in-cluster gate job 真打补跑**（注入 `QUALITY_L2_FULL=1` + 内置 noop 硬护栏；读 `gate` job conclusion，`success`=真打绿、`failure`=门红/noop；GH Action 不可用时回落手动 in-cluster Job）通过后再关单；门红/跑不通 → **不关单**（修/revert 或落 `merged_unverified` 留晨间复验）。证据（gate run 链接 + L1+L2 三态计数 + 「L2 非 noop」声明）已挂关单评论 / `acceptance.md`
- [ ] Issue 已 closed（`gh issue view "$ISSUE_NUM" --json state` 返回 `CLOSED`）
- [ ] **7.3.A** `$PROJ_PUSHED_SHA` 通过 `merge-base --is-ancestor` 强校验在 `origin/main` 历史里
- [ ] **7.3.C** 本 session worktree（`issue-gen-${PROJ_SESSION_ID}` 精确匹配）已通过常规 / `--force` 删掉
- [ ] **7.3.D** 本地 `$PROJ_FEAT_BRANCH` 已用 `-D` 删掉（不是 `-d`，避免 squash-merge 误判）
- [ ] **7.3.E** 远程 `origin/$PROJ_FEAT_BRANCH` 已删 或 确认 GitHub 自动删过
- [ ] **7.3.F** 三项验证（worktree / 本地分支 / 远程分支）全部 ✅
- [ ] 反向校验：`git worktree list` 显示其他 session 的 worktree **数量未减少**（未误伤）
- [ ] 关闭原因评论已挂

---

### 步骤 ⑧ 收尾《工作过程总结》+ 可选短版汇报（对应 8️⃣）

**本步骤的主交付 = 产出一份 ~1000 字《工作过程总结》**，格式与七段骨架见统一 SoT：[`_shared/closing-summary.md`](../_shared/closing-summary.md)（**必读必执行**，四技能共用一份）。

- 把本轮「澄清 → spec → ②.5 方案门 → Issue → 开发 → K8s 实测（含前端浏览器）→ review → 关 Issue」全链路按共享文件七段（结论 / 总览 / **过程分步** / 测试与质量门 / 关键决策 / 遗留与风险 / **下一步建议**）落成 ~1000 字、表格优先的总结。
- dev 侧填充侧重见共享文件 §2 表对应行。
- **可选**：之后再附一段 ≤300 字 [wiki-session-report](../wiki-session-report/SKILL.md) 三段式（背景/进展/待办）作"群发短版"，与 ~1000 字过程总结互补（一个供存档/周报，一个供即时转发）。

末尾追加一行（与 wiki-code-commit 收口对齐）：

> 本轮已收口，上下文可压缩 —— 直接输入 `/compact` 即可压缩对话、释放上下文。

#### 步骤 ⑧.0 撤销遗留的兜底唤醒 / 后台轮询（强制）

> 实战教训（#1122）：步骤 ⑤ 等 CD 时若用 `ScheduleWakeup` 设过兜底唤醒，闭环跑完后它**仍会触发**，导致「工作早已完成却又空跑一轮」核验，白烧 token。

- 闭环结束前，**撤销本轮设过、尚未触发的兜底唤醒**（不再传 `prompt` 即结束 `/loop`；或显式说明本轮无后续唤醒）。
- 原则：**只对 harness 追踪不到的外部状态（CD / CI / 远程队列）才设长兜底（≥1200s）**；对 harness 能完成通知的后台任务（`run_in_background` 的 Bash / Agent）**不设短轮询**，等其完成通知即可。

#### 步骤 ⑧ 通过判据

- [ ] 已产出 ~1000 字（800–1200）《工作过程总结》，七段齐全、表格占主体（按 [`_shared/closing-summary.md`](../_shared/closing-summary.md)）
- [ ] 「过程分步」表让外人能看懂"怎么一步步做到的"；「下一步建议」每条有可执行触发（`/skill` 或人工动作）
- [ ] 含 Issue 链接（人话文本，不是裸 URL）；非技术同事能读懂结论与下一步
- [ ] 末尾含 `/compact` 提示行

#### 步骤 ⑧.1 释放 session 锁（强制收尾，与 §0.6 配套）

汇报输出后最后一步：
```bash
rm -f .claude/locks/issuegen-${ISSUE_NUM}-${SPEC_ID}-${SESSION_SHA8}.lock
echo "🔓 已释放 session 锁: issuegen-${ISSUE_NUM}-${SPEC_ID}-${SESSION_SHA8}"
```
不要漏。漏了会产生僵尸锁，4 小时后才会被下次启动清理。

---

## 5. 红线 / 反例（违反任一 = 本次闭环不合格）

### 5.1 流程红线

- ❌ 步骤 ① 跳过 ASK 直接脑补争议项，把"模糊"包装成"已确认"
- ❌ 步骤 ② 只在 Issue body 写需求，不落 `web/specs/` 或 `algo/specs/`（spec 是 SoT）
- ❌ 步骤 ③ 用 `personal-account` 账号建 Issue（会 404 / 权限错位），必须 `export GH_TOKEN="$(gh auth token --user zhaod39_example-corp)"`
- ❌ 步骤 ④ 把开发任务拆给后台 subagent 并行跑（用户已明确要求单流水线）
- ❌ 步骤 ④ 跳过静态检查 / 跳过 Post-push CI 验证就宣告"做完了"
- ❌ 步骤 ④ `build-images` 绿即宣告部署完成、不 watch 链式 CD Deploy run 到终态（漏掉「CD 判红镜像没滚上去且无人管」，用户实测故障根因）；或 `deploy-k8s` 因 Pod 起不来（A 类代码问题）却当环境抖动无脑 `gh run rerun`；或 `post-deploy-acceptance-gate` 门红仍放行（须按 [`_shared/dev-acceptance-gate.md`](../_shared/dev-acceptance-gate.md) §5 归因处置，见 4.6.1）
- ❌ 步骤 ⑤ 在 debug-host 跑 `docker compose` 或本机起 docker 当测试环境
- ❌ 步骤 ⑤ 拿"旧镜像"跑测试谎报通过（必须先确认 deploy image tag 含 `$PROJ_PUSHED_SHA`）
- ❌ 步骤 ⑥ 跳过 Code review 直接关 Issue；或把 P1 当 P2 偷偷创建 Follow-up
- ❌ 步骤 ⑦ 关闭三件套缺任一项就 `gh issue close`
- ❌ 步骤 ⑧ 手写一段散文当汇报，不调用 `wiki-session-report`
- ❌ 中途无谓暂停问"是否继续步骤 X"（破坏性动作除外：force push / 删未合并分支 / revert，这些必须先问用户）
- ❌（TOP-1）步骤 ⑥ 在写满代码的主上下文里"自己审自己"，不起 fresh-context subagent；或步骤 ① 深度调研把大量源码灌进主上下文（§2.2-C）
- ❌（TOP-2）步骤 ④ test-after：先写实现再补测试，或测试从没跑出过红，或为迁就实现改断言 / mock 对齐被测 bug（§2.2-A）
- ❌（TOP-3）跳过步骤 ②.5 技术方案审批门，spec 写完直接建 Issue + 写码（"方案无悬念"已在 spec 注明的除外）（§2.2-B）
- ❌（前端真测）改动命中 `web/ui/` 却只跑 headless（curl/pytest）就宣告通过，不用真实浏览器验证交互/控制台/网络/焦点/跨组件同步（§5.3.B）
- ❌（前端真测）浏览器测试命中旧 bundle 不硬刷新就判通过；或没确认页面服务的是本 session SHA（[[nginx_sub_filter_dynamic_url_misses]] 项 B）
- ❌（双轨判定）以 `git-track-classify.sh` 输出强推 direct、无视 pre-push hook 拦截反复重试 direct（§4.4：hook 才是权威）
- ❌（本仓 PR）用 `gh pr merge --auto`（本仓 PR 0 check，--auto 永久挂起）；或对 PR 分支 `git push --force` 更新（仓库禁 force-push，§4.4）
- ❌（deploy-gap）步骤 ⑤ 对被并发取消的本 SHA build 无限 dispatch + 死等，不走 §5.2 ancestor / 行为确证兜底；或兜底判过却谎报"本 SHA build 绿"
- ❌（开工同步）步骤 ① 不先 `fetch + rebase origin/main` 就在落后的 harness worktree 上调研、对着旧代码建错心智模型（§1.0）
- ❌（review 喂料）步骤 ⑥ review subagent 不传本 worktree 绝对路径 / 不贴 diff 文本，导致它读错 worktree 报假 P1（§6.1）
- ❌（兜底唤醒）闭环完成后不撤销遗留的 `ScheduleWakeup` 兜底，导致空跑一轮（§8.0）

### 5.2 多 Session 隔离红线（违反一条就会毁别人的代码）

- ❌ 跳过步骤 ⓪，在项目主检出（`$PROJECT_ROOT`）上 `git checkout main` / `git merge` / `git commit`（与其他 session 共享 working tree，必然互踩）
- ❌ 步骤 ④ `git checkout main && git pull && git merge feat/...` —— 必须用 `git push HEAD:main`，不切到 main 分支
- ❌ 步骤 ④ push 被 reject 后用 `git push --force` / `--force-with-lease` 把别人后推的 commit 顶掉
- ❌ 步骤 ④ push 前不 `git fetch origin && git rebase origin/main`，带着旧 base push
- ❌ 步骤 ⑤ 用 `git rev-parse origin/main` 当锚点等 rollout —— 必须用 `$PROJ_PUSHED_SHA`（远程 main 可能已被其他 session 推进）
- ❌ 步骤 ⑦ `git worktree list | grep issue-gen | xargs ...`（通配匹配）—— 必须 `grep -F "issue-gen-${PROJ_SESSION_ID}"` 精确锁定
- ❌ 步骤 ⑦ `git branch -D` 强删未 merge 的本 session 分支（丢工作），或动其他 session 的分支
- ❌ 步骤 ⑦ 顺手大扫除 `.claude/worktrees/` 里历史遗留的所有 `agent-*` / `issue-gen-*`（不在本次范围）
- ❌ 步骤 ⑦ 用 `git branch -d` / `git branch --merged` 检查 PR 轨的 feature 分支（squash merge 会误判"未合并"导致永远删不掉，**必须**走 7.3.A 强校验 + `-D` 安全删）
- ❌ 步骤 ⑦ 把"代码已合 main + Issue 已关"当成结束、跳过 7.3.C/D/E 清理 → 每跑一次留一条死分支 + 一个孤儿 worktree，几次后 `.claude/worktrees/` 就爆
- ❌ 步骤 ⑦ 跳过 7.3.F 后验证就宣告完成（"我发起了删除"≠"真的删掉了"）
- ❌ 启动时跳过 §0.6 锁探测（多 session 同 issue + spec 并发的根因）
- ❌ 步骤 ③ 分支命名沿用旧 `feat/issue-<num>-<slug>` 不加 session-sha8（多 session 同 issue 必撞）
- ❌ 步骤 ④ 建 PR 前不查同文件 in-flight PR（产生同文件双 PR）
- ❌ 步骤 ⑧ 完成后忘记 `rm` 锁文件（僵尸锁污染）
- ✅ 八步顺序执行、每步有可验证 done 判据、每个 session 在独占 worktree 里活完一辈子、清理只动自己产物、清完必跑后验证

---

## 6. 自检清单（宣告闭环前逐条过，任一不过 → 回到对应步骤）

- [ ] **步骤 ⓪**：已起 `.claude/worktrees/issue-gen-${PROJ_SESSION_ID}` worktree + `wip/issue-gen-${PROJ_SESSION_ID}` 临时分支；`PROJ_SESSION_ID` / `PROJ_WORKTREE` / `PROJ_TMP_BRANCH` 三个环境变量已 export
- [ ] **步骤 ①**：目标/范围/归属 spec/验收判据 四项已落字；争议已 ASK 收口
- [ ] **步骤 ②**：spec 路径已确定；spec.md 含四段；tasks/testlist 同步；markdown 链接巡检过；改动落在本 session worktree 内
- [ ] **步骤 ③**：Issue 已建（`$ISSUE_NUM` 已记）；body 含五段；spec 已反链 Issue；临时分支已重命名为 `$PROJ_FEAT_BRANCH`
- [ ] **步骤 ④**：始终在 `$PROJ_WORKTREE` + `$PROJ_FEAT_BRANCH` 上；tasks.md 每条都 commit；静态检查全绿；push 前 `git fetch + rebase origin/main`；用 `git push HEAD:main`（rebase + retry，未 force-push）；**判轨以 pre-push hook 为准（非 classify 脚本）**；**PR 轨未用 `--auto`（本仓 0 check）、未 force-push 更新分支**；`$PROJ_PUSHED_SHA` 已锁定；CI 按该 SHA 全绿或确认未命中 paths；**build 绿后已按 §5 watch 链式 CD Deploy run 到终态（`success`/`skipped`/ancestor 兜底放行；`deploy-k8s` 或 `post-deploy-acceptance-gate` failure 已归因处置），未在 build 绿就宣告部署完成（见 4.6.1）**
- [ ] **步骤 ⑤**：本 session 代码已上线——**B（本 SHA 滚到）/ C-2（线上行为确证）/ C-1（ancestor 校验）任一成立**且如实标注（未死等被并发取消的本 SHA build、未谎报"本 SHA build 绿"）；testlist 100% 通过；P1 清零；**前端改动（命中 `web/ui/`）已用真实浏览器（Playwright/webapp-testing）测过，控制台无 error + 关键页截图留证，headless 与 browser 结果都进 acceptance.md**
- [ ] **步骤 ⑥**：Review 已做（subagent 喂了本 worktree 绝对路径 + diff 文本，P1 已回查证据核实）+ P1 清零；扩散排查结论已写；关闭证据评论已贴
- [ ] **步骤 ⑦**：关闭三件套齐；Issue 状态 CLOSED；7.3.A `$PROJ_PUSHED_SHA` 通过 `merge-base --is-ancestor` 校验在 `origin/main`；7.3.C/D/E 已发起 worktree + 本地分支(`-D`) + 远程分支三项删除；**7.3.F 后验证三项全 ✅**（worktree / 本地分支 / 远程分支真的不在了）；其他 session 的 worktree 数量未减少（未误伤）
- [ ] **步骤 ⑧**：已调用 `wiki-session-report`；三段式 + 表格 + `/compact` 提示行齐；**已撤销本轮遗留的 `ScheduleWakeup` 兜底唤醒 / 短轮询（无完成后空跑一轮风险，§8.0）**
- [ ] **全程**：未中途暂停问"是否继续"；未把任意两步并行；未违反 §5.1 / §5.2 红线任一条；从未在主检出 `$PROJ_ROOT` 上动过 `checkout` / `merge` / `commit` / `push`（只 `git fetch`）

### 6.1 跨 Session 防冲突（与 §0.6 §0.7 配套，必过）

- [ ] §0.6 session 锁已写入 `.claude/locks/issuegen-${ISSUE_NUM}-${SPEC_ID}-${SESSION_SHA8}.lock`（拿到 issue 编号 / spec id 后已重命名 pending→真实值）
- [ ] 启动时已探测同 target 活锁，命中已走 `AskUserQuestion` 三选一
- [ ] 步骤 ③ 分支名已用 §0.7 的 `feat/issue-${ISSUE_NUM}/${SESSION_SHA8}/<slug>` 三段格式
- [ ] 步骤 ④ push 前已 `git fetch origin main && git rebase origin/main`
- [ ] 步骤 ④ PR 轨建 PR 前已 `gh pr list --search` 查同文件 in-flight PR
- [ ] 步骤 ⑧.1 已 `rm` 自己的锁文件（防僵尸锁）

### 6.2 TOP-3 致命问题优化项（本次新增，必过）

- [ ] **TOP-1 上下文隔离**：步骤 ① 深度调研（若需要）、步骤 ⑥ code review 都走了 **fresh-context、顺序阻塞** subagent，未在主上下文自审；进步骤 ⑤ 前已把步骤 ④ 实现压成 ≤10 行摘要
- [ ] **TOP-2 真 TDD**：步骤 ④ 每条子任务都经历「🔴红 → 🟢绿 → 🔴反验证」三相；testlist 每条都过过红；没有为迁就实现改断言 / mock 对齐 bug
- [ ] **TOP-3 方案审批门**：步骤 ②.5 已把技术方案卡摆给用户并拍板（或方案无悬念已在 spec 注明跳过），未跳过直接写码

---

## 7. 关联技能

| 技能 | 何时联动 |
|---|---|
| [wiki-prompt-gen](../wiki-prompt-gen/SKILL.md) | 用户只要 prompt 不要执行 → 转交 |
| [wiki-issue-acceptance](../wiki-issue-acceptance/SKILL.md) | 已有 Issue + 实现，只验收 → 转交 |
| [wiki-code-commit](../wiki-code-commit/SKILL.md) | 步骤 ④ 合 main + Post-push CI 验证骨架直接引用 |
| [wiki-code-review](../wiki-code-review/SKILL.md) | 步骤 ⑥ 重型 review 触发 |
| [wiki-issue-review](../wiki-issue-review/SKILL.md) | 步骤 ⑥ 怀疑代码 ↔ spec 不一致时联动 |
| [wiki-session-report](../wiki-session-report/SKILL.md) | 步骤 ⑧ 强制调用 |
| [wiki-dev-public](../wiki-dev-public/SKILL.md) | 步骤 ⑤ VPN 不通需切公网兜底时联动 |

---

## 8. 参考资料

- 项目级硬约束 → 项目根 `CLAUDE.md`（调研 / Pre-merge / 双轨 Git / CI / 扩散 / K8s 等小节）
- 双轨判定 → `scripts/git-track-classify.sh`
- CI 质量门禁 SoT → `.github/workflows/build-images.yml`
- 外网兜底访问 → memory `[[public_network_dev_fallback]]`
- 内网研发态切换 → memory `[[intranet_dev_checklist]]`
- Redis broker 丢消息 → memory `[[redis_broker_message_loss]]`
- Schema 漂移机制 → memory `[[prod_schema_drift_mechanism]]`
