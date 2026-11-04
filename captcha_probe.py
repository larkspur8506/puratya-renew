#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Turnstile 探针 v2：patchright（去 CDP 指纹）+ 真实 Chrome，尝试免费拿续期用 turnstile_token。"""

import os
import sys
import time

try:
    from patchright.sync_api import sync_playwright  # 更强的反检测分支
    ENGINE = "patchright"
except Exception:  # noqa: BLE001
    from playwright.sync_api import sync_playwright
    ENGINE = "playwright"

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
  for (let i = 0; i < 60 && !window.turnstile; i++) await new Promise(r => setTimeout(r, 250));
  if (!window.turnstile) return {state: 'script-missing'};
  window.__tsState = 'rendering';
  return await new Promise((resolve) => {
    const box = document.createElement('div');
    box.id = 'tsbox';
    box.style.cssText = 'position:fixed;left:20px;top:20px;z-index:2147483647;background:#fff;padding:10px';
    document.body.appendChild(box);
    const t = setTimeout(() => { window.__tsState = 'timeout'; resolve({state: 'timeout-90s'}); }, 90000);
    window.turnstile.render(box, {
      sitekey: sitekey,
      action: 'renew',
      theme: 'light',
      callback: (tok) => { clearTimeout(t); window.__tsToken = tok; resolve({state: 'ok', len: tok.length}); },
      'error-callback': (e) => { clearTimeout(t); window.__tsState = 'error:' + String(e); resolve({state: 'error', detail: String(e)}); },
      'expired-callback': () => { window.__tsState = 'expired'; },
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


def frame_text(page):
    out = []
    for f in page.frames:
        if "challenges.cloudflare.com" in (f.url or ""):
            try:
                out.append(f.inner_text("body")[:200].replace("\n", " | "))
            except Exception as e:  # noqa: BLE001
                out.append("<read fail %s>" % str(e)[:60])
    return out


def main():
    if not TOKEN:
        log("[!] 缺少 MWS_TOKEN")
        return 2
    headless = os.environ.get("PROBE_HEADLESS", "0") == "1"
    log("[i] engine:", ENGINE, "headless:", headless)

    with sync_playwright() as p:
        launch_kw = dict(headless=headless, args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox", "--disable-dev-shm-usage", "--lang=ja-JP",
        ])
        try:
            browser = p.chromium.launch(channel="chrome", **launch_kw)
            log("[i] 用系统 Chrome（channel=chrome）")
        except Exception as e:  # noqa: BLE001
            log("[i] 系统 Chrome 不可用，退回捆绑 chromium:", str(e)[:120])
            browser = p.chromium.launch(**launch_kw)

        ctx = browser.new_context(
            user_agent=UA, locale="ja-JP", timezone_id="Asia/Tokyo",
            viewport={"width": 1366, "height": 900},
            extra_http_headers={"Accept-Language": "ja-JP,ja;q=0.9,en;q=0.8"},
        )
        ctx.add_cookies([{
            "name": COOKIE_NAME, "value": TOKEN, "domain": "cloud.m-ws.cc",
            "path": "/", "secure": True, "httpOnly": True, "sameSite": "Lax",
        }])
        page = ctx.new_page()
        log("[i] 打开面板首页…")
        page.goto(FRONTEND + "/dashboard", wait_until="domcontentloaded", timeout=60000)
        time.sleep(5)
        log("[i] url:", page.url, "| title:", page.title()[:60])

        me = page.evaluate(JS_ME)
        log("[i] /api/auth/me ->", {k: (v if k != "key" else (v[:14] + "…" if v else "")) for k, v in me.items()})
        sitekey = me.get("key") or ""
        if not sitekey:
            page.screenshot(path="probe.png")
            browser.close()
            return 1

        log("[i] 渲染 Turnstile（action=renew）…")
        res = page.evaluate(JS_RENDER, sitekey)
        log("[i] render ->", res)

        tok = page.evaluate("() => window.__tsToken || ''")
        if not tok:
            # 交互式挑战：在页面坐标上真点一次复选框
            for i in range(8):
                time.sleep(3)
                tok = page.evaluate("() => window.__tsToken || ''")
                if tok:
                    break
                box = None
                try:
                    box = page.locator("#tsbox iframe").first.bounding_box()
                except Exception:  # noqa: BLE001
                    pass
                log("   [i] 第%d轮 state=%s frame_text=%s box=%s" % (
                    i + 1, page.evaluate("() => window.__tsState || ''"), frame_text(page), box))
                if box and i in (0, 3):
                    page.mouse.click(box["x"] + 30, box["y"] + box["height"] / 2)

        page.screenshot(path="probe.png")
        tok = page.evaluate("() => window.__tsToken || ''")
        if not tok:
            log("[✗] 未拿到 turnstile_token; state=", page.evaluate("() => window.__tsState || ''"))
            log("    frame_text:", frame_text(page))
            browser.close()
            return 1

        log("[✓] 拿到 turnstile_token，长度", len(tok))
        out = page.evaluate(JS_RENEW, [BOT_ID, tok])
        log("[i] POST /api/bots/%s/renew -> %s %s" % (BOT_ID, out["status"], out["body"][:300]))
        browser.close()
        return 0 if out["status"] == 200 else 1


if __name__ == "__main__":
    sys.exit(main())
