#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Turnstile 探针：真实 Chromium + 已登录 cookie，尝试免费拿到续期用 turnstile_token。

用法（GitHub Actions 里跑）：MWS_TOKEN=... MWS_BOT_ID=11973 python3 captcha_probe.py
只做诊断，不写回 secret；成功时会调用一次 /renew（顺带把倒计时续上）。
"""

import json
import os
import re
import sys
import time

from playwright.sync_api import sync_playwright

FRONTEND = "https://cloud.m-ws.cc"
COOKIE_NAME = "__Host-mrtcloud_token"
BOT_ID = os.environ.get("MWS_BOT_ID", "11973")
TOKEN = (os.environ.get("MWS_TOKEN") or "").strip()
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")

JS_ME = """async () => {
  try {
    const r = await fetch('/api/auth/me', {credentials: 'include'});
    if (!r.ok) return {err: r.status};
    const j = await r.json();
    return {key: j.turnstile_site_key || '', user: j.username || (j.user && j.user.username) || ''};
  } catch (e) { return {err: String(e)}; }
}"""

JS_RENDER = """async (sitekey) => {
  const load = () => new Promise((res, rej) => {
    if (window.turnstile) return res();
    const s = document.createElement('script');
    s.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit';
    s.async = true;
    s.onload = () => res();
    s.onerror = () => rej(new Error('api.js load failed'));
    document.head.appendChild(s);
  });
  await load();
  return await new Promise((resolve) => {
    const box = document.createElement('div');
    box.style.cssText = 'position:fixed;left:20px;top:20px;z-index:2147483647;background:#fff;padding:10px';
    document.body.appendChild(box);
    const t = setTimeout(() => resolve({err: 'timeout-90s'}), 90000);
    window.turnstile.render(box, {
      sitekey: sitekey,
      action: 'renew',
      theme: 'light',
      callback: (tok) => { clearTimeout(t); window.__tsToken = tok; resolve({ok: true, len: tok.length, head: tok.slice(0, 10)}); },
      'error-callback': (e) => { clearTimeout(t); resolve({err: 'error-callback ' + String(e)}); },
      'expired-callback': () => {},
    });
  });
}"""

JS_RENEW = """async ([bid, tok]) => {
  const r = await fetch('/api/bots/' + bid + '/renew', {
    method: 'POST', credentials: 'include',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({turnstile_token: tok}),
  });
  return {status: r.status, body: (await r.text()).slice(0, 500)};
}"""


def log(*a):
    print(*a, flush=True)


def main():
    if not TOKEN:
        log("[!] 缺少 MWS_TOKEN")
        return 2
    headless = os.environ.get("PROBE_HEADLESS", "0") == "1"
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
            ],
        )
        ctx = browser.new_context(
            user_agent=UA,
            locale="ja-JP",
            timezone_id="Asia/Tokyo",
            viewport={"width": 1366, "height": 900},
        )
        ctx.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        ctx.add_cookies([{
            "name": COOKIE_NAME, "value": TOKEN, "domain": "cloud.m-ws.cc",
            "path": "/", "secure": True, "httpOnly": True, "sameSite": "Lax",
        }])
        page = ctx.new_page()
        page.on("console", lambda m: log("   [console]", m.type, m.text[:200]))

        log("[i] 打开面板…")
        page.goto(FRONTEND + "/dashboard", wait_until="domcontentloaded", timeout=60000)
        time.sleep(4)
        log("[i] url:", page.url, "| title:", page.title()[:80])

        me = page.evaluate(JS_ME)
        log("[i] /api/auth/me ->", {k: (v if k != "key" else (v[:12] + "…" if v else "")) for k, v in me.items()})
        sitekey = me.get("key") or ""
        if not sitekey:
            log("[!] 没拿到 turnstile_site_key")
            page.screenshot(path="probe.png", full_page=False)
            browser.close()
            return 1

        log("[i] 渲染 Turnstile（action=renew）…")
        res = page.evaluate(JS_RENDER, sitekey)
        log("[i] render ->", res)

        if not res.get("ok"):
            # 交互式挑战：尝试点 iframe 里的复选框
            for attempt in range(6):
                time.sleep(3)
                tok = page.evaluate("() => window.__tsToken || ''")
                if tok:
                    res = {"ok": True, "len": len(tok), "head": tok[:10]}
                    break
                frames = [f for f in page.frames if "challenges.cloudflare.com" in (f.url or "")]
                log("   [i] 第%d次尝试，turnstile frames: %d" % (attempt + 1, len(frames)))
                for f in frames:
                    try:
                        box = f.locator("body").bounding_box()
                        log("     frame url:", f.url[:90], "box:", box)
                        f.locator("body").click(position={"x": 30, "y": 30}, timeout=5000)
                    except Exception as e:
                        log("     点击失败:", str(e)[:120])
            page.screenshot(path="probe.png")

        if not res.get("ok"):
            log("[✗] 未拿到 turnstile_token:", res)
            browser.close()
            return 1

        tok = page.evaluate("() => window.__tsToken")
        page.screenshot(path="probe.png")
        log("[✓] 拿到 turnstile_token，长度", len(tok))
        out = page.evaluate(JS_RENEW, [BOT_ID, tok])
        log("[i] POST /api/bots/%s/renew -> %s %s" % (BOT_ID, out["status"], out["body"][:300]))
        browser.close()
        return 0 if out["status"] == 200 else 1


if __name__ == "__main__":
    sys.exit(main())
