---
name: wiki-issue-acceptance
description: Issue 验收技能（通用研发流，跨 git 项目复用；典型项目：示例项目）。当用户说"验收 issue"、"验收 spec XXX"、"验收功能"、"acceptance test"、"我要进行验收"、"帮我验收"时触发。执行完整的验收闭环：读取 SDD 文档设计**三层测试用例（L1 单元/契约 + L2 API/DB 集成 + L3 E2E UI，普通需求 ≥20 个、EPIC 需求 ≥60 个）** → 用 kubectl 在项目测试环境 K8s（namespace 见项目《环境档案》DEPLOY_NAMESPACE）实跑 + Playwright 打线上地址（项目《环境档案》PROD_URL）验真实用户路径 → **每条结论留原始证据 + 对抗式证伪复核，防"幻觉判过"** → 生成测试报告 → 修复不通过的 P1 问题 → push main 让 CD 自动滚 → 等待 CI/CD 绿 → 重复直到 100% 通过 → **把通过用例固化为 `tests/acceptance/ac` / `tests/acceptance/browser` 可复跑回归资产并更新 AC-STATUS 追溯矩阵** → 关闭 GitHub Issue。**测试环境唯一项目指定的 K8s namespace（见《环境档案》DEPLOY_NAMESPACE），禁止用本机 docker / docker-compose 当测试环境**。
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
- **devlock**：取锁一律 `python3 ~/.claude/mcp/devlock/cli.py lock-acquire <资源逗号表> --session "$SESSION_SHA8" --label "wiki-issue-acceptance #$ISSUE_NUM" --issue $ISSUE_NUM --ttl 900 --wait 3600`（Bash `run_in_background` 收口）；**禁止 MCP 阻塞式 `lock_acquire` 排队**（waiter 会被 reap），MCP 工具仅作 `lock_status` / `lock_heartbeat` / `lock_reap` 等短调用。v6 车道锁面：bug 道（staging 验证）取 `CI,CD,STAGING`，dev 道（dev 验证）取 `CI,CD,DEV`，对称分段释放（CI 绿放 CI → rollout 确认放 CD → 重测只持环境锁）。
<!-- /hardening-rules:v1 -->
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

**兜底（cron 永不依赖本机）**：示例项目 仓库 `.github/workflows/issue-claim-reap.yml` 每 30 min 跑一次，调用 `reap_stale_claims` 等价 Python/Shell 自动回收僵尸 claim。
<!-- /issue-claim:v1 -->



<!-- parallel-default-rule:v1 -->
## 0. 执行模式：**默认串行**（显式覆盖全局并行默认）

**本技能显式 opt-out 全局并行化默认规则**。原因——验收闭环依赖三类不可并行的共享状态：

| 共享资源 | 不可并行原因 |
|---------|------------|
| 测试环境 K8s namespace（`DEPLOY_NAMESPACE`） | 单实例 Postgres / Redis / MinIO，并发 pytest 会污染数据，导致 TC 假阳/假阴 |
| `main` 分支 + CD 流水线 | 多 session 同时 push main → rebase race；CD 只滚最新 commit，前者代码可能未在测试环境跑过即被覆盖（参考 [cd_moving_tag_frozen](~/.claude/projects/<project-slug>/memory/cd_moving_tag_frozen.md)） |
| `kubectl rollout` / `kubectl patch` | 同 Deployment 并发重启 → 后者打断前者 rollout |

**串行执行的硬约束**：

1. 同一时刻整台机器**只能跑一个 wiki-issue-acceptance session**。步骤 0 末尾抢文件锁，进程退出自动释放。
2. 一个 session 内验收多个 Issue 时，**逐个 Issue 走完闭环再切下一个**（步骤 8.2），不要把"修代码 + push" 阶段并发起来。
3. **唯一允许的局部并行**：步骤 1 读 `spec.md / plan.md / testlist.md` 三个独立文件时可起 3 个 Read 并行（无写操作、无状态共享）；步骤 3.4 不同 Deployment 的 `kubectl logs` 读操作可并行。其它阶段一律串行。

> 如果你来自其它技能的"≥5 单元就 worktree 并行"默认——本技能场景下**该默认不适用**，按上面约束执行。

---

## 0.5 Session 锁声明与冲突探测（启动第一步，强制；与步骤 0 的机器级 flock 配套）

**目的**：步骤 0 的 `flock` 只保证"整台机器同时只能跑一个 wiki-issue-acceptance"，但不能识别"两个用户/两台机器并发验同一个 issue"。本节增加**按 issue 区分的文件锁**，让"同一 issue 同时有两个 session 跑验收"显式暴露，便于走 AskUserQuestion 合并/取消/强制并行。

执行顺序（步骤 0 第一件事，先于机器级 flock 之前做）：

1. **生成 session-sha8**：
   ```bash
   SESSION_SHA8=$(echo "$(git config user.email)-$(git rev-parse HEAD)-$(date +%s)" \
     | shasum | cut -c1-8)
   echo "本次 session-sha8 = $SESSION_SHA8"
   ```
2. **生成 target-id**（= `acc-<issue-num>`；用户给的 issue 编号填实，未给前用 `acc-pending`）：
   ```bash
   TARGET_ID="acc-${ISSUE_NUM:-pending}"
   ```
3. **探测同 issue 活锁**：
   ```bash
   mkdir -p .claude/locks
   ls .claude/locks/acceptance-${ISSUE_NUM:-pending}-*.lock 2>/dev/null
   ```
4. **命中已有锁** → 立即 `AskUserQuestion` 三选一：
   - **合并/续作**：放弃本次启动，让用户切到那个 session（避免两个 session 同验一个 issue）
   - **取消**：本次终止
   - **强制并行**：用户明确知晓冲突风险后继续（写入锁备注，冲突责任在用户）
5. **无命中** → 写自己的锁（拿到真正 ISSUE_NUM 后重命名 pending→真实编号）：
   ```bash
   cat > .claude/locks/acceptance-${ISSUE_NUM:-pending}-${SESSION_SHA8}.lock <<EOF
   {
     "session_sha8": "${SESSION_SHA8}",
     "target": "${TARGET_ID}",
     "worktree": "$(pwd)",
     "started": "$(date -Iseconds)",
     "user": "$(git config user.email)"
   }
   EOF
   ```
6. **流程全部完成后**（步骤 9 输出汇总之后）自动删锁：
   ```bash
   rm -f .claude/locks/acceptance-${ISSUE_NUM}-${SESSION_SHA8}.lock
   ```
7. **崩溃恢复**：若锁文件 `started` 距今 > 4 小时 → 视为僵尸锁，直接清理后继续。

> 📌 锁路径：仓库根 `.claude/locks/`，加入 `.gitignore`。本节与步骤 0 的机器级 `flock` 互补：`flock` 防本机互踩 K8s/CD，本节防多机/多人同 issue 双验收。

### 0.5.1 跨 Session 防冲突硬约束（与 §0.5 配套）

| # | 维度 | 要求 |
|---|---|---|
| 1 | Session 锁 | 启动前必须按 §0.5 写 `.claude/locks/acceptance-<issue-num>-<session-sha8>.lock`；命中同 issue 锁必须 `AskUserQuestion` 三选一；步骤 9 末尾必须 `rm` 自己的锁 |
| 2 | 分支命名 | 步骤 6.3 PR 模式开新分支必须用 `fix/acc-<issue-num>/<session-sha8>` 格式（不是原 `acceptance/issue-<num>-<timestamp>`），物理不可能撞名 |
| 3 | Base 同步 | 步骤 6.2 直推 / 6.3 PR 任何 push 前必须 `git fetch origin main && git rebase origin/main`（直推路径已强制，PR 路径也强制） |
| 4 | In-flight PR 查重 | 步骤 6.3 PR 模式建 PR 前必须 `gh pr list --search "involves:@me state:open"` 查同文件 in-flight PR，命中 → 追加 commit 到那个 PR，禁止新开重复 PR |

---

## 0.8 研发资源锁（双车道分段串行 · 条件启用 + 优雅降级）

**目的**：多 session 并行跑各种 wiki-* 研发技能时，「push main → CD 滚验证环境 → 在验证环境上 L1/L2 实跑」整段碰同一套全局共享资源（main 分支 / CI / CD / 单实例验证 namespace 的 PG·Redis·MinIO）。v6 双车道（SoT = 项目根 CLAUDE.md《环境路由规则》，2026-06-11 #1630 方案B，PR#1697）：cd.yml 按 merge commit 首行判道——`fix*`/`hotfix*`/`Revert "fix*`/含 `[lane:bug]` → bug 道滚 `staging`；其余 → dev 道滚 `dev`。本技能是「修 P1 → push main → 等 CD 滚 → 在验证环境重测」的**循环**（步骤 6→7→回 3），尤其怕：A 正在验证环境跑 L1/L2 验收时，B 合 main 滚 CD **覆盖镜像 / 污染 PG·Redis·MinIO 数据** → A 这一轮验收结论假绿/假红。本节用中心化排队工具 **`devlock`**（MySQL FIFO 复合锁，库 `claude_code_dev`，代码 `tools/devlock/`）让这段**全局串行、公平排队、零资源竞争**。

**与既有机制的关系（升级而非替换）**：步骤 0 的机器级 `flock` + §0.5 按 issue 的文件锁挡的是「同机/同 issue 双跑」；本技能步骤 6.4 的 `$SHA` 镜像 tag 校验 + deploy-gap 兜底是**乐观补丁**——只能**事后检测**「我重测时拿到的镜像不含本 SHA」，**挡不住** B 在 A 跑 L1/L2 期间合 main 滚 CD **污染 A 正在验证的环境数据面**。`devlock` 的 v6 复合锁**按验证环境分流**：staging 道（bug 复验 / 已获人工授权）= `{CI,CD,STAGING}`；dev 道（新功能验收，未授权 staging）= `{CI,CD,DEV}`（**不取 STAGING**）——把整段从「乐观重试」升级为**悲观串行 + 分段释放**：A 持锁期间 B 物理排队，CI/CD 段用完即还、重测段只持环境锁，结构性消除污染并缩短他人等待。三者**并存不冲突**——flock/§0.5 防 issue 双取，devlock 防资源竞争，步骤 6.4 的 `$SHA` 校验继续作为「CD 是否已滚到本 SHA」的就绪探针（降级无锁时仍是唯一防线）。

