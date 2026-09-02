# 测试策略

本节描述测试分层的**理想目标**。新人上手请先读 [`tests/TESTING.md`](../tests/TESTING.md)（真实分布、执行方式、双向追溯），本节末尾的「当前现状与差异」汇总了理想与实际的差距及演进方向，两份文档互为镜像。

## 分层

| 层级 | 位置 | 边界 | 默认执行 |
| --- | --- | --- | --- |
| 单元测试 | 各上下文现有测试目录，逐步归入 `contexts/*/tests/unit/` | 纯内存，无网络、无真实大模型、无大文件 | 是 |
| 集成测试 | `contexts/*/tests/integration/` 或现有模块测试 | SQLite、文件系统、OpenCV/laspy、外部二进制适配 | fast 模式排除 |
| 契约测试 | `tests/contract/` | HTTP schema、地图瓦片索引和算法结果契约 | 是 |
| 验收测试 | `tests/acceptance/` | 从用户场景验证跨上下文行为 | 按夹具能力 |
| 系统/性能 | `tests/system/` | 服务进程、真实 LAS、模型和硬件性能 | 仅显式执行 |

## 原则

- 从规格包的 `testlist.md` 选择一个行为，先观察失败，再写最小实现。
- 测试描述业务行为，不锁定私有实现。
- 涉及随机采样、模型推理或几何估计时固定种子或使用确定性小夹具。
- 依赖真实 LAS、网络下载、模型权重、GPU 或外部二进制的测试必须打 `integration`、`slow` 或 `system` 标记。
- 失败路径与成功路径同等重要，尤其是无地图、无匹配、无内点、损坏索引和任务恢复。

## 命令

```bash
./scripts/run-all-tests.sh fast
./scripts/run-all-tests.sh all
```

## 当前现状与差异（2026-08）

下表汇总本节理想分层与当前实际的差距。详细分布、marker 定义、执行命令见 [`tests/TESTING.md`](../tests/TESTING.md)。

| 理想层 | 理想位置 | 当前位置 | 差距 |
| --- | --- | --- | --- |
| 单元测试 | `contexts/*/tests/unit/` | `services/tests/`、`api/tests/` | 代码未迁入上下文，测试跟着代码走 |
| 集成测试 | `contexts/*/tests/integration/` | `services/tests/`（打 `integration` marker） | 语义符合，只是没落在 contexts/ 下 |
| 契约测试 | `tests/contract/` | `api/tests/test_localization_contract.py`、`services/tests/test_benchmark_contract.py` | 位置不同，内容存在 |
| 验收测试 | `tests/acceptance/` | 根 `tests/test_frontend_*.py` | 位置与内容基本对应 |
| 系统/性能 | `tests/system/` | `services/tests/test_benchmark_*.py`（打 `slow`/`system`） | 位置不同，内容存在 |
| 测试清单 | `tests/testlists/*.testlist.md` | `specs/<feature-id>/testlist.md` | 放进了规格包，位置不同但可追溯 |

**分层手段**：理想按子目录分层；实际按 pytest marker（`integration` / `slow` / `system`）分层，fast 模式排除后三者。`run-all-tests.sh fast` 等价于 `pytest -q -m "not integration and not slow and not system"`，当前实测 154 passed / 2 skipped。

**演进建议**：不要一次性重排目录——成本高且易破坏存量。按「下一个真实特性做最小完整闭环」节奏，新测试放进对应模块目录并标注 TL/AC，等上下文代码真正迁入 `contexts/<bc>/` 时再顺势归位。
