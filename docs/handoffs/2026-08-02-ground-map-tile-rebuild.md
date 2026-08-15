# 交接：四向斜地面 MapTile 清理与全量重建

日期：2026-08-02  
状态：代码/规格已修复，旧资产尚未删除，全量重建尚未执行  
工作目录：`/Users/pangjinfu/code/opencode-demo`

## 1. 用户最终确认的生产契约

1. 生产位置必须包含完整过滤后的轨迹位置与完整网格位置，不能只取少量轨迹点。
2. 每个位置只生成四个欧拉角视图：
   - `yaw ∈ {0°, 90°, 180°, 270°}`
   - `pitch = -15°`
   - `roll = 0°`
3. 四张图均为斜向地面视图，不是垂直俯视、水平八向或多 pitch 实验图。
4. 每个 MapTile 必须记录实际渲染位置、Euler、world→camera 四元数、COLMAP 渲染行、FOV、坐标约定、accepted 状态和拒绝原因。
5. 多 pitch/水平八向只能写 `projections/experiments/*`，不得覆盖生产索引。

权威规格：

- `specs/002-realtime-localization-optimization/spec.md`：AC-002-08 至 AC-002-10
- `specs/002-realtime-localization-optimization/clarify.md`：CL-002-07、CL-002-08
- `specs/002-realtime-localization-optimization/decisions.md`：DEC-002-05、DEC-002-06
- `specs/002-realtime-localization-optimization/bugfix-ground-facing-tile-poses.md`

## 2. 数量核对：为什么应为 2,732 个计划视图

当前真实输入审计结果：

| 项目 | 数量 | 依据 |
| --- | ---: | --- |
| 原始轨迹点 | 194 | `_load_poses_and_offset("las")` |
| 正式规则过滤后轨迹点 | 107 | `_filter_trajectory_poses(..., min_time_sec=1.0, min_dist_m=4.0)` |
| 点云范围网格点 | 576 | 10m 网格，来自当前 octree manifest 边界 |
| 生产位置 | 683 | 107 + 576 |
| 每位置视图 | 4 | 四个正交 yaw，pitch=-15、roll=0 |
| 计划视图 | 2,732 | 683 × 4 |

注意：`2,732` 是 planned 数量。渲染失败、低密度、黑像素比例超限或 XYZ 不可用的视图仍会保留 pose/reason，但不会计入 accepted PNG 数量。

此前 `2,316` 来自错误实验运行：仅保留 3 个轨迹点，加 576 个网格点，即 `(3 + 576) × 4`。这不是完整生产计划。

## 3. 已完成的代码修复

### 地图准备

- `services/las_processor/projection_octree.py`
  - `GROUND_VIEW_DIRECTIONS` 固定四向 `pitch=-15/roll=0`。
  - 显式 Euler 是否存在按字段判断，`0/0/0` 不再错误回退轨迹四元数。
  - MapTile schema v2 保存完整实际渲染位姿和发布状态。
  - 生成器不再自动删除历史文件，`tile_index.json` 是发布边界。
- `services/las_processor/tile_index_migration.py`
  - 可把旧 32 向索引迁移为四向 p-15 临时发布集合。
- `scripts/render_ground_tiles.py`
  - 正式全量渲染入口。
  - 生产目录拒绝 `--max-poses`，防止再次发布不完整轨迹集合。
- `scripts/render_multi_pitch_tiles.py`、`scripts/render_horizontal_tiles.py`
  - 默认输出实验目录并拒绝生产 `projections/`。

### 消费端边界

- `services/localizer/salad_roma_v2.py`：描述子缓存先与当前 accepted MapTile 求交集，禁止旧 key fallback。
- `services/localizer/salad_roma.py`、`services/localizer/__init__.py`、`services/matcher/__init__.py`：排除 rejected MapTile。
- `services/localizer/verify_projection.py`、两条诊断脚本：只读取 accepted 发布清单，不再 glob 磁盘中的水平/多 pitch 历史图。

### 回归测试

- `services/tests/test_core.py`
  - 四向 Euler、forward 世界 Z<0、显式零 Euler、完整 MapTile pose。
  - 完整轨迹位置与网格位置合并后再乘四向。
- `services/tests/test_map_tile_migration.py`
  - 32 向迁移、缺失资产原因。
  - 生产入口拒绝 `--max-poses`。
- `services/tests/test_salad_roma.py`、`services/tests/test_localization_regressions.py`
  - V2、原版与诊断路径都不能重新消费旧实验资产。

最新门禁：

```text
./scripts/run-all-tests.sh fast  -> 73 passed, 4 deselected
./scripts/validate-specs.sh      -> 通过，3 个规格包
./scripts/drift-check.sh         -> 0 error，6 个历史运行产物 warning
git diff --check                 -> 通过
```

## 4. 当前运行资产真实状态

旧资产仍然存在，删除命令没有执行成功：

