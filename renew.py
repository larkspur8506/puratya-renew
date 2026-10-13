#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MWS (cloud.m-ws.cc) 自动续期脚本

原理：MWS 的 Bot/Site 有 7 天倒计时，到期自动停止。
     点一次 Renew 按钮 = POST /api/bots/{id}/renew，把倒计时重置回 7 天。
     本脚本每周一、三、五跑一次，把所有 Bot/Site 全部续期，永不停止。

登录态：__Host-mrtcloud_token（JWT，约 26 天有效）。
       过期或剩余不足 MWS_REFRESH_BEFORE_DAYS（默认 7 天）时，
       若配置了 DISCORD_TOKEN，则走 Discord OAuth 换新 JWT（和 bothosting 同一套路），
       再用 GH_TOKEN 写回 GitHub Secret MWS_TOKEN。
       也接受 Authorization: Bearer <token>。

通知：走 notify-gateway（notify.py 上报结构化结果，网关统一发邮件 + Telegram）。
     仓库只需配 NOTIFY_URL / NOTIFY_TOKEN，不内置 SMTP / TG。
依赖：requests（pip install requests）
"""

import os
import sys
import json
import urllib.error
from datetime import datetime, timedelta

import requests

from auth import (
    discord_relogin,
    discord_token,
    jwt_seconds_left,
    persist_mws_token,
)
from notify import notify, notify_text

# 通知抬头（可通过环境变量覆盖，例如 NOTIFY_FLAG=🇫🇮 NOTIFY_SERVICE=Bot-hosting）
FLAG = os.environ.get("NOTIFY_FLAG", "🇯🇵")
SERVICE = os.environ.get("NOTIFY_SERVICE", "puratya (MWS)")

# 面板：https://cloud.m-ws.cc ；后端也可以直连 https://cloud-api.m-ws.cc（无 /api 前缀）
FRONTEND = "https://cloud.m-ws.cc"
API = os.environ.get("MWS_API", FRONTEND + "/api").rstrip("/")

# Cloudflare 对 Python 默认 UA 返回 403 (error 1010)，需伪装成浏览器
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

COOKIE_NAME = "__Host-mrtcloud_token"


def http(path, method="GET", token=None):
    """请求 API，返回 (status_code, body)。网络异常返回 (0, 错误信息)。"""
    headers = {
        "User-Agent": UA,
        "Accept": "application/json",
        "Origin": FRONTEND,
        "Referer": FRONTEND + "/",
    }
    if token:
        headers["Cookie"] = COOKIE_NAME + "=" + token
        # 新后端同时认 Cookie 和 Bearer，双通道更稳
        headers["Authorization"] = "Bearer " + token
    if method != "GET":
        headers["Content-Type"] = "application/json"
    try:
        resp = requests.request(method, API + path, headers=headers, timeout=30)
        return resp.status_code, resp.text
    except requests.RequestException as e:
        return 0, str(e)


def now_str():
    """本地时间（workflow 里设 TZ=Asia/Shanghai 则显示北京时间）。"""
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")


def collect(token):
    """拉取所有 Bot / Site，返回 [(kind, id, name, remaining_hours, expires_at), ...]。"""
    items = []
    for kind, path, key in [("Bot", "/bots", "bots"), ("Site", "/sites", "sites")]:
        status, body = http(path, token=token)
        if status != 200:
            print("[!] 获取 {} 失败: HTTP {} {}".format(path, status, body))
            items.append((kind, None, "<列表获取失败 HTTP {}>".format(status), None, None))
            continue
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            data = []
        lst = data if isinstance(data, list) else data.get(key, [])
        for obj in lst or []:
            oid = obj.get("id")
            name = obj.get("name") or obj.get("username") or "id:{}".format(oid)
            timer = obj.get("timer") or {}
            rem = timer.get("remaining_hours")
            expires = (obj.get("expires_at") or obj.get("expire_at")
                       or obj.get("expired_at") or timer.get("expires_at"))
            items.append((kind, oid, name, rem, expires))
    return items


def renew_one(token, kind, oid):
    """续期单个对象，返回 (ok, status, body)。"""
    path = "/bots/{}/renew".format(oid) if kind == "Bot" else "/sites/{}/renew".format(oid)
    status, body = http(path, method="POST", token=token)
    return (status == 200), status, body


def _report(level, title, content, details):
    """上报通知；失败只打日志，不阻断续期主流程。"""
    try:
        res = notify(title, content, level=level, details=details)
        via = res.get("via") if isinstance(res, dict) else "?"
        print("[i] 通知已发送（{}）".format(via))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        print("::warning::通知上报失败 HTTP {}: {}".format(e.code, body))
    except Exception as e:
        print("::warning::通知上报失败: {}".format(e))


def _manual_token_help():
    return (
        "请重新登录 {}，F12 抓取 {}，更新到 GitHub Secret MWS_TOKEN。"
        "若要自动换票，再配 DISCORD_TOKEN + GH_TOKEN（与 bothosting 相同）。"
    ).format(FRONTEND, COOKIE_NAME)


def ensure_token():
    """返回可用的 MWS JWT；必要时用 Discord OAuth 换新并写回 Secrets。

    重要：换票只是「提前续命」的锦上添花。若换票失败（Discord token 失效等）
    但手上这枚 JWT 仍能通过 /auth/me，那就**继续用它去续期**，只发一条告警，
    绝不因为换票失败而中断续期 —— 否则 Bot 会因为没人点 Renew 而被停。
    只有「手上这枚 JWT 本身已失效（401）/完全没配」时才退出 1。
    """
    token = os.environ.get("MWS_TOKEN", "").strip()
    dc = discord_token()
    refresh_days = int(os.environ.get("MWS_REFRESH_BEFORE_DAYS", "7") or "7")
    need_refresh = False
    reason = ""
    me_body = ""
    token_ok = False

    if not token:
        need_refresh = True
        reason = "未设置 MWS_TOKEN"
    else:
        status, me_body = http("/auth/me", token=token)
        if status == 401:
            need_refresh = True
            reason = "MWS_TOKEN 已失效（HTTP 401）"
        elif status != 200:
            print("[✗] 验证 token 异常: HTTP {} {}".format(status, me_body))
            sys.exit(1)
        else:
            token_ok = True
            left = jwt_seconds_left(token)
            if left is not None and left < refresh_days * 86400:
                need_refresh = True
                reason = "MWS_TOKEN 将在 {:.1f} 天后过期，提前换票".format(left / 86400.0)
            elif left is not None:
                print("[i] MWS_TOKEN 剩余约 {:.1f} 天".format(left / 86400.0))

    if not need_refresh:
        return token, me_body

    print("[!] {}".format(reason))
    if not dc:
        note = reason + "\n" + _manual_token_help()
        if token_ok:
            text = format_report("⚠️ 换票失败（已用现有 MWS_TOKEN 继续续期）", note=note)
            print(text)
            _report_text(text, level="warning")
            return token, me_body
        text = format_report("❌ 换票失败：MWS_TOKEN 已失效，请手动更新", note=note)
        print(text)
        _report_text(text, level="failed")
        sys.exit(1)

    try:
        token = discord_relogin(dc)
    except Exception as e:
        note = "{}\n{}\n{}".format(reason, e, _manual_token_help())
        if token_ok:
            text = format_report("⚠️ 换票失败（已用现有 MWS_TOKEN 继续续期）", note=note)
            print(text)
            _report_text(text, level="warning")
            return token, me_body
        text = format_report("❌ 换票失败：MWS_TOKEN 已失效，请手动更新", note=note)
        print(text)
        _report_text(text, level="failed")
        sys.exit(1)

    wrote = persist_mws_token(token)
    if not wrote:
        print("[!] 新 token 仅用于本次运行；下次仍可能 401，请检查 GH_TOKEN")

    status, me_body = http("/auth/me", token=token)
    if status != 200:
        title = "⚠️ 新 MWS token 验证失败 ({})".format(now_str())
        content = "Discord 登录后 /auth/me HTTP {} {}".format(status, me_body)
        print(title)
        print(content)
        _report("failed", title, content, None)
        sys.exit(1)

    extra = "已写回 GitHub Secret" if wrote else "未写回 Secret（缺 GH_TOKEN 或 gh 失败）"
    text = format_report("✅ 换票成功（新 MWS_TOKEN 已生效）",
                         note="{}\n{}".format(reason, extra))
    print(text)
    _report_text(text, level="success")
    return token, me_body


def _parse_expiry(value):
    """把 expires_at 解析成 datetime（容错：ISO 字符串 / epoch 秒 / epoch 毫秒）。"""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        try:
            ts = float(value)
            return datetime.fromtimestamp(ts / 1000.0 if ts > 1e11 else ts).astimezone()
        except (OverflowError, OSError, ValueError):
            return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt.astimezone() if dt.tzinfo else dt


def _fmt_expiry(expires_at, remaining_hours):
    """到期日期 YYYY/MM/DD；没有 expires_at 就用「现在 + 剩余小时」推。"""
    dt = _parse_expiry(expires_at)
    if dt is None and remaining_hours is not None:
        try:
            dt = datetime.now().astimezone() + timedelta(hours=float(remaining_hours))
        except (TypeError, ValueError):
            dt = None
    return dt.strftime("%Y/%m/%d") if dt else "未知"


def _fmt_left(remaining_hours):
    """剩余时间，形如 `23h59min后` / `167h后（约 6天23小时）`。"""
    if remaining_hours is None:
        return "未知"
    try:
        hours = float(remaining_hours)
    except (TypeError, ValueError):
        return "未知"
    if hours < 48:
        whole = int(hours)
        minutes = int(round((hours - whole) * 60))
        if minutes >= 60:
            whole, minutes = whole + 1, 0
        return "{}h{}min后".format(whole, minutes)
    days = int(hours // 24)
    rest = int(round(hours - days * 24))
    return "{:.0f}h后（约 {}天{}小时）".format(hours, days, rest)


def format_report(status_line, items=None, note=None, account=None):
    """按用户指定模板渲染通知：

        🇯🇵 puratya (MWS) 续期通知

        ✅ 续期成功
        👤 登录账户: ****
        📅 到期时间: 2026/10/20
        ⏱️ 可续期时间: 167h后（约 6天23小时）
        ⏱️ 登录时间: 2026-10-13 21:53:13
    """
    lines = ["{} {} 续期通知".format(FLAG, SERVICE), "", status_line]
    lines.append("👤 登录账户: {}".format(account or "***"))
    rows = list(items or [])
    for kind, oid, name, rem, expires in rows:
        if len(rows) > 1:
            lines.append("—  {} {}（id:{}）".format(kind, name, oid))
        lines.append("📅 到期时间: {}".format(_fmt_expiry(expires, rem)))
        lines.append("⏱️ 可续期时间: {}".format(_fmt_left(rem)))
    if note:
        lines.append(note)
    lines.append("⏱️ 登录时间: {}".format(
        datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")))
    return "\n".join(lines)


def _report_text(text, level="success"):
    """发一条「原文」通知（模板文本已经排好版，不再加工）。"""
    try:
        res = notify_text(text, level=level)
        via = res.get("via") if isinstance(res, dict) else "?"
        print("[i] 通知已发送（{}）".format(via))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        print("::warning::通知上报失败 HTTP {}: {}".format(e.code, body))
    except Exception as e:
        print("::warning::通知上报失败: {}".format(e))


def main():
    token, body = ensure_token()

    try:
        who = json.loads(body).get("username")
    except (json.JSONDecodeError, AttributeError):
        who = "?"
    print("[✓] 登录有效: {}".format(who))
    print("[i] API: {}".format(API))

    # 1) 拉取并续期
    items = collect(token)
    if not items:
        print("[i] 账号下没有 Bot / Site，跳过。")
        _report_text(format_report("✅ 续期成功", items=[], account=who,
                                   note="账号下没有 Bot / Site，无需续期。"))
        return

    failed = 0
    for kind, oid, name, rem, expires in items:
        if oid is None:
            print("[✗] {} {}：列表获取失败".format(kind, name))
            failed += 1
            continue
        ok, status, body = renew_one(token, kind, oid)
        rem_txt = "，续期前剩 {}h".format(int(rem)) if rem is not None else ""
        if ok:
            print("[✓] {} {} (id:{}) 续期成功{}".format(kind, name, oid, rem_txt))
        else:
            print("[✗] {} {} (id:{}) 续期失败 HTTP {} {}".format(kind, name, oid, status,
                                                            body.strip()[:200]))
            failed += 1

    # 2) 汇总 + 通知（按用户模板）
    total = len(items)
    success = total - failed
    if failed == 0:
        status_line, level = "✅ 续期成功", "success"
    elif success:
        status_line, level = "⚠️ 部分成功（{}/{}）".format(success, total), "partial"
    else:
        status_line, level = "❌ 续期失败", "failed"
    text = format_report(status_line, items=items, account=who,
                         note=None if failed == 0 else "详情见 Actions 运行日志。")
    print("\n" + text)
    _report_text(text, level=level)
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    main()
