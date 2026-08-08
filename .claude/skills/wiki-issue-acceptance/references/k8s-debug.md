# K8s 测试环境（namespace `$DEPLOY_NS`）故障排查速查

> 环境变量（`$DEPLOY_NS` / `$INTERNAL_SLB` / `$KUBECONFIG_PATH` / `$PROD_URL` / `$GIT_REPO` / `$DEBUG_HOST`）由主技能 SKILL.md 启动期从**当前项目根 `CLAUDE.md` 的《项目环境档案》**解析（见 `~/.claude/skills/_shared/project-profile.md`）；本项目解析出来即 `staging` / `$INTERNAL_SLB` / `~/.kube/config` / `https://app.example.com` / `your-org/your-repo` / `debug-host`。本文 deployment 名（`web-bff` 等）见《环境档案》`KEY_DEPLOYMENTS`，下用 示例。
> 全部命令默认在本机执行（VPN 内网或公网兜底已开），`KUBECONFIG=$KUBECONFIG_PATH`、namespace `$DEPLOY_NS`。
> **不要**用 `ssh $DEBUG_HOST "docker compose ..."` —— 那是历史 bug 排障机，不是本验收技能的测试环境。

## 0. 前置检查

```bash
export KUBECONFIG="$KUBECONFIG_PATH"

# 集群入口探针（不通就别重试 kubectl；IP/端口为 示例项目 集群 API 示例，换项目按实际入口替换）
nc -z -G 5 10.0.0.30 6443 && echo "入口可达" || echo "❌ 连 VPN 或公网兜底 on"

# 身份+权限
kubectl auth whoami
kubectl get pods -n "$DEPLOY_NS" --no-headers | wc -l
```

## 1. Pod 状态速查

```bash
# 全部 Pod（状态/重启/年龄/节点）
kubectl get pods -n "$DEPLOY_NS" -o wide

# 只看不健康的
kubectl get pods -n "$DEPLOY_NS" --field-selector=status.phase!=Running

# 指定 Deployment 的 Pod
kubectl get pods -n "$DEPLOY_NS" -l app=web-bff

# Pending / CrashLoopBackOff 详情
kubectl describe pod -n "$DEPLOY_NS" <pod-name> | tail -40
```

## 2. 日志查看

```bash
# 最近 100 行（按 Deployment 自动选活跃 Pod）
kubectl logs deploy/<svc> -n "$DEPLOY_NS" --tail 100 --timestamps

# 实时跟（30s 超时）
timeout 30 kubectl logs -f deploy/web-bff -n "$DEPLOY_NS"

# 多容器（带 sidecar/init）
kubectl logs deploy/web-bff -n "$DEPLOY_NS" -c <container-name> --tail 100

# 多服务同时看（开多终端或逐个）
kubectl logs deploy/web-bff -n "$DEPLOY_NS" --tail 50 --timestamps
kubectl logs deploy/celery-worker -n "$DEPLOY_NS" --tail 50 --timestamps

# 错误检索
kubectl logs deploy/web-bff -n "$DEPLOY_NS" --tail 500 2>&1 | grep -iE "error|exception|traceback"

# 上一次崩溃日志（OOMKilled / CrashLoopBackOff 必查）
kubectl logs deploy/celery-worker -n "$DEPLOY_NS" -p --tail 100
```

## 3. 常见故障及处理

### web-bff 无响应 (5xx / 连接拒绝)

```bash
# 1. Pod 是否健康
kubectl get pods -n "$DEPLOY_NS" -l app=web-bff
kubectl describe pod -n "$DEPLOY_NS" <pod-name> | grep -A5 "Conditions:"

# 2. 启动日志（最近 100 行）
kubectl logs deploy/web-bff -n "$DEPLOY_NS" --tail 100 --timestamps

# 3. SLB → Pod 链路诊断（本机直打 SLB）
curl -sf -o /dev/null -w "HTTP %{http_code}\n" http://$INTERNAL_SLB:30115/health

# 4. 重启（rolling，秒级断流）
kubectl rollout restart deploy/web-bff -n "$DEPLOY_NS"
kubectl rollout status deploy/web-bff -n "$DEPLOY_NS" --timeout=3m
```

### celery-worker 任务卡住

```bash
# Worker 日志
kubectl logs deploy/celery-worker -n "$DEPLOY_NS" --tail 100

# Redis 队列积压
kubectl exec -n "$DEPLOY_NS" deploy/redis -- redis-cli llen celery

# 看 active workers（Celery Inspect）
kubectl exec -n "$DEPLOY_NS" deploy/celery-worker -- \
  celery -A web.bff.celery_app inspect active

# 重启 worker
kubectl rollout restart deploy/celery-worker -n "$DEPLOY_NS"
```

