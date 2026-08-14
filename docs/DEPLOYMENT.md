# DSA 云端客户端部署

## 部署前

1. 管理员把用户添加为模板仓库协作者(读权限):
   `python scripts/manage_collaborators.py add --owner <tpl-owner> --repo <tpl-repo> --pat <admin-pat> --user <username>`
2. 用户创建 PAT,最小权限 `repo` + `workflow`:
   https://github.com/settings/tokens/new?scopes=repo,workflow
   - 2FA 用户:经典 PAT 仍可用,但建议 fine-grained PAT(见下)
   - 若需完整配额卡片,额外勾选 `user`
   - 泄露应急:立即到 https://github.com/settings/tokens 撤销该 PAT
3. 模板仓库须为 private 且勾选 "Template repository"。

## 用户自部署(交互)

```bash
python scripts/deploy_user.py \
  --template-owner <tpl-owner> --template-repo <tpl-repo> \
  --owner <username> --pat <user-pat> \
  --llm-key <可选> --notify-webhook <可选> --stock-list 600519,600036 \
  --no-dry-run
```

## 代理部署(用户交付 PAT)

同上命令,由部署 agent 执行。授权边界:只在本次部署用途内使用,PAT 用完即废。

## 常用选项

- `--dry-run`(默认):只打印调用,不真正执行
- `--overwrite-secrets`:强制覆盖已有 secrets(默认 merge,只写不存在的)
- `--heartbeat-test`:部署后触发一次 stocks-only 运行,验证 heartbeat 续命链路
- 再次部署幂等:重复执行会跳过已存在仓库与已有 secrets

## 部署后验证

1. 打开 https://github.com/<owner>/dsa-cloud-<owner>/actions,确认 Actions 运行
2. 确认 `meta/heartbeat` 分支出现 heartbeat 提交:
   https://github.com/<owner>/dsa-cloud-<owner>/tree/meta/heartbeat
3. 打开 exe 客户端,输入用户名/仓库名/PAT 登录

## 故障排查

| 现象 | 原因 | 处理 |
|---|---|---|
| generate 返回 422 | 仓库已存在 | 正常,幂等跳过 |
| Actions 不运行 | 未启用 / 未安装 | 重跑 deploy(enable_actions) |
| heartbeat 分支无更新 | 仓库默认 workflow 权限被限制为 read-only 且拒绝 job 级提升 | Settings → Actions → Workflow permissions 允许提升;或改用 fine-grained PAT |
| 配额超限 | private 2000 分钟/月 | 等月重置或升级计划 |
