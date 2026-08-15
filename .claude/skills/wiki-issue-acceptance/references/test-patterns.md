# 测试策略与 API 模式参考（测试环境 K8s namespace `$DEPLOY_NS`）

> 环境变量（`$DEPLOY_NS` / `$INTERNAL_SLB` / `$KUBECONFIG_PATH` / `$PROD_URL` / `$DEBUG_HOST`）由主技能 SKILL.md 启动期从**当前项目根 `CLAUDE.md` 的《项目环境档案》**解析（见 `~/.claude/skills/_shared/project-profile.md`）；本项目解析出来即 `staging` / `$INTERNAL_SLB` / `~/.kube/config` / `https://app.example.com` / `debug-host`。下面端口（30115/30120 等）为 示例。
> 全部 API 通过 K8s SLB `$INTERNAL_SLB` 直打，**不要** SSH 到排障机（`$DEBUG_HOST`）跑 docker compose。
> BFF: `http://$INTERNAL_SLB:30115` / Algo: `http://$INTERNAL_SLB:30120` / 线上地址: `$PROD_URL`

## BFF API 通用规范

### 响应格式

所有接口统一包装：

```json
{ "code": 0, "message": "ok", "data": { ... } }
{ "code": 4001, "message": "参数错误", "data": null }
```

- `code == 0`：成功
- `code != 0`：失败，具体见 message

### 分页协议

list 类接口统一返回：

```json
{
  "code": 0,
  "data": {
    "items": [...],
    "total": 100,
    "page": 1,
    "page_size": 20
  }
}
```

测试要点：
- 空库时返回 `items: [], total: 0`（不是 null）
- `total` 与实际 COUNT 一致
- 超出范围的 page 返回空 items，不报错

### 认证

目前无 JWT 校验（已记录为 clarify 待决策），测试时无需 Authorization header。
若需要打线上域名（headless acceptance），用本地签 token，参考 memory `[[acceptance_jwt_credentials]]`，**不入库 / 不外传**。

---

## 按功能域的测试重点

### 数据采集（Spec 002）
- 摄像头 CRUD 与状态流转（online/offline/error）
- 快照入库：文件落 MinIO + DB 记录一致
- 分页过滤：按状态、时间范围、摄像头 ID

### 样本池（Spec 003）
- 样本入池条件（质量阈值、去重）
- 入池后状态变更
- 样本导出格式（ZIP 包含图片 + label JSON）

### 预标注（Spec 004）
- 任务创建：样本范围 + 模型版本参数校验
- Celery 任务状态轮询（pending → running → success/failed）
- CVAT 导入状态追踪
- 失败任务重试幂等

### 标注工作流（Spec 005）
- CVAT 任务创建成功（cvat-server / cvat-proxy Pod 可达）
- 标注任务状态同步
- 完成标注后数据集状态变更

### 数据集（Spec 006）
- 版本号自增规则
- 发布触发 Celery 任务
- 导出包含正确的图片 + 标注文件

### 训练评估（Spec 007 / MLflow）
- TrainingRun 记录写入
- MLflow experiment 关联（`http://$INTERNAL_SLB:30125`）
- 指标（mAP、precision、recall）正确存储

### 模型管理（Spec 008）
- 模型版本关联 TrainingRun
- 版本状态流转（draft → published → deprecated）

### 反馈循环（Spec 009）
- AI 事件接收与入库
- 事件关联摄像头/快照
- 触发入池或忽略逻辑

### 算法接口（Algo API :30120 → targetPort 38020）
- `/health` 返回 200
- 推理接口输入格式（base64 图片 / MinIO 路径）
- 推理结果格式（bbox, score, class_id）

---

## 常用 Curl 模板

```bash
# 环境变量（一次设好，本机直打 K8s SLB）
export BFF=http://$INTERNAL_SLB:30115
export ALGO=http://$INTERNAL_SLB:30120

# GET list 带分页
curl -sf "$BFF/api/v1/<resource>?page=1&page_size=20" | python3 -m json.tool

# POST 创建
curl -sf -X POST "$BFF/api/v1/<resource>" \
  -H 'Content-Type: application/json' \
  -d '{"key": "value"}' | python3 -m json.tool

# PUT 更新
curl -sf -X PUT "$BFF/api/v1/<resource>/<id>" \
  -H 'Content-Type: application/json' \
  -d '{"key": "value"}' | python3 -m json.tool

# DELETE
curl -sf -X DELETE "$BFF/api/v1/<resource>/<id>" | python3 -m json.tool

# Algo 推理
curl -sf -X POST "$ALGO/api/v1/detect" \
  -H 'Content-Type: application/json' \
  -d '{"image_base64": "..."}' | python3 -m json.tool

# 线上域名（公网兜底也可用，回流到测试环境 K8s）
curl -sk "$PROD_URL/api/v1/<endpoint>" | python3 -m json.tool
```

