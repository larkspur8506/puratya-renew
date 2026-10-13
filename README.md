# MWS 自动续期（puratya-renew）

[cloud.m-ws.cc](https://cloud.m-ws.cc)（MWS，原 cloud.puratya.com）的 Bot / 网站有 **7 天倒计时**，到期会自动停止。点一下 `Renew` 按钮就能把倒计时重置回 7 天。

这个项目帮你**每周一、三、五自动点续期**，让你挂在上面的 Bot / 网站永不停止，完全免费、不用自己每天登录去点。

> **域名已更换（2026-09）**：面板从 `cloud.puratya.com` 迁到 [`cloud.m-ws.cc`](https://cloud.m-ws.cc)。续期接口没变，但 `__Host-` Cookie 绑死主机名，旧域名上的 token **不能再用**。请在新域名重新登录，按下面步骤重新抓 `MWS_TOKEN`。

## 原理

续期按钮背后其实就是一次请求：

```
POST /api/bots/{id}/renew     # Bot 续期
POST /api/sites/{id}/renew    # 网站续期
```

脚本打的是 `https://cloud.m-ws.cc/api`（和浏览器里 Network 看到的一样）。后端实际在 `cloud-api.m-ws.cc`，一般不用改。如果以后又换域名，可设环境变量 `MWS_API` 覆盖。

脚本每周一、三、五定时跑一次，把账号下所有 Bot / 网站全部续期，然后**直接推送到你自己的 Telegram**（Bot API）。

> 本仓库已改为**直发 Telegram**，不再依赖原作者的 notify-gateway。要通知就配两个 Secret：`TG_BOT_TOKEN` + `TG_CHAT_ID`；不配也能正常续期，只是没有通知。
> 旧的 `NOTIFY_URL` / `NOTIFY_TOKEN`（notify-gateway）仍作为**回退**保留：没配 TG 时才走它。

> **重要行为（2026-10 起）**：自动换票失败（例如 Discord Token 失效）**不再中断续期**。只要手上那枚 JWT 还能通过 `/auth/me`，脚本就继续用它续期，只发一条告警 —— 避免「因为换票失败，导致 Bot 没人点 Renew 被停」。

## 用法（3 步）

### 1. Fork 本仓库

点右上角 **Fork**。

### 2. 填 Secrets

进入你的仓库 → **Settings** → **Secrets and variables** → **Actions** → **New repository secret**，填：

| Name             | 值                                   | 必填 |
| ---------------- | ------------------------------------ | ---- |
| `MWS_TOKEN`      | 面板登录 JWT（下面教你怎么拿）         | 建议 |
| `DISCORD_TOKEN`  | Discord 用户 Token，JWT 剩余不足 7 天时自动 OAuth 换票 | 自动换票必填 |
| `GH_TOKEN`       | GitHub classic PAT，**只勾 `repo`**，用来把新 JWT 写回 `MWS_TOKEN` | 自动换票必填 |
| `TG_BOT_TOKEN`   | 你自己的 Bot Token（@BotFather 申请，形如 `123456:AA...`） | ✅ 要通知就填 |
| `TG_CHAT_ID`     | 目标聊天/频道 ID（个人是纯数字；频道是 `-100...`） | ✅ 要通知就填 |
| `TG_THREAD_ID`   | 可选：频道/群里的话题（topic）ID       | 可选 |
| `TG_API_BASE`    | 可选：自建反代用，默认 `https://api.telegram.org` | 可选 |
| `NOTIFY_URL`     | 可选回退：notify-gateway 上报地址（不配 TG 时才用） | 可选 |
| `NOTIFY_TOKEN`   | 可选回退：网关里该项目分配的 Key       | 可选 |

> `DISCORD_TOKEN` + `GH_TOKEN` 配齐后，和 [bothosting](https://github.com/2Bdou/bothosting) 一样：JWT 剩余不足 7 天时用 Discord 重新登录，并强制写回 `MWS_TOKEN`。只配 `MWS_TOKEN` 也能续期，但大约一个月后还是要手动抓一次。
>
> **换票失败不会再拖垮续期**（2026-10 起）：Discord Token 失效时只发一条告警，然后继续用现有 JWT 续期；只有「现有 JWT 本身已 401 / 完全没配」才会失败退出。
>
> Actions 自带的 `GITHUB_TOKEN` **不能**改 Secrets，必须另建 classic PAT，Secret 名称就叫 `GH_TOKEN`。

> 通知走自己的 Telegram：`TG_BOT_TOKEN` + `TG_CHAT_ID`。`TG_CHAT_ID` 拿法：把 bot 拉进目标频道/群（频道要设为管理员），然后 `curl "https://api.telegram.org/bot<TOKEN>/getUpdates"` 看 `chat.id`；频道的 id 是 `-100` 开头。想发到自己的收藏夹/私聊就直接给 bot 发一条消息，再用 `getUpdates` 取 id。
>
> 原作者的 `NOTIFY_URL` / `NOTIFY_TOKEN`（notify-gateway）已降级为**可选回退**：只有没配 TG 时才走网关。两者都没配时，脚本只在日志里打一条 warning，续期照常。


### 3. 手动跑一次验证

仓库 → **Actions** → 左侧 **MWS Renew** → **Run workflow** → **Run workflow**。看到绿色 ✅ 就成功了。

## 怎么拿 MWS_TOKEN

必须在**新域名**上抓，旧站 `cloud.puratya.com` 上的 Cookie 已被站长清掉。

1. 浏览器打开并登录 [cloud.m-ws.cc](https://cloud.m-ws.cc)
2. 按 `F12` 打开开发者工具 → 顶部选 **Network（网络）**
3. 刷新页面（或点一下 `Renew` 按钮）
4. 点任意一个 `bots` / `renew` / `auth/me` 请求（地址会是 `cloud.m-ws.cc/api/...`）
5. 在 **Request Headers** 里找到 `cookie:` 这一行，复制 `__Host-mrtcloud_token=` **后面那一长串**（是 `eyJ...` 开头的）
6. 粘贴进 GitHub Secret `MWS_TOKEN`（覆盖旧值）

也可以在同一请求里复制 `authorization: Bearer ` 后面的 JWT，内容和 Cookie 里那串是一样的。

## ⚠️ Token 有效期 / 自动换票

`MWS_TOKEN` 是个 JWT，**约 26 天后过期**。MWS 本身没有 refresh 接口（登录只有 Discord OAuth），所以不能「续 JWT」，只能重新登录拿一张新的。

自动换票（和 bothosting 同一套路）：

1. 配 `DISCORD_TOKEN`（从 Discord 网页版 Network 里 `authorization` 字段复制，和 bothosting 用的是同一种）
2. 配 `GH_TOKEN`（classic PAT，只勾 `repo`，建议永不过期）
3. 脚本发现 JWT 失效或剩余不足 7 天时：向 MWS 要 OAuth `state` → 用 Discord Token 调 `oauth2/authorize` → 打开 `cloud-api.m-ws.cc/auth/callback` 拿到新 JWT → `gh secret set MWS_TOKEN`
4. 日志里应出现 `MWS_TOKEN 已写回 GitHub Secrets`

`DISCORD_TOKEN` 一般比 JWT 耐用得多（改密码 / 退出所有会话才会作废）。它失效时通知会写 `Discord Token 已失效（HTTP 401）`，再按下面步骤更新即可。

没配 Discord 时，过期仍会通知你手动抓 `MWS_TOKEN`。

## 怎么拿 DISCORD_TOKEN

1. 浏览器登录 Discord **网页版**
2. F12 → Network（网络）→ 点任意频道
3. 找一条 API 请求，Request Headers 里的 `authorization` 就是（不要带 `Bot ` 前缀）
4. 粘贴进 GitHub Secret `DISCORD_TOKEN`

可以和 bothosting 仓库用同一串。不要把这串提交进 git。

## 改运行时间

默认每周一、三、五**北京时间 09:00** 跑一次。要改，编辑 `.github/workflows/renew.yml` 里的 `cron`（注意 GitHub 用 UTC 时间，北京时间减 8 小时；5 个字段是「分 时 日 月 星期」，星期 1=周一）：

```
'0 1 * * 1,3,5'   # UTC 01:00 = 北京时间 09:00，周一三五
```

- 每天：`'0 1 * * *'`
- 每 3 天：`'0 1 */3 * *'`（月末会跳，介意就用星期枚举）

改完 commit 到默认分支生效。

## 本地跑一次

```bash
python3 -m pip install -r requirements.txt
export MWS_TOKEN='你的 JWT'
# 可选：通知（直发 Telegram）
export TG_BOT_TOKEN='123456:AA...'
export TG_CHAT_ID='-100xxxxxxxxxx'
# 可选回退：NOTIFY_URL / NOTIFY_TOKEN（notify-gateway）
python3 renew.py
```

## 免责声明

本项目仅供个人使用，用于续期你自己的账号资源。请遵守 MWS 平台的服务条款，不要用于批量注册或薅羊毛。
