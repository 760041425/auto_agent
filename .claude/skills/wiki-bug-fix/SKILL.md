---
name: wiki-bug-fix
description: Bug 全生命周期单流水线技能（通用研发流，跨 git 项目复用；典型项目：示例项目）。从「issue 中的 bug / 用户报错」一口气走到「Issue 关闭 + 进展汇报」八步闭环：① 拉 Issue + 复现 + RCA（含 5 Why + memory 反查）→ ② Spec 增量 + bugfix-<num>.md 落 RCA → ③ Issue 同步（已有→评论，未建→补建，强制 [BUG] 前缀 + bug label）→ ④ 单流水线 TDD 开发（先写回归测试让它红 → 修代码让它绿 → 同 PR 扩散修复 → 合 main）→ ⑤ 项目测试环境 K8s（namespace 见项目《环境档案》DEPLOY_NAMESPACE）before/after 验证（含触发原 bug 精确路径）→ ⑥ Review + 扩散覆盖矩阵 → ⑦ 关 Issue（五件套：三件套 + RCA 摘要 + 扩散矩阵；浏览器真验截图上传 evidence 分支贴评论作关闭证据）→ ⑧ 串联 /wiki-session-report 输出非技术汇报。当用户说「/wiki-bug-fix」「wiki-bug-fix」「修这个 bug」「fix bug #N」「这个报错怎么修」「定位+修复」「线上挂了快修」「回归测试都补一下」「bug 闭环」时触发。严格遵循项目根 CLAUDE.md（调研 / Pre-merge / 双轨 Git / CI / 扩散 / K8s 等硬约束）；测试环境唯一项目指定的 K8s namespace，禁止用本机 docker / docker-compose 当测试环境。P0/P1/P2 一律走完整八步，不开简化通道。
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
- **devlock**：取锁三段式 `lock_try_acquire` → 失败 `lock_status` 查持锁者 → 超 10 分钟无心跳 `lock_reap` 后重试；禁止无界裸 `lock_acquire`。
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

| 时机 | 调用 | 说明 |
|---|---|---|
| 拿到 `ISSUE_NUM` 后第一时间 | `claim_issue "$ISSUE_NUM" "<起步阶段名>"` | 退出码 2 = 已被他人占用 → **立刻 abort 报告用户**（禁止抢占），退出码 0 才继续 |
| 每个步骤切换 | `advance_phase "$ISSUE_NUM" "<阶段中文名>" [$PR_NUM]` | 用本技能步骤号 + 中文短描述（如 "④TDD 红→绿"），让查询时一眼看懂 |
| 长阶段（>30 min）内 | `heartbeat_issue "$ISSUE_NUM"` | 放循环 / 等待轮询里兜底，防 60 min 心跳超时被 reap |
| 真正 `gh issue close` 之后 | `close_issue_claim "$ISSUE_NUM" "$PR_NUM"` | 移 `wip:claude-code` label + comment 追加 `— Closed via PR #X` |
| 中途撤回（非异常） | `release_issue "$ISSUE_NUM" "<reason 中文>"` | 例如用户中断 / 主动让位 |
| 卡住等用户决策 | `park_issue "$ISSUE_NUM" "<reason 中文>"` | swap label `wip` → `parked`；夜间技能必走 |

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

**当本技能识别出任务可拆为 ≥5 个相互独立的子单元**（典型：扩散排查的同模式位置 / 多模块回归测试 / 多 spec 文件巡检等），**优先**走以下骨架，**不要**顺序硬扛：

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

> ⚠️ **本技能用户已明确要求「单流水线」**：8 个步骤共享同一份 Issue / spec / git 状态 / K8s 命名空间 / Issue 评论流，**强制全程顺序执行**，不许把任意两步拆给后台 subagent 并行跑。并行规则仅在「步骤 ④ 内部扩散修复 ≥5 个互不依赖的代码位置时」或「步骤 ⑥ 扩散排查矩阵 ≥5 个模块时」才在该单步内局部启用，主流水线绝对顺序。

参考骨架：`/batch` slash command。

---

## 0.5 多 Session 隔离硬约束（每个 Bug 独占 worktree）

**前提**：用户经常同时开多个 Claude 会话各跑一个 `/wiki-bug-fix`。若大家都在项目主检出（`$PROJECT_ROOT`）上 `git checkout main && git merge` —— 互相覆盖、丢 commit、push reject、清理误删 —— **必然**发生。

**隔离模型**（强制，与 wiki-issue-dev §0.5 一致，只把分支名前缀从 `feat/` 改为 `fix/`）：

| 维度 | 规则 |
|---|---|
| **工作目录** | 每个 session 在 `.claude/worktrees/bug-fix-<session-id>/` 独占 worktree，**禁止**在主检出 `<é¡¹ç®æ ¹>/` 根目录上直接动 git |
| **分支命名** | 临时名 `wip/bug-fix-<session-id>`（步骤 ⓪ 起）→ 拿到 Issue 编号后重命名为 `fix/issue-<num>-<slug>`（步骤 ③ 末尾） |
| **拉新代码** | 步骤 ④ 合 main 前**必须** `git fetch origin && git rebase origin/main`，拿到其他 session 已推的 commit 再 push |
| **push 冲突** | 被 reject（远程比本地新）→ rebase + retry，**最多 3 次**；3 次失败 → 停下报告用户（可能存在 force-push / 历史改写，必须人工介入） |
| **rollout 竞态** | 步骤 ⑤ 等"本地 main SHA"滚到 K8s，若 A 推完后 B 又推了一个 commit → A 的镜像 SHA 永远不会被滚到（被 B 的覆盖）→ **必须**在 push 后立刻锁定 `$PROJ_PUSHED_SHA = $(git rev-parse origin/main)`，等到该 SHA 出现在 deploy image tag 才能开测；若被 B 抢先覆盖，A 必须**重新走步骤 ④ rebase + push**，再等新 SHA |
| **清理范围** | 步骤 ⑦ 只删 `.claude/worktrees/bug-fix-<本 session-id>` 和**本 session 创建的** `fix/issue-<本 ISSUE_NUM>-*` / `wip/bug-fix-<本 session-id>` 分支；其他 `.claude/worktrees/bug-fix-*` / `.claude/worktrees/issue-gen-*` 和 `fix/issue-*-*` / `feat/issue-*-*` 一律**不碰** |
| **主检出禁动** | 项目主检出（`$PROJECT_ROOT`）**永远只用 `git fetch`**，从不在它上面 `checkout` / `merge` / `commit` / `push`，避免成为多 session 的共享互斥点 |

**生成 session-id**（步骤 ⓪ 第一件事）：

```bash
SESSION_ID="$(date +%Y%m%d-%H%M%S)-$$"     # 时间 + PID，全机器唯一
echo "$SESSION_ID"
```

之后整个流程把它作为本 session 的「身份证」，写到所有路径/分支名/Issue body 里，清理时严格 grep 比对。

---

## 0.6 Session 锁声明与冲突探测（启动第一步，强制；与 §0.5 配套）

**目的**：§0.5 通过 worktree + session-id 做"互不踩"，本节通过 **文件锁 + 同 bug 探测** 防止"两个 session 都在修同一个 bug Issue"。

执行顺序（步骤 ⓪ 起 worktree 之前 / 同步 / 之后立刻做）：

1. **生成 session-sha8**（贯穿本次流程全程，作为锁文件命名空间，与 §0.5 的 `SESSION_ID` 区分用途）：
   ```bash
   SESSION_SHA8=$(echo "${SESSION_ID}-$(git config user.email)" | shasum | cut -c1-8)
   echo "本次 session-sha8 = $SESSION_SHA8"
   ```
2. **生成 target-id**（= `bugfix-<issue-num>`；步骤 ① 拿到 Issue 编号后就能填实，未拿到前先用 `bugfix-pending`）：
   ```bash
   TARGET_ID="bugfix-${ISSUE_NUM:-pending}"
   ```
3. **探测同 bug 活锁**：
   ```bash
   mkdir -p .claude/locks
   ls .claude/locks/bugfix-${ISSUE_NUM:-pending}-*.lock 2>/dev/null
   ```
4. **命中已有锁** → 立即 `AskUserQuestion` 三选一：
   - **合并/续作**：放弃本次启动，让用户切到那个 session（避免两个 session 都在改同一个 bug）
   - **取消**：本次终止
   - **强制并行**：用户明确知晓冲突风险后继续（写入锁备注，冲突责任在用户）
5. **无命中** → 写自己的锁（步骤 ① 拿到 `ISSUE_NUM` 后**重命名锁文件**把 `pending` 替换为真实编号）：
   ```bash
   cat > .claude/locks/bugfix-${ISSUE_NUM:-pending}-${SESSION_SHA8}.lock <<EOF
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
   rm -f .claude/locks/bugfix-${ISSUE_NUM}-${SESSION_SHA8}.lock
   ```
7. **崩溃恢复**：若锁文件 `started` 距今 > 4 小时 → 视为僵尸锁，直接清理后继续。

> 📌 锁路径：仓库根 `.claude/locks/`，加入 `.gitignore`。本步骤不出业务产物，但必须执行，自检清单会校验。
> 📌 本节与 §0.5 互补：§0.5 用 worktree 物理隔离，§0.6 用文件锁逻辑探测 + 让"同一 issue 多 session 启动"显式暴露。

### 0.6.1 接管协议（接力模式专用，强制；与 §1 输入态 D / 步骤 ⓪′ 配套）

接力别人（夜跑 / 其他 session）已开的 PR 时，`gh issue` 的 claim（`gh issue edit --add-label wip:claude-code` 或 claim 脚本）常返回 **rc=2（已被占用）**。此时**禁止**凭一句「用户授权我接力，合法 handoff」主观放行。必须按下表逐项核验，**三检全过**才接管：

| # | 检查项 | 命令 / 判据 | 不过怎么办 |
|---|---|---|---|
| 1 | 原 session 已结束 | issue label 含 `night-bugfix-done` / `*-done`，且**无** `wip:claude-code`（或 claim 评论里有 `— Released: <时间>`） | 仍有活锁 → 停下问用户：原 session 是否真结束 |
| 2 | PR 处于可接力态 | `gh pr view <PR> --json mergeable,state -q '.state+" "+.mergeable'` = `OPEN MERGEABLE`（或 CLEAN） | DIRTY/CONFLICTING → 先解冲突或回退给原 session |
| 3 | 用户明确授权 | 用户话术含「接力」「接手」「从 ⑤ 起」等显式授权 | 无显式授权 → `AskUserQuestion` 确认 |

三检全过 → 接管（v5 devlock 切主后必须走原子交接，禁止直接打 label）：
```bash
/opt/anaconda3/bin/python3 ~/.claude/mcp/devlock/cli.py handoff "$ISSUE_NUM" \
  --session "$SESSION_SHA8" --skill wiki-bug-fix --phase "⑤接力起跑"
```
（单事务旧认领→HANDED_OFF + 新认领 ACTIVE，无双活窗口；`wip:claude-code` label 由 devlock 投影自动维持）+ 覆写 claim 评论标注「接力接管 from <原 session>，于 <时间>」。任一不过 → **不接管**，停下报告用户。

> 📌 #871/#964 教训：两次都靠主观「合法 handoff」跳过核验，虽结果对，但与并行 /loop session 撞车的风险全靠运气规避（见 [[dev_auto_skipped_claim_lock_collision]]）。本协议把「接管」从主观判断升级为可复现的三检。

---

## 0.7 分支命名硬约束（防多 session 撞名）

`fix/issue-<num>-<slug>` 是 §0.5 §3.4 的命名格式，但**多 session 同 bug** 时会撞同名分支。强制升级为：

```bash
# 步骤 ③.4 把临时分支重命名为正式 fix 分支时，附加 session-sha8 后缀
SLUG="<kebab-slug>"
FEAT_BRANCH="fix/issue-${ISSUE_NUM}/${SESSION_SHA8}/${SLUG}"
git branch -m "$PROJ_TMP_BRANCH" "$FEAT_BRANCH"
export PROJ_FEAT_BRANCH="$FEAT_BRANCH"
```

> 例：`fix/issue-512/a3f8c2b1/celery-task-stuck`。两个 session 同时修 #512 也物理不可能撞名。
> 步骤 ④ push、步骤 ⑦ 清理时按 `fix/issue-${ISSUE_NUM}/${SESSION_SHA8}/` 精确锁定，不影响 §0.5 既有的 grep 规则。

## 0.8 研发资源锁（staging 串行 · 条件启用 + 优雅降级）

**目的**：多 session 并行修 bug 时，「合 main → CD 滚 `staging` → 在 `staging` 上 before/after 验证」整段碰同一套共享资源（main 分支 / CI / CD / 单实例 `staging` namespace 的 PG·Redis·MinIO）。本节用中心化排队工具 **`devlock`**（MySQL FIFO 复合锁，MCP server，库 `claude_code_dev`，代码 `tools/devlock/`）让这段**全局串行、公平排队、零资源竞争**。

**为什么是「升级」而非「新增」**：本技能 §0.5「rollout 竞态」+ 步骤 ④.7 `$PROJ_PUSHED_SHA` + 步骤 ⑤.2「等本 session SHA 滚到 namespace」是一套**乐观补丁**——它只能**事后检测**「我的镜像被 B 覆盖了」然后回 ④ 重推，**挡不住** B 在 A 跑 before/after 期间合 main 滚 CD **污染 A 正在验证的环境**（A 的 after 可能跑在 B 的镜像上 → 假绿/假红）。`devlock` 的复合锁 `MAINLINE = {CI,CD,STAGING}` 把整段从「乐观重试」升级为**悲观串行**：A 持锁期间 B 物理排队，结构性消除污染。两者**并存不冲突**——devlock 负责「整段不被插队」，`$PROJ_PUSHED_SHA` 继续作为「CD 是否已滚到本 SHA」的就绪探针（降级无锁时仍是唯一防线）。

**条件启用（硬约束 · 防污染通用性）**：本技能跨项目复用，**仅当**「`devlock` MCP 可达 **且** 当前项目《环境档案》已声明 `DEPLOY_NAMESPACE`」时启用资源锁；否则（MCP 不可用 / 非本项目 / 申请超时）**打一行 WARN 后跳过锁、回退现有 `$PROJ_PUSHED_SHA` 乐观行为，绝不阻断主流水线**。接力模式（步骤 ⓪′）同样适用：在 squash merge 前按下表取锁。

| 时机 | 调用 | 说明 |
|---|---|---|
| 步骤 ④.6 rebase 后、`git push HEAD:main` / `gh pr merge` **前** | `python3 ~/.claude/mcp/devlock/cli.py lock-acquire MAINLINE --session "$PROJ_SESSION_ID" --label "wiki-bug-fix #$ISSUE_NUM" --issue $ISSUE_NUM --ttl 900 --wait 3600`（Bash `run_in_background` 收口；**禁用 MCP 阻塞式 `lock_acquire` 排队**——waiter 随 MCP 连接死亡被 reap，2026-06-11 四 session 连环实证） | 拿到 `granted` 才合 main；超时/降级 → WARN 跳过 |
| 步骤 ④→⑤ 期间 | 心跳由守护进程自动续租(60s/拍,见 §0.8 心跳守护);守护未起时退回每 5min `lock_heartbeat(request_id)` | 续租防被回收 |
| **CI 构建绿**（build-images 本 SHA success）后 | `cli.py lock-release $REQUEST_ID --resources CI` | **分段释放**（CLAUDE.md《环境路由规则》v6）：CI 用完即还，别让排队者陪跑验证长尾 |
| **staging rollout 确认**含本 SHA 后 | `cli.py lock-release $REQUEST_ID --resources CD` | CD 用完即还；真验段**只持 STAGING** |
| 步骤 ⑤ 验证结束（**成败都走**；含 §5.5 回 ④ 改代码前） | `lock_release(request_id)`（放剩余 `STAGING`） | 触发下一个排队 session 递补 |

> 🌗 **v6 双车道（2026-06-11 #1630/PR#1697 落地）三点提醒**：① **squash 标题必须 `fix(` 前缀**（标题前缀=CD 车道选择器，`fix*`/`Revert "fix*`/`[lane:bug]` 才滚 staging；写成 feat/chore = CD 滚去 dev、staging 不动——此时持 STAGING 锁内 `gh workflow run cd.yml -f namespace=staging` 补滚，禁止改标题重推）。② **开工追平（可选）**：拿到 `STAGING` 后若 staging 部署 SHA 落后 main 且本单复现/验证依赖近期 dev 改动 → 持锁窗口内 `gh workflow run cd.yml -f namespace=staging`（image_tag 留空=最新 main per-SHA）追平后再采 BEFORE 证据；不依赖则跳过。③ 🚃 **搭车验证**：本 session 持 STAGING 期间，其他 commit 已含于部署 SHA 的 session 可只读搭车验证（规约见 CLAUDE.md《环境路由规则》）；反之本 session 复验已合并修复时若他人持锁且 ancestor 通过，也可搭车不排队。

```text
# 步骤 ④.6 rebase 后、合 main 前（cli 阻塞取锁；勿用 MCP lock_acquire 排队）：
if devlock_available() and project_has_namespace():
    req = bash_bg('python3 ~/.claude/mcp/devlock/cli.py lock-acquire MAINLINE '
                  '--session $PROJ_SESSION_ID --label "wiki-bug-fix #$ISSUE_NUM" --issue $ISSUE_NUM --ttl 900 --wait 3600')
    if not req.granted: WARN("devlock 超时，降级跳过资源锁（回退 $PROJ_PUSHED_SHA 乐观）"); req = None
else:
    WARN("devlock 不可用/非本项目，跳过资源锁"); req = None
# 持锁期间: 心跳由守护进程自动续租(60s/拍,见 §0.8 心跳守护);守护未起时退回每 5min: if req: lock_heartbeat(req.request_id)
# v5 分段释放: CI 绿 → cli.py lock-release $REQ --resources CI；rollout 确认 → --resources CD；真验只持 STAGING
# 步骤 ⑤ 结束(成败都走) / §5.5 回 ④ 前: if req: lock_release(req.request_id)   # 放剩余 STAGING
```

**与 `wiki-issue-claim-lib` 的关系（正交并存）**：claim 锁 = **issue 维度**（`wip:claude-code` label 防两 session 取同一 Issue，§0.6/§0.7 留痕机制保留不变）；devlock = **资源维度**（防多 session 同时碰 `staging`）。两者互补，缺一不可。

> 跨机多 session 生效需各机注册该 MCP 且连**同一共享 MySQL**（见 `tools/devlock/README.md`）；当前若指向 `localhost` 仅本机多 session 生效。语义为 **advisory（协作）锁**，只约束「调了本 MCP」的 session。

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

# /wiki-bug-fix — 示例项目 Bug 闭环八步单流水线

## 1. 触发场景

- 用户给出**一个 bug Issue 编号** / **一段报错日志/截图** / **口头描述线上异常**，希望"一条龙跑到 Issue 关闭"。
- 用户说："修这个 bug"、"fix bug #123"、"这个报错怎么修"、"线上挂了快修"、"定位 + 修复"、"/wiki-bug-fix"。
- 用户希望**不要中途反复确认**，只在「需求/方案争议」「P0 是否需回滚」「破坏性动作」三处停下来问。