**条件启用（硬约束 · 防污染通用性）**：本技能跨项目复用，**仅当**「`devlock` MCP 可达 **且** 当前项目《环境档案》已声明 `DEPLOY_NAMESPACE`」时启用资源锁；否则（MCP 不可用 / 非本项目 / 申请超时）**打一行 WARN 后跳过锁、回退步骤 6.4 的 `$SHA` 校验 + deploy-gap 乐观行为，绝不阻断主流水线**。

**车道选择器（标题前缀决定锁面）**：本技能修 P1 后 push main，验证环境在 `staging`（bug 复验 / 已获人工授权）→ merge commit 标题用 `fix(` 前缀（或含 `[lane:bug]`），锁面 `CI,CD,STAGING`；验证在 `dev` → **禁用 `fix(`**（用 `feat(`/`chore(`），锁面 `CI,CD,DEV`。前缀与锁面必须同向，否则 CD 滚错环境、锁也白持。

**循环语义（acceptance 特有）**：验收是「修→push→测」每轮重来直到 100% 通过。**每一轮 push main 前都要 `cli.py lock-acquire`（按车道锁面），每一轮验收收口都要 `cli.py lock-release $REQ` 全放**：本轮没通过要回步骤 6 改 P1 再 push 时，先全放（改码+重跑 CI 往往数十分钟，不该继续占着验证环境让别人干等），下一轮 push 前重新 acquire（FIFO 公平，本 session 回队尾重新排队）。轮内**分段释放**：CI 构建绿 → 放 CI；验证环境 rollout 确认 → 放 CD；L1/L2 重测段**只持环境锁**（STAGING 或 DEV）。L3 Playwright 打线上 `$PROD_URL` 不占验证环境，但 L1/L2 kubectl 实跑 + CD 滚占验证环境，锁按「push → CD → L1/L2 重测」整段分段持有。**docs-only 修复（不触发 build-images）：免锁直合。**

| 时机 | 调用 | 说明 |
|---|---|---|
| 步骤 6.2 / 6.3 rebase 后、`git push origin HEAD:main` / `gh pr merge` **前**（每轮循环都要） | `python3 ~/.claude/mcp/devlock/cli.py lock-acquire <锁面> --session "$SESSION_SHA8" --label "wiki-issue-acceptance #$ISSUE_NUM" --issue $ISSUE_NUM --ttl 900 --wait 3600`（Bash `run_in_background` 收口；**禁止 MCP 阻塞式 `lock_acquire` 排队**，MCP 工具仅作短调用）。锁面按验证环境：staging 道 → `CI,CD,STAGING`；dev 道 → `CI,CD,DEV` | 拿到 `granted=true` 才合 main；超时/降级 → WARN 跳过；docs-only 免锁直合 |
| CI 构建绿（分段释放 ①） | `python3 ~/.claude/mcp/devlock/cli.py lock-release $REQ --resources CI` | CI 资源即时归还，后队 CI 段提前递补 |
| 验证环境 rollout 确认（分段释放 ②） | `python3 ~/.claude/mcp/devlock/cli.py lock-release $REQ --resources CD` | CD 资源归还；此后 L1/L2 重测段**只持环境锁**（STAGING 或 DEV） |
| 步骤 6.4→7→3 期间 | 心跳由守护进程自动续租(60s/拍,见 §0.8 心跳守护);守护未起时退回每 5min `lock_heartbeat(request_id)` | 续租防被回收（重测段只剩环境锁，CI/CD 已在分段点归还） |
| 步骤 7 本轮重测收口（**通过/不通过都走**；不通过→回步骤 6 改 P1 前先释放） | `python3 ~/.claude/mcp/devlock/cli.py lock-release $REQ`（全放） | 释放剩余全部资源，触发下一个排队 session 递补；下一轮 push 前重新 acquire |

```text
# 步骤 6.2/6.3 rebase 后、本轮 push main 前（v6 锁面按验证环境分流）：
if devlock_available() and project_has_namespace():
    RES = "CI,CD,STAGING" if 验证环境 == staging else "CI,CD,DEV"   # dev 道不取 STAGING
    req = cli.py lock-acquire $RES --session "$SESSION_SHA8" \
          --label "wiki-issue-acceptance #$ISSUE_NUM" --issue $ISSUE_NUM --ttl 900 --wait 3600
          # Bash run_in_background 收口；禁止 MCP 阻塞式 lock_acquire 排队
    if not granted: WARN("devlock 超时，降级跳过资源锁（回退步骤 6.4 $SHA 校验乐观）"); req = None
else:
    WARN("devlock 不可用/非本项目，跳过资源锁"); req = None
# docs-only 修复（不触发 build-images）：免锁直合，req = None
# 分段释放：CI 构建绿        → cli.py lock-release $REQ --resources CI
#          验证环境 rollout 确认 → cli.py lock-release $REQ --resources CD
#          L1/L2 重测段只持 STAGING（或 DEV）
# 持锁期间: 心跳由守护进程自动续租(60s/拍,见 §0.8 心跳守护);守护未起时退回每 5min: if req: lock_heartbeat(req.request_id)
# 步骤 7 本轮重测收口(通过/不通过都走): if req: cli.py lock-release $REQ   # 全放
#   └ 不通过→回步骤 6 改 P1：先全放，下一轮 push 前重新 acquire（FIFO 回队尾）
```

**🚃 搭车验证（v6 新增 · `merged_unverified` 复验场景）**：环境锁持锁者 = 列车长。本技能复验 `merged_unverified`（代码已在 main、已被部署）时，若当前部署 SHA 已含本 commit（`git merge-base --is-ancestor` 通过）**且**该环境锁有人持有 → 可**搭车只读验证、无需排队**。三禁：**禁触发滚动 / 禁写共享数据态 / 列车长 release 后结论失效**（失效后须自行取锁重验）。详见项目根 CLAUDE.md《环境路由规则》。

**与 `wiki-issue-claim-lib` 的关系（正交并存）**：claim 锁 = **issue 维度**（`wip:claude-code` label 防两 session 验同一 Issue，§0.7 + §0.5 文件锁机制保留不变）；devlock = **资源维度**（防多 session 同时碰同一验证环境）。两者互补，缺一不可，都保留。

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

守护每 60s 续租;session 崩溃 → 守护停跳 → ≤900s 被 reap(死窗 60min→15min);收口**全放**（`cli.py lock-release $REQ` 不带 `--resources`）后守护下一拍自动退出,无需 kill（分段释放 CI/CD 期间守护继续为剩余环境锁续租）。`$REQUEST_ID` 换成 acquire 返回的 request_id。

---

# Issue 验收技能

## 环境信息（测试环境 K8s namespace `DEPLOY_NAMESPACE` 唯一测试环境）

> 下表所有具体值（namespace / SLB IP / 端口 / 线上域名 / kubeconfig）来自**项目《环境档案》**——
> 本项目解析出来即 `staging` / `10.0.0.10` / `https://app.example.com` / `~/.kube/config`，
> 下面以 **示例项目 实际值作示例**展示；换项目时按各自《环境档案》代入。

| 项 | 值（示例，实际见《环境档案》） |
|---|---|
| 入口 | `kubectl` + `export KUBECONFIG=$KUBECONFIG_PATH`（例：`~/.kube/config`，zshrc 默认已 export） |
| 集群 | ACK Pro `T-K8s-Algo`（cn-shenzhen），API `https://10.0.0.30:6443`（**VPC 内网，必须连阿里云 VPN**，外网走 [public_network_dev_fallback](~/.claude/projects/<project-slug>/memory/public_network_dev_fallback.md) 兜底） |
| Namespace | **`$DEPLOY_NS`**（例：`staging`；账号唯一可读写 ns，cluster-scope 资源一律 403） |
| SLB 出口 | LoadBalancer Service `$INTERNAL_SLB`（例：`10.0.0.10`，按端口分配） |
| Web UI | `http://$INTERNAL_SLB:30110`（或主入口 80）/ 线上地址 `$PROD_URL`（例：`https://app.example.com`） |
| **BFF API** | `http://$INTERNAL_SLB:30115`（示例项目 targetPort 38010） |
| **Algo HTTP** | `http://$INTERNAL_SLB:30120`（示例项目 targetPort 38020，GPU 推理直连） |
| MLflow UI | `http://$INTERNAL_SLB:30125` |
| CVAT UI / API | `http://$INTERNAL_SLB:30130` / `:30135` |
| MinIO 控制台 / S3 | `http://$INTERNAL_SLB:30140` / `:30145` |
| Postgres / Redis 排查口 | `$INTERNAL_SLB:30148` / `:30149`（仅 SRE，强密码） |
| 本地仓库 | `$PROJECT_ROOT`（= `git rev-parse --show-toplevel`，技能运行时工作目录即项目根） |
| SDD 位置 | `web/specs/<id>/` 或 `algo/specs/<id>/`（见《环境档案》`SPEC_DIRS`） |
| API 测试 | `tests/api/`（pytest + httpx，指向 K8s LB 入口） |
| 部署目录 | [`deploy/k8s/`](../../deploy/k8s/) — Kustomize base + 测试环境 overlay |

**主要 Deployment 名**：deployment 名见项目《环境档案》`KEY_DEPLOYMENTS`，以下用 示例；用以下命令动态获取（避免硬编码列表过时）：

```bash
kubectl get deploy -n "$DEPLOY_NS" -o name | sed 's|deployment.apps/||'
```

常见的核心 Deployment（示例，以实际查询为准）：`web-ui` `web-bff` `celery-worker` `algo` `postgres` `redis` `minio` `cvat-server` `mlflow` `opensearch`。新增的 deploy 通过上述命令一次性拉到全量列表。

### 🔴 红线（强约束）

1. **本技能禁止使用 `ssh $DEBUG_HOST "docker compose ..."` 跑测试或重启服务**（示例项目 即排障机 `debug-host`）。docker compose 排障机形态只用于线上 bug 历史回顾排障（项目根 `CLAUDE.md` §Bug Diagnosis Workflow），**不是**本验收技能的测试环境。
2. **禁止使用本机 docker / docker-compose / docker-desktop 跑项目服务做验收**。测试只走测试环境 K8s namespace（`$DEPLOY_NS`）。
3. 验证集群入口可达（示例项目 走阿里云 VPN）：`nc -z -G 5 10.0.0.30 6443` 不通 → 直接提示用户连 VPN 或走公网兜底，**不要重试 kubectl**。（探针 IP/端口为 示例项目 集群 API 示例，换项目按实际入口替换。）
4. **不要 `kubectl get nodes`** 或访问其他 namespace —— 一律 403，徒增噪音。
5. 改 Deployment 前先备份：`kubectl get deploy <name> -n "$DEPLOY_NS" -o yaml > backup/<name>-$(date +%Y%m%d-%H%M%S).yaml`。

