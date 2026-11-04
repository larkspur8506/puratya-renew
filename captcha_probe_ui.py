#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Turnstile 探针 v4：走面板真实 UI（点「延長」→ 面板自己渲染 Turnstile），看自动化浏览器能否过验证。

环境变量：MWS_TOKEN（必需）、MWS_BOT_ID（默认 11973）、PROBE_HEADLESS=1 可切无头。
"""

import os
import re
import sys

try:
    from patchright.sync_api import sync_playwright
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


def log(*a):
    print(*a, flush=True)


def main():
    if not TOKEN:
        log("[!] 缺少 MWS_TOKEN")
        return 2
    headless = os.environ.get("PROBE_HEADLESS", "0") == "1"
    log("[i] engine:", ENGINE, "headless:", headless)
    with sync_playwright() as p:
        kw = dict(headless=headless, args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox", "--disable-dev-shm-usage", "--lang=ja-JP",
        ])
        try:
            browser = p.chromium.launch(channel="chrome", **kw)
            log("[i] channel=chrome")
        except Exception as e:  # noqa: BLE001
            log("[i] 退回捆绑 chromium:", str(e)[:100])
            browser = p.chromium.launch(**kw)
        ctx = browser.new_context(
            user_agent=UA, locale="ja-JP", timezone_id="Asia/Tokyo",
            viewport={"width": 1440, "height": 960},
            extra_http_headers={"Accept-Language": "ja-JP,ja;q=0.9,en;q=0.8"},
        )
        ctx.add_cookies([{
            "name": COOKIE_NAME, "value": TOKEN, "domain": "cloud.m-ws.cc",
            "path": "/", "secure": True, "httpOnly": True, "sameSite": "Lax",
        }])
        page = ctx.new_page()
        page.goto(FRONTEND + "/dashboard", wait_until="domcontentloaded", timeout=60000)
        try:
            page.wait_for_selector("text=延長", timeout=30000)
        except Exception:  # noqa: BLE001
            page.wait_for_timeout(5000)
        log("[i] url:", page.url, "| title:", page.title()[:60])

        btns = page.get_by_role("button", name=re.compile("延長")).all()
        log("[i] 找到「延長」按钮数:", len(btns))
        if not btns:
            log("[!] 页面按钮:", page.locator("button").all_inner_texts()[:20])
            log("[!] 页面文本片段:", page.inner_text("body")[:400].replace("\n", " | "))
            page.screenshot(path="probe.png")
            browser.close()
            return 1

        try:
            with page.expect_response(
                lambda r: "/renew" in r.url and r.request.method == "POST", timeout=150000
            ) as info:
                btns[0].click()
                log("[i] 已点击「延長」，等 Turnstile + renew 响应…")
            resp = info.value
            log("[✓] renew 响应 HTTP", resp.status, resp.text()[:300])
            page.screenshot(path="probe.png")
            browser.close()
            return 0 if resp.status == 200 else 1
        except Exception as e:  # noqa: BLE001
            log("[✗] 没有拿到 renew 响应:", str(e)[:200])
            page.screenshot(path="probe.png")
            try:
                log("[i] 页面提示:", page.inner_text("body")[:500].replace("\n", " | "))
            except Exception:  # noqa: BLE001
                pass
            browser.close()
            return 1


if __name__ == "__main__":
    sys.exit(main())
