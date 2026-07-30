# 测试命名规范

pytest 测试名使用：

```text
test_<subject>_<observable_behavior>[_when_<condition>]
```

示例：

- `test_create_localize_task_rejects_unknown_algorithm`
- `test_pose_selector_prefers_more_inliers`
- `test_preprocess_reports_missing_las`

测试体按 Given / When / Then 分段组织，可用空行表达，不要求机械注释。断言外部可观察结果，例如返回状态、领域结果、持久化记录或发布契约；避免断言私有调用次数，除非该调用本身就是边界契约。

规格场景使用稳定 ID：

- `AC-xxx`：验收标准
- `TL-xxx`：测试清单
- `TASK-xxx`：实施任务
- `RISK-xxx`：风险

测试函数或测试 docstring 应包含对应 `TL-xxx`，以便生成追踪关系。