| 资产 | 当前状态 |
| --- | --- |
| `projections/tiles` | 51GB |
| PNG | 8,550 |
| NPY（含 XYZ 与 normal） | 17,100 |
| 当前临时发布索引 | 579 位置、2,316 planned、1,028 accepted |
| `salad_index.npz` / `salad_index_v2.npz` | 仍存在，基于旧 tile |
| `tile_features_index.json` | 仍存在，基于旧 tile |
| `ace_model.pth` | 仍存在，基于旧 tile，应随旧资产失效 |
| `projections/octree_data` | 399MB，可复用，必须保留 |
| `projections/downsampled_las` | 568MB，可复用，必须保留 |
| `projections/localize` | 定位结果产物，非本次删除目标 |

永久删除曾被安全审批拦截。用户已提出“删除之前生成的”，但系统要求在明确告知 51GB 永久删除不可恢复后再次确认。当前任务结束时尚未收到那句确认，下一位 Agent 不得假设已经删除。

## 5. 下一位 Agent 的建议续作顺序

### A. 先获得删除确认

向用户明确列出删除范围、不可恢复性和保留项，请用户确认“永久删除并重建”。不要用 Python、间接脚本或改名方式绕过删除审批。

删除目标应精确限定为：

- `projections/tiles/`
- `projections/tile_index.json`
- `projections/projection_view_poses.json`
- `projections/salad_index.npz`
- `projections/salad_index_v2.npz`
- `projections/tile_features_index.json`
- `projections/ace_model.pth`
- `projections/verify_result.json`
- `projections/*multi-pitch-backup-20260802T105610.json`
- `projections/*multi-pitch-backup-20260802T105742.json`

保留：`octree_data/`、`downsampled_las/`、`localize/`、LAS 输入、查询图像和报告。

### B. 全量重建

不要启动或联调 `slam-map` 服务。当前渲染器只调用已经编译好的 octree 命令行二进制，不需要启动服务。为避免 `glob("las/*.las")` 选中 `ground_points.las`，必须显式指定正式 LAS：

```bash
.venv/bin/python scripts/render_ground_tiles.py \
  --las las/default_2026-05-28-112428.las
```

禁止加入 `--max-poses`；生产入口也会主动拒绝。当前脚本 FOV 为 75°，如果要调整 FOV，必须先补规格决策和回归测试，不能静默变更。

octree 二进制当前解析路径：

```text
/Users/pangjinfu/code/slam-map/slam-map-engine/octree/build/octree_build
/Users/pangjinfu/code/slam-map/slam-map-engine/octree/build/octree_render
```

这是剩余可移植性风险：代码仍有外部绝对路径。用户此前要求“不启动、不联调 slam-map，将功能迁移过来”；后续可以把二进制/配置路径改为项目配置或本地工具适配，但不要在本次资产重建中扩大范围。

### C. 重建后验收

至少核对：

1. `projection_view_poses.json`：`count == 683`、`len(views) == 2732`。
2. 角度集合严格只有 `(0/90/180/270, -15, 0)`。
3. `tile_index.json` 共 2,732 条，每条都有 `camera_pose`；rejected 有 `reject_reason`。
4. accepted 的 PNG、XYZ NPY、normal NPY 均真实存在。
5. 随机抽查至少 3 个位置的四方向图：正立、斜向地面、没有朝天或横转。
6. 重建 SALAD 描述子后，V2 和原版 top-k 只返回当前 accepted p-15 key。
7. 运行快速测试、规格校验、漂移检查和 `git diff --check`。
8. 更新 TASK-002-11、bugfix 文档 Before/After 与实际 accepted/rejected 数量。

可用审计命令：

```bash
jq '.count, (.views | length), .view_contract' projections/projection_view_poses.json
jq -r 'map([.yaw_deg,.pitch_deg,.roll_deg]) | unique[] | @tsv' projections/tile_index.json
jq '[.[] | select(.accepted == true)] | length' projections/tile_index.json
jq '[.[] | select(.camera_pose == null)] | length' projections/tile_index.json
./scripts/run-all-tests.sh fast
./scripts/validate-specs.sh
./scripts/drift-check.sh
git diff --check
```

## 6. 尚未解决/不要误报完成

- 旧 51GB 投影尚未删除。
- 2,732 个正式视图尚未重建。
- SALAD 描述子和 ACE 模型尚未基于新资产重建。
- 当前 1,028 accepted 是旧 p-15 临时迁移结果，不是最终全量生产资产。
- Spec 003 Phase B 的独立真值 Benchmark 仍是 TODO，不得用同源 NPY 或内点数冒充绝对精度。
- 没有执行 Git commit、push、PR、部署或服务重启。

## 7. 工作区保护

工作区包含大量用户既有未提交修改和运行报告。不要 reset、checkout、clean 或批量覆盖。尤其不要把 `.DS_Store`、历史报告、日志删除等无关变化归因于本次 MapTile 修复。只触碰上述规格、投影生成/消费代码、测试和经用户确认的运行资产。

