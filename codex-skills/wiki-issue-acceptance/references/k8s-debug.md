# K8s 验收与只读排障

所有值从项目文档读取，不使用示例仓库的固定 namespace、端口或 deployment。

## 只读预检

```bash
kubectl config current-context
kubectl auth can-i get pods -n "$DEPLOY_NAMESPACE"
kubectl get deploy,pods,svc -n "$DEPLOY_NAMESPACE" -o wide
kubectl get events -n "$DEPLOY_NAMESPACE" --sort-by=.lastTimestamp
```

不要输出 kubeconfig、token 或 Secret。

## 版本确认

```bash
kubectl get deploy -n "$DEPLOY_NAMESPACE" \
  -o custom-columns=NAME:.metadata.name,IMAGE:.spec.template.spec.containers[*].image
```

把实际镜像标签/摘要与待验提交或发布版本比较。无法证明版本对应时，环境结论不得判 pass。

## 日志

```bash
kubectl logs deploy/<name> -n "$DEPLOY_NAMESPACE" --tail=200 --timestamps
kubectl logs deploy/<name> -n "$DEPLOY_NAMESPACE" -p --tail=100
kubectl describe pod <pod> -n "$DEPLOY_NAMESPACE"
```

限制时间窗和行数，先脱敏再保存证据。

## 数据检查

- 默认只做 SELECT、队列长度、对象列表等只读检查；
- 使用唯一测试前缀，记录测试前后数据；
- 不 flush、truncate、删除共享数据；
- 写入、迁移、scale、restart、patch、rollout undo 都需用户授权。

## 常见误判

- 本地代码已改，但测试环境仍运行旧镜像；
- Pod Ready，但业务依赖或后台 worker 不可用；
- API 200，但页面 bundle/缓存仍是旧版本；
- mock 测试通过，但数据库 schema 已漂移；
- 重试掩盖了幂等或消息丢失问题。

## 阻塞表达

环境不可达、权限不足或版本不明时，记录：

1. 已执行的只读检查；
2. 原始错误摘要；
3. 受影响的 AC/用例；
4. 需要用户或平台方提供的条件。
