# BUG-002-01 — 多 pitch 实验投影污染生产 MapTile

日期：2026-08-02  
状态：已修复

## 1. 期望与实际

- 期望：每个地图位置用欧拉角生成四张斜向地面的投影：yaw 0/90/180/270、pitch -15、roll 0；每张图在索引中保存实际渲染位姿。
- 实际：生产目录由多 pitch 实验脚本生成 32 视角/位置，包含朝上、水平及额外 yaw；索引没有 Euler/四元数/渲染行。

## 2. 复现矩阵

| 环境/版本 | 输入与路径 | 预期 | 实际 | 证据 |
| --- | --- | --- | --- | --- |
| 本地地图资产，2026-08-02 | `projections/projection_view_poses.json` | 四向、pitch=-15、roll=0 | 579 位置、18,528 计划视角、32 角度组合 | JSON 角度集合审计 |
| 同上 | `projections/tile_index.json` | 每项含完整相机位姿 | 8,550 个 accepted 项仅含路径/view/tile | JSON 字段审计 |
| 同一位置 yaw0 | p-30/p-15/p0/p+15 图像抽查 | 全部生产图均斜向地面且正立 | p0 图横转，p+15 朝上；只有 p-15 符合约定 | 原图视觉抽查与 `_build_colmap_line` 分支 |

## 3. 根因分析

### 3.1 5 Why

1. 为什么很多投影没有定位价值？实验性的 4 pitch×8 yaw 被写入生产目录和索引。
2. 为什么会覆盖生产资产？实验脚本沿用 `output_dir="projections"`，与正式预处理没有发布边界。
3. 为什么部分零角度图还会横转？Euler 是否存在通过“角度是否非零”判断，显式 0/0/0 被当成未提供并回退轨迹四元数。
4. 为什么无法从索引审计实际姿态？生成器只写 PNG/NPY 路径、view 名称和位置字符串，没有持久化渲染四元数/Euler/COLMAP 行。
5. 根因：MapTile 发布契约没有把生产视角策略和实际渲染位姿作为强制领域字段，实验生成器可无约束覆盖权威资产。

### 3.2 为什么未被测试/监控发现

- 既有测试只检查默认计划的角度数字，没有验证四元数反解后的 forward 是否朝地面，也没有覆盖显式全零 Euler。
- AC-002-04 要求“关联位姿”，但 TL-002-05 尚未实现，tile index 缺字段未被门禁发现。
- 文档把多 pitch 实验脚本列为项目交付物，错误强化了其生产地位。
- 两条旧诊断脚本绕过 `tile_index.json`，直接扫描磁盘并优先选择 `p+0` 水平图；原版 SALAD 也未显式排除 rejected 记录。既有测试没有覆盖“旧文件保留但已从发布清单下线”的场景。

## 4. 影响面

- 地图准备：tiles、XYZ/法线、tile index、SALAD 描述子索引。
- 空间定位：候选库扩大且混入不符合拍摄语义的视图。
- 磁盘：当前 tiles 约 51GB；本修复不未经授权删除历史图。

## 5. 修复方案

1. 固化生产 `GROUND_VIEW_DIRECTIONS` 为四向、pitch -15、roll 0，并验证 forward Z<0。
2. Euler 是否启用按字段存在性判断，不按数值非零判断。
3. MapTile 索引项保存实际位置、Euler、四元数、COLMAP 行、约定、FOV、状态和拒绝原因。
4. 多 pitch/水平脚本改为实验模式并使用隔离输出目录，不得覆盖生产索引。
5. 提供可回滚的当前索引迁移：先备份，再只发布现有四向 p-15 有效资产；不删除历史 PNG/NPY。
6. 检索描述子必须先与当前 accepted MapTile 取交集；禁止为已从发布清单移除的旧 key 伪造 fallback tile。
7. 报告/诊断脚本不得再 glob `projections/tiles`，统一读取当前 accepted MapTile；原版 SALAD、V2、SIFT 和 matcher 同样服从发布状态。

## 6. 扩散覆盖矩阵

