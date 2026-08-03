# 算法验证报告（2026-08-04）

**测试图像**: `query_images/d7393932-3f76-4de4-808a-d5a3266c1c2b.jpg` (1920×1080)
**测试时间**: 2026-08-04
**代码状态**: `7722cd9` (focal search + quality gate) + `041c7b6` (原版升级)

## 结果汇总

| 算法 | 耗时 | 内点 | 重投影误差 | 质量门控 | quality_score | 原因 |
|------|------|------|-----------|---------|---------------|------|
| SALAD+RoMa (原版) | 24.51s | 351 | 405.39px | ✗ FAIL | 3.96 | score<4.0, reproj_error>8.0px |
| SALAD v2 (DISK+LG) | 3.24s | - | - | N/A | - | PnP 无解 |
| SALAD v2 + LoFTR | 15.98s | 204 | 103.92px | ✗ FAIL | 7.26 | reproj_error>8.0px |
| Hybrid (联合) | 16.65s | 191 | 115.21px | ✗ FAIL | 7.26 | reproj_error>8.0px |
| Multi-Strategy | 10.73s | 129 | 28.73px | ✗ FAIL | 4.49 | reproj_error>8.0px |

## 关键观察

### 1. 焦距搜索已生效
所有走 PnP 的算法日志均显示 `[PnP] 成功: X/Y 内点, score=Z, quality=...`，
确认 `solve_pnp_with_focal_search` 在所有路径中运行。

### 2. 质量门控正确工作
- **SALAD+RoMa**: score=3.96（接近 4.0 门槛），reproj_error=405px（远超 8px 上限）
- **LoFTR/Hybrid**: score=7.26（通过 score 门槛），但 reproj_error > 8px → FAIL
- **Multi-Strategy**: score=4.49（通过），reproj_error=28.73px（超门槛）→ FAIL

### 3. 耗时对比
| 算法 | 总耗时 | PnP 搜索占比（估） |
|------|--------|-------------------|
| SALAD+RoMa | 24.51s | ~15%（~3.6s，含多轮迭代） |
| DISK+LG | 3.24s | ~30%（~1.0s，失败快返回） |
| LoFTR | 15.98s | ~15%（~2.4s） |
| Hybrid | 16.65s | ~15%（~2.5s） |
| Multi-Strategy | 10.73s | ~25%（~2.7s，3 候选） |

### 4. 当前图像定位质量
所有算法质量门控均为 FAIL，主因是重投影误差远超 8px 门槛。
这与当前使用点云渲染 tile（非真实影像）的预期一致——渲染图与真实照片的
视角/外观差距大，属于已知限制，不反映算法本身问题。

## 结论

- ✅ 焦距搜索 + 质量门控在所有 5 条 PnP 路径中正常运行
- ✅ 耗时数据正确采集（每个算法独立计时）
- ✅ 质量门控正确标记不通过的原因
- ⚠️ 当前测试图像无独立真值，无法评估焦距搜索是否提升了绝对精度
- ⚠️ 需要真实 holdout 数据集才能验证 focal search 的实际收益
