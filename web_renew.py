#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用真实浏览器（SeleniumBase UC）走面板 UI 点「延長」，过 Cloudflare Turnstile。

MWS 的续期接口从 2026-11 起要求 turnstile_token，纯 API 一律 403 captcha_required。
本模块配合 singbox.start_singbox()（NODE_LINK 代理）在 GitHub Actions 里跑：
代理出口干净 + UC 模式浏览器 → Turnstile 托管挑战通常能自动通过。

对外只有一个入口：renew_bots_via_web(token, bot_ids, proxy=...)
"""

import os
import sys
import time

import requests

FRONTEND = "https://cloud.m-ws.cc"
API = os.environ.get("MWS_API", FRONTEND + "/api").rstrip("/")
COOKIE_NAME = "__Host-mrtcloud_token"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")

CLICK_JS = """
var botName = (arguments[0] || '');
var norm = function (s) { return (s || '').replace(/\\s+/g, ' ').trim(); };
var all = [].slice.call(document.querySelectorAll('button, a'));
var btns = all.filter(function (b) {
  return norm(b.textContent) === '\u5ef6\u9577' ||
         norm(b.getAttribute('title')) === '\u30bf\u30a4\u30de\u30fc\u3092\u30ea\u30bb\u30c3\u30c8\uff087\u65e5\u5ef6\u9577\uff09';
});
if (!btns.length) {
  return {ok: false, why: 'no-button',
          seen: all.filter(function (b) { return norm(b.textContent).length; })
                    .map(function (b) { return norm(b.textContent).slice(0, 20); }).slice(0, 25),
          url: location.href, body: norm(document.body.innerText).slice(0, 200)};
}
var target = btns[0];
if (botName) {
  var hit = btns.filter(function (b) {
    var n = b;
    for (var i = 0; i < 6 && n; i++, n = n.parentElement) {
      if ((n.textContent || '').indexOf(botName) >= 0) return true;
    }
    return false;
  })[0];
  if (hit) target = hit;
}
target.scrollIntoView({block: 'center'});
target.click();
return {ok: true, count: btns.length};
"""


def _p(*a):
    print(*a, flush=True)


def _remaining(token, bot_id, proxy=None):
    """读 /api/bots，返回该 bot 的 timer.remaining_hours（拿不到返回 None）。"""
    headers = {"User-Agent": UA, "Accept": "application/json",
               "Cookie": "{}={}".format(COOKIE_NAME, token),
               "Authorization": "Bearer " + token}
    proxies = {"http": proxy, "https": proxy} if proxy else None
    try:
        r = requests.get(API + "/bots", headers=headers, proxies=proxies, timeout=30)
        if r.status_code != 200:
            return None
        data = r.json() or []
        # /api/bots 可能返回 [..] 或 {"bots": [..]}，两种都兼容
        lst = data if isinstance(data, list) else (data.get("bots") or [])
        for obj in lst:
            if isinstance(obj, dict) and str(obj.get("id")) == str(bot_id):
                return (obj.get("timer") or {}).get("remaining_hours")
    except (requests.RequestException, ValueError):
        return None
    return None


def renew_bots_via_web(token, bot_ids, proxy=None, timeout=180, headless=True, bot_name=None):
    """跑浏览器点「延長」。返回 {bot_id: (ok, detail)}。"""
    from seleniumbase import SB

    before = {bid: _remaining(token, bid, proxy) for bid in bot_ids}
    _p("[i] 续期前剩余: {}".format(before))
    result = {bid: (False, "未尝试") for bid in bot_ids}

    with SB(uc=True, headless=headless, proxy=proxy, locale_code="ja",
            ad_block_on=False, test=False) as sb:
        sb.uc_open_with_reconnect(FRONTEND + "/dashboard", reconnect_time=4)
        set_ok = False
        try:
            sb.driver.execute_cdp_cmd("Network.setCookie", {
                "name": COOKIE_NAME, "value": token, "url": FRONTEND + "/",
                "path": "/", "secure": True, "httpOnly": True, "sameSite": "Lax",
            })
            set_ok = True
            _p("[i] cookie 已通过 CDP 写入")
        except Exception as e:  # noqa: BLE001
            _p("[!] CDP 写 cookie 失败:", str(e)[:160])
        if not set_ok:
            try:
                sb.add_cookie({
                    "name": COOKIE_NAME, "value": token, "url": FRONTEND + "/",
                    "path": "/", "secure": True, "httpOnly": True, "sameSite": "Lax",
                })
            except Exception as e:  # noqa: BLE001
                _p("[!] add_cookie 也失败:", str(e)[:160])
        sb.uc_open_with_reconnect(FRONTEND + "/dashboard", reconnect_time=5)
        _p("[i] 已打开面板:", sb.get_current_url())

        clicked = sb.execute_script(CLICK_JS, bot_name or "")
        _p("[i] 点击「延長」:", clicked)
        if not clicked or not clicked.get("ok"):
            sb.save_screenshot("web-renew-no-button.png")
            return {bid: (False, "找不到「延長」按钮: {}".format(clicked)) for bid in bot_ids}

        time.sleep(6)
        try:
            sb.save_screenshot("web-renew-after-click.png")
            frames = sb.execute_script(
                "return [].slice.call(document.querySelectorAll('iframe'))"
                ".map(function (f) { return f.src || ''; })"
                ".filter(function (s) { return s.indexOf('challenges.cloudflare.com') >= 0; }).length;")
            _p("[i] 页面上 Turnstile iframe 数:", frames)
        except Exception as e:  # noqa: BLE001
            _p("[!] 诊断失败:", str(e)[:120])

        deadline = time.time() + timeout
        last = dict(before)
        while time.time() < deadline:
            time.sleep(10)
            cur = {bid: _remaining(token, bid, proxy) for bid in bot_ids}
            for bid in bot_ids:
                b, c = before.get(bid), cur.get(bid)
                if c is not None and b is not None and float(c) > float(b) + 12:
                    result[bid] = (True, "剩余 {:.0f}h → {:.0f}h".format(float(b), float(c)))
            if all(result[bid][0] for bid in bot_ids):
                break
            last = cur
        sb.save_screenshot("web-renew-final.png")
        _p("[i] 续期后剩余: {}".format(last))
    return result
