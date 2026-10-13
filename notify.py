#!/usr/bin/env python3
"""续期结果通知：优先直接推送到自己的 Telegram，其次回退到 notify-gateway。

优先级：
  1) TG_BOT_TOKEN + TG_CHAT_ID  → 直接调 Telegram Bot API sendMessage（本仓库默认路径）
  2) NOTIFY_URL + NOTIFY_TOKEN  → 原作者 2Bdou/notify-gateway（仅在没配 TG 时作为回退）
  3) 两者都没配                  → 只打 ::warning::，不阻断续期主流程

TG 相关 Secret（在 GitHub 仓库 Settings → Secrets 里加）：
  TG_BOT_TOKEN   自己的 Bot Token（@BotFather 申请，形如 123456:AA...）
  TG_CHAT_ID     目标聊天/频道 ID（频道形如 -100xxxxxxxxxx，个人是纯数字）
  TG_THREAD_ID   可选，频道/群里的话题(topic) ID
  TG_API_BASE    可选，自建反代用，默认 https://api.telegram.org
"""

from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

TG_BOT_TOKEN = os.environ.get("TG_BOT_TOKEN", "").strip()
TG_CHAT_ID = os.environ.get("TG_CHAT_ID", "").strip()
TG_THREAD_ID = os.environ.get("TG_THREAD_ID", "").strip()
TG_API_BASE = (os.environ.get("TG_API_BASE") or "https://api.telegram.org").rstrip("/")

NOTIFY_URL = os.environ.get("NOTIFY_URL", "")
NOTIFY_TOKEN = os.environ.get("NOTIFY_TOKEN", "")

# 网关（以及自建反代）后面常挂 Cloudflare，urllib 默认 UA 会被拦成 403 error 1010，
# 必须伪装成浏览器 UA。
NOTIFY_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
             "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

LEVEL_EMOJI = {
    "success": "✅",
    "partial": "⚠️",
    "warning": "⚠️",
    "failed": "❌",
    "error": "❌",
    "info": "ℹ️",
}

TG_MAX_CHARS = 3900          # Telegram 单条上限 4096，留点余量
TG_TRUNCATED_SUFFIX = "\n…（内容过长，已截断）"


def render_text(title, content="", level="success", details=None):
    """把通知渲染成一条纯文本消息（不用 Markdown/HTML，避免转义踩坑）。"""
    emoji = LEVEL_EMOJI.get((level or "").lower(), "ℹ️")
    lines = ["{} {}".format(emoji, title or "").rstrip()]
    if content:
        lines.append("")
        lines.append(str(content))
    if isinstance(details, dict) and details.get("total"):
        lines.append("")
        lines.append("共 {} · 成功 {} · 失败 {}".format(
            details.get("total"), details.get("success", 0), details.get("failed", 0)))
    return "\n".join(lines).strip()


def _chunks(text, limit=TG_MAX_CHARS):
    """超过单条上限时按行切块，保证不丢内容。"""
    if len(text) <= limit:
        return [text]
    out, cur = [], ""
    for line in text.split("\n"):
        while len(line) > limit:                     # 单行就超长：硬切
            out.append(line[:limit])
            line = line[limit:]
        if len(cur) + len(line) + 1 > limit:
            out.append(cur.rstrip("\n"))
            cur = ""
        cur += line + "\n"
    if cur.strip():
        out.append(cur.rstrip("\n"))
    return out or [text[:limit] + TG_TRUNCATED_SUFFIX]


def send_telegram(text):
    """直发 Telegram。返回最后一条 API 响应。"""
    if not (TG_BOT_TOKEN and TG_CHAT_ID):
        raise RuntimeError("未配置 TG_BOT_TOKEN / TG_CHAT_ID")
    url = "{}/bot{}/sendMessage".format(TG_API_BASE, TG_BOT_TOKEN)
    last = None
    for chunk in _chunks(text):
        payload = {
            "chat_id": TG_CHAT_ID,
            "text": chunk,
            "disable_web_page_preview": "true",
        }
        if TG_THREAD_ID:
            payload["message_thread_id"] = TG_THREAD_ID
        req = urllib.request.Request(
            url,
            data=urllib.parse.urlencode(payload).encode("utf-8"),
            headers={"User-Agent": NOTIFY_UA,
                     "Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            last = json.loads(resp.read().decode("utf-8"))
    return last


def send_gateway(title, content, level, details, source):
    """原作者 notify-gateway 的兼容路径（仅在没配 TG 时使用）。"""
    payload = {
        "source": source,
        "title": title,
        "content": content,
        "level": level,
        "channel": ["email", "telegram"],
        "data": details or {"total": 0, "success": 0, "failed": 0, "details": []},
    }
    req = urllib.request.Request(
        NOTIFY_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": "Bearer {}".format(NOTIFY_TOKEN),
            "Content-Type": "application/json",
            "User-Agent": NOTIFY_UA,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def notify(title, content="", level="success", details=None, source="puratya-renew"):
    """发通知。返回 {"via": "telegram"|"gateway", "result": ...}。"""
    if TG_BOT_TOKEN and TG_CHAT_ID:
        return {"via": "telegram",
                "result": send_telegram(render_text(title, content, level, details))}
    if NOTIFY_URL:
        return {"via": "gateway",
                "result": send_gateway(title, content, level, details, source)}
    raise RuntimeError("未配置通知通道：需要 TG_BOT_TOKEN + TG_CHAT_ID"
                       "（或回退用 NOTIFY_URL + NOTIFY_TOKEN）")


if __name__ == "__main__":
    print(json.dumps(
        notify(
            "MWS 续期完成",
            "续期完成报告",
            level="partial",
            details={
                "total": 5,
                "success": 3,
                "failed": 2,
                "details": [
                    {"id": "bot_001", "name": "Bot A", "status": "success"},
                    {"id": "bot_002", "name": "Bot B", "status": "failed", "error": "HTTP 403"},
                    {"id": "site_001", "name": "Site C", "status": "partial", "message": "1/2 续期成功"},
                ],
            },
        ),
        ensure_ascii=False,
        indent=2,
        default=str,
    ))
