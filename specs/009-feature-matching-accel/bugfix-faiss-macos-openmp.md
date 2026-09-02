# Bug Fix — macOS PyTorch + FAISS 双 OpenMP 运行时导致进程 abort

## 1. 期望与实际

- 期望：FAISS 是可选加速，运行时不安全或不可用时自动回退 numpy，不影响定位与测试进程。
- 实际：macOS 当前 venv 同时加载 PyTorch 与 FAISS 后，首次 `Index.search()` 触发 `OMP Error #15`，Python 进程以 134 退出，异常无法捕获。

## 2. 复现矩阵

| 环境/版本 | 输入与路径 | 预期 | 实际 | 证据 |
| --- | --- | --- | --- | --- |
| macOS arm64 / Python 3.12 / torch 2.13 / faiss 1.15 / numpy 2.5.1 | `pytest services/tests/test_accel_009.py::test_faiss_search_matches_numpy_brute_force` | 等价测试通过或安全 skip | `Fatal Python error: Aborted`，OMP Error #15 | 2026-08-31 本地复现 |
| 同环境，仅导入 FAISS | 100×728 `IndexFlatIP.search` | 返回 top-k | 正常 | 对抗试验 |
| 同环境，同时导入 torch + FAISS | 相同 search | 返回 top-k | RC=134 | 最小复现 |

`otool -L` 证据：PyTorch 链接 `torch/lib/libomp.dylib`，FAISS wheel 链接 `faiss/.dylibs/libomp.dylib`，是两份不同路径的 OpenMP 运行时。

## 3. 根因分析

### 3.1 5 Why

1. 为什么 pytest 直接退出？FAISS 首次 search 初始化第二份 `libomp`，运行时主动 abort。
2. 为什么会加载第二份？PyTorch 和 FAISS wheel 分别捆绑自己的 `libomp.dylib`。
3. 为什么可选后端没有回退？`_has_faiss()` 只验证 import 成功，不验证“与已加载 PyTorch 共存是否安全”。
4. 为什么 try/except 无效？abort 发生在原生运行时，不是 Python exception。
5. 为什么旧测试未保护？等价测试把“可 import”等同“可运行”，且直接在 pytest 进程调用原生 search。

### 3.2 为什么未被测试/监控发现

原测试在未安装 FAISS 时会 skip；安装 FAISS 后才进入冲突路径。没有“运行时不兼容时不得调用原生 search”的隔离测试。

## 4. 影响面

- 受影响：`salad_roma_v2` fast_mode 在 macOS 选择 FAISS；相关单测与任何同进程 PyTorch+FAISS 调用。
- 不受影响：Linux/CUDA 的 FAISS 路径、numpy 余弦回退、LoFTR/XFeat 匹配、PnP 语义。

## 5. 修复方案

1. Darwin 下 `_has_faiss()` 返回 false；本模块必然已加载 PyTorch，因此选择安全 numpy 后端；
2. `_build_faiss_index()` 与 `_faiss_search()` 都先检查运行时能力；
3. 等价测试按 `_has_faiss()` 能力 skip，而非仅按 import；
4. 新增 TL-009-10，用会爆炸的 fake index 证明不兼容时不会进入 search。

没有使用 `KMP_DUPLICATE_LIB_OK=TRUE`，因为 OpenMP 官方错误信息明确把它标为不安全 workaround，可能产生崩溃或静默错误。

## 6. 扩散覆盖矩阵

| 同模式位置 | 是否受影响 | 处理 | 测试 |
| --- | --- | --- | --- |
| `services/localizer/salad_roma_v2.py` `_build_faiss_index` | 是 | 运行时能力前置守卫 | TL-009-10 |
| 同文件 `_faiss_search` | 是 | search 前二次守卫 | TL-009-10 |
| 同文件调用方 `_salad_retrieve_v2` | 否 | 已按 `_has_faiss()` 回退 numpy | 009 近邻测试 |
| 其他 `contexts/` / `services/` | 否 | `rg` 未发现第二个 FAISS 后端 | 扩散检索 |

## 7. 回归测试

- Red：`test_faiss_search_skips_runtime_marked_incompatible` 旧实现调用 fake search 并失败；
- Green：同测试 1 passed；
- 近邻：`services/tests/test_accel_009.py` 11 passed, 1 skipped。
- 全仓 fast：140 passed, 2 skipped, 5 deselected；漂移检查 0 错误。

## 8. 风险与回滚

- 风险：macOS 放弃 363 条描述子上的 FAISS 加速；该规模 numpy 检索成本远小于 DINO/LoFTR 推理，安全优先。
- 回滚：恢复三处 FAISS 守卫；仅当依赖统一链接同一 `libomp` 且有子进程探针证据时允许。

## 9. Before/After

| 阶段 | 结果 |
| --- | --- |
| Before | pytest 进程 abort，RC=134 |
| After | 009 近邻测试 11 passed, 1 skipped；macOS 自动走 numpy |

扩散结论：检索 `_has_faiss`、`IndexFlatIP`、`import faiss`、`libomp` 与不安全环境变量模式，覆盖 `contexts/`、`docs/`、`specs/`、`services/`；FAISS 运行时代码仅 1 个适配器，3 个同模式入口已全部加守卫或复用守卫，无第二处同类问题。

2026-09-01 扩散复核：安装 hloc 后发现 `pycolmap/.dylibs/libomp.dylib` 与 `torch/lib/libomp.dylib` 是同类冲突，且 LightGlue 包入口会急切加载含额外原生依赖的可选提取器。覆盖检索 `pycolmap`、`lightglue`、`libomp`、`KMP_DUPLICATE_LIB_OK` 于 `services/`、`docs/`、`specs/`；新增调用仅位于 `hloc_baseline_010.py`，已用前端模块隔离和显式 full-pipeline skip 处理，未使用不安全环境变量。

## 10. Changelog

- 2026-08-31：完成根因修复、TDD 与扩散排查。
