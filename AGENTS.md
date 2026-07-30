# LAS 影像 3D 查询与视觉定位

本仓库采用 **DDD 定边界、SDD 管变更、TDD 驱动实现** 的渐进式工程流程。

## 权威信息源

- 领域与架构：`docs/`
- 限界上下文及当前代码映射：`contexts/`
- 特性规格包：`specs/<feature-id>/`
- 旧需求入口：`spec/`（只作兼容索引，不再作为权威规格）
- 自动化测试：当前位于 `api/tests/`、`services/tests/`，跨上下文测试位于 `tests/`

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