---

## 验收流程（9 步闭环 + 步骤 7.5 资产固化）

> 本版强化三件事：**① 测试层次**——步骤 2 三层（L1/L2/L3）设计、步骤 3.6 强制 L3 E2E UI；**② 结果可信度**——步骤 3.7 原始证据留存 + 3.8 对抗式证伪复核；**③ 测试资产沉淀**——步骤 7.5 把通过用例固化为 `tests/acceptance` 回归库 + 更新 AC-STATUS 追溯矩阵。三者均为关 Issue 的硬门禁（步骤 8.4）。

### 步骤 0：环境前置检查 + 抢占串行锁

```bash
# KUBECONFIG 取自《环境档案》KUBECONFIG_PATH（例：~/.kube/config，zshrc 已设；老 terminal 需 source ~/.zshrc）
export KUBECONFIG="$KUBECONFIG_PATH"

# 集群入口探针（示例项目 集群 API IP/端口示例，换项目按实际入口替换）
nc -z -G 5 10.0.0.30 6443 && echo "入口可达" || { echo "❌ 集群入口未连，提示用户连 VPN 或走公网兜底"; exit 1; }

# 权限自检
kubectl auth whoami
kubectl get pods -n "$DEPLOY_NS" --no-headers | head -5

# 抢占串行锁（防多 session 互踩测试环境 K8s namespace / main 分支）
LOCK_DIR="$HOME/.claude/locks"
LOCK_FILE="$LOCK_DIR/wiki-issue-acceptance.lock"
mkdir -p "$LOCK_DIR"
exec 200>"$LOCK_FILE"
flock -n 200 || {
  echo "❌ 已有另一个 wiki-issue-acceptance session 在跑（$LOCK_FILE）"
  echo "   如果确认对方已退出，可手动 rm $LOCK_FILE 后重试"
  exit 1
}
echo "$$" >&200   # 写入当前 PID，便于排查
# 进程退出时 flock 自动释放，无需手动 rm
```

任一失败 → 停在这一步，告诉用户原因，不要硬冲后续 kubectl。

---

### 步骤 1：明确验收目标

用户说明要验收的 issue 或 spec 编号后，必须读取（缺则跳过）：

1. `web/specs/<id>/spec.md` 或 `algo/specs/<id>/spec.md` — 功能范围与验收标准
2. `web/specs/<id>/plan.md` — 接口设计、数据模型
3. `web/specs/<id>/testlist.md` — 已有测试清单（可复用）

用一段话确认理解的功能范围，请用户确认后继续。

---

### 步骤 2：设计测试用例（普通 ≥20 条 / EPIC ≥60 条，三层 + 四类双维度覆盖）

基于 SDD 文档设计。每条用例同时带两个维度标签：**测试层次（L1/L2/L3）** 与 **类别（四类）**。

#### 2.0 用例数量梯度（按需求规模定下限，先判规模再设计）

设计前先判定本次验收目标的规模，按下表取**最低用例数**（达不到下限禁止进入步骤 3）：

| 规模 | 判定信号（任一命中即升级） | TC 数量下限 | L3 E2E 下限 |
|------|--------------------------|------------|------------|
| **普通需求** | 单 Issue / 单 spec、单一用户路径、改动收敛在 1–2 个模块 | **≥20 条** | web spec ≥3 条（happy/edge/error 各 ≥1） |
| **EPIC 需求** | 跨多个 spec 或多个子 Issue（带 `epic` 标签 / spec 列出 ≥3 个子功能）、跨 web+algo、贯穿"端到端业务闭环"（如完整训练闭环、样本闭环）、含 ≥3 条独立用户主路径 | **≥60 条** | 每条独立用户主路径 ≥3 条 L3，合计随子功能数线性增长 |

**EPIC 拆分硬约束（防"60 条灌水凑数"）**：

- EPIC 必须**按子功能/子 Issue 切分**，每个子功能各自满足"普通需求 ≥20 条"的微缩版下限（happy/edge/error/业务规则四类齐全 + ≥1 条 L3），再汇总到 ≥60。
- 每个子功能必须能映射到 spec 里的一组 AC（步骤 2.3 追溯列）；映射不到 AC 的子功能要么回 spec 补 AC，要么不计入 EPIC 范围。
- 60 条只是下限，**子功能越多越要按比例上浮**（经验值：每个子功能 ≥15 条，独立主路径越多 L3 越多），不要为了卡 60 而砍真实路径。

#### 2.1 测试层次（测试金字塔，强制分层）

| 层次 | 含义 | 跑法 | 沉淀落点（步骤 7.5） |
|------|------|------|----------------------|
| **L1 单元/契约** | 纯函数 / 响应契约 / 状态机规则，不依赖真实服务 | pytest（httpx MockTransport 等） | `tests/acceptance/unit/` 或 `tests/acceptance/ac/` |
| **L2 API/DB 集成** | 打 K8s SLB 真实 BFF/Algo + 验数据落库 | curl / pytest 打 SLB（`$INTERNAL_SLB`） | `tests/api/` 或 `tests/acceptance/ac/` |
| **L3 E2E UI** | 真实用户路径，浏览器点到底 | **Playwright 打线上地址（`$PROD_URL`）** | `tests/acceptance/browser/*.spec.ts` |

**分层硬约束（防"只验后端、不验用户能不能用"）**：

- **web spec**（`web/specs/<id>/`）：必须含 **≥1 条 L3 E2E**（用户真实操作链路），且 L2 覆盖核心 API。
- **algo spec**（`algo/specs/<id>/`）：必须含 **≥1 条 L2 打 Algo HTTP `:30120` 的真实推理**（不能只 L1 mock）。
- 纯契约/纯算法逻辑可用 L1 补充，但 **L1 不计入"用户可用"证据** —— 单凭 L1 全绿不得判验收通过。

#### 2.2 类别（仍需覆盖四类）

| 类别 | 占比目标 | 说明 |
|------|---------|------|
| Happy path | ≥40% | 核心功能正常流程 |
| Edge case | ≥20% | 边界值、空数据、极端输入 |
| Error handling | ≥20% | 非法参数、无权限、服务不可用 |
| 业务规则校验 | ≥20% | spec 中声明的业务约束 |

#### 2.3 用例格式（每条强制带 层次 / 证据采集 / 证伪点）

```
TC-{N:02d}: {测试标题}
  层次：L1 单元/契约 | L2 API/DB | L3 E2E UI
  类别：Happy / Edge / Error / 业务规则
  追溯：spec.md L{行号} 的 AC-{id}（关联到具体验收标准，供 AC-STATUS 矩阵用）
  前提：{初始状态}
  操作：{HTTP 请求 / kubectl 命令 / Playwright 步骤}
  预期：{期望结果}
  证据采集：{判过时必须留下的原始物证——HTTP 状态码+响应体片段 / DB 查询行 / 截图路径}
  证伪点：{如果这功能其实坏了，会在哪一步暴露——给对抗复核（步骤 3.8）用}
```

> **追溯（可信度关键）**：每条 TC 必须映射到 spec 里的一条具体验收标准（AC）。无法映射到 AC 的"用例"通常是脑补，删掉或回 spec 补 AC。这一列直接喂给步骤 7.5 的 `AC-STATUS.md` 追溯矩阵。

列表展示给用户确认（含层次分布统计：L1/L2/L3 各几条）后，进入步骤 3。

---

### 步骤 3：在测试环境 K8s（namespace `$DEPLOY_NS`）上执行测试

> **三层都要真跑**：3.1–3.5 是 L1/L2（Pod 健康 + API + DB），**3.6 是 L3 E2E UI（web spec 强制）**，3.7 是证据留存，3.8 是对抗式复核。任何一条 ✅ 必须同时满足"有原始证据（3.7）+ 过了对抗复核（3.8）"，否则只能记 ⚠️ 存疑，不得记 ✅。

> 本步骤所有 `kubectl` 命令中的 deployment 名（`web-bff` / `celery-worker` / `algo` / `postgres` 等）见项目《环境档案》`KEY_DEPLOYMENTS`，以下用 **示例**；namespace 一律用 `$DEPLOY_NS`、SLB 用 `$INTERNAL_SLB`、线上地址用 `$PROD_URL`（值来自《环境档案》）。

#### 3.1 确认 Pod 状态

```bash
kubectl get pods -n "$DEPLOY_NS" \
  -o custom-columns=NAME:.metadata.name,STATUS:.status.phase,READY:.status.containerStatuses[*].ready,RESTARTS:.status.containerStatuses[*].restartCount,AGE:.metadata.creationTimestamp \
  | grep -E "web-bff|web-ui|celery-worker|algo|postgres|redis|minio|cvat"
```

`web-bff` / `postgres` / `celery-worker` 必须 `Running` 且 `READY=true`。

#### 3.2 确认线上版本（镜像 SHA → 代码 commit）

```bash
# 当前线上 Deployment 跑的镜像（deploy 名见《环境档案》KEY_DEPLOYMENTS，下用 示例）
kubectl get deploy web-bff celery-worker algo -n "$DEPLOY_NS" \
  -o custom-columns=NAME:.metadata.name,IMAGE:.spec.template.spec.containers[0].image

# 取 image tag 里的 SHA 与本地 main 比对
LOCAL_SHA=$(git rev-parse origin/main | cut -c1-7)
echo "Local main: $LOCAL_SHA"
```

镜像 tag 落后本地 main → 说明 CD 还没滚到当前 commit，把当前测试结果与待发版本区分开告诉用户。

#### 3.3 执行 API 测试

```bash
# 单条 curl —— 通过 SLB 直打 BFF（无需 ssh，本机 VPN 内可达）
curl -sf "http://$INTERNAL_SLB:30115/<endpoint>" | python3 -m json.tool

# pytest 批量
BFF_BASE_URL=http://$INTERNAL_SLB:30115 \
ALGO_BASE_URL=http://$INTERNAL_SLB:30120 \
  python -m pytest tests/api/test_<spec>.py -v --tb=short 2>&1 | tail -60

# 域名走线上 SLB（公网态也可用，会回流到测试环境 K8s）
curl -sk "$PROD_URL/api/v1/<endpoint>" | python3 -m json.tool
```

