# 002-移动场景实时定位优化方案

## 背景

当前定位管线（SALAD 检索 + RoMa 密集匹配 + 多轮 PnP 迭代）在固定摄像头场景可工作，但在移动场景下存在三个核心瓶颈：

| 瓶颈 | 原因 | 影响 |
|------|------|------|
| 多轮渲染开销大 | 每迭代一次重新投影 2D 图像和 npy | 延迟剧增 |
| RoMa 匹配计算重 | Transformer 密集匹配，单对需 100-300ms | 不适合移动端 |
| 缺少全局几何约束 | 仅有 2D 外观特征，缺少法线/拓扑/平面约束 | 初始位姿不准，迭代次数多 |

## 架构改进方向

### 一、引入显式空间感特征（把 3D 几何打入 2D 图像）

**当前问题**：投影图像只包含 RGB 或简单深度 npy，光照变化或纹理缺失时匹配极易失效。

**改进方案**：渲染阶段增加两类几何特征图：

**1. 表面法向量图 (Surface Normal Map)**
- 用 Open3D / PDAL 从点云计算法向量 (Nx, Ny, Nz)
- 渲染为 RGB 法线图（RGB 对应法线方向）
- 法线图对光照不变，提供三维朝向感（地面朝上、墙面朝前）

**2. 三维绝对坐标图 (Absolute XYZ Map)**
- 将点云的 (X, Y, Z) 全局坐标渲染为 3 通道图像
- 类似 DSAC++ / Scene Coordinate Regression 的做法
- 模型直接预测像素对应的 3D 坐标，一步 PnP 省去 RoMa

### 二、放弃多轮迭代：单次（Single-pass）位姿估计

**方案 1：2D-3D 场景坐标回归 (Scene Coordinate Regression)**
- 代表算法：DSAC* / ACE (Accelerated Coordinate Encoding)
- 用 LAS 点云训练轻量 ACE 网络（训练仅需几分钟）
- 查询图 → ACE → 直接输出每个像素的 3D 坐标 → 单次 RANSAC-PnP
- 延迟：**10-20ms**，非常适合移动场景

**方案 2：显式 3D 检索替代 2D SALAD**
- 保存每张预渲染图的真值位姿 (R, t) 和 3D 局部特征
- 引入 IMU/连续帧运动先验，缩小检索范围
- 提高 Top-1 位姿准确度

### 三、替换 RoMa：轻量级特征匹配器

| 匹配器 | 精度 | 速度（单对） | 移动端 | 建议 |
|--------|------|-------------|--------|------|
| RoMa（当前） | Dense 极高 | 100-300ms | ❌ | 仅用于离线建图 |
| **LightGlue** | Sparse 高 | 10-20ms | ✅ | **强烈推荐替换** |
| MAST3R/DUSt3R | Dense 3D 极高 | ~50ms | ⚠ 需 GPU | 可省去 PnP |

### 四、推荐管线架构

```
【离线/预处理阶段】
LAS 点云
  ├─ 计算法线/几何特征
  ├─ 训练 ACE 场景坐标回归网络
  └─ 渲染 RGB-Normal 数据库

【在线/移动端毫秒级定位】
现实拍摄图像
  │
  ├─ (可选) IMU/传感器先验 → 缩小检索半径
  │
  ▼ 【一步到位预测 3D 坐标】(ACE / LightGlue)
2D 像素 - 3D 点云直接对应关系
  │
  ▼ 【单次 RANSAC-PnP】(无需重投影迭代)
精准相机位姿 (R, t) → 快速投影物框/单应性变换
```

## 实施路线

### Phase 1：渲染增强（短期）
- 投影管线增加法线图、绝对 XYZ 图渲染
- 增强 SALAD 特征维度（RGB + Normal + XYZ 联合描述子）

### Phase 2：匹配器替换（中期）
- RoMa → SuperPoint + LightGlue
- 单次匹配 10-20ms，省去多轮迭代

### Phase 3：场景坐标回归（长期）
- 引入 ACE / DSAC* 训练管线
- 查询图直接回归 3D 坐标
- 端到端单次定位 10-20ms

## 参考算法

- **DSAC***: 《DSAC – Differentiable RANSAC for Camera Localization》(CVPR 2017/2020)
- **ACE**: 《Accelerated Coordinate Encoding: Learning to Relocalize in Minutes》(CVPR 2023)
- **LightGlue**: 《LightGlue: Local Feature Matching at Light Speed》(ICCV 2023)
- **MAST3R**: 《Grounding Image Matching in 3D with MASt3R》(ECCV 2024)