> **🔀 输入态 D：接力模式（relay / handoff）** —— 当用户说「接力 night /loop」「PR #N 已开 / ready_unverified」「跳过 ①-④ 从 ⑤ 起」，或夜跑/其他 session 已完成复现+RCA+TDD+PR 时触发。
> **接力模式不走完整八步**，改走 **步骤 ⓪′ 接力接管子流水线**（见 §4「步骤 ⓪′」）：核 PR → 接管 claim → 检出 PR 分支到 worktree → 直接进 ⑤ 真验 → ⑥ review（**必须早于** ⑧ merge）→ ⑦ 关单 → ⑧ 汇报。
> 红线：接力模式下**禁止从零重建 spec/Issue/TDD**（夜跑已产出），但 ⑤⑥⑦⑧ 的所有硬门（AFTER 已上线、红线 label 决策、扩散矩阵、五件套、浏览器真验截图上传 T8）**一个都不能省**。

> 反例（请走其他技能）：
> - 模糊「需求/新功能」而非 bug → 用 [wiki-issue-dev](../wiki-issue-dev/SKILL.md)。
> - 只是写 prompt / 出任务书 → 用 [wiki-prompt-gen](../wiki-prompt-gen/SKILL.md)。
> - 已有 Issue + 实现，只是验收 → 用 [wiki-issue-acceptance](../wiki-issue-acceptance/SKILL.md)。
> - 只是合代码进 main → 用 [wiki-code-commit](../wiki-code-commit/SKILL.md)。
> - 只是 review 代码 ↔ spec 差异 → 用 [wiki-issue-review](../wiki-issue-review/SKILL.md)。

---

## 2. 核心硬约束（六条通用 + 四条 bug 专有，缺一不合格）

### 2.1 通用六条（来自项目根 `CLAUDE.md`，调研 / Pre-merge / 双轨 Git / CI / 扩散 / K8s 等小节）

1. **争议必走 ASK**：步骤 ① 发现任何「复现条件不明 / 影响面不明 / P0 是否需回滚 / 修复方案有分歧」→ 一次性用 `AskUserQuestion` 问，**最多一轮**，用户答完直接进步骤 ②，不二次确认。
2. **Spec 是 SoT**：步骤 ② 必须落到 `web/specs/<id>/` 或 `algo/specs/<id>/`，新增 `bugfix-<num>.md`（RCA 全文）+ spec.md 末尾 Changelog；不许只在 Issue body 里写根因。
3. **单流水线 = 全程顺序**：八步之间禁止并行；步骤 ④ 扩散修复 / 步骤 ⑥ 扩散排查可在「≥5 个真正独立位置」时局部并行（按 §0 红线）。
4. **测试环境唯一项目指定的 K8s namespace（`DEPLOY_NAMESPACE`，例：`staging`）**：步骤 ⑤ 禁止 `ssh <DEBUG_HOST> "docker compose ..."`（排障机 `DEBUG_HOST`，例：`debug-host`），禁止本机 docker / docker-compose 当测试环境。
5. **关闭五件套缺一不可**：步骤 ⑦ 关 Issue 前必须凑齐 ① 静态检查全绿、② `kubectl rollout status` ready、③ Post-push CI 全绿、④ **RCA 摘要**、⑤ **扩散覆盖矩阵**；少一件禁止 close。
6. **不可中途暂停**：除「需求/方案争议（ASK 一次）」「P0 是否需回滚（必问用户）」「破坏性动作（force push / 删未合并分支 / revert / 生产数据补救）」外，八步一气呵成，不要逐步征求"是否继续"。

### 2.1.1 跨 Session 防冲突四条（与 §0.5 §0.6 §0.7 配套）

| # | 维度 | 要求 |
|---|---|---|
| 6a | Session 锁 | 启动前必须按 §0.6 写 `.claude/locks/bugfix-<issue-num>-<session-sha8>.lock`；命中同 bug 锁必须 `AskUserQuestion` 三选一；步骤 ⑧ 末尾必须 `rm` 自己的锁 |
| 6b | 分支命名 | 按 §0.7 用 `fix/issue-<num>/<session-sha8>/<slug>` 三段格式（含 session-sha8），物理不可能撞名 |
| 6c | Base 同步 | 步骤 ④ 任何 commit / push 前必须 `git fetch origin main && git rebase origin/main`（已在 §4 步骤 ④.6 落实，本约束作为硬性提醒） |
| 6d | In-flight PR 查重 | 步骤 ④.6 PR 轨建 PR 前必须对每个计划改动的文件跑 `gh pr list --search "involves:@me state:open"` 查同文件 in-flight PR，命中 → **追加 commit 到那个 PR**，禁止新开重复 PR |
| 6e | 接管协议 | **接力模式**下 claim 返回 rc=2 时，必须按 §0.6.1 三检（原 session 已结束 / PR 可接力 / 用户授权）全过才接管，禁止主观「合法 handoff」放行 |

### 2.2 bug 专有四条

7. **必须真实复现 + 工具链匹配 bug 类型**：步骤 ① 在本 session worktree / 项目测试环境 K8s（`DEPLOY_NAMESPACE`）中**真实复现** bug，且**复现工具必须与 bug 类型一一对应**（Web→浏览器 MCP + 构造数据 / 后台 API→curl 或 pytest 重发请求 / 数据库→构造 SQL / 算法→API 或 CLI 实跑，详见 §4 步骤 ①.2.2 矩阵）。错配工具（如 Web bug 只跑 curl 不开浏览器 / DB bug 不构造 SQL / 算法 bug 不实跑只读代码）视为复现无效，禁止进入 RCA。复现不出来 → 不许开始改代码。
8. **必须做 RCA**：步骤 ① 强制 5 Why + memory 反查（`~/.claude/projects/<project-slug>/memory/` 已沉淀 10+ 条 示例项目 经典坑），不许只打补丁不写根因。
9. **强制 TDD**：步骤 ④ **必须先写能复现 bug 的回归测试 → 跑一遍红 → commit `test:` → 改代码 → 跑一遍绿 → commit `fix:`**。test commit 必须在 fix commit 之前（`git log` 可验证）。先改代码再补测试 = 违反硬约束。
10. **强制扩散排查**：步骤 ④ 开发前用 `rg` 估算同模式位置，步骤 ⑥ 开发后再核一次，产出「扩散覆盖矩阵表」贴 Issue 评论 + bugfix-<num>.md。矩阵缺失 → 禁止关 Issue。

### 2.3 时序与质量硬门（v2 第一刀强化，违反任一 = 本次闭环不合格）

> 这些约束治的是「步骤都做了、但顺序/深度不对仍判通过」的隐性失格（#1151/#871/#964 实战教训）。每条都在对应步骤有落地校验。

| # | 硬门 | 落地步骤 | 红线 |
|---|---|---|---|
| T1 | **Web bug 浏览器复现先于读码** | §1.2.2.A | Playwright/Chrome 截图 + network 必须在**任何 `Read 源码`** 之前完成；先读码猜根因再开浏览器「确认」= 复现退化为确认性证据，失格 |
| T2 | **扩散 rg 前置闸** | §4.3 → §4.4 | `spread-pre.txt` 必须在写 fix 代码**之前**产出；§4.4 开头校验该文件存在，否则禁止改码（扩散范围必须事前规划，非事后归纳） |
| T3 | **RCA 深化回写** | §4.2/§4.4 | TDD 阶段发现更深根因（如 pg_insert 绕过 listener）→ 必须回写 bugfix-`<num>`.md §2 + Issue 评论，保持 spec 是 SoT |
| T4 | **红线 label 决策门** | §6.1 | `needs-contract-review` 等红线 label 存在且发现真实缺口时，**禁止技能自判「不阻塞」**，必须 `AskUserQuestion` 让用户裁决（阻塞修复 / 合并+follow-up / 升级人工），决策留痕 |
| T5 | **关单前 AFTER 已上线（部署祖先硬校验）** | §5.2 / §7.1 | 关 Issue 前：从**测试员同款环境**（`DEPLOY_NAMESPACE`，例：staging）**实查**部署镜像 tag → 抽出其 git SHA `$DEPLOYED_SHA` → `git merge-base --is-ancestor $FIX_SHA $DEPLOYED_SHA` **必须为真**（本修复 commit 是当前测试环境部署镜像的祖先，而非仅"tag 字面含本 SHA"）；**且** AFTER 已在该镜像上、以测试员同款环境 + 同款 UI 路径（**禁 dev / pod 内 DB 直验 / 合成种子替代**）用 before 同路径复跑且结果相反；祖先校验不过（build/CD 仍 in_progress / 镜像仍旧 SHA / 本 SHA 不在部署镜像历史）→ 打 `wip:待部署` label、**禁止 close**（治「抢跑关单→测试打旧镜像→假打回 reopen」，06-29 批量重开根因） |
| T6 | **接力 review 早于 merge** | 步骤 ⓪′ | 接力模式下 ⑥ code review 必须在 ⑧ squash merge **之前**完成；merge 后才 review = P1 只能 hotfix，失格 |
| T7 | **缺陷性判定门先于 RCA** | §1.2.5 | 进 §1.3 RCA 改代码**之前**必须做 `实际行为` vs `spec 明文期望` 比对；判「非缺陷」的证据**必须来自 spec（禁代码自证，循环论证无效）**，缺 spec 证据或 spec 无明文时**禁止误关**、必须 ASK；误报关闭走 §1.2.5.A 独立口径（**不套**步骤 ⑦ 五件套） |
| T8 | **关单评论必附浏览器真验截图** | §5.3.B′ / §6.5.0 / §7.1 | AFTER 截图必须产自**部署含本 SHA 镜像之后**的真实浏览器复跑（硬刷新，时间戳晚于 rollout）；截图必须**上传到 GitHub（evidence 分支，§6.5.0）**并把链接贴进关闭证据评论——只留本地 `.bugfix-evidence/` 路径 = 证据随 ⑦.3 清理蒸发，失格。Web/UI bug 缺 before/after 截图链接、或纯后端 bug 缺「无 UI 面豁免说明 + 终端证据链接」→ 禁止 close |
| T9 | **AC 逐条覆盖（治半修）** | §1.3.C / §6.5 / §7.1 | RCA 阶段必须把 bug 标题 + 正文拆成验收标准清单 `AC-1/AC-2…`（一个诉求一条，含隐含次诉求——如"卡住"与"无提示"是两条）；关单前**每条 AC 都必须有「对应修复 commit + 对应回归/真验证据」逐条对齐**，产出 **AC 覆盖矩阵**贴 PR 描述 + 关闭证据评论；任一 AC 无证据（半修：只覆盖主诉求漏次诉求，#2691 根因）→ 禁止 close |
| T10 | **PROD 精确路径真实验收 + 对抗式证伪** | §6.7 | 关单前 Web/UI bug 必须串联 `/wiki-issue-acceptance <N>`：Playwright 打 `PROD_URL`（例：app.example.com）走**触发原 bug 的精确用户路径**，抓 before/after 截图 + console + network，做**对抗式证伪复核**（主动找"仍复现"的反证）防「幻觉判过」；验收失败 → `git revert` 回退 + Issue 保持 open，**禁止 close** |

---

## 3. 八步闭环总览（与用户 8 项需求 1:1 映射）