#### 3.4 遇到 5xx 时查 Pod 日志

```bash
# 最近 100 行（按 Deployment 取，自动定位活跃 Pod）
kubectl logs deploy/web-bff -n "$DEPLOY_NS" --tail 100 --timestamps

# 多容器（带 sidecar）指定 container
kubectl logs deploy/web-bff -n "$DEPLOY_NS" -c web-bff --tail 100

# 实时跟（限 30s 超时，避免阻塞）
timeout 30 kubectl logs -f deploy/web-bff -n "$DEPLOY_NS"

# 错误检索
kubectl logs deploy/web-bff -n "$DEPLOY_NS" --tail 500 2>&1 | grep -iE "error|exception|traceback"

# 上一次崩溃的日志（OOMKilled / CrashLoopBackOff）
kubectl logs deploy/celery-worker -n "$DEPLOY_NS" -p --tail 100
```

#### 3.5 验证数据落库

**测试数据隔离原则**（测试环境 namespace 是与开发共用的环境，不要污染他人数据）：

- **写操作必须打标记**：测试创建的记录用可识别 prefix，如 `acceptance-${ISSUE_NUM}-${TIMESTAMP}-xxx`、`title="[acc-#21] ..."`、bucket key 前缀 `acceptance/<issue>/`。
- **查询用 `WHERE created_at > NOW() - INTERVAL '1 hour'` 收口**，避免误读历史数据导致 TC 误判。
- **测试结束后清理自己写入的数据**（按 prefix DELETE / mc rm），减少环境腐烂。
- **绝不 TRUNCATE 表、绝不 FLUSHALL redis、绝不 `mc rb` bucket**——会害死其它人的开发会话。

```bash
# Postgres：在 Pod 内执行 psql（查询时带时间窗或 prefix）
kubectl exec -n "$DEPLOY_NS" deploy/postgres -- \
  psql -U appuser -d appdb -c "SELECT id, status, created_at FROM <table>
    WHERE created_at > NOW() - INTERVAL '1 hour'
    ORDER BY created_at DESC LIMIT 10;"

# Redis：队列积压 / key 检查（只查询，不 FLUSH）
kubectl exec -n "$DEPLOY_NS" deploy/redis -- redis-cli llen celery
kubectl exec -n "$DEPLOY_NS" deploy/redis -- redis-cli --scan --pattern 'celery-task-meta-*' | head

# MinIO：bucket / object 列表（限定 prefix）
kubectl exec -n "$DEPLOY_NS" deploy/minio -- \
  mc ls local/<bucket-name>/acceptance/ --recursive | head -20
```

> 注意（实战经验，类似环境通用）：[redis_broker_message_loss](~/.claude/projects/<project-slug>/memory/redis_broker_message_loss.md) —— 测试环境 redis 无 AOF/RDB，restart 后队列消息会丢；判断"任务没起"用 `algo_job_id IS NULL` 而不是 `celery_task_id IS NULL`。
> 注意（实战经验）：[prod_schema_drift_mechanism](~/.claude/projects/<project-slug>/memory/prod_schema_drift_mechanism.md) —— 测试环境用 create_all + schema_sync，DROP/改列的 migration 永不落库；本地绿不代表线上 schema 跟得上。
> 注意：[test_suite_shared_db_seed_fragility](~/.claude/projects/<project-slug>/memory/test_suite_shared_db_seed_fragility.md) —— 直接操作 DB 的测试必须 autouse 清理自己的行，否则破坏共享 SQLite seed 的下游计数。

#### 3.6 L3 E2E UI 验证（**web spec 强制；只验后端 = 没验收**）

后端 200 不等于用户能用。web spec 的每条核心用户路径必须在浏览器里真跑一遍。

> **完整标准（入口/工具/八维交互检查表/证据五件套/固化为回归/判过标准）= 统一 SoT：[`_shared/frontend-browser-testing.md`](../_shared/frontend-browser-testing.md)，必读必执行**（dev/acceptance/bug-fix 共用一份）。

**acceptance 侧额外硬约束（高于共享文件的最低线）**：

- web spec **每条核心用户路径**都要有 L3 覆盖（不是只跑 1 条 happy path 就交差）：happy / edge / error 各需 L3 样本。
- 两种执行方式：① Playwright MCP 交互式探查（首轮验收/调试）② 固化的 `tests/acceptance/browser/*.spec.ts`（回归资产，步骤 7.5 落库）。
- 每条 L3 ✅ 必须留**截图 + console/network 无异常确认**写进 acceptance.md 证据列。

#### 3.7 证据留存（每条 ✅ 必须有原始物证 —— 治"幻觉判过"第一层）

判 ✅ 不接受"我看了一下没问题"。每条用例在 acceptance.md 的"证据"列必须贴**可复核的原始物证**之一：

| 层次 | 必留证据 |
|------|---------|
| L1 | 测试函数名 + `pytest` 该用例 PASSED 的输出行 |
| L2 | **真实 HTTP 状态码 + 响应体关键字段片段**（curl 输出），写库类再附 **`psql` 查到的目标行**（带 `created_at` 时间窗证明是本次写的） |
| L3 | **截图路径** + console/network 无异常的确认 |

> 反例（一律视为未验证）：只写"接口正常""功能 OK""返回成功"而无状态码/响应体/行数据；只贴 curl 命令不贴输出；L3 只说"页面能打开"不贴截图。

#### 3.8 对抗式证伪复核（治"幻觉判过"第二层 —— 借鉴 LLM-as-judge 对抗验证）

对**每条已初判 ✅ 的用例**，切换到"怀疑者视角"，按其"证伪点"（步骤 2.3）主动找它其实没过的证据：

1. **反向用例**：happy 判过后，跑一条"本该失败"的反向操作，确认系统**真的会拒/会报错**（而不是无论什么输入都返回 200 → 那其实是没生效）。
2. **证据自洽核对**：响应体字段值 ↔ DB 落库值 ↔ UI 显示值 三处交叉核对一致（典型抓 [catalog_labeled_frames_cache_drift] 这类 cache 与真值偏离）。
3. **防 flaky 二次复跑**：对 P1 核心链路用例**连跑 2 次**，两次都过才算稳定；只过 1 次 → 标记 flaky，记 ⚠️ 并查根因（redis 重启丢消息 [redis_broker_message_loss] / flush-before-delay race [celery_flush_before_delay_race]）。
4. **mock 对齐陷阱**：若该用例依赖 mock/SimpleNamespace，警惕 [orm_field_typo_with_mock_aligned] —— mock 写了同样的错字段名会让单测永远绿；L2 真打一次接口交叉验证。

只有"初判 ✅ + 有原始证据（3.7）+ 过对抗复核（3.8）"三者齐备，才在报告里记 ✅；缺任一记 ⚠️ 存疑并当作未通过处理。

---

### 步骤 4：生成测试报告（**acceptance.md = Single Source of Truth**）

写入 `web/specs/<id>/acceptance.md` 或 `algo/specs/<id>/acceptance.md`（不存在则新建）。

> **重要约定**：acceptance.md 是验收的**唯一事实源**（SoT）。后续步骤 9.1 向 GitHub Issue 贴的 comment **必须从这个文件 cat 出来再贴**，禁止人工二次编辑或在 comment 里加新内容，避免文件 / comment 双写漂移。

填字段前先在 shell 求值（不要在模板里留 `YYYY-MM-DD` 这种占位）：

```bash
DATE=$(date '+%F')                                          # 例：2026-05-27
LOCAL_SHA=$(git rev-parse origin/main | cut -c1-7)          # 例：a1b2c3d
IMAGE_TAG=$(kubectl get deploy web-bff -n "$DEPLOY_NS" \
  -o jsonpath='{.spec.template.spec.containers[0].image}' | awk -F: '{print $NF}')
```

markdown 模板（写入文件前**所有 `<...>` 占位符必须替换为实际值**）：

```markdown
# Spec <ID> 验收记录

**验收日期**：<DATE>
**验收人**：Dev User
**测试环境**：测试环境 K8s namespace（`$DEPLOY_NS`，SLB `$INTERNAL_SLB`；例：`staging` / `10.0.0.10`）
**线上镜像 SHA**：<IMAGE_TAG>
**本地 main commit**：<LOCAL_SHA>
**关联 Issue**：#<issue-number>
**验收结论**：✅ 全部通过 / ⚠️ 有 P1 问题待修复

---

## 测试结果

| # | 测试用例 | 层次 | 类别 | 追溯AC | 状态 | 证据（原始物证） | 复核 | 备注 |
|---|---------|------|------|--------|------|----------------|------|------|
| TC-01 | ... | L2 | Happy | AC-007-06 | ✅ | `200 {"code":0,...}` + psql 行 | 反向✓/复跑2✓ | |
| TC-02 | ... | L3 | Edge | AC-007-08 | ❌ | 截图 evidence/tc02.png | — | 白屏，见问题#1 |

**通过：X / 总计：N**　**层次分布：L1 a / L2 b / L3 c**　**对抗复核通过：Y / ✅ 数**

> 状态只允许三种：✅（初判过 + 有证据 + 过对抗复核）、⚠️（存疑/flaky，按未通过处理）、❌（失败）。

---

## 发现的问题

| # | 描述 | 严重级别 | 建议修复方案 |
|---|------|---------|------------|
| 1 | ... | P1 | ... |

---

## 修复记录

| # | 问题 | 修复 commit | 状态 |
|---|------|------------|------|
```

写入后**立刻 git add + commit**（即使 P1 还没修，先把测试结果落盘），避免 session 中断丢失。

---

### 步骤 5：问题分析 + 严重级别判定

对每个 ❌ 的用例：

1. 查 Pod 日志（步骤 3.4）定位根因
2. 分类：代码 bug / K8s 配置（env/资源/调度）/ 设计缺陷 / 测试本身写错
3. 按下方**严重级别判定矩阵**标 P1 / P2

#### 5.1 严重级别判定矩阵

判定一个问题是 P1（阻塞验收，必须修后才能关 Issue）还是 P2（创 follow-up Issue 跟进），按以下任一条命中即升 P1：

