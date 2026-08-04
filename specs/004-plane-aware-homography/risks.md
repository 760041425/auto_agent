# 004 风险

| RISK-ID | 概率/影响 | 触发信号 | 缓解 | 回滚 |
| --- | --- | --- | --- | --- |
| **RISK-004-01** | 中/高 | 地面阈值过严（0.2m）导致部分地面点被当作 outlier | 阈值可配置（0.15m–0.3m）；回退到全点拟合路径保证不崩溃 | 调大 `plane_distance_threshold` 到 0.3m |
| **RISK-004-02** | 中/高 | 某些场景无明确地面（纯室内、地下、桥面坡道） | RANSAC 仍会提取最大平面（可能是墙面/天花板）；若最大平面内点占比 <50% 则回退到全点拟合 | 通过 `plane_segmentation.n_ground_inliers / n_total_points` 占比诊断 |
| **RISK-004-03** | 低/高 | RANSAC 随机性导致结果波动 | 固定 seed=1337；1000 次迭代下正确提取概率 >99% | 增加 `max_iterations` 或更换 seed |
| **RISK-004-04** | 中/中 | 地面点恰好共线（如狭窄走廊），RANSAC 提取的平面不稳定 | 3 点采样时检查共线性（法向量模长 <1e-6 则跳过该组）；精化阶段用最小二乘平滑 | 共线时回退到全点拟合 |
| **RISK-004-05** | 低/高 | 分层 H 拟合精度反而不如全点拟合（地面点噪声大、立面点恰好共面） | 合成数据测试覆盖；真实场景若 median_m 增大则回退 | 参数 `plane_distance_threshold=100.0` 强制全点拟合 |
| **RISK-004-06** | 中/低 | 现有调用方（`salad_roma_v2.py`）依赖 `context` 的特定字段结构 | 只新增 `plane_segmentation` 字段，不修改/删除旧字段；现有测试全部通过 | 删除 `plane_segmentation` 字段即可回退 |
| **RISK-004-07** | 低/中 | RANSAC 计算耗时增加（每任务多 1000 次 × N 点距离计算） | N 通常 <256，1000 次迭代 <10ms；可忽略 | 减少 `max_iterations` 到 500 |
| **RISK-004-08** | 中/高 | 平面检测引入新 bug，导致原本 `reliable=true` 的 case 变成 `false` | TDD 先写失败测试；合成数据验证；真实场景复跑对比 | 关闭平面检测（`plane_distance_threshold=100.0`） |
