# 任务与影像上下文

## 职责

- 接收、校验和保存查询影像；
- 创建比较任务或定位任务；
- 管理 `pending → processing/running → completed/failed` 生命周期；
- 在进程重启后恢复可恢复任务；
- 持久化报告和失败信息。

## 当前代码

- `api/models.py`
- `api/database.py`
- `api/routes/images.py`
- `api/routes/tasks.py`
- `api/routes/localize.py`

## 待统一规则

- `processing` 与 `running` 应统一为明确的任务状态模型；
- 删除影像时需要定义关联任务、报告和文件的级联策略；
- 比较任务与定位任务的恢复语义应一致；
- 后台线程是当前适配实现，不是领域概念，未来可以替换为持久任务队列。