| 维度 | P1 信号（任一命中即 P1） | P2 信号（全部命中才是 P2） |
|------|----------------------|------------------------|
| **数据正确性** | 落库字段错 / 状态机停在错误态 / 数据丢失 | 仅显示文案不准、排序不稳但语义正确 |
| **核心链路** | spec 中标 MVP / 主流程的用例失败 | 边缘 / 非主路径用例失败 |
| **用户可见性** | 用户操作直接报 5xx / 白屏 / 卡死 | 仅在异常输入下出错，且有错误提示 |
| **可恢复性** | 需人工介入清理脏数据 / 重启服务 | 用户重试一次就能成功 |
| **影响面** | 阻塞其他模块联调 / 阻塞下一阶段 | 单点缺陷，不传播 |
| **安全 / 权限** | 越权读写、token 泄漏、SQL 注入 | （所有 security 问题一律 P1，无 P2 通道） |
| **性能** | 接口超时（>30s）/ 内存泄漏 / 进程 OOM | 响应慢但能完成（<30s）、有优化空间 |

**判定流程**：

```
对每个失败 TC：
  ├── 触发"任一 P1 信号"？  → 标 P1，进入步骤 6 修复
  └── 全部命中 P2 信号？      → 标 P2，进入步骤 8.3 创 follow-up
```

> 拿不准时一律按 P1 处理（宁可严格不可漏）。

#### 5.2 常见根因速查

- `5xx` 持续 + `kubectl logs -p` 看到 OOMKilled → 调 `resources.limits.memory`（参考 [redis_broker_message_loss](~/.claude/projects/<project-slug>/memory/redis_broker_message_loss.md) redis 512Mi→2Gi 那次）
- Pod `Pending` 长时间 → 检查 GPU 资源 / nodeSelector 是否被钉到节点 2
- celery 任务卡在 pending → redis 是否被 restart（[redis_broker_message_loss](~/.claude/projects/<project-slug>/memory/redis_broker_message_loss.md)）；或 `db.flush()` 后 `.delay()` race（[celery_flush_before_delay_race](~/.claude/projects/<project-slug>/memory/celery_flush_before_delay_race.md)）
- 字段不存在/类型不匹配 → schema 漂移（[prod_schema_drift_mechanism](~/.claude/projects/<project-slug>/memory/prod_schema_drift_mechanism.md)）
- ORM AttributeError 但单测全绿 → mock 同名 typo（[orm_field_typo_with_mock_aligned](~/.claude/projects/<project-slug>/memory/orm_field_typo_with_mock_aligned.md)）
- OpenSearch 元数据返回空 → 同步 outbox 6 层叠加（[opensearch_sync_broken](~/.claude/projects/<project-slug>/memory/opensearch_sync_broken.md)，PR #324+#326 已修，部署若早于此 SHA 需先升镜像）

---

### 步骤 6：代码修复（P1 问题）

K8s 不是 SSH 拉代码重启的形态——**靠把代码合进 main，让 CD 自动滚**。

> **🔒 资源锁（§0.8，条件启用 · 在下面 6.2 / 6.3 任何 `git push origin HEAD:main` / `gh pr merge` 之前）**：rebase 干净后、本轮真正合 main **之前**，若 `devlock` 可达且本项目有 `DEPLOY_NAMESPACE` → 按验证环境取 v6 车道锁面：staging 道（bug 复验 / 已获人工授权）`python3 ~/.claude/mcp/devlock/cli.py lock-acquire CI,CD,STAGING --session "$SESSION_SHA8" --label "wiki-issue-acceptance #$ISSUE_NUM" --issue $ISSUE_NUM --ttl 900 --wait 3600`；dev 道（新功能验收，未授权 staging）资源表改 `CI,CD,DEV`（**不取 STAGING**）。一律 Bash `run_in_background` 收口，**禁止 MCP 阻塞式 `lock_acquire` 排队**；拿到 `granted=true` 才继续合 main；超时/不可用 → WARN 跳过（降级回退步骤 6.4 的 `$SHA` 镜像 tag 校验 + deploy-gap 乐观行为，不阻断）；docs-only 修复（不触发 build-images）免锁直合。**标题前缀=车道选择器**：验证在 staging → merge commit 标题 `fix(` 前缀（或含 `[lane:bug]`）；验证在 dev → 禁用 `fix(`（用 feat/chore），前缀与锁面必须同向。**分段释放**：CI 构建绿 → `cli.py lock-release $REQ --resources CI`；验证环境 rollout 确认 → `--resources CD`；步骤 7 L1/L2 重测段**只持环境锁**（STAGING 或 DEV）。心跳由守护进程自动续租(60s/拍,见 §0.8 心跳守护);守护未起时退回每 5min `lock_heartbeat(request_id)`。6.2 直推轨与 6.3 PR 轨都在拿到锁后才执行。**验收是循环——每一轮回到步骤 6 重新 push 时都要重新 acquire**（上一轮已在步骤 7 收口处全放）。

#### 6.1 选择合入策略：PR 还是直推 main？

| 场景 | 策略 | 触发条件 |
|------|------|--------|
| **改动 < 50 行 + 仅修测试用例发现的明确 bug + main 无并发 push 风险** | 直推 main（双轨 Git Workflow 的"修复轨"） | 改动小、风险低、链路短；本技能默认走这条 |
| **改动 ≥ 50 行 / 跨 web+algo / 涉及数据库 schema / 涉及配置变更** | 走 feature branch + PR | 改动大、需要 review、需要 CI 跑齐再合 |
| **本验收已锁住整个 main（步骤 0 锁存在）+ 想避免覆盖别人未跑过的 commit** | feature branch + PR + **手动** `gh pr merge --squash`（**禁 `--auto`**，本仓 PR 0 check 会永久挂起，[[dev_auto_openpr_ci_mismatch]]） | 本地全绿后手动串行 merge |

> 完整规则参照项目根 `CLAUDE.md` 的「双轨 Git Workflow」小节。本技能因步骤 0 已抢机器级锁，单 session 内一般可直推；但**遇到不确定的场景一律走 PR**。

#### 6.2 直推 main（小修复）

```bash
# 修代码 + 同步更新 SDD 文档 + 在 acceptance.md「修复记录」表格追加一行
git add -A
git commit -m "fix(<scope>): <一句话> — acceptance #<issue>"
git fetch origin main
git rebase origin/main || { echo "❌ rebase 冲突，必须人工解决再继续"; exit 1; }
git push origin HEAD:main
```

#### 6.3 PR 模式（大修复 / 高风险）

> **分支命名硬约束（§0.5.1 #2）**：必须用 `fix/acc-<issue-num>/<session-sha8>` 三段格式，含本 session 的 sha8，避免多 session 同 issue 撞分支名。
> **Base 同步硬约束（§0.5.1 #3）**：开分支前必须 `git fetch origin main`，并基于 `origin/main` 起分支。
> **In-flight PR 查重硬约束（§0.5.1 #4）**：建 PR 前必须 `gh pr list --search` 查同文件 in-flight PR，命中追加 commit 而非新开。

```bash
ISSUE_NUM=<num>
git fetch origin main
BRANCH="fix/acc-${ISSUE_NUM}/${SESSION_SHA8}"
git checkout -b "$BRANCH" origin/main
# 修代码 + commit
git push -u origin "$BRANCH"

# 同文件 in-flight PR 检查
export GH_TOKEN="$(gh auth token --user zhaod39_example-corp)"
PLANNED_FILES=( $(git diff --name-only origin/main...HEAD) )
for FILE in "${PLANNED_FILES[@]}"; do
  CONFLICT_PR=$(gh pr list --state open --search "involves:@me" --repo "$GIT_REPO" \
    --json number,headRefName,files \
    --jq ".[] | select(.files[].path == \"$FILE\") | .number" | head -1)
  if [ -n "$CONFLICT_PR" ]; then
    echo "⚠️ $FILE 已被 PR #$CONFLICT_PR 占用 → 应追加 commit 到该 PR 而非新开"
  fi
done

gh pr create --repo "$GIT_REPO" \
  --base main --head "$BRANCH" \
  --title "[acceptance/#${ISSUE_NUM}] <一句话>" \
  --body "Acceptance fix for #${ISSUE_NUM}. Closes #${ISSUE_NUM} when merged + CD green."

# ❌ 禁用 --auto：本仓 PR 永远 0 check，--auto 会永久挂起（[[dev_auto_openpr_ci_mismatch]]）
# ✅ 本地静态检查全绿 + rebase 干净 → 手动 squash merge（多 session 串行靠步骤 0 的 main 锁 + 手动顺序，不靠 GitHub merge queue）
gh pr merge "$PR_NUM" --squash --delete-branch --repo "$GIT_REPO"
```

> **判轨以 pre-push hook 为权威**：6.2 直推前 `git-track-classify.sh` 仅预判，被 hook 拦截即转本 6.3 PR 轨。**禁 force-push（仓库规则）**：6.2 rebase 冲突宁可停下人工解、**不得 `git push --force`**；6.3 PR 开出后若需 rebase，用 `gh pr update-branch "$PR_NUM"` 或推新分支重开 PR，不要 force-push 更新 PR 分支。

#### 6.4 跟 CD rollout（deploy-gap 兜底，不死等本 SHA）

```bash
SHA=$(git rev-parse origin/main | cut -c1-7)
echo "Target SHA: $SHA, waiting for CD..."

# 等 build-images + cd workflow（通常 5–10 min）
export GH_TOKEN="$(gh auth token --user zhaod39_example-corp)"   # 不再用全局 gh auth switch
RUN_ID=$(gh run list --repo "$GIT_REPO" \
  --commit "$(git rev-parse origin/main)" --limit 1 \
  --json databaseId -q '.[0].databaseId')
gh run watch --repo "$GIT_REPO" "$RUN_ID"

# rollout 跟到 ready（deploy 名见《环境档案》KEY_DEPLOYMENTS，按本次实际改的 deploy 替换；用步骤 3.1 命令查全量）
kubectl rollout status deploy/web-bff -n "$DEPLOY_NS" --timeout=5m
kubectl rollout status deploy/celery-worker -n "$DEPLOY_NS" --timeout=5m

# 校验当前 deploy 镜像 tag 是否含本 SHA（防拿旧镜像重测谎报通过）
CURR_TAG=$(kubectl get deploy web-bff -n "$DEPLOY_NS" -o jsonpath='{.spec.template.spec.containers[0].image}')
echo "$CURR_TAG" | grep -q "$SHA" && echo "✅ 已滚到本 SHA，重测" || echo "⚠️ 未含本 SHA → 走下方 deploy-gap 兜底"
```