> ⚠️ 测试环境 redis 无 AOF/RDB（示例项目 staging 经验），restart 后 in-flight 消息会丢，参考 memory `[[redis_broker_message_loss]]`。

### postgres 连接失败

```bash
# Pod 健康
kubectl get pods -n "$DEPLOY_NS" -l app=postgres

# 直接 ping psql
kubectl exec -n "$DEPLOY_NS" deploy/postgres -- psql -U appuser -d appdb -c 'SELECT 1;'

# 连接数
kubectl exec -n "$DEPLOY_NS" deploy/postgres -- \
  psql -U appuser -d appdb -c \
  "SELECT count(*) FROM pg_stat_activity WHERE datname='appdb';"

# 外部访问（仅 SRE，强密码端口）
psql "postgresql://appuser:<pwd>@$INTERNAL_SLB:30148/appdb" -c "SELECT 1;"
```

### algo 服务不可用 / GPU Pending

```bash
# 直接打 SLB
curl -sf http://$INTERNAL_SLB:30120/health | python3 -m json.tool

# Pod 调度状态
kubectl get pod -n "$DEPLOY_NS" -l app=algo -o wide

# Pending 时看事件
kubectl describe pod -n "$DEPLOY_NS" <algo-pod> | tail -30
# → 通常是 "Insufficient aliyun.com/gpu-mem" 或被钉到节点 2 满载

# 看 image tag（确认 CD 是否到位）
kubectl get deploy algo -n "$DEPLOY_NS" \
  -o jsonpath='{.spec.template.spec.containers[0].image}'

# 钉死节点 2 时移回节点 1（参考项目根 CLAUDE.md 的调度落点硬约束）
mkdir -p backup
kubectl get deploy algo -n "$DEPLOY_NS" -o yaml > backup/algo-$(date +%Y%m%d-%H%M%S).yaml
kubectl patch deploy algo -n "$DEPLOY_NS" --type=json \
  -p '[{"op":"remove","path":"/spec/template/spec/nodeSelector"}]'
kubectl rollout status deploy/algo -n "$DEPLOY_NS"
```

### MinIO 文件上传失败

```bash
# Pod 健康
kubectl get pods -n "$DEPLOY_NS" -l app=minio

# 日志
kubectl logs deploy/minio -n "$DEPLOY_NS" --tail 50

# bucket / object 列表（Pod 内 mc 已就绪）
kubectl exec -n "$DEPLOY_NS" deploy/minio -- mc ls local/ | head
kubectl exec -n "$DEPLOY_NS" deploy/minio -- mc ls local/<bucket>/ --recursive | head -20

# 外部 S3 API 健康
curl -sf -o /dev/null -w "HTTP %{http_code}\n" http://$INTERNAL_SLB:30145/minio/health/live
```

### CVAT 不可达

```bash
# 三个组件都看：cvat-proxy（30130）/ cvat-server（30135）/ cvat-ui
kubectl get pods -n "$DEPLOY_NS" -l 'app in (cvat-proxy,cvat-server,cvat-ui)'

curl -sf -o /dev/null -w "proxy %{http_code}\n" http://$INTERNAL_SLB:30130/
curl -sf -o /dev/null -w "server %{http_code}\n" http://$INTERNAL_SLB:30135/api/server/about

kubectl logs deploy/cvat-server -n "$DEPLOY_NS" --tail 100
```

## 4. 代码更新后的发布流程

K8s 不是 SSH `git pull` 形态——靠 push 到 main → `build-images.yml` 构镜像 → `cd.yml` 自动 `kubectl set image` → rollout。

```bash
# 1. push 后跟 CI
SHA=$(git rev-parse origin/main)
export GH_TOKEN="$(gh auth token --user zhaod39_example-corp)"
gh run list --repo "$GIT_REPO" --commit "$SHA" --limit 5 \
  --json databaseId,name,status,conclusion

# 2. CI 绿后跟 rollout
kubectl rollout status deploy/web-bff -n "$DEPLOY_NS" --timeout=5m
kubectl rollout status deploy/celery-worker -n "$DEPLOY_NS" --timeout=5m
kubectl rollout status deploy/algo -n "$DEPLOY_NS" --timeout=5m

# 3. 健康自检（rollout status 已阻塞到就绪，用 curl 自带重试替代裸 sleep）
curl -sf --retry 5 --retry-delay 2 --retry-all-errors http://$INTERNAL_SLB:30115/health | python3 -m json.tool
curl -sf --retry 5 --retry-delay 2 --retry-all-errors http://$INTERNAL_SLB:30120/health | python3 -m json.tool

# 4. 确认 image tag 真的换了（避免 moving tag 冻结，参考 memory [[cd_moving_tag_frozen]]）
kubectl get deploy web-bff celery-worker algo -n "$DEPLOY_NS" \
  -o custom-columns=NAME:.metadata.name,IMAGE:.spec.template.spec.containers[0].image
```