> **步骤 ⓪「起 worktree」** 是隔离前置，不算进用户八项需求，但**强制**执行（否则多 session 互相毁代码）。
> **接力模式（输入态 D）走 [步骤 ⓪′](#步骤-接力接管子流水线仅接力模式--输入态-d-走此分支替代-)，跳过 ①-④（夜跑已完成），直接 ⑤→⑥→合并→⑦→⑧。**

| 步 | 动作 | 对应用户需求 | 主要工具 |
|---|---|---|---|
| **步骤 ⓪** | 起 Bug 独占 worktree + 临时分支 | 前置（多 session 隔离） | Bash(git worktree add) |
| **步骤 ①** | 拉 Issue + 真实复现 + RCA + memory 反查 | 1️⃣ issue 中的 bug + bug 分析 | gh issue view / Read / Bash(curl) / Grep |
| **步骤 ②** | Spec 增量 + bugfix-<num>.md 落 RCA | 2️⃣ spec | Read / Edit / Write |
| **步骤 ③** | Issue 同步（已有→评论，未建→补建）+ 分支重命名 | 3️⃣ Issue | Bash(gh issue edit/comment/create) |
| **步骤 ④** | TDD 单流水线开发（test 红 → fix 绿 → 扩散同 PR → 合 main） | 4️⃣ 开发 | Edit / Write / Bash |
| **步骤 ⑤** | 测试环境 K8s（`DEPLOY_NAMESPACE`）before/after 实测（锁定本 session SHA） | 5️⃣ K8s 测试 | Bash(kubectl / curl / pytest) |
| **步骤 ⑥** | Review + 扩散覆盖矩阵 + Issue 评论关闭证据 | 6️⃣ 闭环佐证 | Skill(wiki-code-review) / Bash(gh issue comment) |
| **步骤 ⑦** | 关闭 Issue（五件套）+ 严格限定清理本 session worktree/分支 | 7️⃣ 关 Issue | Bash(gh issue close / git worktree remove) |
| **步骤 ⑧** | 串联 `/wiki-session-report` | 8️⃣ 汇报 | Skill(wiki-session-report) |

---

## 4. 步骤逐条执行手册

### 步骤 ⓪ 起 Bug 独占 worktree + 临时分支（前置，强制）

> 目标：本 session 完全跑在 `.claude/worktrees/bug-fix-<session-id>/` 这个独占目录里，**永不**碰项目主检出（`$PROJECT_ROOT`）的工作树（只允许在它上面 `git fetch`）。

#### 0.1 生成 session-id 并起 worktree

```bash
PROJECT_ROOT=$(git rev-parse --show-toplevel)   # 项目主检出根；不写死任何绝对路径
PROJ_ROOT="$PROJECT_ROOT"                         # 下文沿用 PROJ_ROOT 变量名（=项目根），保持脚本一致
SESSION_ID="$(date +%Y%m%d-%H%M%S)-$$"
WORKTREE_PATH="${PROJ_ROOT}/.claude/worktrees/bug-fix-${SESSION_ID}"
TMP_BRANCH="wip/bug-fix-${SESSION_ID}"

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

- [ ] `pwd` 输出 `.claude/worktrees/bug-fix-${SESSION_ID}`（不是主检出）
- [ ] `git branch --show-current` 输出 `wip/bug-fix-${SESSION_ID}`
- [ ] `git rev-parse HEAD == git rev-parse origin/main`（worktree 起点 = 最新 main）
- [ ] `core.hooksPath` 已设为 `scripts/hooks`

> 后续步骤 ① ② ③ ④ ⑤ ⑥ ⑦ 全部在 `$PROJ_WORKTREE` 内执行；除步骤 ⓪ / ④ 末尾的 `git fetch origin` 外，绝不在主检出 `$PROJ_ROOT` 上动 git。

---

### 步骤 ⓪′ 接力接管子流水线（**仅接力模式 / 输入态 D 走此分支**，替代 ①-④）

> 触发：用户说「接力 night /loop」「PR #N 已开」「跳过 ①-④ 从 ⑤ 起」。夜跑/其他 session 已完成复现+RCA+TDD+开 PR，本 session 只做**真验 + 评审 + 合并 + 关单**。
> **顺序不可乱**：核 PR → 接管 claim → 检出 PR 分支 → ⑤ 真验 → ⑥ review（**必须早于 merge**）→ 合并（§4.6）→ ⑦ 关单 → ⑧ 汇报。

#### ⓪′.1 核 PR 现状

```bash
gh pr view "$PR_NUM" --repo "$GIT_REPO" \
  --json number,state,mergeable,headRefName,labels,files \
  -q '{state,mergeable,head:.headRefName,labels:[.labels[].name]}'
```
- `state=OPEN` 且 `mergeable=MERGEABLE/CLEAN` → 可接力；否则先解冲突。
- 记录 labels：含 `needs-contract-review` / `cross-module` 等**红线 label** → 步骤 ⑥ 必须按 §2.3 T4 走决策门。

#### ⓪′.2 接管 claim（按 §0.6.1 三检，强制）

claim 返回 rc=2 时**不许主观放行**，逐项核验 §0.6.1 三检表（原 session 已结束 / PR 可接力 / 用户授权）。三检全过 → 覆写 claim 评论标注「接力接管」+ 重打 `wip:claude-code`。

#### ⓪′.3 检出 PR 分支到独占 worktree（替代「基于 main 起 worktree」）

```bash
PR_BRANCH=$(gh pr view "$PR_NUM" --repo "$GIT_REPO" --json headRefName -q .headRefName)
git -C "$PROJ_ROOT" fetch origin "$PR_BRANCH"
WORKTREE_PATH="${PROJ_ROOT}/.claude/worktrees/bug-fix-${SESSION_ID}"
git -C "$PROJ_ROOT" worktree add "$WORKTREE_PATH" "$PR_BRANCH"   # 直接基于 PR 分支，不用 main + 临时 checkout
cd "$WORKTREE_PATH"
export PROJ_WORKTREE="$WORKTREE_PATH" PROJ_FEAT_BRANCH="$PR_BRANCH"
```
> 📌 #964 教训：用「main + 手动 checkout PR 文件」导致 worktree 双步清理不干净。接力直接 `worktree add <pr-branch>` 一步到位、清理也只删本 worktree。

#### ⓪′.4 进入 ⑤ 真验 → ⑥ review → 合并 → ⑦ 关单 → ⑧ 汇报

- **⑤ 真验**：按步骤 ⑤ 做 before/after（接力的 before = 线上旧镜像，after = 合并部署后的新镜像），见 §5.3。
- **⑥ review**：按步骤 ⑥ 做 code review + 扩散矩阵。**红线（T6）：review 必须在 squash merge 之前完成**；红线 label 触发 §2.3 T4 决策门。
- **🔒 合并前取锁（§0.8，条件启用）**：squash merge **之前**若 `devlock` 可达且本项目有 `DEPLOY_NAMESPACE` → `python3 ~/.claude/mcp/devlock/cli.py lock-acquire MAINLINE --session "$PROJ_SESSION_ID" --label "wiki-bug-fix #$ISSUE_NUM 接力" --issue $ISSUE_NUM --ttl 900 --wait 3600`（Bash `run_in_background`；勿用 MCP 阻塞式 `lock_acquire` 排队）拿到 `granted` 才合并；心跳由守护进程自动续租(60s/拍,见 §0.8 心跳守护);守护未起时退回每 5min `lock_heartbeat(request_id)`。**v5 分段释放**：CI 绿 → `cli.py lock-release $REQ --resources CI`；rollout 确认 → `--resources CD`；AFTER 复跑只持 `STAGING`，收口后（成败都走）`lock_release` 放剩余。超时/不可用 → WARN 降级跳过（回退 `$PROJ_PUSHED_SHA` 乐观）。
- **合并**：本地 rebase 干净 → squash merge。接力默认 **不用 `--auto`**（见 §4.6 auto 口径）。
- **⑤ AFTER 上线确认**：merge 后必须等 CD 把本 SHA 滚到 namespace（§5.2 含 deploy-gap SOP），在新镜像上复跑 AFTER（§2.3 T5）。
- **⑦ 关单**：按步骤 ⑦ 五件套 + AFTER 已上线硬门。**⑧ 汇报**：串联 `/wiki-session-report`。

#### ⓪′.5 步骤 ⓪′ 通过判据

- [ ] PR 三检通过（state/mergeable/红线 label 已识别）
- [ ] claim 按 §0.6.1 三检接管（非主观放行）
- [ ] worktree 基于 PR 分支（非 main + 手动 checkout）
- [ ] ⑥ review 在 ⑧ merge 之前完成（T6）
- [ ] AFTER 在合并部署后的新镜像上复跑通过（T5），非 build/CD in_progress 即关单

---

### 步骤 ① 拉 Issue + 真实复现 + RCA + memory 反查（对应 1️⃣，**bug 专有重头戏**）

#### 1.1 拉 Issue（三种输入态分别处理）

| 输入态 | 动作 |
|---|---|
| **A. 用户给 Issue 编号** | `gh issue view <num> --comments --repo "$GIT_REPO"` 拉全文 + 已有讨论 |
| **B. 用户贴报错/截图/日志** | 先存原始证据到 `$PROJ_WORKTREE/.bugfix-evidence/`（截图/日志原文），步骤 ③ 再建 Issue |
| **C. 用户口头描述** | ASK 一次确认是 bug（与"新需求"区分），获取最小复现信息后转 B |

```bash
# 输入态 A：
export GH_TOKEN="$(gh auth token --user zhaod39_example-corp)"
ISSUE_NUM=<num>
gh issue view "$ISSUE_NUM" --repo "$GIT_REPO" --comments \
  > .bugfix-evidence/issue-${ISSUE_NUM}-raw.md
```

#### 1.2 复现矩阵（必填，写进 bugfix-<num>.md）

##### 1.2.1 七维度通用表（所有 bug 类型必填）

| 维度 | 必填内容 | 备注 |
|---|---|---|
| **Bug 类型** | Web 前端 / 后台 API / 数据库 / 算法 / 其他 | **决定 1.2.2 用哪套复现工具链（强制硬约束 7）** |
| **触发条件** | 最小复现步骤（≤5 步） | 越短越好 |
| **期望行为** | 一句话 | 引用 spec 原文最佳 |
| **实际行为** | 一句话 + 错误日志/截图引用 | 文件路径 `.bugfix-evidence/...` |
| **复现率** | 100% / 偶发 N% / 仅特定数据 | 偶发 → 标 P1 + 时序问题候选 |
| **环境** | 测试环境 namespace（`DEPLOY_NAMESPACE`）/ 公网线上（`PROD_URL`）/ 本地 / 生产 | 跨环境差异是关键线索 |
| **首次出现** | commit SHA / 日期 / 版本号 | `git bisect` 候选 |
| **影响面** | 阻塞用户数 / 数据是否污染 / 是否需回滚 | 决定 P0/P1/P2 |

##### 1.2.2 按 Bug 类型选复现工具链（**强制硬约束**，类型 ↔ 工具一一对应）

> ⚠️ **红线**：错配工具链 = 复现无效，禁止进入 RCA。
> 典型错配：Web bug 只跑 curl 不开浏览器 / 后台 API bug 只看日志不重发请求 / DB bug 不构造 SQL 只看代码 / 算法 bug 不跑 CLI 或 API 只盯 logs。

| Bug 类型 | 必须用的复现工具 | 禁止只靠 |
|---|---|---|
| Web 前端 | Playwright / Chrome MCP / Preview MCP + **必须构造前置数据** | 只看 console 截图 / 只读代码 |
| 后台 API | curl / httpie / pytest（指测试环境 BFF，namespace `DEPLOY_NAMESPACE`） | 只看 Pod 日志不重发请求 |
| 数据库 | `kubectl exec ... psql` 构造 SQL / `mcp__mysql__*` | 只看 ORM 代码不验真实数据 |
| 算法 | API 调用算法入口 / CLI 脚本 / `kubectl exec deploy/algo` 跑 Python | 只读模型代码不实跑 |

---

###### 1.2.2.A Web 前端 bug（UI 异常 / 交互 / 路由 / 渲染 / 表单）

> **前端浏览器测试统一标准（入口/工具/八维交互检查表/证据五件套/固化为回归/判过标准）= [`_shared/frontend-browser-testing.md`](../_shared/frontend-browser-testing.md)，必读必执行**（dev/acceptance/bug-fix 共用一份）。下面是 bug-fix 侧的复现落盘细则（与共享文件 §4/§6 一致）。

> ⚠️ **硬门 T1：浏览器复现必须先于任何读码**。Web bug 的第一个动作就是开浏览器（Playwright/Chrome/Preview MCP），抓到异常的 network 响应 / 截图后**再**去追代码链路。
> - ✅ 正确：开浏览器 → 抓到 `created_by: null` / toast=0 等异常现象 → 由现象反推代码根因（**发现性证据**）。
> - ❌ 失格：先 `Read` 一堆源码猜根因 → 最后才开浏览器「确认猜想」（复现退化为**确认性证据**，存在猜错根因/漏真因风险，#1151 实证：复现排在 6 次读码之后）。
> - 自检：本步产出的截图 + network 日志的时间戳，必须早于任何前端/后端源码 `Read`。

**必备**：浏览器工具 + 构造前置数据 + 截图 + DOM 快照 + console/network 日志

**工具优先级**：
1. `mcp__playwright__*`（默认）—— DOM 可断言，截图/日志可落盘，操作脚本能直接固化为 TDD 第一拍的回归测试
2. `mcp__Claude_in_Chrome__*` —— 用户已登录的现实账号能复现需要 SSO 的场景
3. `mcp__Claude_Preview__*` —— 本地 dev server 起得起来时

**强制 5 件套（缺一不视为复现）**：
- [ ] **构造前置数据**（DB seed / API mock / fixture），数据 ID / SQL 落 `.bugfix-evidence/seed.sql`
- [ ] 浏览器**实际打开页面**到出 bug 的状态，操作脚本落 `.bugfix-evidence/repro-steps.md`
- [ ] **截图**（出 bug 前 + 出 bug 后两张），落 `.bugfix-evidence/before.png` / `after.png`
- [ ] **console 日志**抓全（`mcp__playwright__browser_console_messages`），落 `.bugfix-evidence/console.log`
- [ ] **network 请求**抓全（`mcp__playwright__browser_network_requests`），落 `.bugfix-evidence/network.har`，**尤其失败的 4xx/5xx**

**样例命令**：

```bash
# 1) 构造前置数据（必做，否则复现率不稳定）
# deployment 名见项目《环境档案》KEY_DEPLOYMENTS，以下用 示例（postgres）
kubectl exec -n "$DEPLOY_NS" deploy/postgres -- psql -U appuser \
  -f - <<'SQL' | tee .bugfix-evidence/seed.sql
INSERT INTO samples (id, dataset_id, pool_status) VALUES (9999, 42, 'IN_USE');
SQL

# 2) 用 Playwright 自动化操作（推荐做法：操作流程直接编进 spec.ts 当回归测试）
#    通过 mcp__playwright__browser_navigate / browser_fill_form / browser_click / browser_take_screenshot
#    把流程固化为 tests/acceptance/browser/test_bug_${ISSUE_NUM}.spec.ts（canonical 位置，见 _shared/test-traceability-and-assets.md §2）

# 3) 关键证据落盘（MCP 工具调用，伪代码示意）
# mcp__playwright__browser_take_screenshot     → .bugfix-evidence/web-bug-${ISSUE_NUM}-before.png
# mcp__playwright__browser_click(...触发...)
# mcp__playwright__browser_take_screenshot     → .bugfix-evidence/web-bug-${ISSUE_NUM}-after.png
# mcp__playwright__browser_console_messages    → .bugfix-evidence/console.log
# mcp__playwright__browser_network_requests    → .bugfix-evidence/network.json
```

> 内网研发态走线上地址（`PROD_URL`，例：https://app.example.com，经私网 SLB），公网兜底走 [[public_network_dev_fallback]]；浏览器不读 NO_PROXY 注意 Clash 代理。

---

###### 1.2.2.B 后台 API bug（5xx / 4xx 错误码 / 返回值不对 / 性能 / 鉴权）

**必备**：API 调用复现 + 请求/响应原文 + Pod 日志 + 关联 DB 状态快照

**工具优先级**：
1. `curl` / `httpie`（默认，命令行可重复、易于落盘）
2. `pytest` + `BFF_BASE_URL=http://${INTERNAL_SLB}:30115` 指测试环境（内网 SLB `INTERNAL_SLB` + BFF NodePort，示例端口 30115；推荐：复现脚本 = TDD 第一拍回归测试）
3. `gh api` 或 `kubectl exec ... -- curl`（鉴权/网络隔离时）

**强制 4 件套**：
- [ ] **请求原文**：method + URL + headers（Bearer 脱敏）+ body，落 `.bugfix-evidence/req.http`
- [ ] **响应原文**：status + headers + body，落 `.bugfix-evidence/resp.json`
- [ ] **Pod 日志** 触发时刻 ±2 min（`kubectl logs --since=5m`），落 `.bugfix-evidence/pod-logs-*.txt`
- [ ] **关联 DB 状态快照**（请求前 + 请求后 `SELECT *`），帮助判定是否落库 / 落错列

**样例命令**：

```bash
# 1) 请求 + 响应原文双落盘（SLB IP=INTERNAL_SLB，端口为各服务 NodePort；以下用 示例端口）
curl -sv -X POST "http://${INTERNAL_SLB}:30115/api/v1/<endpoint>" \
  -H "Authorization: Bearer ${JWT}" \
  -H "Content-Type: application/json" \
  -d @.bugfix-evidence/req-body.json \
  2> .bugfix-evidence/req.http \
  | tee .bugfix-evidence/resp.json

# 2) Pod 日志（BFF + worker 双拉，按时间窗；deployment 名见《环境档案》KEY_DEPLOYMENTS，以下用 示例）
kubectl logs -n "$DEPLOY_NS" deploy/web-bff      --since=5m --tail=500 \
  > .bugfix-evidence/pod-logs-bff.txt
kubectl logs -n "$DEPLOY_NS" deploy/celery-worker --since=5m --tail=500 \
  > .bugfix-evidence/pod-logs-worker.txt

# 3) acceptance JWT 签名见 [[acceptance_jwt_credentials]]（不要外传/不要 commit）
```

---

###### 1.2.2.C 数据库 bug（数据不一致 / 字段类型 / 约束 / 迁移 / 索引性能）

**必备**：构造 SQL 复现 + 表结构快照 + before/after 数据对比 + migration 状态

**工具优先级**：
1. `kubectl exec -n "$DEPLOY_NS" deploy/postgres -- psql -U appuser`（默认，直连测试环境 PG；deployment 名见《环境档案》KEY_DEPLOYMENTS，以下用 示例 postgres）
2. `mcp__mysql__*`（若该 bug 涉及 mysql 侧实例）

**强制 5 件套**：
- [ ] **表结构快照**：`\d+ <table>` 落 `.bugfix-evidence/schema-before.txt`（对照 alembic / SQLModel 查 schema 漂移，参考 [[prod_schema_drift_mechanism]]）
- [ ] **复现 SQL**：能在干净状态下 SELECT/INSERT/UPDATE 重现异常，SQL 落 `.bugfix-evidence/repro.sql`，stdout 落 `.bugfix-evidence/repro-out.txt`
- [ ] **before 数据**：触发前目标行 `SELECT *`，落 `.bugfix-evidence/data-before.txt`
- [ ] **after 数据**：触发后再 `SELECT *`，落 `.bugfix-evidence/data-after.txt`，`diff` 一下
- [ ] **migration 状态**：`alembic current` / `\dt <schema>.*` 看 schema 是否与代码同步

**样例命令**：

```bash
# 1) 表结构快照（deployment 名见《环境档案》KEY_DEPLOYMENTS，以下用 示例 postgres）
kubectl exec -n "$DEPLOY_NS" deploy/postgres -- \
  psql -U appuser -c '\d+ samples' > .bugfix-evidence/schema-before.txt

# 2) before 数据
kubectl exec -n "$DEPLOY_NS" deploy/postgres -- \
  psql -U appuser -c "SELECT id, pool_status, updated_at FROM samples WHERE dataset_id=42 ORDER BY id LIMIT 10;" \
  > .bugfix-evidence/data-before.txt

# 3) 复现 SQL（heredoc 整段落盘可重跑）
kubectl exec -i -n "$DEPLOY_NS" deploy/postgres -- \
  psql -U appuser <<'SQL' | tee .bugfix-evidence/repro-out.txt
\timing on
-- 复现：批量更新后枚举回退
UPDATE samples SET pool_status = 'IN_USE' WHERE id IN (1,2,3);
SELECT id, pool_status FROM samples WHERE id IN (1,2,3);
SQL

# 4) after 数据
kubectl exec -n "$DEPLOY_NS" deploy/postgres -- \
  psql -U appuser -c "SELECT id, pool_status, updated_at FROM samples WHERE dataset_id=42 ORDER BY id LIMIT 10;" \
  > .bugfix-evidence/data-after.txt

diff .bugfix-evidence/data-before.txt .bugfix-evidence/data-after.txt \
  > .bugfix-evidence/data-diff.txt || true
```

---

###### 1.2.2.D 算法 bug（推理结果错 / 训练崩 / 性能退化 / Celery 任务卡 / GPU OOM）

**必备**：API 调用算法入口 或 CLI/脚本可重复跑出错 + 输入数据指纹 + 中间产物 + 算法 job_id 全链路日志

**工具优先级**：
1. **API 测试**（首选）—— 触发 `algo` deployment 的 `/predict` / `/train` 入口，让 K8s 实际跑一遍
2. **CLI / Python 脚本**（次选）—— `algo/scripts/repro_bug_<num>.py` 在 worktree 内可重复执行
3. `kubectl exec -it deploy/algo` 进 pod 用算法侧 venv 跑 REPL 或单步调试

**强制 5 件套**：
- [ ] **输入数据指纹**：dataset_version_id + sample_id + MinIO key + md5，落 `.bugfix-evidence/input.txt`
- [ ] **触发命令**：CLI 行 或 API call 落 `.bugfix-evidence/repro-cmd.sh`，跑一遍 stdout 落 `.bugfix-evidence/repro-out.txt`
- [ ] **algo_job_id / celery_task_id**：从 BFF 拿到的 ID 落 `.bugfix-evidence/job-id.txt`（参考 [[celery_flush_before_delay_race]] / [[redis_broker_message_loss]] 排查决策树）
- [ ] **Celery worker + algo pod 日志**：按 task_id grep，落 `.bugfix-evidence/algo-trace.txt`
- [ ] **中间产物**（heatmap / 中间向量 / loss curve / GPU mem 曲线），落 `.bugfix-evidence/intermediate/`

**样例命令**：

```bash
# 1) API 触发算法（首选；SLB IP=INTERNAL_SLB + 算法服务 NodePort，以下用 示例端口 30120）
curl -sf -X POST "http://${INTERNAL_SLB}:30120/api/algo/predict" \
  -H "Content-Type: application/json" \
  -d '{"sample_id": 9999, "model_version": "v1.2"}' \
  | tee .bugfix-evidence/algo-resp.json

JOB_ID=$(jq -r '.algo_job_id' .bugfix-evidence/algo-resp.json)
echo "$JOB_ID" > .bugfix-evidence/job-id.txt

# 2) 全链路日志（按 job_id grep，algo + worker 双拉；deployment 名见《环境档案》KEY_DEPLOYMENTS，以下用 示例）
kubectl logs -n "$DEPLOY_NS" deploy/algo           --since=10m --tail=2000 \
  | grep "$JOB_ID" > .bugfix-evidence/algo-trace.txt
kubectl logs -n "$DEPLOY_NS" deploy/celery-worker  --since=10m --tail=2000 \
  | grep "$JOB_ID" >> .bugfix-evidence/algo-trace.txt

# 3) CLI 兜底（在 algo pod 内跑，复用算法 venv；GPU 沙箱见 [[debughost_dual_environment]]）
kubectl exec -n "$DEPLOY_NS" deploy/algo -- \
  python -m algo.scripts.repro_bug_${ISSUE_NUM} \
  > .bugfix-evidence/algo-cli-out.txt 2>&1
```

##### 1.2.3 复现证据落盘清单（全类型通用）

所有证据**必须**落 `$PROJ_WORKTREE/.bugfix-evidence/`，便于步骤 ④ TDD 写测试时直接引用、步骤 ⑤ before/after 对比时直接读、步骤 ⑥ Issue 评论时直接贴。命名规范：

```
.bugfix-evidence/
├── repro-cmd.sh             # 一键复现脚本（所有类型必备）
├── repro-steps.md           # 复现步骤说明（playwright 操作流 / SQL 顺序 / API 调用顺序）
├── seed.sql                 # 前置数据（Web/API/DB/算法 凡需构造数据均落此）
├── req.http / resp.json     # 后台 API 类
├── before.png / after.png   # Web 类
├── console.log / network.json # Web 类
├── schema-before.txt        # DB 类
├── data-before.txt / data-after.txt / data-diff.txt # DB 类
├── repro.sql                # DB 类
├── input.txt / job-id.txt   # 算法类
├── algo-trace.txt           # 算法类
├── intermediate/            # 算法类中间产物
├── pod-logs-*.txt           # 所有类型补 K8s 日志
└── rca-draft.md             # RCA 草稿，步骤 ② 迁到 bugfix-<num>.md
```

##### 1.2.4 复现不出来 → 红线

禁止开始改代码。三种处置：

| 场景 | 处置 |
|---|---|
| 偶发但有规律（如「凌晨某时间窗」「特定数据」） | bugfix-<num>.md 复现率字段标 **偶发 + 已知规律**，构造对应数据后再回 1.2.2 重跑；仍不复现 → ASK |
| 完全复现不出来 | **ASK 用户**索取：用户 ID / 触发时间戳 / 浏览器 console 截图 / 复现录屏 / 报错全栈；拿到后回 1.2.2 选**正确类型**工具链重跑 |
| **工具链错配**（如 Web bug 只跑 curl） | 视为复现无效，强制回 1.2.2 用匹配类型的工具重做 |
| **经核实为误报 / 现象不存在**（多工具复现 + ASK 后仍无法复现，且 §1.2.5 比对确认当前行为符合 spec，或属数据/配置问题） | **不要无限 ASK 循环**：转 §1.2.5.A 误报判定子流程，凭 **spec 证据**误报关闭（或转需求）；spec 证据不足 → 最后一次 ASK 用户确认后再处置 |

> 📌 「完全复现不出来」反复 ASK 仍无果，**不是死循环的理由**：转 §1.2.5 缺陷性判定门，以 spec 为唯一裁决依据，给出「真缺陷待补信息 / 误报关闭 / 转需求」三选一的明确结论，不让 Issue 悬而不决（用户体验第一）。

---

#### 1.2.5 缺陷性判定门（复现矩阵后、RCA 前 · 强制 · 防「给非缺陷编根因打补丁」）

> 目的：**bug ≠「看起来不对的现象」**。§1.2 拿到 `实际行为` + `期望行为` 后，进 RCA 改代码**之前**必须先判一刀：这个现象到底是不是「**违反约定**」的真缺陷。判错方向（把符合设计的行为当 bug 修）= 改坏正确代码、制造新 bug + 浪费报障人与开发的时间。

##### 判定唯一依据 = spec（SoT），**禁止用代码自证**

- ⛔ **红线（核心）**：判定「实际行为是预期的、不是 bug」，**证据必须来自 spec** —— `web/specs/<id>/spec.md` / `algo/specs/<id>/` / AC 验收标准 / 设计稿 / PRD / 接口契约文档，引用格式 `spec文件:行号 + 原文摘录`。
- ⛔ **禁止用代码证明「这是预期行为」**：代码本身可能就是 bug，「代码就是这么写的 / 函数逻辑如此 / 实现一直这样」是**循环论证**，**不构成**非缺陷证据。代码只能用来定位现象成因，**不能用来裁决对错**。
- 仅当 spec 对**该场景**有**明文规定**时才能进入比对；spec 对该场景**无明文** → 见下方③，**不许**默认判误报。

##### 三路判定

| 比对结果 | 判定 | 去向 |
|---|---|---|
| ① `实际行为` **≠** spec 明文期望（拿到 `spec:行号` 反向佐证「应该怎样」） | **真缺陷** | 正常进 §1.3 RCA |
| ② `实际行为` **==** spec 明文期望（`spec:行号` 原文为证「现在就是对的」） | **疑似误报** | 进「误报判定子流程」§1.2.5.A |
| ③ spec 对该场景**无明文** / spec 自身也写错 | **不可判** | **禁止**判误报；`AskUserQuestion` 确认期望 + 标记为 spec 缺口（可能是需求 → 转 [wiki-issue-dev](../wiki-issue-dev/SKILL.md)；也可能 spec 漏写 → 回步骤 ② 补 spec 后再判） |

##### 1.2.5.A 误报判定子流程（**用户体验第一，绝不甩锅报障人**）

误报有两类，处置不同，但**都先备齐 spec 证据三件套**（缺一不许关闭）：
- [ ] **spec 证据**：`spec文件:行号` + 原文摘录（证明当前行为是约定行为，来源是 spec 不是代码）
- [ ] **复现结果**：§1.2 落盘的实际现象（证明现象已被真核实，不是「没看就说不是 bug」）
- [ ] **差异说明**：报障人的「期望」与 spec 约定差在哪、为什么 spec 约定成现在这样

| 误报子类 | 特征 | 处置（提升体验为第一原则） |
|---|---|---|
| **(b) 设计分歧** | 代码对、spec 对，是**人对功能的预期 ≠ 现有设计** | **不简单关 `invalid`**：Issue 评论说明「当前行为符合 `spec:行号` 设计」+ **明确告知「若你认为该设计应改变，这是一个需求、不是 bug」** + 引导 `/wiki-issue-dev`；打 `invalid` + `needs-design-discussion` 标签，`gh issue close --reason "not planned"`（或依用户意愿留开、转需求队列） |
| **(d) 数据/配置问题** | 代码与 spec 均正确，是环境数据脏 / 配置错 / 操作前提不满足；换干净数据或正确配置后现象消失 | Issue 给**具体的数据/配置修复指引**（可复制的 SQL / 配置项 / 正确操作步骤）+ 说明「非代码缺陷」；打 `invalid` 标签，`gh issue close --reason "not planned"` |

**误报关闭口径（与步骤 ⑦「修好关闭五件套」严格区分，不要混用）**：
- 误报关闭**不要求**五件套（无 RCA fix / 无扩散矩阵 / 无 AFTER 上线，因为根本没改代码）；
- 但**强制 spec 证据三件套**齐全，否则**禁止关闭**（缺 spec 证据 = 回 ③ 当「不可判」处理）；
- Issue 评论必须**先肯定报障人**（如「感谢反馈，已按 §1.2 复现并核实现象」）再给结论，语气友好、不指责；
- 关闭后调 `close_issue_claim "$ISSUE_NUM"` 移除 `wip:claude-code` label（释放认领）；步骤 ⑧ 汇报里**如实**写「判定为误报关闭」而非「已修复」。

**ASK 升级（宁可问，不可误关真 bug）**：若 spec 证据不足以让你 100% 确信、或报障人可能掌握你不知道的业务背景 → **先 `AskUserQuestion`**，把 spec 依据原文摆给用户看、请其确认是否接受「非缺陷」结论，再决定 关闭 / 转需求 / 回 RCA。

##### 1.2.5 通过判据

- [ ] 已对 `实际行为` vs `spec 明文期望` 做过**显式比对**，结论三选一（真缺陷 / 误报 / 不可判）
- [ ] 判误报时，spec 证据三件套齐全，且裁决证据**来自 spec 非代码**
- [ ] 设计分歧类已引导转需求、数据配置类已给修复指引，评论均先肯定报障人
- [ ] 证据不足 / spec 无明文时已走 `AskUserQuestion`，**未擅自关闭**

---

#### 1.3 RCA（5 Why + memory 反查双路径）

##### 1.3.A 5 Why

```
症状（现象层）
  └─ Why1: 直接触发原因
      └─ Why2: 编码/设计原因
          └─ Why3: 流程/约束原因
              └─ Why4: 同类是否扩散？
                  └─ 根因（修这里才真治本）
```

每层都要有「代码证据 file:line」或「日志证据」，不许脑补。

##### 1.3.B memory 反查（强制，示例项目 已沉淀 10+ 条经典坑）

```bash
MEM_DIR=~/.claude/projects/<project-slug>/memory
# 用 bug 关键词 grep，命中即可借用
grep -lir "<bug-关键词1>\|<bug-关键词2>" "$MEM_DIR"/*.md
# 也直接看 MEMORY.md 的目录索引
head -30 "$MEM_DIR/MEMORY.md"
```

**已知经典坑速查表**（高命中场景，无需 grep 也建议先眼扫一遍）：

| memory 文件 | 命中场景 |
|---|---|
| `redis_broker_message_loss.md` | Celery stuck pending / 消息丢失 / pod restart 后任务消失 |
| `celery_flush_before_delay_race.md` | `.delay()` 后 worker `SELECT` 比 BFF commit 还快 → silent no-op |
| `orm_field_typo_with_mock_aligned.md` | sweep 死循环 + 单测永远绿（mock 同步写错名） |
| `prod_schema_drift_mechanism.md` | 本地绿线上 500 / DROP 列 migration 不落库 |
| `opensearch_sync_broken.md` | OpenSearch 元数据/检索异常 |
| `sample_pool_status_enum_half_migrated.md` | 样本 pool_status 枚举半迁移历史排障线索 |
| `cd_moving_tag_frozen.md` | Harbor moving tag 冻结、手动 dispatch 留空部旧代码 |
| `test_suite_shared_db_seed_fragility.md` | pytest 共享 session SQLite seed 互踩 |
| `acceptance_jwt_credentials.md` | acceptance/headless 跑测要本地签 token |
| `intranet_dev_checklist.md` / `public_network_dev_fallback.md` | VPN/网络态切换 |
| `debughost_dual_environment.md` | debug-host 仅留 docker-compose 排障 |

**命中**：直接借用既有结论作为 RCA 主线，不重复劳动。
**未命中**：本次结束后**沉淀新 memory**（写入 `$MEM_DIR/<short-slug>.md`，更新 `MEMORY.md` 索引）。

##### 1.3.C 验收标准拆解 → AC 清单（强制硬门 T9 · 治半修，改代码前必产出）

> **为什么在 RCA 后、改代码前**：一个 bug 常含 **多个诉求**，首修只覆盖主诉求、漏次诉求 = 半修，测试验收必然打回（#2691 实测：标题「卡在排队中 **且** 无任何资源不足提示」，首修 #2722 只解决"卡排队"漏了"无提示"，07-01 才补 UI Alert，白白一个 reopen 往返）。把 bug 拆成逐条可验的 AC，让「修什么、验什么、关单对什么」三者对齐。

**拆解规约**：读 Issue **标题 + 正文 + 复现步骤 + 用户期望**，逐句抽出**独立可验证的诉求**，一诉求一条 `AC-N`。特别注意：
- 标题里的「**且 / 并 / 同时 / 还**」「A，B」并列结构 → 拆成多条；
- 「报错」类 bug 常隐含两条：`AC-1 不再报错/不再 5xx` + `AC-2 给出正确的用户可读提示/字段级文案`（#2669 就是漏了后者）；
- 「卡住/无反应」类常隐含：`AC-1 功能恢复` + `AC-2 异常态有明确 UI 提示`（#2691）；
- 展示类常隐含：`AC-1 数据正确` + `AC-2 边界态（空/超长/离线）不崩不压缩`。

产出 **AC 清单表**（写进 `.bugfix-evidence/rca-draft.md`，步骤 ② 迁进 `bugfix-<num>.md`）：

| AC | 诉求（来自 Issue 原文） | 验证口径（怎样算过） | 计划修复位置 | 计划回归用例 |
|----|----------------------|---------------------|-------------|-------------|
| AC-1 | <标题/正文摘录> | <HTTP 200 / toast 出现 / 字段有值…> | `<file:line>` | TC-R01 |
| AC-2 | <隐含次诉求，如"无提示"> | <Alert 文案出现 …> | `<file:line>` | TC-R02 |

> ⚠️ AC 清单在步骤 ④ 之前必须存在；步骤 ⑥ 关单证据里逐条对齐（AC 覆盖矩阵，§6.5）；任一 AC 无「修复 commit + 证据」→ 硬门 T9 拦截，禁止 close。

#### 1.4 严重度 / 影响面定级 → P0/P1/P2

| 等级 | 判据 | 处置 |
|---|---|---|
| **P0** | 生产数据污染 / 安全漏洞 / 全员阻塞 / 资金类异常 | **必问用户**：是否需先回滚生产代码或停服？决定后才进步骤 ② |
| **P1** | 核心功能不可用 / 关键路径 5xx / 数据展示错误但未落库 | 本流水线必修，走完整八步 |
| **P2** | edge case / cosmetic / 极低频偶发 | 建 Follow-up Issue，本次可不修 |

> **不开 P0 简化通道**（用户已确认）：P0 越紧急越要按规程走，跳步会埋更大雷；只把 ASK 环节压缩到 1 分钟内（一次性问完是否回滚 + 是否需补救数据）。

#### 1.5 在 worktree 内落证据 + 草稿

```bash
mkdir -p .bugfix-evidence
# 第 1.2 节的复现矩阵 / 第 1.3 节的 5 Why + memory 反查结论
# 都先写进临时 markdown，步骤 ② 再迁到 spec 目录
$EDITOR .bugfix-evidence/rca-draft.md
```

#### 1.6 步骤 ① 通过判据

- [ ] **已在 worktree 内 / 测试环境 K8s（`DEPLOY_NAMESPACE`）上真实复现 bug**（贴 curl / 截图 / 日志为证，存 `.bugfix-evidence/`）
- [ ] 复现矩阵 7 个维度全部填写（**不许有"未知"**，未知项必须 ASK）
- [ ] RCA 5 Why 链条完整，每层带代码 file:line 或日志证据
- [ ] memory 反查结论已写（命中哪条 / 未命中 → 计划新沉淀）
- [ ] **AC 清单已拆（§1.3.C，硬门 T9）**：标题+正文的每条独立诉求（含隐含次诉求）一条 `AC-N`，每条有「验证口径 + 计划修复位置 + 计划回归用例」，无并列诉求被漏拆
- [ ] 严重度 P0/P1/P2 已定级；P0 已 ASK 用户回滚决策

---

### 步骤 ② Spec 增量 + bugfix-<num>.md 落 RCA（对应 2️⃣）

#### 2.1 判定 spec 路径

bug-fix **几乎永远是增量**（罕见全新模块）：

| 情形 | 路径 | 动作 |
|---|---|---|
| 已有归属 spec | `web/specs/<id>/` 或 `algo/specs/<id>/` | 增量改 + 新增 `bugfix-<num>.md` |
| 跨多个 spec | 取主导模块 spec，其他 spec 仅追加 Changelog | 主 spec 放 bugfix-<num>.md |
| 无归属 spec（罕见） | 临时建 `web/specs/9xx-orphan-fixes/` 集中收容 | bugfix-<num>.md + 最小 spec.md 骨架 |

#### 2.2 bugfix-<num>.md 必含 6 段

```markdown
# Bug Fix #<ISSUE_NUM> — <一句话标题>

> 跟踪 Issue：[#<num>](https://github.com/<GIT_REPO>/issues/<num>)
> 关联 Spec：[spec.md](./spec.md) / [plan.md](./plan.md)
> 严重度：P0/P1/P2
> 完成日期：YYYY-MM-DD

## 1. 复现矩阵
（步骤 1.2 七维度表迁过来）

## 2. 根因分析（RCA）
### 2.1 5 Why
（步骤 1.3.A 链条）
### 2.2 Memory 反查结论
- 命中：[[<memory-file-name>]] 第 N 节，结论：...
- 或未命中，本次将新沉淀：[[<slug>]]（步骤 ⑧ 后补）

## 3. 影响面
- 用户：阻塞 N 人 / 时间窗 X
- 数据：是否污染、是否需补救
- 系统：是否触发雪崩 / 是否消息丢失

## 4. 修复方案
- 主修复：file:line 代码改动摘要 → 为什么这么改、不那么改
- 扩散修复：见第 5 节

## 5. 扩散排查（前置预估）
- grep 模式：`<pattern>`
- 预估命中位置（步骤 ④ 前置 rg 结果）：
  | # | 文件 | 行号 | 是否同模式 | 处置 |
  |---|------|------|-----------|------|
  | 1 | ... | ... | ✅/❌ | 本 PR 修 / Follow-up |
- 步骤 ⑥ 后置核对结论（开发完回填）：见 [§6 关闭证据](#6-关闭证据)

## 6. 回归测试清单
- TC-R01 触发原 bug 精确路径（步骤 ④ TDD 用，必须先红后绿）
- TC-R02 happy path 不退化
- TC-R03 边界条件不触发同模式
（≥3 条，全部加进 testlist.md）

## 7. Changelog
- 主 spec.md 末尾追加一行：「YYYY-MM-DD 修复 #<num>：<一句话>，详见 [bugfix-<num>.md](./bugfix-<num>.md)」
```

#### 2.3 同步改的其他 spec 文件

| 文件 | 动作 |
|---|---|
| `spec.md` | 末尾 `## Changelog` 追加本次 bug 一行（链接到 bugfix-<num>.md） |
| `plan.md` | 若改动了接口/数据模型，行级修订；否则不动 |
| `decisions.md` | 新增一段「为何这样修，不那么修」，引用 bugfix-<num>.md §4 |
| `tasks.md` | 追加修复子任务（≤5 条，每条带 done 判据） |
| `testlist.md` | 追加回归用例（≥3 条，来自 bugfix-<num>.md §6） |

每份 markdown 必须满足 CLAUDE.md §4.3 文档链接规则：
- 出链 ≥3（引用 CLAUDE.md / 关联 spec / 关联代码路径）
- 入链 ≥1（被父 spec / docs/index 链入）
- 无裸 URL（统一 `[文本](路径)` 或 `[[name]]` wiki 链接）

#### 2.4 步骤 ② 通过判据

- [ ] spec 路径已确定（已有 / 跨多 spec / 新建 orphan-fixes）
- [ ] `bugfix-<num>.md` 6 段全填（占位符 `<ISSUE_NUM>` 在步骤 ③ 拿到编号后回填）
- [ ] spec.md / decisions.md / tasks.md / testlist.md 已同步增量
- [ ] markdown 链接巡检过（`grep -nE '\[\[|\]\(' bugfix-*.md`）

> ⚠️ **产物存在性自检（强制，治 #1151 系统性漏写）**：`bugfix-<num>.md` + spec.md Changelog 不是全部。`decisions.md`（为何这样修/不那么修）、`tasks.md`（≤5 子任务带 done 判据）漏写是 #1151 实证的系统性遗漏。改码前跑：
>
> ```bash
> SPEC_DIR="<spec-path>"
> for f in spec.md decisions.md tasks.md testlist.md; do
>   test -f "$SPEC_DIR/$f" && grep -q "${ISSUE_NUM}" "$SPEC_DIR/$f" \
>     && echo "✅ $f 含本 bug 增量" \
>     || echo "⚠️ $SPEC_DIR/$f 缺本 bug（#${ISSUE_NUM}）增量——补齐再进步骤 ③"
> done
> ```
> 若 spec 改了接口/数据模型（如新增 `to_dict()` 字段）→ `plan.md` 也须行级修订（见 §2.3 / §4.5 契约守卫）。

---

### 步骤 ③ Issue 同步 / 补建 + 分支重命名（对应 3️⃣）

#### 3.1 切到对项目仓库有权限的 gh 账号

> 账号名按项目而定（示例项目 用 `zhaod39_example-corp`；其它项目用对 `GIT_REPO` 有写权限的账号），下面用 示例。

```bash
export GH_TOKEN="$(gh auth token --user zhaod39_example-corp)"     # 示例：个人账号 personal-account 对项目仓库（GIT_REPO）会 404
gh auth status                          # 确认 active 是对 GIT_REPO 有权限的账号
```

#### 3.2.A 已有 Issue（输入态 A）→ 评论拉齐 + 补 label

```bash
# 补 bug + 严重度 label（如已有则幂等；_gh_retry 来自 claim-lib，EOF/限流自动退避）
_gh_retry 3 2 -- gh issue edit "$ISSUE_NUM" --repo "$GIT_REPO" \
  --add-label "bug" --add-label "P1"     # 按 1.4 定级调整

# 贴 RCA 摘要评论，方便其他人不打开 spec 也能看根因
_gh_retry 3 2 -- gh issue comment "$ISSUE_NUM" --repo "$GIT_REPO" \
  --body "$(cat <<EOF
## 🔍 RCA 摘要（来自 /wiki-bug-fix 步骤 ①②）

**根因（一句话）**：<…>

**复现路径**：<最小复现 ≤5 步>

**影响面**：<量化：用户数 / 数据 / 时间窗>

**严重度**：P1（不阻塞全员，但核心功能 X 不可用）

**关联 spec**：
- [\`<spec-path>/bugfix-${ISSUE_NUM}.md\`](../blob/main/<spec-path>/bugfix-${ISSUE_NUM}.md)
- [\`<spec-path>/spec.md\`](../blob/main/<spec-path>/spec.md)

**扩散预估**：grep 模式 \`<pattern>\`，初查 N 处同模式位置，本 PR 一并修。

**回归测试**：TC-R01/02/03（见 testlist.md），TDD 模式先红后绿。

**执行轨道**：本 Issue 由 \`/wiki-bug-fix\` 单流水线驱动（spec 已更新 → TDD 开发 → K8s 实测 → review → 关 Issue → 汇报）。
EOF
)"
```

#### 3.2.B 未建 Issue（输入态 B/C）→ 补建

> **📛 Issue 标题命名规范（强制，全 wiki-issue-* 技能统一）**：所有 `gh issue create` 标题一律
> **`[类型][SPEC-XX][XX模块][XX功能]<一句话描述>`** —— 四段方括号紧挨 + 描述，方括号内无空格。
> - **类型** ∈ `需求 / 任务 / BUG / 优化 / 重构 / 文档 / 调研`（bug 补建固定用 `BUG`）
> - **SPEC-XX**：所属 spec 编号（如 `SPEC-018`）；跨 spec 基建 / 纯环境问题对不上 → 填 `SPEC-NA`
> - **XX模块**：业务模块（如 `抽帧`、`样本池`、`训练`）；对不上 → 填 `通用`
> - **XX功能**：具体功能点（如 `进度条`、`列表分页`）；对不上 → 填 `其他`
> - Follow-up 跟进 Issue：类型按性质选（多为 `任务`/`优化`），描述末尾加 `（Follow-up #N）`
> 例：`[BUG][SPEC-018][抽帧][进度条]多分片上传进度条按分片跳冻结`

```bash
SPEC_PATH="web/specs/<id>-<name>"       # 或 algo/specs/...
ISSUE_URL=$(gh issue create \
  --repo "$GIT_REPO" \
  --title "[BUG][SPEC-XX][XX模块][XX功能]<一句话症状>" \
  --label "bug,P1" \
  --body "$(cat <<EOF
## 背景
<2-3 句：用户/系统在何时何场景遇到 bug>

## 症状
<期望 vs 实际，附 .bugfix-evidence/ 关键证据摘录>

## 复现步骤
1. ...
2. ...
3. ...

## 影响面
- 阻塞用户：<量化>
- 数据污染：<是否，如是说明范围>
- 是否需回滚：<是/否；P0 必填>

## 根因（RCA 摘要）
<一句话根因；详见 ${SPEC_PATH}/bugfix-<num>.md>

## 验收判据
- [ ] 复现路径转绿（TC-R01 通过）
- [ ] Happy path 不退化（TC-R02 通过）
- [ ] 扩散排查矩阵覆盖 ≥<N> 处同模式位置
- [ ] Post-push CI 全绿

## 关联 Spec
- [${SPEC_PATH}/bugfix-<num>.md](${SPEC_PATH}/bugfix-<num>.md)
- [${SPEC_PATH}/spec.md](${SPEC_PATH}/spec.md)

## 测试环境
项目测试环境 K8s namespace \`${DEPLOY_NS}\`（见项目《环境档案》DEPLOY_NAMESPACE；内网入口 SLB \`${INTERNAL_SLB}\`，例：10.0.0.10）

## 执行轨道
本 Issue 由 \`/wiki-bug-fix\` 单流水线驱动：复现+RCA → spec → TDD → K8s 实测 → review → 关 Issue → 汇报。
EOF
)")
ISSUE_NUM=$(echo "$ISSUE_URL" | grep -oE '[0-9]+$')
echo "✅ Created Issue #$ISSUE_NUM → $ISSUE_URL"
```

#### 3.3 在 bugfix-<num>.md 顶部回填 Issue 链接

把步骤 ② 留的占位符 `<ISSUE_NUM>` 全部替换为 `$ISSUE_NUM`，并在 spec.md Changelog 中补真实链接。

#### 3.4 临时分支重命名为正式 fix 分支

```bash
SLUG="<kebab-slug，按症状 3-5 词>"
FEAT_BRANCH="fix/issue-${ISSUE_NUM}-${SLUG}"     # 注意是 fix/ 不是 feat/
git branch -m "$PROJ_TMP_BRANCH" "$FEAT_BRANCH"
export PROJ_FEAT_BRANCH="$FEAT_BRANCH"
echo "✅ Branch renamed: $PROJ_TMP_BRANCH → $FEAT_BRANCH"
```

> worktree 目录路径不改（不需要 mv，git 自动识别）；只改分支名，方便步骤 ⑦ 清理时按 `fix/issue-${ISSUE_NUM}-*` 精确锁定。

#### 3.5 步骤 ③ 通过判据

- [ ] Issue 已存在 / 已建，URL 已记 `$ISSUE_URL` / 编号已记 `$ISSUE_NUM`
- [ ] Issue body 含「背景 / 症状 / 复现步骤 / 影响面 / RCA 摘要 / 验收判据 / 关联 Spec」七段
- [ ] Issue 已挂 `bug` + `P0/P1/P2` 两个 label
- [ ] bugfix-<num>.md 已回填真实 Issue 编号
- [ ] 临时分支已重命名为 `fix/issue-${ISSUE_NUM}-<slug>`（`$PROJ_FEAT_BRANCH` 已 export）

---

### 步骤 ④ TDD 单流水线开发（对应 4️⃣）

> **「单流水线」= 顺序执行，禁止把开发任务拆给多个后台 subagent 并行跑**（用户已明确要求）。
> 例外：扩散修复阶段（4.4）若有 ≥5 个真正独立的代码位置（互不引用、互不共享文件），可在「该子步内」按 §0 红线启用局部并行；主流水线步骤 ① 到 ⑧ 之间永远顺序。

#### 4.1 确认仍在本 session 的独占 worktree 内

```bash
cd "$PROJ_WORKTREE"                                       # 强制回到本 session worktree
[ "$(git branch --show-current)" = "$PROJ_FEAT_BRANCH" ] || \
  { echo "❌ 不在 $PROJ_FEAT_BRANCH 上，禁止继续"; exit 1; }
pwd | grep -q "bug-fix-${PROJ_SESSION_ID}" || \
  { echo "❌ 不在本 session worktree，禁止继续"; exit 1; }
```

#### 4.2 **TDD 第一拍：先写回归测试 → 跑一遍红**（强制硬约束 9）

按 bugfix-<num>.md §6 的 TC-R01/02/03 写测试。TC-R01 必须能**精确复现原 bug**：

> **回归测试落 canonical 位置**（统一见 [`_shared/test-traceability-and-assets.md`](../_shared/test-traceability-and-assets.md) §2，不再用私有 `tests/regression/`）：

```bash
# 功能性回归（后端/算法 AC 级）
$EDITOR tests/acceptance/ac/test_bug_${ISSUE_NUM}.py
# 前端 UI bug E2E
$EDITOR tests/acceptance/browser/test_bug_${ISSUE_NUM}.spec.ts
```

跑一遍**期望红**：

```bash
python -m pytest tests/acceptance/ac/test_bug_${ISSUE_NUM}.py -v 2>&1 \
  | tee .bugfix-evidence/tdd-red.txt
# 前端 UI bug：( cd tests/acceptance/browser && npx playwright test test_bug_${ISSUE_NUM}.spec.ts --reporter=line )
```

**全绿 → 红线**：说明测试没真正复现 bug，回 1.2 重新理复现路径，禁止进 4.3。

测试转红后 commit：

```bash
git add tests/acceptance/ac/test_bug_${ISSUE_NUM}.py   # UI bug 则 tests/acceptance/browser/test_bug_${ISSUE_NUM}.spec.ts
git commit -m "test: add failing regression for bug #${ISSUE_NUM}

Reproduces: <一句话>
Expected to fail until fix lands.

Refs #${ISSUE_NUM}"
```

#### 4.3 扩散排查前置 rg（强制硬约束 10）

在改代码前估算同模式位置，决定本 PR 修复范围：

```bash
PATTERN='<bug-pattern，可正则>'
echo "🔍 扩散排查前置 grep（决定本 PR 范围）"
rg -n "$PATTERN" web/ algo/ docs/ \
  | tee .bugfix-evidence/spread-pre.txt

# 把命中位置整理进 bugfix-<num>.md §5 表
$EDITOR <spec-path>/bugfix-${ISSUE_NUM}.md
```

策略：
- 同模式且本 PR 可触达 → 本 PR 一并修（标 ✅ 本 PR）
- 同模式但跨大模块 / 改动量超 200 LOC → 拆 Follow-up Issue（标 🔁 Follow-up）
- 误判（不是同模式）→ 标 ❌ 无关，给一行理由

#### 4.4 **TDD 第二拍：改代码 → 跑一遍绿**

> ⚠️ **硬门 T2：扩散 rg 前置闸**。改任何 fix 代码之前，校验 4.3 的前置 rg 结果已落盘，否则禁止改码（扩散范围必须事前规划，#1151 实证：rg 在改完 5 处代码后才跑 = 事后归纳，若扫到真同模式位置会陷入「已建 PR 后追加大改动」）。
>
> ```bash
> test -s .bugfix-evidence/spread-pre.txt \
>   || { echo "❌ 未见 4.3 扩散前置 rg 结果，禁止改码——先回 4.3 跑 rg 并写进 bugfix §5"; exit 1; }
> ```

按 bugfix-<num>.md §4 的修复方案改代码。**主修复 + 扩散修复同 PR**，分多个 commit：

```bash
# 主修复
$EDITOR <主修复文件>
git add <主修复文件>
git commit -m "fix: <root cause one-liner> (refs #${ISSUE_NUM})

<3-5 行：为什么这么改、不那么改、影响范围>"

# 扩散修复（每个同模式位置一个 commit）
for FILE in $SPREAD_FILES; do
  $EDITOR "$FILE"
  git add "$FILE"
  git commit -m "fix: same pattern in $FILE (refs #${ISSUE_NUM})"
done

# 跑测试期望全绿
python -m pytest tests/acceptance/ac/test_bug_${ISSUE_NUM}.py -v 2>&1 \
  | tee .bugfix-evidence/tdd-green.txt
```

`git log --oneline | head -20` 必须看到 **test commit 在 fix commit 之前**，否则违反硬约束 9。

> ⚠️ **硬门 T3：RCA 深化回写**。若 TDD 第一拍跑红后发现了**比步骤 ① 更深的根因**（典型：#1151 看红才发现「Postgres pg_insert 走 core、绕过 AuditMixin listener → prod 真 NULL，而 SQLite 走 ORM 填 'system'」这一层），必须**回头**把这层补进 `<spec>/bugfix-<num>.md §2 的 5 Why`，并在 Issue 评论追加一句，保持 spec 是 SoT、不留文档债。
>
> 同理：若某个 TC 在第一拍**意外变绿**（测试路径 ≠ bug 路径，如 SQLite 兜底 'system'），不许「看到绿后再改断言让它合理」，而要在 bugfix §6 里把「该路径下的兜底值是可接受的 acceptance criterion」**事前写清**，再调断言。

#### 4.5 静态检查门禁（与 build-images.yml 逐字对齐）

按 [wiki-code-commit](../wiki-code-commit/SKILL.md) §4 跑：

```bash
CHANGED=$(git diff --name-only origin/main...HEAD)
echo "$CHANGED" | grep -q '^web/ui/'      && (cd web/ui && npm run typecheck && npm run lint)
echo "$CHANGED" | grep -q '^web/backend/' && ruff check --select F821 web/backend/src/ web/backend/tests/
echo "$CHANGED" | grep -qE '^algo/'       && (cd algo && uvx ruff check --select F821 src/ shared_kernel/ contexts/data-factory-context/ --exclude contexts/data-factory-context/tests/integration)
```

工具缺失 → 按 wiki-code-commit §4.3 先修工具再复跑；**不许跳过检查直接 push**。

> ⚠️ **API schema 变更契约守卫（#1151 教训）**：若本次改了任何 `to_dict()` / Response 模型 / 跨服务响应字段（BFF↔algo、BFF↔前端），即使是「加字段」向后兼容，也必须：
> - 跑 [[cross_service_contract_field_drift]] 的 ADR-007 守卫（`assert_no_typo_fields` / `test_*_schema.py`）；
> - 同步更新对应 fixture / 前端 interface，防 mock-aligned 永绿陷阱（fixture 自构造带新字段 → CI 全绿但真实路径没测到）；
> - 检查下游消费者是否解包新结构（如前端 `classifyError` 能否读嵌套 `detail.message`）。
> ```bash
> git diff origin/main...HEAD --name-only | grep -qE 'to_dict|schemas?/|models?/|api/.*\.py' \
>   && echo "⚠️ 命中可能的 Response schema 变更——跑契约守卫 + 同步 fixture/前端 interface"
> ```

#### 4.6 合代码到 main（双轨 + rebase + retry，**不动主检出**）

> **判轨以 pre-push hook 为唯一权威**（[[git_cached_removal_rebase_deletes_worktree]]）：`git-track-classify.sh` 对 staged-but-uncommitted 会**假判 `direct`**，只能当预判。正确顺序 = 先 `git commit` → 直接 `git push HEAD:main`：hook 放行即 direct 轨；hook 拦截（输出 `track=pr` / 引用 CLAUDE.md §5.0）即**立刻转 PR 轨，不要反复重试 direct**。

```bash
bash scripts/git-track-classify.sh   # 仅预判参考；最终以 push 时 pre-push hook 判定为准
```

**通用前置：rebase 拿其他 session 已推的 commit**

```bash
git fetch origin main
git rebase origin/main              # 有冲突 → 解，禁止 --strategy=ours/theirs 一把梭
```

**🔒 资源锁（§0.8，条件启用 · 在下面任何 `git push HEAD:main` / `gh pr merge` 之前）**：rebase 干净后、真正合 main **之前**，若 `devlock` 可达且本项目有 `DEPLOY_NAMESPACE` → `python3 ~/.claude/mcp/devlock/cli.py lock-acquire MAINLINE --session "$PROJ_SESSION_ID" --label "wiki-bug-fix #$ISSUE_NUM" --issue $ISSUE_NUM --ttl 900 --wait 3600`（=CI,CD,STAGING，bug 道 v6 锁面不变；Bash `run_in_background` 收口，勿用 MCP 阻塞式 `lock_acquire` 排队），拿到 `granted` 才继续合 main；超时/不可用 → WARN 跳过（降级回退 §0.8 的 `$PROJ_PUSHED_SHA` 乐观行为，不阻断）。释放按 §0.8 分段：CI 绿放 CI → rollout 确认放 CD → 真验只持 STAGING → 步骤 ⑤ 收口全放。心跳由守护进程自动续租(60s/拍,见 §0.8 心跳守护);守护未起时退回每 5min `lock_heartbeat(request_id)`。direct 轨与 pr 轨都在拿到锁后才执行。🌗 squash 标题必须 `fix(` 前缀（=CD bug 道选择器，v6 见 §0.8 提醒）。

**direct 轨（轻量直推，在 worktree 内完成，不切 main）：**

```bash
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
  [ "$i" = 3 ] && { echo "❌ 3 次 push 均被 reject，停下报告用户"; exit 1; }
done
```

> §5.1 适用场景：docs-only / 单文件 hotfix(≤20 LOC) / 配置 yml only(≤50 LOC) / 新建 TD。
> **不要** `git checkout main`，用 `git push HEAD:main` 直接顶推。

**pr 轨（≥2 个代码文件 / 任一 spec.md / cross-module，bug-fix 多数走这条）：**

> **建 PR 前先查同文件 in-flight PR（硬约束 6d）**：两个 session 同改一个文件容易开出双 PR：
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
> 命中且属于本 bug 同语义改动 → 追加 commit 到那个 PR；属于不同 bug 但碰巧改同文件 → 跟那个 PR 的 owner 协调先后顺序，**不要新开重复 PR**。

> 🚫 **硬约束（治本隐患 A/B · #1435 教训）：PR 标题与正文一律禁用 GitHub auto-close 关键字**（`close` / `closes` / `closed` / `fix` / `fixes` / `fixed` / `resolve` / `resolves` / `resolved` 紧跟 `#N` 的任意大小写形式）。
> **为什么**：本流程**验证后置** —— 步骤 ⑤ 的 K8s AFTER 真验必须等 CD 把镜像滚到测试环境之后才能跑，天然在 PR merge **之后**。一旦 PR 带 auto-close 关键字 → GitHub 在 merge 瞬间（步骤 ④.6）就自动关闭 Issue，**早于 AFTER 真验**（硬门 T5）。后果：① 真验失败时 Issue 已是 CLOSED，得 reopen + revert，状态错乱；② Issue「已关」与「实际未验」脱节。**#1435 实测**：PR merge `06:30:04` → Issue 自动关 `06:30:05` → AFTER 真验 + 五件套 `07:25` 才补，中间 **55min** 处于「已关但未验」僵尸窗口，全靠 ScheduleWakeup 续命才补上；若期间进程退出，Issue 会永久卡在「已合 main + 已自动关 + wip 锁残留 + AFTER 未验」。
> **正确姿势**：PR 只用**非关闭性引用**建立关联（`Ref #N` / `关联 #N` / 裸 `#N`，GitHub 不会据此自动关）；**关 Issue 由真验门控** —— 全流程**唯一**的 `gh issue close`（步骤 ⑦.2）必须在硬门 T5（AFTER 已上线且 before/after 相反）通过后才执行。真验失败 → Issue 保持 open → 直接 revert，无需 reopen。

```bash
git push -u origin "$PROJ_FEAT_BRANCH"
export GH_TOKEN="$(gh auth token --user zhaod39_example-corp)"
gh pr create --base main --head "$PROJ_FEAT_BRANCH" \
  --title "fix: <one-line bug summary> (ref #${ISSUE_NUM})" \   # ⚠️ 用 ref 非 close：禁 auto-close（见上方硬约束）
  --body "$(cat <<EOF
## Summary
- 关联 Issue: Ref #${ISSUE_NUM}（**禁用 close/fixes/resolves**：本 PR 不 auto-close，由步骤 ⑦.2 在 AFTER 真验通过后显式 \`gh issue close\`）
- 根因：<一句话>
- 修复：主修复 + 扩散 N 处
- 回归测试：TC-R01/02/03

## Test plan
- [ ] 静态检查全绿（tsc/eslint/ruff F821）
- [ ] 回归测试 TC-R01 在 TDD 第一拍是红的（commit 历史可证）
- [ ] CI build-images + CD Deploy 全绿
- [ ] 测试环境 K8s（${DEPLOY_NS}）触发原 bug 路径转绿（步骤 ⑤）
- [ ] Issue 关闭由步骤 ⑦.2 在 AFTER 真验通过后显式 close（本 PR 不含 auto-close 关键字，merge 不会自动关 Issue）
EOF
)"
PR_NUM=$(gh pr view --json number -q .number)
```

> ⚠️ **`--auto` 口径（统一，避免 #1151 vs #871/#964 不一致）**：本仓 **CI 不跑 PR、PR 无必要检查**（[[dev_auto_openpr_ci_mismatch]]），`--auto` 等于**立即合并**、CI 转为 post-push 验证（步骤 ④.8）。因此：
> - **默认不用 `--auto`**：先本地 `git fetch && git rebase origin/main` 确认干净 + 静态检查全绿（§4.5），再显式 `gh pr merge "$PR_NUM" --squash`。
> - 仅当用户明确允许时才用 `--auto`；用时必须在日志声明「本仓 PR 无必要检查 → auto 立即合并，CI 转 post-push」。
> - 接力模式（步骤 ⓪′）**一律不用 `--auto`**（用户已反复要求）。
>
> ```bash
> # 默认路径（推荐）：本地 rebase 干净 + 静态绿 → 显式合并
> git fetch origin main && git rebase origin/main      # 确认无冲突
> gh pr merge "$PR_NUM" --squash --repo "$GIT_REPO"
> ```

#### 4.7 锁定本 session 推上去的 SHA（防 rollout 竞态）

```bash
git fetch origin main
export PROJ_PUSHED_SHA=$(git rev-parse origin/main)
echo "✅ Locked SHA for this session: $PROJ_PUSHED_SHA"
```

> 若稍后有其他 session 又推了新 commit，`origin/main` 会被超过；步骤 ⑤ 会等到 K8s deploy image tag 出现 `$PROJ_PUSHED_SHA` 才开测——若被覆盖到永远不出现，需回步骤 ④ 重 rebase + push，刷新 `$PROJ_PUSHED_SHA`。
>
> 🔒 **持 §0.8 资源锁时**：本 session 持有 `MAINLINE` 锁期间，其他 session 在「合 main → CD → 验证」段物理排队，`origin/main` **不会**被它们插队覆盖——`$PROJ_PUSHED_SHA` 退化为「CD 是否已滚到本 SHA」的就绪探针，而非竞态防线。**降级无锁时**，`$PROJ_PUSHED_SHA` 的「锁定 + 等滚 + 被覆盖则回 ④」仍是唯一防线，行为不变。

#### 4.8 Post-push CI 验证

```bash
export GH_TOKEN="$(gh auth token --user zhaod39_example-corp)"
gh run list --repo "$GIT_REPO" --commit "$PROJ_PUSHED_SHA" --limit 10 \
  --json databaseId,name,status,conclusion
RUN_ID=$(gh run list --repo "$GIT_REPO" --commit "$PROJ_PUSHED_SHA" --limit 1 --json databaseId -q '.[0].databaseId')
gh run watch "$RUN_ID" --repo "$GIT_REPO" --exit-status
```

- 空结果（改动全在 paths 白名单外）→ Issue 评论注明「未命中 paths，预期不触发」
- CI 红 → 在**当前 worktree** 修复（`git add` / `git commit` / 重走 4.6）；不要起新分支
- 环境抖动 `gh run rerun`；不可恢复先问用户是否 `git revert`

#### 4.9 步骤 ④ 通过判据

- [ ] 始终在 `$PROJ_WORKTREE` 内、`$PROJ_FEAT_BRANCH` 上
- [ ] `git log --oneline` 显示 **test commit 在 fix commit 之前**（TDD 顺序硬约束）
- [ ] 扩散排查前置 rg 已跑，结果写进 bugfix-<num>.md §5
- [ ] 同模式位置「本 PR 修 / Follow-up / 无关」全部分类
- [ ] 静态检查命中模块全绿
- [ ] push main 前已 `git fetch + rebase origin/main`（rebase + retry，未 force-push）
- [ ] **资源锁（§0.8 条件启用）已处理**：`devlock` 可达且本项目有 `DEPLOY_NAMESPACE` → 合 main 前已 `cli.py lock-acquire MAINLINE`（=CI,CD,STAGING）拿到 `granted`，且 CI 绿/rollout 确认两个分段点已按 §0.8 归还 CI/CD；否则已打 WARN 降级跳过（二者择一，禁止静默）。squash 标题为 `fix(` 前缀（CD bug 道路由）
- [ ] `$PROJ_PUSHED_SHA` 已锁定
- [ ] Post-push CI（build-images + cd）按 `$PROJ_PUSHED_SHA` 全绿

---

### 步骤 ⑤ 测试环境 K8s（`DEPLOY_NAMESPACE`）before/after 实测（对应 5️⃣）

> **🔒 持锁验证（§0.8，条件启用）**：本步**只持 `STAGING`**——CI 已在构建绿后、CD 已在 rollout 确认后按 v5 分段归还（见 §0.8 表），**禁止把 CI/CD 满持到验证结束**（2026-06-11 #1585 满持 55 分钟致 4 个开发道 session 排队连环 EXPIRED 的教训）。心跳由守护进程自动续租(60s/拍,见 §0.8 心跳守护);守护未起时退回每 5min `lock_heartbeat(request_id)`，保证 before/after 期间 `staging` 不被其他 session 滚 CD 污染；**本步结束（无论通过 / 失败 / §5.5 回 ④ 修）** 都必须 `lock_release(request_id)` 释放剩余 `STAGING`，让下一个排队 session 递补。降级（无锁）模式下本提示不适用，回退 §5.2 的 `$PROJ_PUSHED_SHA` 等滚机制。

#### 5.1 环境前置检查

```bash
export KUBECONFIG="$KUBECONFIG_PATH"     # kubeconfig 路径见《环境档案》KUBECONFIG_PATH，例：~/.kube/config
nc -z -G 5 10.0.0.30 6443 && echo "VPN OK" \
  || { echo "❌ VPN 未连：提示用户连项目内网 VPN 或走 dev-public 兜底（[[public_network_dev_fallback]]）"; exit 1; }
kubectl auth whoami
kubectl get pods -n "$DEPLOY_NS" --no-headers | head -5
```

#### 5.2 等 CD 把**本 session 推的** SHA 滚到测试 namespace（防多 session 镜像竞态）

> deployment 名见项目《环境档案》`KEY_DEPLOYMENTS`，以下用 示例（`web-bff` / `celery-worker` / `algo`）。

```bash
SHORT_SHA=$(echo "$PROJ_PUSHED_SHA" | cut -c1-7)
echo "⏳ 等 deploy image tag 出现 $SHORT_SHA（本 session push 的 commit）"

kubectl get deploy web-bff celery-worker algo -n "$DEPLOY_NS" \
  -o custom-columns=NAME:.metadata.name,IMAGE:.spec.template.spec.containers[0].image

kubectl rollout status deploy/web-bff -n "$DEPLOY_NS" --timeout=10m
kubectl rollout status deploy/celery-worker -n "$DEPLOY_NS" --timeout=10m

CURR_TAG=$(kubectl get deploy web-bff -n "$DEPLOY_NS" -o jsonpath='{.spec.template.spec.containers[0].image}')
echo "$CURR_TAG" | grep -q "$SHORT_SHA" \
  || { echo "⚠️ deploy 镜像($CURR_TAG)不含本 session SHA($SHORT_SHA)——按下方 deploy-gap SOP 排查"; }
echo "✅ deploy 镜像确认 == 本 session push SHA，可开测"
```

##### 5.2.1 镜像不含本 SHA → deploy-gap 标准排查 SOP（强制，不许即兴探查）

镜像 tag 迟迟不含本 SHA 时，**禁止反复盲目重跑**。按固定四步定位 deploy-gap 类型（[[cd_concurrency_cancel_pathfilter_deploy_gap]] / [[algo_f821_gate_reds_all_cd_builds]] / [[build_images_onpush_paths_subset_of_detect]]）：

```bash
# 1) 本 SHA 是否真在 main
git fetch origin main && git merge-base --is-ancestor "$PROJ_PUSHED_SHA" origin/main \
  && echo "✅ 在 main" || echo "❌ 不在 main——回步骤 ④"

# 2) 本 SHA 的 build-images run 结论
gh run list --repo "$GIT_REPO" --commit "$PROJ_PUSHED_SHA" --workflow build-images.yml \
  --json databaseId,status,conclusion -q '.[0]'

# 3) 关键 build job 是否被 skip / cancel（区分三型）
gh run view <run-id> --repo "$GIT_REPO" --json jobs \
  -q '.jobs[] | {name,status,conclusion}'
```

| 现象 | deploy-gap 类型 | 处置 |
|---|---|---|
| build job = `cancelled` | **并发取消**：后续 push 把本 build 取消了 | 主动推一个命中该模块 paths 的小改 / 兜底 `kubectl set image` |
| build job = `skipped` 且某 quality gate = `failure` | **quality-gate 挟持**：任一红门 → `if:!failure()` skip 全部 build | 判失败门是否与本改动相关；无关的 flaky（如 PG port-forward 超时）→ `gh run rerun --failed`，**持续红**则立专项单（如 #1242）+ 低负载窗口重跑，不无限重试 |
| build-images run 根本未触发（`null`） | **paths 过滤死区**：改动文件不在 `on.push.paths` | 确认改动模块是否该触发；纯落死区 → 推一个命中 paths 的小改激活 |

> 📌 #871/#964 教训：deploy-gap 靠即兴多轮 `gh run list/view` 才确诊，应一步到位按此表判型。若判定为仓库级 flaky（非本代码），在 Issue 记录「修复已合入 + 部署被 flaky CD 冻结 + 专项单号」后即可收口，不重开本 Issue。

#### 5.3 **before/after 对比验证**（bug 专有）

##### 5.3.A before 证据

before 来源（任选 ≥1）：
1. **步骤 ① 复现时**已经留下证据（`.bugfix-evidence/repro-*`）→ 直接引用，不用再跑
2. 若 ① 的 before 证据已被覆盖 / 不充分 → 在测试环境 K8s（`DEPLOY_NAMESPACE`）找一个**未滚到 $PROJ_PUSHED_SHA 之前**的 pod 历史日志当 before

##### 5.3.B after 验证（rollout 后立刻跑）

```bash
# 用与 1.2 完全相同的复现命令重跑（SLB IP=INTERNAL_SLB + NodePort，以下用 示例端口）
curl -sf "http://${INTERNAL_SLB}:30115/api/v1/<endpoint>" \
  -H "Authorization: Bearer <jwt>" \
  | tee .bugfix-evidence/after-curl-${ISSUE_NUM}.json

# 关键：after 必须满足期望行为（与 1.2 实际行为相反）
```

##### 5.3.B′ AFTER 浏览器真验截图（硬门 T8 取证，截图将在 §6.5.0 上传 GitHub 作关闭证据）

> 关单评论必须附「浏览器真实验证」截图（硬门 T8），本步负责**产出** AFTER 截图。**禁止**拿步骤 ① 复现期旧截图、本地 dev server 截图充当 AFTER——AFTER 截图必须产自 §5.2 确认 deploy 镜像含本 SHA **之后**。

**适用三档判定**：

| 档 | 判定条件 | 截图要求 |
|---|---|---|
| Web/UI bug | 改动命中 `web/ui/` 或步骤 ① 复现走了浏览器 | before（① 已有）+ after 两张，**必做** |
| 后端 bug 但报障来自页面操作（有用户可见 UI 面） | 报障人是在页面上看到异常的 | 在页面复跑原报障路径，**至少 after 一张** |
| 完全无 UI 面（worker / CLI / 定时任务 / 纯数据） | 用户永远不会在页面看到该行为 | 豁免截图；用终端 after 证据文件（`.txt`/`.json`/`.log`）代替，关闭评论**注明豁免理由** |

**操作**（按 [`_shared/frontend-browser-testing.md`](../_shared/frontend-browser-testing.md) §2 / §8：先确认 SHA + 强制硬刷新）：

```text
# 前提：§5.2 已确认 deploy image tag 含 $SHORT_SHA
# mcp__playwright__browser_navigate(PROD_URL + 原 bug 页面路径)    ← 无痕/硬刷新，防旧 bundle 假通过
# 重放 ①.2.2 完全相同的操作路径，走到原来出 bug 的那一步
# mcp__playwright__browser_take_screenshot → .bugfix-evidence/after-browser-${ISSUE_NUM}.png
#   （视口截图即可，不要 fullPage——控制体积便于上传；「与 before 相反的期望行为」必须入画：
#    toast 文案 / 字段有值 / 列表非空 / 状态胶囊正确等）
# mcp__playwright__browser_console_messages → 确认无新增 error
```

**通过判据**：肉眼看截图就能分辨「修没修好」（after 画面与 before 截图呈现相反结果），且 after 文件时间戳晚于 rollout 完成时间。

##### 5.3.C 跑回归测试套件

> 回归测试**落 canonical 位置**：功能性 → `tests/acceptance/ac/test_bug_<issue>.py`，UI bug → `tests/acceptance/browser/test_bug_<issue>.spec.ts`（**不再用私有 `tests/regression/`**，与 dev/acceptance 同库，统一见 [`_shared/test-traceability-and-assets.md`](../_shared/test-traceability-and-assets.md) §2）。`.bugfix-evidence/` 仅放复现证据，不是测试资产。

```bash
# SLB IP=INTERNAL_SLB + 各服务 NodePort，以下用 示例端口（BFF 30115 / algo 30120）
BFF_BASE_URL=http://${INTERNAL_SLB}:30115 \
ALGO_BASE_URL=http://${INTERNAL_SLB}:30120 \
  python -m pytest tests/acceptance/ac/test_bug_${ISSUE_NUM}.py -v --tb=short 2>&1 \
  | tee .bugfix-evidence/after-pytest.txt

# 整 testlist 跑一遍，确认 happy path 不退化
python -m pytest tests/acceptance/ tests/api/test_<spec>.py -v --tb=short 2>&1 \
  | tee .bugfix-evidence/after-pytest-full.txt
```

> 同步更新 `tests/acceptance/ac/AC-STATUS.md`，加 `BUG-<issue>` 行（`pass`）；前端 bug 回归必须含 L3 spec 才算 `pass`（见共享文件 §3）。

数据落库 / Pod 日志 / 5xx 排查 → 参考 [wiki-issue-acceptance](../wiki-issue-acceptance/SKILL.md) §3.4 / §3.5。

##### 5.3.D 存量数据回填评估（**修同步/写入逻辑类 bug 必做**，#1151 教训）

若 bug 是「写入/同步逻辑漏写字段」类（修了代码，但**历史数据仍是旧的空/错状态**），仅部署新镜像不会让用户看到效果（新数据才对，老数据照旧）。必须显式评估回填：

- **能在测试环境一次性回填** → 当场触发并验证（如 #1151：清 `last_synced_at` + 跑 full sync，验证 `upserted=N`）。
- **生产/数据量大/需灰度** → 建「数据回填」Follow-up Issue，附**完整可执行 runbook**。

> ⚠️ runbook 红线：命令必须**完整可直接跑**，不留 `<source_id>` 类裸占位符——把「如何查出真实 id」的 SQL 也写进去（#1151 的下一步建议曾留 `<source_id>` 占位无法直接执行）。
> ```bash
> # 示例：先查出真实 id，再触发回填（按项目实际表名/任务名替换）
> SRC_ID=$(kubectl exec -n "$DEPLOY_NS" <pg-pod> -- sh -c \
>   "PGPASSWORD=*** psql -h127.0.0.1 -U <user> -d <db> -tAc \
>   \"SELECT id FROM <table> WHERE <cond> LIMIT 1\"" 2>/dev/null)
> echo "source_id=$SRC_ID"   # 用真实值替换，禁止留占位符交付
> ```

#### 5.4 写测试结果到 `<spec>/acceptance.md`

| # | 测试用例 | 类别 | before | after | 状态 |
|---|---------|------|--------|-------|------|
| TC-R01 | 触发原 bug 精确路径 | Regression | 500/异常 | 200/正常 | ✅ |
| TC-R02 | Happy path 不退化 | Happy path | 200 | 200 | ✅ |
| TC-R03 | 边界条件 | Edge | ... | ... | ✅ |

#### 5.5 ❌ 用例 → 修 → 重新走步骤 ④ → 步骤 ⑤

循环到所有 TC 通过 + `kubectl rollout status` ready + Post-push CI 全绿。

> 🔒 **回 ④ 前先 `lock_release(request_id)`（§0.8，条件启用）**：验证失败需回步骤 ④ 改代码时，改码 + 重跑静态检查 + 重新 CI 往往耗时数十分钟，**不应继续占着 `staging`** 让别人干等。先释放资源锁，回 ④ 重新 `lock_acquire` 重新排队（FIFO 公平，本 session 重新到队尾）。降级无锁时无此步。

#### 5.6 步骤 ⑤ 通过判据

- [ ] 线上 deploy 镜像 tag 含 `$PROJ_PUSHED_SHA` 短哈希
- [ ] **before/after 对比证据齐全**（before 来自 ① 复现 or K8s 旧 pod 日志；after 来自 5.3.B 重跑）
- [ ] **AFTER 浏览器截图已产出（T8 取证，§5.3.B′）**：`.bugfix-evidence/after-browser-${ISSUE_NUM}.png` 已落盘且产自含本 SHA 镜像 + 硬刷新（Web/UI bug 或有 UI 面；完全无 UI 面已记豁免理由 + 终端证据文件）
- [ ] 回归测试 TC-R01 在测试环境 K8s（`DEPLOY_NAMESPACE`）上通过（重要：必须在 K8s 上跑，不只本地 pytest）
- [ ] testlist 全部用例 100% 通过（写入 acceptance.md）
- [ ] 任何 P1 已修复并重测通过（无遗留 P1）
- [ ] **测试可追溯产物已写（#1151 漏写治理）**：`<spec>/acceptance.md` 含 before/after 表 + `tests/acceptance/ac/AC-STATUS.md` 已加 `BUG-<issue>  pass  <日期>` 行（前端 bug 须含 L3 spec 才算 pass）
- [ ] **资源锁已释放（§0.8 条件启用）**：验证收口（通过/失败均算）后已 `lock_release(request_id)` 放掉 `STAGING`，且 CI/CD 早在分段点（CI 绿 / rollout 确认）已归还——未把任何资源带出步骤 ⑤；降级无锁则不适用

---

### 步骤 ⑥ Review + 扩散覆盖矩阵 + Issue 评论关闭证据（对应 6️⃣）

#### 6.1 Code review

- **轻量**：本次改动 ≤200 LOC + 单一模块 → 在本对话内对每个 commit 跑 `git show <sha>` 走 12 步 review 心法（参考 [wiki-code-review](../wiki-code-review/SKILL.md)），P1/P2 分类
- **重型**：≥200 LOC 或跨模块 → 触发 [wiki-code-review](../wiki-code-review/SKILL.md)
- **Mock Boundary Audit（独立条目，示例项目 spec-031 QF-42）**：本轮改动含新增/修改的测试文件时，逐项过 `docs/rules/testing-spec.md` §4 checklist（新 mock 仅许外部三方 OBS/CVAT/GPU；自家契约 fixture 须 openapi.json 生成或真实响应录制；方言敏感断言打 `requires_pg`）

发现 P1 → 回步骤 ④ 修 → 重走 ④ ⑤ ⑥；P2 → 创建 Follow-up Issue（见 6.4）。

> ⚠️ **硬门 T4：红线 label 决策门**。若 Issue/PR 带 `needs-contract-review` / `cross-module` 等**红线 label**，review 中发现**真实缺口**（如 #964：后端返 `{detail:{code,message}}` 嵌套，但前端 `classifyError` 只解包 string detail）：
> - **禁止技能自行判定「pre-existing / 不阻塞」直接合并**——这是用户的决策权，不是执行者的。
> - 必须 `AskUserQuestion` 让用户三选一：① 本 PR 内阻塞修复 ② 合并 + 开 follow-up 跟进 ③ 升级人工复核。决策 + 理由留痕 Issue 评论。
> - 仅当用户选 ②/③ 时才继续合并；选 ① 则回步骤 ④。
> - 📌 #964 教训：技能自行降级红线放行（开了 #1209 就合），下次换实例可能阻塞合并 → 不可复现。

#### 6.2 **扩散排查后置核对**（强制硬约束 10）

把 4.3 的前置 rg 重跑一遍，核对全部修干净：

```bash
PATTERN='<bug-pattern>'
echo "🔍 扩散排查后置 grep（核对修干净）"
rg -n "$PATTERN" web/ algo/ docs/ \
  | tee .bugfix-evidence/spread-post.txt

# 对比前置：理想结果是「后置 = 前置 - 本 PR 已修」
diff .bugfix-evidence/spread-pre.txt .bugfix-evidence/spread-post.txt
```

#### 6.3 产出扩散覆盖矩阵表（强制贴 Issue + bugfix-<num>.md）

| # | 文件 | 行号 | 同模式? | 处置 | 修复 commit | 验证证据 |
|---|------|------|---------|------|------------|----------|
| 1 | web/backend/.../foo.py | 123 | ✅ | 本 PR 修 | abc1234 | tdd-green-backend.txt |
| 2 | algo/.../bar.py | 45 | ✅ | 本 PR 修 | def5678 | tdd-green-algo.txt |
| 3 | docs/FAQ/runbook/baz.md | 12 | ✅ | 同步文档 | ghi9012 | manual review |
| 4 | web/backend/.../legacy.py | 88 | ❌ | 误判（dead code） | — | spread-post.txt 行 N |
| 5 | algo/.../big_refactor.py | 200 | 🔁 | Follow-up #<N> | — | issue 链接 |

矩阵贴进：
1. `<spec>/bugfix-<num>.md` §5 后置核对结论段
2. Issue 关闭证据评论（见 6.5）

#### 6.4 P2 跟进 Issue（如有 🔁 项）

> ⚠️ **开单前去重（#964 教训，强制）**：开 follow-up 前必须先搜索并**读疑似重复单正文**（不能只凭标题判断「无关」）：
> ```bash
> gh issue list --repo "$GIT_REPO" --state all --search "<关键词>" --json number,title,state
> gh issue view <疑似重复号> --repo "$GIT_REPO" --json title,body,state -q '.title,.state'   # 必读正文
> ```
> #964 曾仅凭标题判 #683「无关」就开了 #1209，若 #683 实为同一历史跟踪单即造重复。命中真重复 → 在既有单追评论，不新开。

```bash
gh issue create \
  --repo "$GIT_REPO" \
  --title "[<类型>][SPEC-XX][XX模块][XX功能]<P2 / 大改动扩散修复一句话>（Follow-up #${ISSUE_NUM}）" \
  --body "来自 #${ISSUE_NUM} 扩散排查发现的同模式位置，本次未修：

1. 位置：<file:line>
2. 模式：<pattern>
3. 不修原因：跨大模块 / 改动量超 200 LOC / 改动方案分歧

不阻塞 #${ISSUE_NUM}，本轮先关闭。" \
  --label "enhancement,follow-up"
```

#### 6.5.0 浏览器真验截图上传 GitHub（硬门 T8 · 必须在贴 6.5 评论之前完成）

> **为什么要上传**：`.bugfix-evidence/` 随步骤 ⑦.3 清理 worktree 一起蒸发，本地路径写进评论 = 证据失效。`gh issue comment` 无法附图（GitHub 无公开的 issue 附件上传 API），所以截图统一上传到仓库**长期 `evidence` 分支**（纯 `gh api` Contents API：不碰本地 git、不受 pre-push hook 影响、永不合 main、多 session 不同路径互不冲突），评论里贴 blob 链接 + 内嵌图。evidence 分支的 commit 时间天然可审计「AFTER 截图晚于 rollout」。

```bash
# 前置：ISSUE_NUM / GIT_REPO 必须已 export（或执行前把下文变量直接替换为实际值）
bash -c '
set -euo pipefail
: "${ISSUE_NUM:?需先 export ISSUE_NUM}" "${GIT_REPO:?需先 export GIT_REPO}"
# _gh_retry 来自 claim-lib；子 shell 里没有就降级为直跑（不影响主流程）
source ~/.claude/skills/wiki-issue-claim-lib/scripts/issue-claim.sh 2>/dev/null || true
command -v _gh_retry >/dev/null 2>&1 || _gh_retry() { shift 2; [ "${1:-}" = "--" ] && shift; "$@"; }

EVID_BRANCH="evidence"                  # 长期证据分支（永不合 main，全 bug 共用）
EVID_PREFIX="bugfix-${ISSUE_NUM}"       # 本单证据目录（同 issue 重跑会覆盖更新，幂等）

# 0) evidence 分支不存在 → 从 main HEAD 建一次（git 对象共享，不额外占空间）
if ! gh api "repos/$GIT_REPO/branches/$EVID_BRANCH" >/dev/null 2>&1; then
  MAIN_SHA=$(gh api "repos/$GIT_REPO/git/ref/heads/main" -q .object.sha)
  _gh_retry 3 2 -- gh api -X POST "repos/$GIT_REPO/git/refs" \
    -f ref="refs/heads/$EVID_BRANCH" -f sha="$MAIN_SHA" >/dev/null
  echo "✅ 已创建 evidence 分支"
fi

# 1) 上传函数：$1=本地文件 $2=远端文件名；stdout 输出可贴评论的 raw 链接
upload_evidence() {
  local f="$1" name="$2" path="bugfix-${ISSUE_NUM}/$2"
  local b64=$(mktemp) payload=$(mktemp)
  [ -f "$f" ] || { echo "⚠️ 缺 $f，跳过" >&2; return 1; }
  # >900KB 先降采样（评论加载快 + API body 不膨胀；macOS 用 sips，Linux 换 magick/convert）
  local size; size=$(stat -f%z "$f" 2>/dev/null || stat -c%s "$f")
  if [ "$size" -gt 921600 ] && [[ "$f" == *.png ]]; then sips -Z 1600 "$f" >/dev/null 2>&1 || true; fi
  base64 < "$f" | tr -d "\n" > "$b64"
  # 同路径已存在（§5.5 循环重跑）→ 带旧 sha 走 update，避免 422
  local exist_sha; exist_sha=$(gh api "repos/$GIT_REPO/contents/$path?ref=$EVID_BRANCH" -q .sha 2>/dev/null || true)
  jq -n --arg m "evidence(#${ISSUE_NUM}): $name" --arg b "$EVID_BRANCH" --arg s "$exist_sha" \
    --rawfile c "$b64" \
    "{message:\$m, branch:\$b, content:\$c} + (if \$s != \"\" then {sha:\$s} else {} end)" > "$payload"
  _gh_retry 3 2 -- gh api -X PUT "repos/$GIT_REPO/contents/$path" --input "$payload" >/dev/null
  rm -f "$b64" "$payload"
  echo "https://github.com/$GIT_REPO/blob/$EVID_BRANCH/$path?raw=true"
}

# 2) 逐张上传（文件名按 ① / ⑤.3.B′ 实际落盘名替换；无 UI 面 bug 改传终端证据 txt/json）
BEFORE_IMG_URL=$(upload_evidence ".bugfix-evidence/web-bug-${ISSUE_NUM}-before.png" "before.png" || true)
AFTER_IMG_URL=$(upload_evidence ".bugfix-evidence/after-browser-${ISSUE_NUM}.png" "after.png" || true)
echo "BEFORE_IMG_URL=$BEFORE_IMG_URL"
echo "AFTER_IMG_URL=$AFTER_IMG_URL"
'
```

**红线**：
- ❌ Web/UI bug 的 `AFTER_IMG_URL` 为空就去贴 6.5 评论（先回 §5.3.B′ 补拍）。
- ❌ 把截图传到任何**外部图床/对象存储**（内部系统截图外泄）；只允许传本仓 evidence 分支。
- ❌ 用本地路径 `.bugfix-evidence/xxx.png` 直接写进评论充当「截图证据」（worktree 清理后 404 等效）。
- 📌 私有仓库内嵌 `![img](…?raw=true)` 可能因 camo 代理裂图——**可点击链接是主证据**，内嵌渲染是 best-effort；两者都要写（见 6.5 模板）。
- 📌 console / network 日志（`console.log` / `network.har`）**可选**同函数顺手上传，证据更完整。

#### 6.5 在 Issue 上贴**关闭五件套**评论

```bash
gh issue comment "$ISSUE_NUM" --repo "$GIT_REPO" \
  --body "$(cat <<EOF
## ✅ 完成证据（准备关闭）

**完成日期**：$(date '+%Y-%m-%d')
**线上镜像 SHA**：\`$SHORT_SHA\`
**本地 main commit**：\`$(git rev-parse origin/main | cut -c1-7)\`

### 关闭五件套

| 项 | 结果 |
|---|---|
| 静态检查 | <web tsc+eslint ✅ / algo ruff F821 ✅> |
| K8s rollout | \`kubectl rollout status\` ✅ ready |
| Post-push CI | build-images ✅ + cd ✅ |
| **RCA 摘要** | 根因：<…>；详见 [bugfix-${ISSUE_NUM}.md](../blob/main/<spec-path>/bugfix-${ISSUE_NUM}.md) |
| **扩散覆盖矩阵** | 见下表 |

### Before / After 对比

| | before | after |
|---|--------|-------|
| HTTP 状态 | 500 | 200 |
| 错误信息 | <…> | 正常返回 |
| 数据落库 | <…> | <…> |
| 日志 | <evidence file> | <evidence file> |

### 浏览器真验截图（硬门 T8 · §6.5.0 已上传 evidence 分支）

| 截图 | 链接（主证据，可点击） | 拍摄时机 |
|---|---|---|
| BEFORE（复现态） | [before.png](${BEFORE_IMG_URL}) | 步骤 ① 复现（旧镜像） |
| AFTER（修复态） | [after.png](${AFTER_IMG_URL}) | 步骤 ⑤ rollout 含 \`$SHORT_SHA\` 后硬刷新真实浏览器复跑 |

![before](${BEFORE_IMG_URL})
![after](${AFTER_IMG_URL})

> 内嵌图在私有仓可能不渲染，以上方链接为准。无 UI 面 bug 此节改写为：「无 UI 面，豁免浏览器截图（理由：<…>）；终端 AFTER 证据：[after.txt](<URL>)」。

### 回归测试结果

| # | 测试用例 | 类别 | 结果 | TDD 历史 |
|---|---------|------|------|----------|
| TC-R01 | 触发原 bug 精确路径 | Regression | ✅ | <test sha> 红 → <fix sha> 绿 |
| TC-R02 | Happy path | Happy path | ✅ | — |
| TC-R03 | Edge case | Edge | ✅ | — |
| ... | ... | ... | ✅ | — |

**通过：N / N（100%）**
**验收报告**：\`<spec-path>/acceptance.md\`

### 扩散覆盖矩阵

| # | 文件 | 行号 | 同模式? | 处置 | 修复 commit |
|---|------|------|---------|------|------------|
| 1 | <…> | <…> | ✅ | 本 PR 修 | <sha> |
| ... | ... | ... | ... | ... | ... |
| N | <…> | <…> | 🔁 | Follow-up #<num> | — |

### Code Review 结论

- 模式：<轻量内联 / wiki-code-review 完整>
- P1：0（已全部修复）
- P2：<N 个，已创建 Follow-up Issue #<num> / 无>

### Memory 沉淀

- 命中已有：[[<memory-file>]]（如有）
- 新沉淀：[[<new-slug>]]（步骤 ⑧ 后补，如有）

---

满足关闭五件套 + 回归 100% + Review P1 清零 + 扩散覆盖矩阵完整，可关闭。
EOF
)"
```

#### 6.6 步骤 ⑥ 通过判据

- [ ] Code review 已做（轻量 or wiki-code-review），P1 清零
- [ ] 扩散排查后置 rg 已跑，结果写进 bugfix-<num>.md §5 + Issue 评论
- [ ] 扩散覆盖矩阵 100% 分类（✅本 PR / 🔁 Follow-up / ❌ 误判 三选一，无悬空）
- [ ] **浏览器真验截图已上传 evidence 分支（T8，§6.5.0）**：`BEFORE_IMG_URL` / `AFTER_IMG_URL` 非空（或无 UI 面豁免 + 终端证据已上传），链接已写进关闭证据评论
- [ ] 「关闭证据评论」已贴到 Issue（含五件套 + before/after + **真验截图链接** + 回归 + 扩散矩阵 + Review + Memory 沉淀）
- [ ] P2 Follow-up Issue 已建（如有 🔁 项）

---

### 步骤 ⑦ 关闭 Issue（对应 7️⃣）

#### 7.1 关闭前最后一次自检（缺任一项禁止 close）

**五件套**：
- [ ] 静态检查全绿
- [ ] `kubectl rollout status` ready
- [ ] Post-push CI 全绿（或确认未命中 paths）
- [ ] RCA 摘要已写进 Issue 评论 + bugfix-<num>.md
- [ ] 扩散覆盖矩阵完整（每个位置都有分类与证据）

**bug 专有**：
- [ ] 回归测试 TC-R01 在测试环境 K8s（`DEPLOY_NAMESPACE`）上通过
- [ ] before/after 对比证据完整
- [ ] TDD 顺序（test commit 在 fix commit 之前）git log 可验证
- [ ] P0 数据补救（若需）→ 已建 Follow-up Issue 且关联本 Issue

**硬门 T5：AFTER 已上线（关单前最后一道，不达成禁止 close）**：
- [ ] 部署镜像 tag **已含本 SHA**（`kubectl get deploy ... -o jsonpath='{...image}' | grep $SHORT_SHA`），不是 build/CD 仍 `in_progress`、镜像仍旧 SHA
- [ ] AFTER 已**在该新镜像上**用 before 同路径复跑，且结果与 before 相反（toast 出现 / `created_by` 有值 / 错误码正确等）
- [ ] **例外**：若部署被仓库级 flaky CD 冻结（按 §5.2.1 判型确属非本代码）→ 允许在 Issue 显式记录「修复已合入 main + 部署被 deploy-gap 冻结 + 专项单号 + 后续验证命令」后收口，但**必须在评论里写明 AFTER 尚未在线上验证**，不得伪称已上线

> 📌 #964 教训：曾在 build `in_progress`、镜像仍旧 SHA 时就贴「已上线」证据并关单，AFTER 实际未达成。此门强制区分「已上线验证」与「合入待部署」两种状态。

**硬门 T8：浏览器真验截图已上传（关单证据，缺失禁止 close）**：
- [ ] Web/UI bug（或有 UI 面）：before + after 截图已上传 evidence 分支（§6.5.0），链接已贴进 §6.5 关闭证据评论且可点开
- [ ] AFTER 截图产自含本 SHA 镜像 + 硬刷新的真实浏览器复跑（evidence 分支 commit 时间晚于 rollout 时间，可审计）
- [ ] 完全无 UI 面：关闭证据评论已写明「无 UI 面，豁免浏览器截图（理由）」且终端 AFTER 证据文件已同样上传 evidence 分支并贴链

#### 7.2 关闭

> 🔒 **这是全流程唯一关闭 Issue 的入口**（PR 已禁用 auto-close 关键字，见步骤 ④.6）。前置**硬门 T5**（AFTER 已在含本 SHA 的新镜像上复跑且 before/after 相反）必须先通过，未达成禁止执行本命令——**「关 Issue」由 AFTER 真验门控，不由 PR merge 门控**。真验失败 → Issue 保持 open → 走 revert，不会出现「已关但未修好」的错位（隐患 A/B 教训 · #1435）。

```bash
_gh_retry 3 2 -- gh issue close "$ISSUE_NUM" --repo "$GIT_REPO" \
  --comment "✅ 验收通过（$(date '+%Y-%m-%d')），关闭。

根因：<一句话>
修复：本 PR 含主修复 + 扩散 N 处 + 回归测试 TC-R01/02/03
关闭证据：见上一条评论（五件套 + before/after + 浏览器真验截图 + 扩散矩阵）
真验截图：after → ${AFTER_IMG_URL}（evidence 分支，T8）
本应拦截的门：F<N>[, F<M>…]（F1-F10 可多选；任何围栏都拦不住时填「无 → guard-debt 候选」）
围栏状态：<已建/建设中/缺失/待评 四态选一>"
```

> 📋 **关单回填两字段（spec-031 F10 / #1685 / AD-1685-3，与五件套配对、additive 不改五件套）**：「本应拦截的门」+「围栏状态」两字段独立记录，填写规约与样例关单（#1430）见仓内 SoT [`docs/practices/bug-families.md`「关单回填规约」节](docs/practices/bug-families.md)——先按 RCA 的 `[[*]]` 锚点在 Top20 家族矩阵找行直接抄「对应围栏/guard-debt」「围栏状态」两列，不在 Top20 的按 spec-031 §3.1.2 F1-F10 定义人工判门（典型项目 示例项目 专属字段；其他项目无 spec-031 围栏体系时可省略）。

> **P0 例外**：若 RCA 发现生产数据已污染，**不要直接 close**——先建「数据补救」Follow-up Issue（含补救脚本、灰度计划、回滚预案）并在本 Issue 评论里关联，等数据补救完成后再关本 Issue。

#### 7.3 同步清理（**强校验 + 强清理 + 后验证**，严格限定本 session 产物）

**触发条件（强）**：本 session 代码已落 `origin/main` 且 `gh issue close` 已成功——此时必须**主动**清理，不留尾巴。

**清理范围 = 只本 session 产物**：

| 类别 | 命中规则 | 处置 |
|---|---|---|
| Worktree | 路径含 `bug-fix-${PROJ_SESSION_ID}` | `git worktree remove`（失败 → `--force`） |
| 本地分支 | 名称 == `$PROJ_FEAT_BRANCH`（含 `fix/issue-${ISSUE_NUM}-`） | `git branch -D`（前提：SHA 已在 origin/main） |
| 远程分支 | 仅 PR 轨；远程 `$PROJ_FEAT_BRANCH` 仍存在 | `gh api ... DELETE`（GitHub 通常 PR merge 后自动删） |
| 临时分支 | `wip/bug-fix-${PROJ_SESSION_ID}`（步骤 ③.4 已重命名走，理论上不该存在） | 若残留 → `git branch -D` |

**绝对禁止触碰**：其他 session 的 `.claude/worktrees/*`（含 `agent-*` / `funny-*` / `issue-gen-*` / `bug-fix-<别的-id>`）、其他 session 的 `fix/issue-<别的-num>-*` / `feat/issue-*-*` 分支、历史遗留分支/worktree。

##### 7.3.A 强校验（不通过 → 拒绝清理，不静默兜底）

```bash
set -e
[ -n "$PROJ_PUSHED_SHA" ]   || { echo "❌ \$PROJ_PUSHED_SHA 未设，回步骤 ④.7 重锁定"; exit 1; }
[ -n "$PROJ_SESSION_ID" ]   || { echo "❌ \$PROJ_SESSION_ID 未设"; exit 1; }
[ -n "$PROJ_FEAT_BRANCH" ]  || { echo "❌ \$PROJ_FEAT_BRANCH 未设"; exit 1; }
[ -n "$PROJ_ROOT" ]         || PROJ_ROOT=$(git rev-parse --show-toplevel)   # 项目主检出根（=PROJECT_ROOT），不写死绝对路径

git -C "$PROJ_ROOT" fetch origin main --quiet
ORIGIN_MAIN_SHA=$(git -C "$PROJ_ROOT" rev-parse origin/main)
echo "📍 origin/main = $ORIGIN_MAIN_SHA"
echo "📍 本 session push = $PROJ_PUSHED_SHA"

if ! git -C "$PROJ_ROOT" merge-base --is-ancestor "$PROJ_PUSHED_SHA" "$ORIGIN_MAIN_SHA"; then
  echo "❌ \$PROJ_PUSHED_SHA 不在 origin/main 历史 → 代码未真正合进去，**拒绝清理**"
  echo "   排查：回 ④ rebase + 重 push，刷新 \$PROJ_PUSHED_SHA 再来"
  exit 1
fi
echo "✅ 本 session 代码已落 origin/main，强校验通过 → 可清理"
```

##### 7.3.B 离开 worktree

```bash
ORIG_PWD=$(pwd)
cd "$PROJ_ROOT"
echo "📍 cd → $PROJ_ROOT（离开 worktree 才能安全 remove）"
```

##### 7.3.C 删 worktree（常规 → --force 二段 fallback）

```bash
WT_PATH=$(git worktree list --porcelain \
  | awk -v sid="$PROJ_SESSION_ID" '/^worktree / { if ($2 ~ ("bug-fix-" sid "$")) print $2 }' \
  | head -1)

if [ -z "$WT_PATH" ]; then
  echo "ℹ️  没找到匹配的 worktree，跳过"
else
  echo "🧹 移除 worktree: $WT_PATH"
  if git worktree remove "$WT_PATH" 2>/dev/null; then
    echo "  ✅ 常规 remove 成功"
  else
    echo "  ⚠️  常规 remove 失败，尝试 --force"
    git worktree remove --force "$WT_PATH" && echo "  ✅ --force 成功" \
      || echo "  ❌ --force 仍失败，留给用户手动 \`rm -rf $WT_PATH && git worktree prune\`"
  fi
fi
git worktree prune --quiet
```

##### 7.3.D 删本地分支（用 -D 安全删，前提是 7.3.A 已证 SHA 在 main）

```bash
if git show-ref --quiet "refs/heads/$PROJ_FEAT_BRANCH"; then
  echo "🧹 删本地分支: $PROJ_FEAT_BRANCH"
  git branch -D "$PROJ_FEAT_BRANCH" && echo "  ✅ 已删"
else
  echo "ℹ️  本地分支 $PROJ_FEAT_BRANCH 不存在，跳过"
fi

# 残留临时分支（理论上步骤 ③.4 已 rename 走，兜底清一下）
if git show-ref --quiet "refs/heads/wip/bug-fix-${PROJ_SESSION_ID}"; then
  echo "🧹 删残留临时分支: wip/bug-fix-${PROJ_SESSION_ID}"
  git branch -D "wip/bug-fix-${PROJ_SESSION_ID}"
fi
```

##### 7.3.E 删远程 fix 分支（仅 PR 轨；GitHub 通常 PR merge 后自动删）

```bash
export GH_TOKEN="$(gh auth token --user zhaod39_example-corp)" 2>/dev/null || true
if git ls-remote --heads origin "$PROJ_FEAT_BRANCH" 2>/dev/null | grep -q "$PROJ_FEAT_BRANCH"; then
  echo "🧹 删远程分支: origin/$PROJ_FEAT_BRANCH"
  gh api "/repos/${GIT_REPO}/git/refs/heads/${PROJ_FEAT_BRANCH}" -X DELETE 2>&1 \
    && echo "  ✅ 已删" \
    || echo "  ⚠️  删除失败（PR 可能已自动删），可在 GitHub UI 手删"
else
  echo "ℹ️  远程 $PROJ_FEAT_BRANCH 不存在，跳过"
fi
```

##### 7.3.F 清理后验证（必跑）

```bash
echo ""
echo "━━━━━━━━━━ 清理后状态验证 ━━━━━━━━━━"
PASS=true

if git worktree list | grep -qF "bug-fix-${PROJ_SESSION_ID}"; then
  echo "❌ worktree 残留: $(git worktree list | grep "bug-fix-${PROJ_SESSION_ID}")"
  PASS=false
else
  echo "✅ worktree 已清"
fi

if git show-ref --quiet "refs/heads/$PROJ_FEAT_BRANCH"; then
  echo "❌ 本地分支 $PROJ_FEAT_BRANCH 残留"
  PASS=false
else
  echo "✅ 本地分支已清"
fi

if git ls-remote --heads origin "$PROJ_FEAT_BRANCH" 2>/dev/null | grep -q "$PROJ_FEAT_BRANCH"; then
  echo "❌ 远程 origin/$PROJ_FEAT_BRANCH 残留"
  PASS=false
else
  echo "✅ 远程分支已清（或本就被 GitHub 自动删）"
fi

OTHER_WT_COUNT=$(git worktree list | grep -cE "bug-fix-|issue-gen-" || true)
echo "ℹ️  仍存在的其他 session worktree 数量: $OTHER_WT_COUNT（应保持 ≥0，本次只清了 1 个）"

if $PASS; then
  echo "━━━━━━━━━━ ✅ 清理完成 ━━━━━━━━━━"
else
  echo "━━━━━━━━━━ ⚠️  清理不完全，看上面 ❌ 项 ━━━━━━━━━━"
  exit 1
fi
```

##### 7.3.G 事后补救：如果本步骤被跳过 / 上次没清干净

```bash
cd "$(git rev-parse --show-toplevel)"   # 在项目主检出根目录下跑（不写死绝对路径）
for WT in $(git worktree list --porcelain | awk '/^worktree .*bug-fix-/ { print $2 }'); do
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
    echo "  状态:    ⚠️  分支未在 origin/main → 跳过"
  fi
done
git worktree prune
```

#### 7.4 步骤 ⑦ 通过判据（清理硬约束）

- [ ] Issue 已 closed（`gh issue view "$ISSUE_NUM" --json state` 返回 `CLOSED`）
- [ ] 关闭五件套齐（静态/rollout/CI/RCA/扩散矩阵）
- [ ] **T8 真验截图证据齐**：before/after 截图已上传 evidence 分支且链接在关闭评论里可点开（或无 UI 面豁免已注明 + 终端证据已上传）
- [ ] 回归测试 TC-R01 在测试环境 K8s（`DEPLOY_NAMESPACE`）上通过的证据已贴 Issue
- [ ] TDD 顺序（test commit 在 fix commit 之前）git log 可验证
- [ ] P0 数据补救（若需）→ Follow-up Issue 已建并关联
- [ ] **7.3.A** `$PROJ_PUSHED_SHA` 通过 `merge-base --is-ancestor` 强校验在 `origin/main` 历史里
- [ ] **7.3.C** 本 session worktree（`bug-fix-${PROJ_SESSION_ID}` 精确匹配）已删
- [ ] **7.3.D** 本地 `$PROJ_FEAT_BRANCH` 已用 `-D` 删
- [ ] **7.3.E** 远程 `origin/$PROJ_FEAT_BRANCH` 已删 或 确认 GitHub 自动删过
- [ ] **7.3.F** 三项验证全部 ✅
- [ ] 反向校验：`git worktree list` 显示其他 session 的 worktree **数量未减少**（未误伤）

---

### 步骤 ⑧ 收尾《工作过程总结》+ 可选短版汇报（对应 8️⃣）

**本步骤的主交付 = 产出一份 ~1000 字《工作过程总结》**，格式与七段骨架见统一 SoT：[`_shared/closing-summary.md`](../_shared/closing-summary.md)（**必读必执行**，四技能共用一份）。

- 把本轮「复现+RCA → spec/bugfix-md → Issue → TDD 红绿 → before/after → 扩散修 → 关 Issue」全链路按共享文件七段落成 ~1000 字、表格优先的总结；bug-fix 侧填充侧重见共享文件 §2 表对应行。
- **bug-fix 专有要素必须进表**（融进七段，不要丢）：
  - 「②总览/③过程」含**根因白话一句话**（不扔代码）、**影响面量化**（阻塞 N 用户 / 污染 N 条数据 / 持续 X 小时）。
  - 「④质量门」含 **K8s before/after 一行对比** + 回归 3+ 条 + **扩散覆盖矩阵（修了 N 处）**。
  - 「⑥遗留」含 **Memory 沉淀**（步骤 ① 反查未命中则新写一条 + 更新 MEMORY.md 索引）、生产数据补救项。
- **可选**：之后再附一段 ≤300 字 [wiki-session-report](../wiki-session-report/SKILL.md)（背景/进展/待办）作"群发短版"。

末尾追加一行（与 wiki-code-commit 收口对齐）：

> 本轮已收口，上下文可压缩 —— 直接输入 `/compact` 即可压缩对话、释放上下文。

#### 步骤 ⑧ 通过判据

- [ ] 已产出 ~1000 字（800–1200）《工作过程总结》，七段齐全、表格占主体（按 [`_shared/closing-summary.md`](../_shared/closing-summary.md)）
- [ ] bug-fix 专有要素齐：根因白话 + 影响面量化 + before/after 对比 + 扩散矩阵（修 N 处）
- [ ] Memory 沉淀已落（反查未命中则新写 + 更新索引），写进「遗留与风险」段
- [ ] 「下一步建议」每条有可执行触发；末尾含 `/compact` 提示行

#### 步骤 ⑧.1 释放 session 锁（强制收尾，与 §0.6 配套）

汇报输出后最后一步：
```bash
rm -f .claude/locks/bugfix-${ISSUE_NUM}-${SESSION_SHA8}.lock
echo "🔓 已释放 session 锁: bugfix-${ISSUE_NUM}-${SESSION_SHA8}"
```
不要漏。漏了会产生僵尸锁，4 小时后才会被下次启动清理。

---

## 5. 红线 / 反例（违反任一 = 本次闭环不合格）

### 5.1 通用流程红线（与 wiki-issue-dev 一致）

- ❌ 步骤 ① 跳过 ASK 直接脑补争议项
- ❌ 步骤 ② 只在 Issue body 写根因，不落 bugfix-<num>.md（spec 是 SoT）
- ❌ 步骤 ③ 用对项目仓库无权限的个人账号（例 `personal-account`）操作项目仓库（`GIT_REPO`）（404 / 权限错位）
- ❌ 步骤 ④ 把开发任务拆给后台 subagent 并行跑（单流水线红线）
- ❌ 步骤 ④ 跳过静态检查 / 跳过 Post-push CI 验证就宣告"做完了"
- ❌ 步骤 ④ PR 标题/正文用 `close`/`closes`/`fixes`/`resolves` 等 auto-close 关键字关联 Issue —— merge 瞬间即自动关 Issue，早于步骤 ⑤ AFTER 真验（硬门 T5），真验失败时 Issue 已 CLOSED 需 reopen+revert（隐患 A/B · #1435）；只能用 `Ref #N` 非关闭性引用，关 Issue 唯一入口是步骤 ⑦.2
- ❌ 步骤 ⑤ 在 debug-host 跑 `docker compose` 或本机起 docker 当测试环境
- ❌ 步骤 ⑤ 拿"旧镜像"跑测试谎报通过（必须先确认 deploy image tag 含 `$PROJ_PUSHED_SHA`）
- ❌ 步骤 ⑥ 跳过 Code review 直接关 Issue；或把 P1 当 P2 偷偷创建 Follow-up
- ❌ 步骤 ⑦ 关闭五件套缺任一项就 `gh issue close`
- ❌ 步骤 ⑧ 手写一段散文当汇报，不调用 `wiki-session-report`
- ❌ 中途无谓暂停问"是否继续步骤 X"

### 5.2 多 Session 隔离红线（与 wiki-issue-dev §5.2 一致，把 `feat/` 替换为 `fix/`、`issue-gen-` 替换为 `bug-fix-`）

- ❌ 跳过步骤 ⓪，在主检出上 `git checkout main / merge / commit`
- ❌ 步骤 ④ `git checkout main && git merge` —— 必须 `git push HEAD:main`
- ❌ push 被 reject 后用 `--force` / `--force-with-lease` 顶
- ❌ push 前不 rebase
- ❌ 步骤 ⑤ 用 `git rev-parse origin/main` 当锚点（必须用 `$PROJ_PUSHED_SHA`）
- ❌ 步骤 ⑦ 通配 `grep bug-fix` 清理 —— 必须 `grep -F "bug-fix-${PROJ_SESSION_ID}"`
- ❌ 步骤 ⑦ `git branch -d` / `--merged` 检查 PR 轨（squash merge 误判，必须 `-D`）
- ❌ 步骤 ⑦ 顺手大扫除 `.claude/worktrees/` 里其他 session 的工作树
- ❌ 启动时跳过 §0.6 锁探测（多 session 同 bug 并发的根因）
- ❌ 步骤 ③ 分支命名沿用旧 `fix/issue-<num>-<slug>` 不加 session-sha8（多 session 同 bug 必撞）
- ❌ 步骤 ④.6 建 PR 前不查同文件 in-flight PR（产生同文件双 PR）
- ❌ 步骤 ⑧ 完成后忘记 `rm` 锁文件（僵尸锁污染）

### 5.3 bug 专有红线（违反任一 = 修了个寂寞）

- ❌ **复现不出来就开始改代码**（极易改错地方；先回 ① ASK 用户索取上下文）
- ❌ **复现工具链错配 bug 类型**（Web bug 不开浏览器只 curl / 后台 API bug 不重发请求只读日志 / DB bug 不构造 SQL 只看 ORM 代码 / 算法 bug 不跑 API 或 CLI 只盯日志——见 §4 步骤 ①.2.2 类型 ↔ 工具矩阵）
- ❌ **Web bug 不构造前置数据**（直接打开线上页面靠"碰运气复现"，复现率不稳定就开始 RCA）
- ❌ **跳过 RCA 直接打补丁**（治标不治本，下次同模式再爆）
- ❌ **跳过 memory 反查**（你已沉淀 10+ 条 示例项目 经典坑，再栽一次浪费）
- ❌ **先改代码再补回归测试**（违反 TDD 硬约束 9；test commit 必须在 fix commit 之前）
- ❌ **TDD 第一拍测试一上来就绿**（说明测试没真正复现 bug，禁止进 4.4）
- ❌ **扩散排查只做后置不做前置**（错估范围、临时改 PR scope）
- ❌ **触发原 bug 的精确路径不进 testlist**（下次回归没人能复现）
- ❌ **only 跑本地 pytest，不在测试环境 K8s（`DEPLOY_NAMESPACE`）跑回归**（环境差异是常见漏网）
- ❌ **before/after 对比缺一边**（before 没存证据 / after 没重跑同一命令）
- ❌ **AFTER 截图造假或失位**（拿步骤 ① 复现期旧图充当 AFTER / 本地 dev server 截图冒充测试环境 / rollout 前抢拍——AFTER 截图必须产自含 `$PROJ_PUSHED_SHA` 镜像的线上页面硬刷新复跑，evidence 分支 commit 时间可审计）
- ❌ **截图只留本地不上传**（`.bugfix-evidence/` 随 ⑦.3 清理蒸发；关单评论里写本地路径 = 没有证据，必须走 §6.5.0 上传 evidence 分支贴链接）
- ❌ **P0 bug 不通知用户考虑回滚就埋头修代码**
- ❌ **生产数据已污染却直接 close Issue 不建数据补救 Follow-up**
- ❌ **根因属于 memory 已沉淀过的坑，结束时不更新 memory**（让坑一直坑下去）
- ❌ **扩散覆盖矩阵留悬空位置**（每个 grep 命中必须分类到 ✅本 PR / 🔁 Follow-up / ❌ 误判 之一）

---

## 6. 自检清单（宣告闭环前逐条过，任一不过 → 回到对应步骤）

- [ ] **步骤 ⓪**：已起 `.claude/worktrees/bug-fix-${PROJ_SESSION_ID}` worktree + `wip/bug-fix-${PROJ_SESSION_ID}` 临时分支；三个环境变量已 export
- [ ] **步骤 ①**：已真实复现 bug（贴证据）；复现矩阵 7 维度全填；RCA 5 Why 链完整带证据；memory 反查结论已写；严重度已定级；P0 已 ASK 回滚决策
- [ ] **步骤 ②**：spec 路径已确定；`bugfix-<num>.md` 6 段全填；spec.md/decisions/tasks/testlist 同步增量；markdown 链接巡检过
- [ ] **步骤 ③**：Issue 已存在 / 已建（`$ISSUE_NUM` 已记）；body 含七段；挂 `bug`+`P*` 两 label；bugfix-<num>.md 回填真实编号；临时分支已重命名为 `$PROJ_FEAT_BRANCH`（`fix/issue-` 前缀）
- [ ] **步骤 ④**：始终在 worktree + feature 分支；TDD 顺序（test commit 在 fix commit 之前 git log 可证）；扩散前置 rg 已跑并分类；静态检查全绿；push 前 rebase；用 `git push HEAD:main`；`$PROJ_PUSHED_SHA` 已锁定；CI 全绿
- [ ] **步骤 ⑤**：deploy 镜像 tag 含 `$PROJ_PUSHED_SHA` 短哈希；before/after 对比证据齐；**AFTER 浏览器截图已拍（§5.3.B′，Web/UI bug 或有 UI 面）**；回归 TC-R01 在测试环境 K8s（`DEPLOY_NAMESPACE`）通过；testlist 100% 通过；P1 清零
- [ ] **步骤 ⑥**：Code review P1 清零；扩散后置 rg 已跑；扩散覆盖矩阵无悬空（每位置都有分类与证据）；**真验截图已上传 evidence 分支（§6.5.0，T8）**；关闭证据评论已贴含五件套+before/after+**截图链接**+扩散矩阵+回归+Review+Memory
- [ ] **步骤 ⑦**：关闭五件套齐；**T8 截图证据链接可点开**；Issue 状态 CLOSED；P0 数据补救 Follow-up 已建（如需）；7.3.A 强校验过；7.3.C/D/E 三项删除发起；7.3.F 后验证三项全 ✅；未误伤其他 session
- [ ] **步骤 ⑧**：已调用 `wiki-session-report`；六要素 + 表格 + `/compact` 提示行齐；Memory 新沉淀（若首次发现的坑）
- [ ] **全程**：未中途暂停问"是否继续"；未把任意两步并行；未违反 §5.1 / §5.2 / §5.3 红线任一条；从未在主检出 `$PROJ_ROOT` 上动过 `checkout` / `merge` / `commit` / `push`（只 `git fetch`）

### 6.1 跨 Session 防冲突（与 §0.6 §0.7 配套，必过）

- [ ] §0.6 session 锁已写入 `.claude/locks/bugfix-${ISSUE_NUM}-${SESSION_SHA8}.lock`（拿到 Issue 编号后已重命名 pending→真实编号）
- [ ] 启动时已探测同 bug 活锁，命中已走 `AskUserQuestion` 三选一
- [ ] 步骤 ③ 分支名已用 §0.7 的 `fix/issue-${ISSUE_NUM}/${SESSION_SHA8}/<slug>` 三段格式
- [ ] 步骤 ④ push 前已 `git fetch origin main && git rebase origin/main`（§5.2 已强制，本项作为复核）
- [ ] 步骤 ④.6 PR 轨建 PR 前已 `gh pr list --search` 查同文件 in-flight PR
- [ ] 步骤 ⑧.1 已 `rm` 自己的锁文件（防僵尸锁）

---

## 7. 关联技能

| 技能 | 何时联动 |
|---|---|
| [wiki-issue-dev](../wiki-issue-dev/SKILL.md) | 用户给的是「新需求」不是 bug → 转交 |
| [wiki-prompt-gen](../wiki-prompt-gen/SKILL.md) | 用户只要 prompt 不要执行 → 转交 |
| [wiki-issue-acceptance](../wiki-issue-acceptance/SKILL.md) | 已有 Issue + 实现，只验收 → 转交 |
| [wiki-code-commit](../wiki-code-commit/SKILL.md) | 步骤 ④ 合 main + Post-push CI 验证骨架直接引用 |
| [wiki-code-review](../wiki-code-review/SKILL.md) | 步骤 ⑥ 重型 review 触发 |
| [wiki-issue-review](../wiki-issue-review/SKILL.md) | 步骤 ⑥ 怀疑代码 ↔ spec 不一致时联动 |
| [wiki-session-report](../wiki-session-report/SKILL.md) | 步骤 ⑧ 强制调用 |
| [wiki-dev-public](../wiki-dev-public/SKILL.md) | 步骤 ① / ⑤ VPN 不通需切公网兜底时联动 |
| [wiki-test-gen](../wiki-test-gen/SKILL.md) | 回归测试需 ≥300 用例覆盖时联动 |

---

## 8. 参考资料

- 项目级硬约束 → 项目根 `CLAUDE.md`（调研 / Pre-merge / 双轨 Git / CI / 扩散 / K8s 等小节）
- 双轨判定 → `scripts/git-track-classify.sh`
- CI 质量门禁 SoT → `.github/workflows/build-images.yml`
- 已沉淀的 示例项目 经典坑（memory）：
  - [[redis_broker_message_loss]] — Celery stuck pending / 消息丢失
  - [[celery_flush_before_delay_race]] — flush-before-delay race
  - [[orm_field_typo_with_mock_aligned]] — ORM typo + mock 同步错位
  - [[prod_schema_drift_mechanism]] — 本地绿线上 500
  - [[opensearch_sync_broken]] — OS 元数据同步
  - [[sample_pool_status_enum_half_migrated]] — 枚举半迁移
  - [[cd_moving_tag_frozen]] — Harbor moving tag 冻结
  - [[test_suite_shared_db_seed_fragility]] — pytest 共享 SQLite seed 互踩
  - [[acceptance_jwt_credentials]] — acceptance JWT 签名
  - [[intranet_dev_checklist]] / [[public_network_dev_fallback]] — 网络态切换
  - [[debughost_dual_environment]] — debug-host docker-compose 排障机