> ⚠️ **deploy-gap 兜底（不死等本 SHA，[[cd_concurrency_cancel_pathfilter_deploy_gap]]）**：本验收是「push main → 等 CD 滚 → 重测」循环，多 session 洪峰下本 SHA 的 build 常被并发取消、或本改动不在镜像 build 路径过滤内致镜像不重建——**别无限 `gh run rerun`/dispatch + 死等**。`gh run watch` 上限 2–3 轮 / ~15min，超时即判：①`gh run list --commit <run>` 看是被 `cancelled` 还是真 build；②只要本 commit 在 `origin/main` 线性历史（`git merge-base --is-ancestor`）、且某含它的**后代 commit 的绿 build 已部署**（部署镜像 SHA 是本 commit 后代），即视为部署面就绪，可重测；③确需本镜像新版而兜底也不成立时才 `gh workflow run` 强制重建 / `kubectl set image`（最多重试 2 次）。在验收报告里**如实标注**部署面用的是「本 SHA 绿」还是「后代 build `<SHA>` 落地」，不得谎报。
>
> 🔴 **`build-images` 绿 ≠ 镜像滚上环境（必须继续 watch 链式 CD Deploy · [_shared/dev-acceptance-gate.md](../_shared/dev-acceptance-gate.md) §5，2026-07-01 用户指令）**：`build-images(CI)` 绿只代表镜像造出来了，**它会链式触发 `cd.yml`（CD Deploy, `workflow_run` 事件）滚 K8s，`deploy-k8s` 成功后才跑 `post-deploy-acceptance-gate`**。上面 `gh run watch` 只等到了 build，**不许就此宣告部署完成**——必须按 §5 用 `SHA=<本次 mergeSha>` 定位链式 CD Deploy run 并 `gh run watch` 到终态，再按 §5② 分看两个 job：`deploy-k8s` 失败=**镜像根本没滚上去**（环境仍旧镜像）→ 按 §5③ 归因（A 本轮代码/清单引入 Pod 起不来 → 回步骤 6 修好重走，**禁当环境抖动无脑 rerun**；B 环境抖动 → `gh run rerun --failed`；C 并发顶替 → ancestor 兜底）；`post-deploy-acceptance-gate` 门红 → 按 §2 `failure` 行处置（**验收类=不判过，必须修 P1 或 revert 后重验**）。CD run 到 `success`/`skipped`/兜底成立**之前**别把「rollout 确认」当成立、别放 CD 段锁（§5④）。
>
> **禁止**在 K8s pod 内 `git pull && docker compose up`——K8s 部署单元是 image，代码靠 CI 打镜像。

#### 6.5 仅 manifest 改动（资源 / env，不动镜像）

```bash
kubectl get deploy <name> -n "$DEPLOY_NS" -o yaml > backup/<name>-$(date +%Y%m%d-%H%M%S).yaml
kubectl patch deploy <name> -n "$DEPLOY_NS" --type=strategic --patch '<patch>'
kubectl rollout status deploy/<name> -n "$DEPLOY_NS"
```

> manifest 临时 patch 也要把改动落到 `deploy/k8s/` 的 overlay 里并 commit，否则下次 CD 会被旧 manifest 覆盖（CD apply Kustomize）。

---

### 步骤 6.x：CI / CD 失败的回滚

> **触发条件**：步骤 6 push 后，步骤 7 探测到 CI 红或 `kubectl rollout status` 超时时，跳到本步处置；完成后回到步骤 5 重新分析根因，再走一次步骤 6。

如果步骤 6 推送后 CI 红或 rollout 卡死，按以下顺序处置（**禁止在恐慌中乱重启**）：

#### 6.x.1 先判定失败属于哪一类

```bash
export GH_TOKEN="$(gh auth token --user zhaod39_example-corp)"
SHA=$(git rev-parse origin/main)
gh run list --repo "$GIT_REPO" --commit "$SHA" --limit 5 \
  --json name,status,conclusion,databaseId
```

| 现象 | 处置 |
|------|------|
| `build-images` 红 | 镜像没产出，K8s 仍跑旧版本，**无需回滚**——修编译错误，重新 push。 |
| `cd` 红，rollout 没动 | `kubectl set image` 没成功，K8s 仍跑旧版本，**无需回滚**——看 cd workflow 日志修 manifest / 权限问题。 |
| `cd` 绿但 `kubectl rollout status` 超时 | 新镜像启动失败（CrashLoopBackOff），K8s 旧 ReplicaSet 还在跑 → 见 6.x.2 主动回滚。 |
| `cd` 绿、rollout 完成，但 API 实测仍 5xx | 新代码有 runtime bug → 见 6.x.2 主动回滚 + 在 main 上推 revert commit。 |

#### 6.x.2 主动回滚 Deployment

```bash
# 查看历史
kubectl rollout history deploy/<name> -n "$DEPLOY_NS"

# 回滚到上一版本（K8s 内置）
kubectl rollout undo deploy/<name> -n "$DEPLOY_NS"
kubectl rollout status deploy/<name> -n "$DEPLOY_NS" --timeout=3m

# 验证：API 恢复
curl -sf "http://$INTERNAL_SLB:30115/healthz"
```

#### 6.x.3 在 main 上推 revert commit

```bash
BAD_SHA=$(git rev-parse origin/main)
git revert --no-edit "$BAD_SHA"
git push origin HEAD:main
# 等 CD 把 main 镜像 tag 也滚回去（避免下次 deploy 又拿到坏镜像）
```

> 回滚后**继续步骤 5**（问题分析），分析坏 commit 的根因，再走一次步骤 6 修正。**不要**直接关 Issue。

---

### 步骤 7：等待 CI/CD 并重测

> **🔒 持锁验证（§0.8，条件启用 · v6 分段持有）**：本步重测段**只持步骤 6 取得的环境锁**（staging 道=STAGING / dev 道=DEV；CI、CD 已在「CI 构建绿」「rollout 确认」两个分段点 `cli.py lock-release $REQ --resources CI|CD` 归还）——心跳由守护进程自动续租(60s/拍,见 §0.8 心跳守护);守护未起时退回每 5min `lock_heartbeat(request_id)`（CD 滚 + L1/L2 在验证环境重测可能数十分钟），保证重测期间验证环境不被其他 session 滚 CD 污染。**本轮重测收口（无论通过 / 不通过）都必须 `cli.py lock-release $REQ` 全放**，让下一个排队 session 递补：
> - **本轮全部 TC 通过** → 全放后进入步骤 7.5 / 8；
> - **本轮仍有 TC 不通过** → **先 `cli.py lock-release $REQ` 全放再回步骤 6 改 P1**（改码 + 重跑静态检查 + 重新 CI 往往数十分钟，不应继续占着验证环境让别人干等）；下一轮回步骤 6 push 前重新 `cli.py lock-acquire`（FIFO 公平，本 session 回队尾重新排队；释放/重排队语义与旧版一致）。
>
> 降级（无锁）模式下本提示不适用，回退步骤 6.4 的 `$SHA` 镜像 tag 校验 + deploy-gap 等滚机制。L3 Playwright 打线上 `$PROD_URL` 不占验证环境，重测段已只持环境锁，无须再为 L3 拆分。复验 `merged_unverified` 且他人持环境锁时可走 §0.8 🚃 搭车只读验证（三禁约束）。

```bash
export GH_TOKEN="$(gh auth token --user zhaod39_example-corp)"   # 不切全局 active user
SHA=$(git rev-parse origin/main)
gh run list --repo "$GIT_REPO" --commit "$SHA" --limit 5 \
  --json databaseId,name,status,conclusion
```

确认 **`build-images` 绿 → 链式 `cd.yml`（CD Deploy）watch 到终态、`deploy-k8s` + `post-deploy-acceptance-gate` 两 job 结论已按 [_shared/dev-acceptance-gate.md](../_shared/dev-acceptance-gate.md) §5 处置**且 `kubectl rollout status` 完成后 → **重新执行步骤 3**。
> ⚠️ **判据（凌驾「build 绿就走」）**：验收通过必须含「CD 部署真生效」的证据——**光 `build-images` 绿不算部署完成**。CD Deploy run 未到终态、或 `deploy-k8s` 红（镜像没滚上去，环境仍旧镜像）、或 `post-deploy-acceptance-gate` 门红（检出回归 / L2 noop），本轮**一律不判过**：`deploy-k8s` 红按 §5③ 归因回修（A 代码问题回步骤 6 修 / B rerun / C ancestor 兜底），门红按 §2 修 P1 或 revert 后重验。
直到所有 TC 通过**且本 SHA 的 CD Deploy run 已到 `success`/兜底成立、gate 门绿** → 更新 acceptance.md 结论为「✅ 全部通过」、commit acceptance.md → 进入步骤 8。

任何一个 workflow 红 / rollout 卡死 / **CD Deploy run 判红** → 跳到 **步骤 6.x** 处置（CD `deploy-k8s`/gate 失败先按 §5 归因，代码问题回步骤 6 修，环境抖动才 rerun）。

---

### 步骤 7.5：测试资产沉淀（固化为可复跑回归库，**关 Issue 前强制**）

> **目的（解决"跑完即弃"）**：本次设计的 TC 不能只活在 acceptance.md 里。**每条通过的用例必须落成仓库里可被 CI 反复执行的测试文件**，成为防止本功能日后被改坏的回归护栏。验收的产出物 = 测试报告 **+ 一组新增/更新的 committed 测试**。

> **统一 SoT**：测试落点（canonical 目录）、AC-ID 追溯链、`AC-STATUS.md` 矩阵格式现已抽到 [`_shared/test-traceability-and-assets.md`](../_shared/test-traceability-and-assets.md)，**dev / bug-fix 也喂同一个矩阵**。本步骤 7.5.1-7.5.4 是该共享标准在验收侧的落地；下表与共享文件 §2 一致，如需改约定**只改共享文件**。
> acceptance 设计 TC 时**直接继承 dev/上游 testlist 的 AC-ID**，不重新定义验收标准（P2.4）。

#### 7.5.1 按层次落到既有约定目录（不要另造路径）

| 层次 | 落点（沿用仓库现有约定） | 命名 | 形态 |
|------|------------------------|------|------|
| L1 单元/契约 | `tests/acceptance/unit/` | `test_<feature>.py` | pytest（httpx MockTransport） |
| L2 API/DB（AC 级） | `tests/acceptance/ac/` | `test_AC_<spec>_<NN>.py` | pytest，**首行 docstring 写 SoT `spec.md L<行号>`** |
| L2 API（通用） | `tests/api/` | `test_<feature>.py` | pytest + httpx 打 SLB |
| L3 E2E UI | `tests/acceptance/browser/` | `test_<feature>_<spec>.spec.ts` | Playwright（base URL 取自《环境档案》`PROD_URL`） |

