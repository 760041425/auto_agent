# 测试策略

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
