# LAS 影像 3D 查询与视觉定位

本项目把查询图像与 LAS 点云派生的投影、坐标图和视觉特征进行匹配，提供区域三维坐标查询与相机位姿定位。后端使用 FastAPI、SQLAlchemy 和 SQLite，定位管线包含 SIFT、SALAD、LightGlue、RoMa 与 ACE 等实现路径。

## 快速开始

环境要求：Python 3.10+；完整点云预处理还需要可用的 `pdal` 与 `octree_build`。

```bash
python -m venv .venv
.venv/bin/pip install -e '.[dev]'
./start.sh
```

启动后可访问：

- Web：`http://localhost:8000/`
- API 文档：`http://localhost:8000/docs`
- 健康检查：`http://localhost:8000/api/health`

停止或查看服务：

```bash
./stop.sh
./scripts/status.sh
```

## 工程工作流

仓库使用 DDD + SDD + TDD 的渐进式统一模板：

- `docs/`：长期稳定的产品、领域和技术认知
- `contexts/`：限界上下文、职责和现有代码映射
- `specs/`：每次特性变更的结构化规格包
- `api/`、`services/`、`web/`：存量生产代码；后续按上下文渐进演化
- `tests/`：跨上下文验收、契约、系统测试与共享夹具
- `reports/`：规格追踪和验证报告

开始开发前请阅读 [工程实践](docs/engineering-playbook.md) 和 [协作规则](AGENTS.md)。

## 质量命令

```bash
./scripts/validate-specs.sh
./scripts/run-all-tests.sh fast
./scripts/traceability-report.sh
./scripts/drift-check.sh
```

完整测试（可能需要 LAS 样本、模型权重、外部二进制或较长运行时间）：

```bash
./scripts/run-all-tests.sh all
```

## 运行数据

- `las/`：输入点云与 COLMAP/轨迹元数据
- `query_images/`：上传的查询图像与本地数据库
- `projections/`：投影图、XYZ/法线图、索引和模型产物
- `logs/`：运行日志

这些目录中的大部分内容是本地运行产物，不应提交到版本控制。