AC 级测试文件风格对齐既有样例（`tests/acceptance/ac/test_AC_007_08.py`）：

```python
"""AC-<spec>-<NN>: <一句话验收标准>.

SoT: web/specs/<id>/spec.md L<行号>
"""
import pytest, httpx

def test_T_AC_<spec>_<NN>_happy_<scenario>():
    """happy: ..."""
    ...

def test_T_AC_<spec>_<NN>_negative_<scenario>():
    """negative: 反向证伪（对抗复核固化版）"""
    ...
```

> **对抗式复核要固化进测试**：步骤 3.8 的反向用例必须写成 `*_negative_*` 测试函数，让"系统真的会拒绝非法操作"成为永久回归断言，而不是只在本次手动验过。

#### 7.5.2 更新 AC 追溯矩阵（覆盖率可见化）

更新 `tests/acceptance/ac/AC-STATUS.md`（不存在则按既有格式新建）—— 每条 AC 一行，状态 `pass / partial / fail`，让"哪些验收标准有测试覆盖、覆盖到什么程度"一目了然：

```markdown
| AC ID | Area | Status |
| --- | --- | --- |
| AC-<spec>-<NN> | <功能简述> | pass |
```

#### 7.5.3 落库本地自检 + 入 CI

```bash
# 新增/改动的测试本地先全绿（L1/L2）
python -m pytest tests/acceptance/ac/test_AC_<spec>_<NN>.py tests/acceptance/unit/ -q 2>&1 | tail -30
# L3 Playwright
( cd tests/acceptance/browser && npx playwright test <feature>_<spec>.spec.ts --reporter=line 2>&1 | tail -20 )

# 随 P1 修复同一轨提交（直推 main 或并进 PR，遵步骤 6.1 策略）
git add tests/acceptance/ac/ tests/acceptance/unit/ tests/api/ tests/acceptance/browser/ tests/acceptance/ac/AC-STATUS.md
git commit -m "test(acc-#<issue>): 固化 spec-<id> 验收回归用例 + AC-STATUS"
git fetch origin main && git rebase origin/main && git push origin HEAD:main
```

> 这些测试进 main 后由 CI（`build-images.yml` / 既有 pytest job）随每次提交复跑——这才是"资产沉淀"的兑现：下次有人改坏本功能，**红的是这些测试**，而不是等用户报障。
> 若某条 TC **暂时无法固化**（如依赖一次性脏数据、需人工目检），在 acceptance.md 显式列「未固化用例 + 原因」，不得默默漏掉（参考全局"no silent caps"原则）。

#### 7.5.4 固化门禁

- 本次每条 ✅ 用例都已有对应 committed 测试，或在 acceptance.md「未固化用例」表里登记了原因。
- `AC-STATUS.md` 已更新且与本次结论一致。
- 新增测试本地全绿、已 push 进 main 并被 CI 纳入。

**未过 7.5.4 门禁 → 禁止进入步骤 8 关闭 Issue。**

---

### 步骤 8：关闭 GitHub Issue

**所有测试用例 100% 通过 + CI 绿 + 链式 CD Deploy run 到终态（`deploy-k8s` 滚成功 + `post-deploy-acceptance-gate` 门绿）+ Deployment rollout 完成** 是关闭 Issue 的前提，缺一不可。**`build-images` 绿但 CD Deploy run 判红 / 未到终态 = 镜像没真正部署上去，禁止关单。**

> **🔒 关单前置硬门（[_shared/dev-acceptance-gate.md](../_shared/dev-acceptance-gate.md) §4，2026-06-16，用户指令）**：关闭任何开发类 issue 前，dev 上 **quality-api L1+L2 全量必须真跑过且绿**——门 `skipped`/`l2.noop`/全 skipped **不算通过**（曾因路径/依赖 bug 长期 noop，7806 用例从未真打，#1882/#1883→PR #1888 治本）。门未真跑 → 先按 §4 主动起 in-cluster Job（`QUALITY_L2_FULL=1`，集群内 algo 才可达）补跑通；门红 → 不关单（修/revert）；跑不通 → 落 `merged_unverified` 留复验。关单评论须附「L2 非 noop（真跑 N 条）+ L1+L2 三态计数」证据。

#### 8.1 关闭单个 Issue（带幂等检查 + 同源 comment）

```bash
ISSUE_NUM=<num>
REPO="$GIT_REPO"   # 取自《环境档案》GIT_REPO（例：your-org/your-repo）
ACCEPTANCE_MD=<web|algo>/specs/<id>/acceptance.md   # 步骤 4 写入的 SoT
export GH_TOKEN="$(gh auth token --user zhaod39_example-corp)"

# 8.1.1 幂等检查：若已 closed 跳过
STATE=$(gh issue view "$ISSUE_NUM" --repo "$REPO" --json state -q .state)
if [ "$STATE" = "CLOSED" ]; then
  echo "ℹ️ Issue #$ISSUE_NUM 已 closed，跳过"
else
  # 8.1.2 用 acceptance.md 同源内容贴 comment + close（不二次编辑）
  gh issue close "$ISSUE_NUM" --repo "$REPO" --comment "$(cat <<EOF
## ✅ 验收通过（$(date '+%F')）

**验收报告（SoT）**：\`$ACCEPTANCE_MD\` @ $(git rev-parse --short origin/main)

以下为该报告的快照：

---

$(cat "$ACCEPTANCE_MD")
EOF
)"
fi
```

> **关键**：comment 内容由 `cat acceptance.md` 直接拼接，不允许手工改一个字。文件改了就重生成 comment（先 `gh issue comment` 追加新版本，旧 comment 保留作历史）。

#### 8.2 批量验收时逐个关闭

批量验收多个 Issue 时，**每个 Issue 独立完成 步骤 1–7 闭环后再走 8.1**，不要并发跑 step 6（违反步骤 0 串行约束）。如果上一轮 P1 修复对其他 Issue 也有影响，重跑步骤 3 再确认。

```bash
export GH_TOKEN="$(gh auth token --user zhaod39_example-corp)"
for ISSUE_NUM in 21 22 24 26 27 28 29; do
  STATE=$(gh issue view "$ISSUE_NUM" --repo "$GIT_REPO" --json state -q .state)
  [ "$STATE" = "CLOSED" ] && { echo "skip #$ISSUE_NUM"; continue; }
  ACCEPTANCE_MD=$(ls {web,algo}/specs/*"$ISSUE_NUM"*/acceptance.md 2>/dev/null | head -1)
  [ -z "$ACCEPTANCE_MD" ] && { echo "❌ #$ISSUE_NUM 无 acceptance.md，禁止关闭"; continue; }
  gh issue close "$ISSUE_NUM" --repo "$GIT_REPO" --comment "$(cat <<EOF
## ✅ 验收通过（$(date '+%F')）—— 批量验收

**验收报告（SoT）**：\`$ACCEPTANCE_MD\` @ $(git rev-parse --short origin/main)

---

$(cat "$ACCEPTANCE_MD")
EOF
)"
done
```

#### 8.3 有 P2 问题但仍关闭时

P2 在步骤 5 判定后即可创 follow-up Issue（不必等到关闭时才创）；关闭原 Issue 时在 acceptance.md 「遗留问题」表格里引用 follow-up Issue 编号即可，无需额外动作：

> **📛 Issue 标题命名规范（强制，全 wiki-issue-* 技能统一）**：标题一律 **`[类型][SPEC-XX][XX模块][XX功能]<描述>`**，
> 类型 ∈ `需求/任务/BUG/优化/重构/文档/调研`；SPEC/模块/功能对不上分别填 `SPEC-NA`/`通用`/`其他`；Follow-up 描述末尾加 `（Follow-up #N）`。

```bash
export GH_TOKEN="$(gh auth token --user zhaod39_example-corp)"
gh issue create --repo "$GIT_REPO" \
  --title "[优化][<spec-id>][XX模块][XX功能]<P2 优化项一句话>（Follow-up #<原issue>）" \
  --body "来自 Issue #<原issue> 验收发现的 P2 问题：

1. ...
2. ...

详见 \`<acceptance.md path>\`。" \
  --label "enhancement"
```

返回的 follow-up Issue 编号填进 acceptance.md「遗留问题」表，commit，再走 8.1（comment 同步出 follow-up 引用）。

#### 8.4 验收失败时禁止关闭

- 有任何 **P1 未修复** → 禁止关闭，继续步骤 5→6→7 循环
- CI 未绿 / `kubectl rollout status` 未 ready → 禁止关闭
- acceptance.md 未写入 → 禁止关闭，先补报告
- 步骤 6.x 触发回滚但没复现修复 → 禁止关闭
- **任一 ✅ 缺原始证据（步骤 3.7）或未过对抗复核（步骤 3.8）** → 禁止关闭（视为存疑 ⚠️）
- **web spec 无任何 L3 E2E 通过 / algo spec 无任何 L2 真实推理通过** → 禁止关闭（层次未达标）
- **步骤 7.5 资产固化门禁未过**（用例未落库、AC-STATUS 未更新、新增测试未进 CI）→ 禁止关闭

---

### 步骤 9：输出汇总

> **重要变更（避免双写）**：原 9.1「向 Issue 追加验收报告评论」已并入步骤 8.1 —— 关闭 Issue 时已用 `cat acceptance.md` 同源贴出完整报告。步骤 9 **不再向 Issue 追加任何 comment**，只在对话里输出汇总给用户。

#### 9.1 校验所有 Issue 已正确归档

```bash
export GH_TOKEN="$(gh auth token --user zhaod39_example-corp)"
for ISSUE_NUM in 21 22 24 26 27 28 29; do
  STATE=$(gh issue view "$ISSUE_NUM" --repo "$GIT_REPO" --json state -q .state)
  HAS_REPORT=$(gh issue view "$ISSUE_NUM" --repo "$GIT_REPO" --json comments \
    -q '.comments[].body' | grep -c "验收通过" || true)
  echo "#$ISSUE_NUM: state=$STATE, report_count=$HAS_REPORT"
done
```

任何 `state != CLOSED` 或 `report_count == 0` → 回到步骤 8 补。

