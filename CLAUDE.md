# LAS 影像 3D 查询与视觉定位

项目协作规则以 [AGENTS.md](AGENTS.md) 为准（SoT，本文件不重复其间内容）。

开始实现前先阅读：

1. `docs/engineering-playbook.md`
2. `docs/context-map.md`
3. 当前变更对应的 `specs/<feature-id>/`

最小交付门禁：

```bash
./scripts/validate-specs.sh
./scripts/run-all-tests.sh fast
./scripts/drift-check.sh
```

---

## 硬红线速览

> 其余章节是这几条的展开；工程方法 / 测试门禁等详见 [AGENTS.md](AGENTS.md)。

1. **语言简体中文 + 技能收尾必带表格**：面向人的叙述 / 汇报 / commit / Issue / PR 一律简中（代码 / 命令 / 路径 / 标识符 / 英文报错原文除外）；任一 `/wiki-*` 技能收尾必用 Markdown 表格汇报 + 「下一步建议」1–3 条可执行动作（详见《交互与汇报通用约束》）。
2. **变更必有规格包 + TDD**：非琐碎改动先在 `specs/<feature-id>/` 建规格包（spec / clarify / plan / tasks / checklist / testlist / risks / decisions），再按 Red → Green → Refactor 推进（细则见 [AGENTS.md](AGENTS.md) §变更流程 / §实现约束）。
3. **修一个 bug 必扩散**：定位根因后用 `rg` 在 `contexts/`、`docs/`、`specs/` grep 同模式一并修，报告必带「扩散结论」段（详见 [AGENTS.md](AGENTS.md) §Bug 扩散排查）。
4. **CI/CD 红 = 发现即修**：任一 session 发现 CI / CD / 规格门禁变红，**当回合**负责修到绿或确证中和——根因与自己无关也要处理，禁止另起 follow-up 拖延；当回合到不了绿须用 `AskUserQuestion` 暴露阻塞，不得静默离场（详见 [AGENTS.md](AGENTS.md) §CI/CD 红线纪律）。
5. **Issue 终态归人工验收**：代理完成技术处理后不直接关闭 Issue，按《Issue 终态》转人工验收（详见 [AGENTS.md](AGENTS.md) §Issue 终态覆盖）。

---

## 交互与汇报通用约束（所有会话 / 所有 `/wiki-*` 技能）

1. **语言一律简体中文**：面向用户的全部叙述、标题、汇报都用简中。即使 subagent、工具或 CI 返回英文 / 日文，回写给用户时必须翻成简中，全程不得中途切换语种。领域词汇与 [docs/ubiquitous-language.md](docs/ubiquitous-language.md) 对齐，禁自造同义词。
2. **技能收尾必带表格 + 下一步**：任一 `/wiki-*` 技能跑完一个阶段或全部完成时，必须：
   - 用 Markdown **表格**汇报结果（典型列：步骤 / 事项、状态、产物或证据链接、备注）；
   - 表格后另起「**下一步建议**」小节，给出 **1–3 条可直接执行**的动作——可复制的 shell / `gh` 命令，或 `/wiki-*` 串联指令；禁止只写「可以继续优化」这类空话。
