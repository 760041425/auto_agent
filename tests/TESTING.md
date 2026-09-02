# 测试现状与执行方式

> 这份文档描述**当前测试的真实分布与执行方式**，面向新人快速跑通测试、定位测试文件。
> 手册级的分层理想见 [`docs/testing-strategy.md`](../docs/testing-strategy.md)，本文末尾列出"现状 vs 理想"差异与演进方向。

## 1. 测试实际分布在哪里

当前测试**不在** `tests/` 一处，而是按"模块内测试 + 跨上下文验收"分散在三个位置：

| 位置 | 用途 | 文件数 | 典型内容 |
| --- | --- | --- | --- |
| `services/tests/` | 业务核心与算法测试 | 15 | ACE 坐标一致性、法线估计、特征匹配加速、SLAD+RoMa、平面检测、地面滤波 e2e、benchmark 契约、回归用例 |
| `api/tests/` | API 契约与训练入口 | 4 | 定位接口契约、ACE 训练集成、ACE 归一化 |
| `tests/`（根） | 跨上下文前端验收 | 4 | ACE 前端回归、可靠性、坐标展示 |

**为什么这么分布**：项目存量代码仍在 `services/`、`api/` 内按技术目录组织，尚未按限界上下文迁入 `contexts/<bc>/tests/`（参见 [`contexts/README.md`](../contexts/README.md) "生产代码暂不整体搬迁，后续绞杀式迁移"）。测试跟着代码走，所以落在模块内。

## 2. 测试分层：靠 marker 而非子目录

项目用 **pytest marker** 区分执行频率，**没有**按手册字面建 `unit/ integration/ contract/ acceptance/ fixtures/ builders/ helpers/ testlists/` 子目录。

在 `pyproject.toml` 注册的 marker：

| marker | 含义 | 默认执行 |
| --- | --- | --- |
| `integration` | 需要文件系统夹具或边界适配器 | ❌ fast 排除 |
| `slow` | 明显长于快速反馈回路 | ❌ fast 排除 |
| `system` | 起进程、开端口、需要真实运行时资产 | ❌ fast 排除 |
| 无 marker | 纯内存、快速、小粒度行为 | ✅ 默认执行 |

> 给测试加 marker：`@pytest.mark.slow`，或参数化里 `pytest.param(..., marks=pytest.mark.integration)`。

## 3. 执行命令

```bash
# fast 模式：排除 integration / slow / system，本地快速反馈（CI 门禁默认）
./scripts/run-all-tests.sh fast

# all 模式：跑全量（可能需 LAS 样本、模型权重、外部二进制、较长运行时间）
./scripts/run-all-tests.sh all
```

**`run-all-tests.sh` 的门禁逻辑**：`MODE=fast` 时等价于 `pytest -q -m "not integration and not slow and not system"`；`MODE=all` 时跑全量。脚本还会注入 `KMP_DUPLICATE_LIB_OK=TRUE` 避免 macOS 下 torch+faiss+scipy+PIL 共存时的 OpenMP SIGABRT。

## 4. 常用定向执行

```bash
# 跑单个模块
python -m pytest services/tests/test_ace_coordinate_consistency.py -q

# 跑单个 marker
python -m pytest -q -m "integration"

# 跑单个规格包关联的测试（按文件名前缀，例如 008-ace-true-normal）
python -m pytest -q -k "ace_true_normal"

# 列出所有测试，不执行
python -m pytest --collect-only -q
```

> 注意：`pyproject.toml` 的 `testpaths` 只含 `api/tests` 与 `services/tests`，根 `tests/` 的 4 个前端测试**不在默认收集路径**。跑全量或定向执行根测试时需在命令行显式指定 `tests/`，例如 `python -m pytest tests/ services/tests/ api/tests/ -q`。这是当前配置与理想态的差异之一。

## 5. 规格 ↔ 测试双向追溯

测试文件名与测试文档节点会尽量对齐规格包（`specs/<feature-id>/`）：

- **文件命名**：`test_ace_better_fix.py` 对应 `specs/007-ace-better-fix/`，`test_accel_009.py` 对应 `specs/009-feature-matching-accel/`。
- **用例标注**：测试文档字符串会标注来源，例如 `"""TDD 测试：TL-007-06 / AC-007-06"""`，对应 `specs/007-ace-better-fix/testlist.md` 的 TL-007-06 与 `spec.md` 的 AC-007-06。
- **追溯报告**：`./scripts/traceability-report.sh` 可从规格包生成 AC/TASK/TL/RISK 追踪表。

新增测试时，建议先在对应规格包的 `testlist.md` 占一个 TL 编号，再把测试用例标注该 TL，保持双向可追溯。

## 6. 共享夹具

- `services/tests/conftest.py`：保证项目根目录在 `sys.path` 最前，避免 `site-packages/scripts`（uniception 等包安装）遮蔽本地 `scripts/` 目录。
- 真实 LAS、网络下载、模型权重、GPU、外部二进制依赖，**必须**打 `integration` / `slow` / `system` marker，不得污染 fast 回路。

## 7. 现状 vs 理想（演进方向）

| 维度 | 手册理想 | 当前现状 | 差距 |
| --- | --- | --- | --- |
| 测试目录 | `tests/{unit,integration,contract,acceptance,testlists,fixtures,builders,helpers}` | 三处分散 + marker 分层 | 形态不同，但分层语义存在 |
| 上下文内测试 | `contexts/<bc>/tests/{unit,integration,contract}/` | `services/tests/`、`api/tests/` | 代码未迁入上下文，测试跟着代码走 |
| 契约测试 | `tests/contract/` | `api/tests/test_localization_contract.py`、`services/tests/test_benchmark_contract.py` | 位置不同，内容存在 |
| 验收测试 | `tests/acceptance/` | 根 `tests/test_frontend_*.py` | 位置与内容基本对应 |
| testlists | `tests/testlists/*.testlist.md` | `specs/<feature-id>/testlist.md` | 放进了规格包，与理想位置不同但可追溯 |

**建议**：不要为了对齐手册一次性重排目录（成本高、易破坏存量）。按"下一个真实特性做最小完整闭环"节奏，把新测试放进对应模块目录并标注 TL/AC，等上下文代码真正迁入 `contexts/<bc>/` 时再顺势归位。

## 8. 相关文档

- 手册级分层理想与原则：[`docs/testing-strategy.md`](../docs/testing-strategy.md)
- 测试命名规范：[`docs/test-naming-conventions.md`](../docs/test-naming-conventions.md)
- 重构规则（含测试保护下的重构）：[`docs/refactoring-rules.md`](../docs/refactoring-rules.md)
- 规格包索引：[`specs/README.md`](../specs/README.md)
- 协作规则与交付门禁：[`AGENTS.md`](../AGENTS.md)、[`CLAUDE.md`](../CLAUDE.md)