#### 9.2 在对话中输出《工作过程总结》（~1000 字 + 表格 + 下一步建议）

所有 Issue 评论提交完毕后，**产出一份 ~1000 字《工作过程总结》**，格式与七段骨架见统一 SoT：[`_shared/closing-summary.md`](../_shared/closing-summary.md)（**必读必执行**，四技能共用一份；直接输出对话，不写文件、不回贴 Issue）。

- 验收侧填充侧重见共享文件 §2 表对应行：「②总览」含 Issue#/spec/**TC 总数与通过率**；「③过程」走"设计 TC → K8s 三层执行 → 报告 → 修复 → 资产沉淀 → 关"；「④质量门」含**三层（L1/L2/L3）+ 对抗复核 + AC-STATUS（无 none、前端 AC 全 pass）**；「⑦下一步」含未固化用例 / partial AC 补 L3 / Follow-up。
- 下面的 ASCII 总览块可作为「②总览 / 汇总数据」段的呈现形式之一（批量验收多 Issue 时尤其清晰）：

```
╔══════════════════════════════════════════════════════╗
║           Issue 验收汇总报告                          ║
╚══════════════════════════════════════════════════════╝

验收日期：<DATE>             # 模板渲染时替换为 $(date '+%F')
验收人：Dev User
测试环境：测试环境 K8s namespace <DEPLOY_NS>（SLB <INTERNAL_SLB>；渲染时代入实际值，例：staging / 10.0.0.10）

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Issue 验收结果总览
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

| Issue | 标题         | 测试用例 | 通过   | GitHub 状态 |
|-------|-------------|---------|--------|------------|
| #21   | 目标检测服务  | 12      | 12/12  | ✅ closed  |
| #22   | 跟踪事件引擎  | 11      | 11/11  | ✅ closed  |
| ...   | ...          | ...     | ...    | ...        |

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
汇总数据
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  验收 Issue 总数：N
  全部通过：       N ✅
  测试用例总数：   N
  通过率：         100%
  P1 修复数：      N 个
  P2 待跟进：      N 个（已创建 Follow-up Issue #xxx）
  代码提交：       <sha-list>
  CD 状态：        ✅ build-images 绿 → 链式 CD Deploy run 到终态（deploy-k8s 滚成功 + post-deploy-acceptance-gate 门绿），rollout 完成
  验收报告：       已通过 GitHub comment 存档至各 Issue

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
遗留事项（P2 / Follow-up）
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

  1. #<follow-up-issue>：<描述>（建议下一迭代处理）
  （无则填"无"）

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
验收结论：✅ 本次批量验收全部通过，Issue 已关闭，报告已存档
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
```

#### 注意事项

- **报告 SoT**：`<spec>/acceptance.md` —— 步骤 4 写入，步骤 8.1 用 `cat` 同源贴进 Issue comment，步骤 9 不再二次产出 comment
- 汇总（9.2）只在对话中输出一次，不要写文件、也不要回贴到 Issue
- 批量验收时，每个 Issue 必须独立完成步骤 1–8 闭环再关闭；不允许并发跑步骤 6
- P2 遗留事项必须如实列出，没有则写「无」

#### 9.3 释放 session 锁（强制收尾，与 §0.5 配套）

汇总输出后最后一步：
```bash
rm -f .claude/locks/acceptance-${ISSUE_NUM}-${SESSION_SHA8}.lock
echo "🔓 已释放 session 锁: acceptance-${ISSUE_NUM}-${SESSION_SHA8}"
```
**批量验收**：每个 Issue 闭环后单独释放对应的锁；最后一个 Issue 完成后所有锁应已清。
不要漏。漏了会产生僵尸锁，4 小时后才会被下次启动清理。

---

## 自检清单（宣告完成前过一遍）

### 跨 Session 防冲突（与 §0.5 §0.5.1 配套，必过）

- [ ] §0.5 session 锁已写入 `.claude/locks/acceptance-${ISSUE_NUM}-${SESSION_SHA8}.lock`（拿到 issue 编号后已重命名 pending→真实编号）
- [ ] 启动时已探测同 issue 活锁，命中已走 `AskUserQuestion` 三选一
- [ ] 步骤 6.3 PR 模式开新分支已用 `fix/acc-${ISSUE_NUM}/${SESSION_SHA8}` 三段格式
- [ ] 步骤 6.2 / 6.3 push 前已 `git fetch origin main && git rebase origin/main`
- [ ] 步骤 6.3 建 PR 前已 `gh pr list --search` 查同文件 in-flight PR
- [ ] 步骤 9.3 已 `rm` 自己的锁文件（批量场景每个 issue 各自释放）
- [ ] **研发资源锁（§0.8 条件启用）已处理**：`devlock` 可达且本项目有 `DEPLOY_NAMESPACE` → 本轮 push main 前已按验证环境取 v6 车道锁面（staging 道 `cli.py lock-acquire CI,CD,STAGING` / dev 道 `cli.py lock-acquire CI,CD,DEV`，**禁止 MCP 阻塞式 `lock_acquire` 排队**）拿到 `granted=true`，merge commit 标题前缀与车道同向（staging→`fix(` / dev→禁 `fix(`），心跳由守护进程自动续租(60s/拍,见 §0.8 心跳守护);守护未起时退回每 5min `lock_heartbeat`；否则已打 WARN 降级跳过（二者择一，禁止静默；docs-only 免锁直合不适用本条）
- [ ] **研发资源锁已分段释放并收口（§0.8 条件启用）**：CI 构建绿后已 `cli.py lock-release $REQ --resources CI`、验证环境 rollout 确认后已 `--resources CD`、重测段只持环境锁（STAGING 或 DEV）；每一轮步骤 7 重测收口（通过/不通过均算）后已 `cli.py lock-release $REQ` 全放，未把车道锁带出本轮；不通过回步骤 6 改 P1 前已先全放；降级无锁则不适用

### 测试质量门禁（三层 / 可信度 / 资产，必过）

- [ ] **测试层次**：web spec 有 ≥1 条 L3 E2E 通过；algo spec 有 ≥1 条 L2 真实推理通过；report 标了每条用例的层次且统计了 L1/L2/L3 分布
- [ ] **结果可信度**：每条 ✅ 在 report「证据」列有原始物证（状态码+响应体 / psql 行 / 截图），无"功能正常"式空话
- [ ] **对抗复核**：每条 ✅ 过了反向证伪 + 三处值交叉核对；P1 核心链路已连跑 2 次无 flaky
- [ ] **资产沉淀**：每条 ✅ 已固化为 `tests/acceptance/ac|unit` / `tests/api` / `tests/acceptance/browser` 的 committed 测试（含 `*_negative_*` 对抗用例）
- [ ] **追溯矩阵**：`tests/acceptance/ac/AC-STATUS.md` 已更新且与结论一致；未固化用例已在 acceptance.md 登记原因
- [ ] 新增测试本地全绿 + 已 push 进 main 被 CI 纳入复跑
- [ ] **链式 CD Deploy 已 watch 到终态（[_shared/dev-acceptance-gate.md](../_shared/dev-acceptance-gate.md) §5）**：`build-images` 绿后已按 §5 定位本 SHA 的 `cd.yml` run 并 watch 到 `success`/`skipped`/兜底成立；`deploy-k8s` failure（镜像没滚上去）已按 §5③ 归因处置（A 代码问题回步骤 6 修 / B rerun / C ancestor 兜底），`post-deploy-acceptance-gate` 门红已按 §2 修 P1 或 revert 后重验；未把「build 绿」当「部署完成」

## 红线（违反任一 = 本次验收不合格）

- ❌ 只验 L1/L2 后端就判 web 功能通过（用户根本没在浏览器里验过 = 没验收）
- ❌ 判 ✅ 但「证据」列无原始物证（状态码/响应体/行/截图），靠"看起来没问题"
- ❌ 跳过对抗式证伪复核（步骤 3.8），happy 过了就收 —— 高"幻觉判过"风险
- ❌ 验收跑完不固化测试资产（步骤 7.5），TC 只活在 acceptance.md → 下次改坏无人知
- ❌ 关 Issue 前未更新 `AC-STATUS.md` 追溯矩阵
- ❌ 启动时跳过 §0.5 锁探测（多机器/多人同 issue 并发的根因）
- ❌ 步骤 6.3 PR 分支沿用旧 `acceptance/issue-<num>-<timestamp>` 不加 session-sha8（多 session 同 issue 必撞）
- ❌ 步骤 6 push 前不 `git fetch + rebase`，带着旧 base 推
- ❌ 步骤 6.3 建 PR 前不查同文件 in-flight PR（产生同文件双 PR）
- ❌ 步骤 9.3 完成后忘记 `rm` 锁文件（僵尸锁污染）
- ❌ **`build-images` 绿即判验收通过 / 关单，不 watch 链式 CD Deploy run 到终态**（漏掉「CD 判红镜像没滚上去且无人管」——用户实测故障根因；§5 红线）
- ❌ **CD `deploy-k8s` 因 Pod 起不来（A 类代码问题）却当环境抖动无脑 `gh run rerun`**（起不来 rerun 一百次还是红），或只读总 conclusion 不分 `deploy-k8s`/`gate` 两 job
- ❌ **`post-deploy-acceptance-gate` 门红仍判过 / 关单**（验收类必须修 P1 或 revert 后重验，凌驾 agent 自评）

---

## 参考资料

- 测试策略与 API 模式 → [references/test-patterns.md](references/test-patterns.md)
- K8s 故障排查速查 → [references/k8s-debug.md](references/k8s-debug.md)
- K8s 部署完整环境 → 项目根 `CLAUDE.md` 的 K8s 测试环境小节 / `deploy/k8s/`（例：§5.2 K8s staging）
- 外网兜底访问 → [public_network_dev_fallback](~/.claude/projects/<project-slug>/memory/public_network_dev_fallback.md)
- 内网研发态切换 → [intranet_dev_checklist](~/.claude/projects/<project-slug>/memory/intranet_dev_checklist.md)
- CD moving tag 冻结 → [cd_moving_tag_frozen](~/.claude/projects/<project-slug>/memory/cd_moving_tag_frozen.md)
- Redis broker 消息丢失 → [redis_broker_message_loss](~/.claude/projects/<project-slug>/memory/redis_broker_message_loss.md)
- Schema 漂移 → [prod_schema_drift_mechanism](~/.claude/projects/<project-slug>/memory/prod_schema_drift_mechanism.md)