## pytest 批量

```bash
export KUBECONFIG="$KUBECONFIG_PATH"
BFF_BASE_URL=http://$INTERNAL_SLB:30115 \
ALGO_BASE_URL=http://$INTERNAL_SLB:30120 \
  python -m pytest tests/api/test_<spec>.py -v --tb=short 2>&1 | tail -60
```

---

## Edge Case 检查清单

| 类型 | 测试点 |
|------|-------|
| 空输入 | 必填字段缺失 → 422 |
| 非法 ID | 不存在的 UUID → 404 |
| 超长字符串 | name 字段 > 255 chars → 422 |
| 重复创建 | 唯一约束冲突 → 409 或 422 |
| 越界分页 | page=9999 → 空 items，不报错 |
| 并发写入 | 同时创建同名资源（幂等性） |
| 服务不可用 | `kubectl scale deploy/celery-worker --replicas=0` 后触发异步任务 → 合理错误码 |
| Schema 漂移 | 涉及 DROP/改列字段的 API → 线上可能 500（参考 `[[prod_schema_drift_mechanism]]`） |
| Redis 重启丢消息 | celery 任务在 redis pod restart 后是否能自愈（参考 `[[redis_broker_message_loss]]`） |

---

## 测试数据管理

```bash
# 数据库基本健康
kubectl exec -n "$DEPLOY_NS" deploy/postgres -- \
  psql -U appuser -d appdb -c '\dt'

# 核心表记录数
kubectl exec -n "$DEPLOY_NS" deploy/postgres -- \
  psql -U appuser -d appdb -c \
  'SELECT schemaname, tablename, n_live_tup FROM pg_stat_user_tables ORDER BY n_live_tup DESC LIMIT 20;'
```

测试产生的脏数据处理原则：
- 验收测试尽量使用独立的 name prefix（如 `acceptance-test-`）
- 不清除测试数据（保留便于复查）
- 如需干净环境，在测试前记录原有数据量，测试后对比增量
- **不要**直接在 `[[test_suite_shared_db_seed_fragility]]` 提示的共享 seed 表里插行 — 会破坏下游计数

---

## L3 E2E UI 模式（Playwright 打线上地址 `$PROD_URL`）

> 后端 200 ≠ 用户能用。web spec 必须有 E2E 验真实用户路径。

**交互式（Playwright MCP，首轮验收/调试）**：

```
browser_navigate $PROD_URL/<route>
browser_snapshot                 # 拿 a11y 树定位
browser_click / browser_fill_form / browser_select_option
browser_take_screenshot          # 路径写进 acceptance.md 证据列
browser_console_messages         # 抓前端 error
browser_network_requests         # 抓非预期 4xx/5xx
```

**固化（回归资产）**：

```bash
cd tests/acceptance/browser   # 已有 playwright.config.ts
npx playwright test <feature>_<spec>.spec.ts --reporter=line 2>&1 | tail -40
```

L3 判过 = 用户操作链路走通 + 渲染出预期数据 + console 无 error + 无非预期 4xx/5xx + 截图存证。

前端"看起来不对"先排除已知 anti-pattern（别误判成新 bug）：
- `[[frontend_utc_literal_display]]` UTC 字面值直显 / `[[frontend_truncated_page_as_total]]` 当前页当总数
- `[[frontend_window_open_s3_uri_antipattern]]` s3:// 死链 / `[[placeholder_message_info_antipattern]]` placeholder 静默失败

---

## 测试资产沉淀约定（步骤 7.5）

| 层次 | 落点 | 命名 |
|------|------|------|
| L1 单元/契约 | `tests/acceptance/unit/` | `test_<feature>.py` |
| L2 AC 级 | `tests/acceptance/ac/` | `test_AC_<spec>_<NN>.py`（首行 docstring 写 `SoT: spec.md L<行号>`） |
| L2 通用 API | `tests/api/` | `test_<feature>.py` |
| L3 E2E | `tests/acceptance/browser/` | `test_<feature>_<spec>.spec.ts` |

- 对抗式复核的反向用例固化为 `*_negative_*` 测试函数。
- 追溯矩阵 `tests/acceptance/ac/AC-STATUS.md`：每条 AC 一行，`pass / partial / fail`。
- 新增测试随 P1 修复同轨提交进 main，由 CI 复跑 = 资产兑现。

---

## 关联 memory

- `[[prod_schema_drift_mechanism]]` — 线上 DB schema 漂移机制
- `[[redis_broker_message_loss]]` — Redis broker 无持久化丢消息
- `[[test_suite_shared_db_seed_fragility]]` — pytest 共享 session SQLite 的 seed 脆弱性
- `[[acceptance_jwt_credentials]]` — 公网 BFF 跑 acceptance 用的本地签 token
- `[[opensearch_sync_broken]]` — OpenSearch 同步历史故障线索
