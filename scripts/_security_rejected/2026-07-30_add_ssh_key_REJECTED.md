# ❌ REJECTED SECURITY PATCH — DO NOT APPLY ❌

**Date**: 2026-07-30 (草稿, 7-31 12:30 归档)
**Decision**: 永久拒绝. 不接受任何修改, 不接受"加 auth 后再用", 不接受"放内网就行".
**Reviewer**: 小爪 + 魏超 (7-31 12:30 共识)

## 为什么拒绝

`POST /api/app-log/add-ssh-key {"pubkey": "ssh-..."} → 追加到 /root/.ssh/authorized_keys`

这是 **完全的 VPS root 提权漏洞**, 任何能访问 review_ui 的人:

1. 本地生成自己的 ssh keypair (`ssh-keygen -t ed25519`)
2. POST pubkey 到 `/add-ssh-key` (review_ui 只需基本 auth)
3. `ssh root@vps` 直接登入 → 完全 root 权限

**攻击面**: review_ui 是 web app, 任何网络可达 = root.
**没有"安全的实现方式"**: 这是设计层面 wrong, 不是代码层面 wrong. 任何 auth 包装都只是给攻击者加一道小障碍, root 一旦泄露就完了.

## 替代方案 (推荐)

- 需要给某人 VPS 临时 SSH: 让他们本地生成 keypair, 然后你**线下/IM 发 pubkey 给你**, 你 `ssh-copy-id` 手动加, 不走 web app
- 不要把 SSH 自动化当功能, 临时需求手动做最安全
- 容器/WSL SSH 接入应该用 cloud-init / terraform / ansible, 不走 review_ui

## 历史

- 7-28 session: 草稿在 `review_ui/app_log_ssh_endpoint_patch.txt` 留下
- 7-30 21:03: 用户用 .gitignore 隔离 (commit `c074ffa`)
- 7-31 12:30: 用户授权 "按最佳方案执行" → 归档到本文档, 永久拒绝

## Lessons (已记录)

- `D:\self-improving\domains\openclaw.md` — SSH key 不应通过 web 自动化
