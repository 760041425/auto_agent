# 006 决策记录

| D-ID | 决策 | 理由 | 替代方案 | 状态 |
| --- | --- | --- | --- | --- |
| D-006-01 | **降级方案为主**：ACE 系无坐标差判据时，展示为明确的「⚠ 无法判定」独立状态（含 reason），而非伪造坐标差或沿用旧 LAS 判据 | ① AC-003-14 规定无可用坐标差一律低可信，降级语义天然一致；② 为 train_ace/ace_rgb/ace_normal/ace_better 逐个接入 H+NPY+consistency 三件套成本高（需 PnP/H 基建 + 产物落盘），ACE 系定位为实验算法；③ 收敛快、回归面小 | 「ACE 系接入坐标差判据（方向①前半段）」→ 列为后续增强，需先建新规格（如 007）再实施 | 采用 |
| D-006-02 | 前端徽章 `localizeStatusBadge` 取消 `result.reliable` fallback，只由坐标差判据产生 ✓；缺判据一律「⚠ 无法判定」 | `reliable`（LAS 验证率>0.3）是辅助诊断，不得单独产生"可信"徽章（AC-003-14：内点数/相似度/2D 拟合/LAS 仅作辅助）；消除「✓可信」与「低可信」同屏矛盾 | 保留 fallback 并加警告标注——不采用（矛盾展示仍存） | 采用 |
| D-006-03 | train_ace 后台完成覆写 `result_json` 统一走 `normalize_localization_result`（与 `_append_result` 对齐） | 消除 raw dict 覆写造成字段缺失（inliers/total_3d_points/coordinate_transform）；契约单一真源 | 前端为缺失字段补默认值——治标不治本，且掩盖契约不一致 | 采用 |
| D-006-04 | 前端判定逻辑采用 Python 等价镜像测试（沿用 005 先例），JS 真机验收留人工 | 项目无 Node/JS 测试运行时；005 已验证该模式可行 | 引入 Node 单测 harness——改变工程拓扑，超范围 | 采用 |
| D-006-05 | 徽章/判定统一逻辑应用于所有算法结果（含非 ACE），缺判据一律无法判定 | 前端统一一个真源；避免每算法不同显示 | 只修 ACE 系展示 → 其他算法同样矛盾遗留 | 采用 |