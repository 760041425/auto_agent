# 项目文档导航

本目录保存跨特性、相对稳定的产品、领域、架构与工程规则。具体变更不在这里展开，而进入 `specs/<feature-id>/`。

## 阅读路径

| 目的 | 文档 |
| --- | --- |
| 理解产品边界 | [产品说明](product.md)、[领域愿景](domain-vision.md) |
| 理解业务语言和边界 | [统一语言](ubiquitous-language.md)、[上下文映射](context-map.md)、[子域划分](subdomains.md) |
| 理解代码与技术现状 | [仓库结构](structure.md)、[技术说明](tech.md) |
| 开始一次变更 | [工程实践](engineering-playbook.md)、[测试策略](testing-strategy.md) |
| 编写测试与重构 | [测试命名规范](test-naming-conventions.md)、[重构规则](refactoring-rules.md) |
| 查看本地 wiki skills 状态 | [Skill 安装与使用核验](skill-usage-audit.md) |
| 查看特性变更记录 | [Changelog — Spec 003](CHANGELOG-003.md) |
| 查看跨会话交接 | [交接记录](handoffs/) |

## 权威层级

1. `docs/` 定义长期领域与架构认知；
2. `contexts/` 定义限界上下文及其当前代码映射；
3. `specs/<feature-id>/` 定义一次特性变更的目标、方案、任务和验证；
4. 自动化测试与验证报告证明行为是否实现；
5. `spec/` 仅保留旧链接，不再承载权威内容。

发现冲突时，不静默选择任意版本：先在当前规格包的 `clarify.md` 记录冲突，再更新对应的长期文档或作出特性级决策。