> ⚠️ Harbor 对 `0.1.0-*/latest` 启用镜像不可变 → moving tag 永久冻结；部署只认 per-SHA tag。手动 dispatch 留空曾部旧代码（已修 PR #383）。

## 5. 数据库操作速查

```bash
# 进入 psql 交互
kubectl exec -it -n "$DEPLOY_NS" deploy/postgres -- psql -U appuser -d appdb

# 单条查询（非交互）
kubectl exec -n "$DEPLOY_NS" deploy/postgres -- \
  psql -U appuser -d appdb -c '<SQL>'

# 表记录统计
kubectl exec -n "$DEPLOY_NS" deploy/postgres -- \
  psql -U appuser -d appdb -c \
  "SELECT schemaname, tablename, n_live_tup FROM pg_stat_user_tables ORDER BY n_live_tup DESC LIMIT 20;"

# 看最近 N 条
kubectl exec -n "$DEPLOY_NS" deploy/postgres -- \
  psql -U appuser -d appdb -c \
  "SELECT id, status, created_at FROM <table> ORDER BY created_at DESC LIMIT 10;"
```

## 6. 资源/调度/事件诊断

```bash
# 集群事件（自动按时间倒序，看最近问题最有用）
kubectl get events -n "$DEPLOY_NS" --sort-by='.lastTimestamp' | tail -30

# Deployment 资源 limits
kubectl get deploy -n "$DEPLOY_NS" -o custom-columns=\
NAME:.metadata.name,\
CPU_REQ:.spec.template.spec.containers[*].resources.requests.cpu,\
MEM_REQ:.spec.template.spec.containers[*].resources.requests.memory,\
MEM_LIM:.spec.template.spec.containers[*].resources.limits.memory

# Pod 实时资源（需 metrics-server）
kubectl top pod -n "$DEPLOY_NS" --sort-by=memory | head -15

# OOMKilled 历史排查
kubectl get pods -n "$DEPLOY_NS" \
  -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.containerStatuses[*].lastState.terminated.reason}{"\n"}{end}' \
  | grep OOMKilled
```

## 7. 端口/服务可达性

```bash
# Service / LB endpoints
kubectl get svc -n "$DEPLOY_NS" | grep LoadBalancer
kubectl get endpoints -n "$DEPLOY_NS"

# 本机直打 SLB（VPN 内或公网兜底 sshuttle 内可达）
for p in 30110 30115 30120 30125 30130 30135 30140 30145; do
  printf "%s: " "$p"
  curl -m 3 -sf -o /dev/null -w "HTTP %{http_code}\n" "http://$INTERNAL_SLB:$p/" || echo "DOWN"
done

# 线上域名（绕 SLB 直走域名）
curl -sk -o /dev/null -w "$PROD_URL %{http_code} %{remote_ip}\n" "$PROD_URL/"
```

## 8. 备份/回滚（动 Deployment 前必做）

```bash
mkdir -p backup
# 备份当前 Deployment
kubectl get deploy <name> -n "$DEPLOY_NS" -o yaml > backup/<name>-$(date +%Y%m%d-%H%M%S).yaml

# 回滚到上一个 ReplicaSet
kubectl rollout undo deploy/<name> -n "$DEPLOY_NS"
kubectl rollout status deploy/<name> -n "$DEPLOY_NS"

# 看 rollout 历史
kubectl rollout history deploy/<name> -n "$DEPLOY_NS"
```

## 9. 关联 memory（验收前必读）

- `[[prod_schema_drift_mechanism]]` — 测试环境（示例项目 staging）用 create_all + schema_sync，DROP列/改类型的 migration 永不落库，本地绿不代表线上 schema 跟得上
- `[[redis_broker_message_loss]]` — redis 无 AOF/RDB，restart 擦消息；自愈判别用 `algo_job_id IS NULL` 而非 `celery_task_id IS NULL`
- `[[opensearch_sync_broken]]` — OS metadata 子系统 6 层叠加故障历史，已修
- `[[cd_moving_tag_frozen]]` — Harbor 镜像不可变 + moving tag 永久冻结的部署陷阱
- `[[intranet_dev_checklist]]` — 内网态切换、KUBECONFIG 默认值
- `[[public_network_dev_fallback]]` — 外网态 sshuttle / web-app / npc 兜底脚本