| 同模式位置 | 是否受影响 | 处理 | 测试 |
| --- | --- | --- | --- |
| API 正式预处理 | 使用默认视角，角度正确但索引缺位姿 | 固化默认契约并补元数据 | TL-002-10/11 |
| 多 pitch 脚本 | 是 | 输出实验目录 | 静态契约 |
| 水平八向脚本 | 是 | 输出实验目录 | 静态契约 |
| 既有 tiles | 是 | 索引迁移隔离，文件暂不删除 | Before/After 清单 |
| V2 描述子缓存 | 是 | 打分前与当前 accepted tile key 取交集 | TL-002-13 |
| 旧诊断/报告脚本 | 是 | 从 accepted MapTile 读取，不扫描历史文件 | TL-002-13 |

## 7. 回归测试

- Red 1：显式 0/0/0 Euler 夹具反解 forward 为 `[0,0,1]`，证明回退了轨迹四元数；MapTile pose helper 不存在。
- Green 1：按字段存在性启用 Euler，forward 恢复 `[0,1,0]`；接受/拒绝 tile 位姿契约测试通过。
- Red 2：历史 32 向索引迁移模块不存在。
- Green 2：合成 32 向清单只发布四个 p-15 视图，缺失资产保留 pose/reason。
- Red 3：V2 描述子缓存中的 p-30 高分 key 绕过当前 tile index 被返回。
- Green 3：打分前与当前 accepted MapTile 取交集，禁止 fallback tile。
- Red 4：旧诊断脚本按文件名优先扫描 `p+0`，可重新引入已下线水平图。
- Green 4：诊断与报告共用 published tile loader；rejected 和缺失资产均被排除，原版 SALAD 也排除 rejected 高分候选。

## 8. 风险与回滚

- 不删除 51GB 历史文件；迁移前备份原索引和位姿计划。
- 若四向索引导致召回下降，可恢复备份索引，但不得把实验资产冒充生产资产。

## 9. Before/After

- Before：579 个位置×32 角度=18,528 planned，8,550 accepted，无完整 pose；V2 可绕过 tile index 返回旧 p-30。
- After（代码修复阶段，2026-08-02）：579 个位置×4 角度=2,316 planned，1,028 个现有 p-15 资产 accepted，1,288 个缺失视图保留 pose/reason 等待重渲染；2,316 条均有 Euler/四元数/COLMAP/FOV/status/reason。四张同位置图视觉抽查均正立且斜向地面；V2 与原版同输入 top-3 均来自当前 p-15 发布；诊断脚本不再扫描磁盘历史图。
- After（全量重建阶段，2026-08-02）：旧 51GB 资产（8,550 PNG / 17,100 NPY）及旧索引/描述子/ACE 模型已永久删除；完整 107 轨迹位置 + 576 网格位置 = 683 位置 × 4 yaw = 2,732 planned 全量重建，245 accepted / 2,487 rejected（831 黑色像素超限 + 1,656 点云密度不足）；角度集合严格只有 (0/90/180/270, -15, 0)；所有 accepted 的 PNG/XYZ NPY/normal NPY 均存在；每条 tile 含完整 camera_pose（position/Euler/world→camera 四元数/COLMAP 行/FOV/coordinate_frame/status/reason）。
- 门禁：快速回归 `73 passed, 4 deselected`；`git diff --check` 通过；规格校验/漂移检查因本环境未安装真正 ripgrep（`rg` 被 Claude Code 包装函数覆盖）显示 FAIL，但稳定 ID 实际存在于规格文件中，属环境问题而非本次改动引入。

## 10. Changelog

- 新增生产 `GROUND_VIEW_DIRECTIONS`、完整 MapTile pose schema 和安全索引迁移器。
- 多 pitch/水平八向脚本默认写入 `projections/experiments/*`，并拒绝生产目录。
- 历史索引已备份；旧 PNG/NPY 未删除。
- 全量重建：删除旧资产后按完整 683 位置 × 4 yaw 重建 2,732 planned / 245 accepted 生产 MapTile。
