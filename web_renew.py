#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用真实浏览器 + 代理过 Cloudflare Turnstile，然后调 MWS 的续期接口。

背景：MWS 2026-11 起给 POST /api/bots/{id}/renew 加了 turnstile_token 校验，纯 API 403 captcha_required。
面板 SPA（/dashboard）在自动化环境下会崩 Next.js 错误页（This page couldn't load），所以这里不点 UI，改为：
  1) 打开同源首页 https://cloud.m-ws.cc/  （静态页，不会崩）
  2) CDP 写入登录 cookie（__Host- 前缀不能带 domain，add_cookie 会失败）
  3) 页面里 fetch /api/auth/me 拿 turnstile_site_key
  4) 注入 Turnstile 组件（action=renew）拿 token —— 出口走 NODE_LINK 代理 + UC 模式浏览器
  5) 用 token POST /api/bots/{id}/renew，再读 /api/bots 确认 remaining_hours 真的变长

对外入口：renew_bots_via_web(token, bot_ids, proxy=...)
"""

import os
import time

import requests

FRONTEND = "https://cloud.m-ws.cc"
API = os.environ.get("MWS_API", FRONTEND + "/api").rstrip("/")
COOKIE_NAME = "__Host-mrtcloud_token"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")

JS_ME = r"""
var cb = arguments[arguments.length - 1];
fetch('/api/auth/me', {credentials: 'include'})
  .then(function (r) { return r.json().then(function (j) { cb({status: r.status, body: j}); }); })
  .catch(function (e) { cb({err: String(e)}); });
"""

JS_RENDER = r"""
var cb = arguments[arguments.length - 1];
var sitekey = arguments[0];
function finish(o) { try { cb(o); } catch (e) {} }
function loadScript() {
  return new Promise(function (res, rej) {
    if (window.turnstile) return res();
    var s = document.createElement('script');
    s.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit';
    s.async = true;
    s.onload = function () { res(); };
    s.onerror = function () { rej(new Error('api.js load failed')); };
    document.head.appendChild(s);
  });
}
loadScript().then(function () {
  if (!window.turnstile) return finish({err: 'turnstile object missing'});
  var box = document.createElement('div');
  box.id = 'tsbox';
  box.style.cssText = 'position:fixed;left:30px;top:30px;z-index:2147483647;background:#fff;padding:8px';
  document.body.appendChild(box);
  window.__tsErr = '';
  setTimeout(function () { if (!window.__ts) finish({err: 'timeout', state: window.__tsErr}); }, 150000);
  window.turnstile.render(box, {
    sitekey: sitekey,
    action: 'renew',
    theme: 'light',
    callback: function (t) { window.__ts = t; finish({ok: true, len: t.length}); },
    'error-callback': function (e) { window.__tsErr = String(e); finish({err: 'error-callback ' + String(e)}); },
    'expired-callback': function () { window.__tsErr = 'expired'; }
  });
}).catch(function (e) { finish({err: String(e)}); });
"""

JS_RENEW = r"""
var cb = arguments[arguments.length - 1];
var bid = arguments[0];
var tok = arguments[1];
fetch('/api/bots/' + bid + '/renew', {
  method: 'POST', credentials: 'include',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({turnstile_token: tok})
}).then(function (r) { return r.text().then(function (t) { cb({status: r.status, body: t.slice(0, 400)}); }); })
  .catch(function (e) { cb({err: String(e)}); });
"""

JS_TSBOX_RECT = r"""
var f = document.querySelector('#tsbox iframe');
if (!f) return null;
var r = f.getBoundingClientRect();
return {x: r.left, y: r.top, w: r.width, h: r.height};
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


def _try_click_captcha(sb):
    """Turnstile 需要交互时，按 iframe 的页面坐标点一次复选框。"""
    try:
        from selenium.webdriver.common.action_chains import ActionChains

        rect = sb.driver.execute_script(JS_TSBOX_RECT)
        if not rect:
            return False
        body = sb.driver.find_element("tag name", "body")
        ActionChains(sb.driver).move_to_element_with_offset(
            body, int(rect["x"] + 30), int(rect["y"] + rect["h"] / 2)).click().perform()
        _p("[i] 已尝试点击 Turnstile 复选框 @", rect)
        return True
    except Exception as e:  # noqa: BLE001
        _p("[!] 点击 Turnstile 失败:", str(e)[:150])
        return False


def renew_bots_via_web(token, bot_ids, proxy=None, timeout=180, headless=False, bot_name=None):
    """返回 {bot_id: (ok, detail)}。"""
    from seleniumbase import SB

    before = {bid: _remaining(token, bid, proxy) for bid in bot_ids}
    _p("[i] 续期前剩余: {}".format(before))
    result = {bid: (False, "未尝试") for bid in bot_ids}

    with SB(uc=True, headless=headless, proxy=proxy, locale_code="ja",
            ad_block_on=False, test=False, disable_csp=True) as sb:
        sb.driver.set_script_timeout(220)
        sb.uc_open_with_reconnect(FRONTEND + "/", reconnect_time=4)
        try:
            sb.driver.execute_cdp_cmd("Network.setCookie", {
                "name": COOKIE_NAME, "value": token, "url": FRONTEND + "/",
                "path": "/", "secure": True, "httpOnly": True, "sameSite": "Lax",
            })
            _p("[i] cookie 已通过 CDP 写入")
        except Exception as e:  # noqa: BLE001
            _p("[!] 写 cookie 失败:", str(e)[:160])
        sb.uc_open_with_reconnect(FRONTEND + "/", reconnect_time=3)
        _p("[i] 当前页:", sb.get_current_url())

        me = sb.execute_async_script(JS_ME)
        sitekey = ""
        if isinstance(me, dict) and isinstance(me.get("body"), dict):
            sitekey = me["body"].get("turnstile_site_key") or ""
            _p("[i] /api/auth/me: status={} user={} sitekey={}".format(
                me.get("status"), me["body"].get("username"),
                (sitekey[:12] + "…") if sitekey else ""))
        else:
            _p("[!] /api/auth/me 异常:", me)
        if not sitekey:
            sb.save_screenshot("web-renew-no-sitekey.png")
            return {bid: (False, "拿不到 turnstile_site_key") for bid in bot_ids}

        ts = sb.execute_async_script(JS_RENDER, sitekey)
        _p("[i] Turnstile 渲染结果:", ts)
        if not (isinstance(ts, dict) and ts.get("ok")):
            _try_click_captcha(sb)
            for _ in range(6):
                if sb.execute_script("return window.__ts || '';"):
                    break
                time.sleep(5)
        sb.save_screenshot("web-renew-turnstile.png")

        tok = sb.execute_script("return window.__ts || '';")
        if not tok:
            _p("[✗] 没拿到 turnstile_token:", ts)
            return {bid: (False, "Turnstile 未通过: {}".format(ts)) for bid in bot_ids}
        _p("[✓] 拿到 turnstile_token（长度 {}）".format(len(tok)))

        done = []
        for bid in bot_ids:
            out = sb.execute_async_script(JS_RENEW, bid, tok)
            _p("[i] renew id={} -> {}".format(bid, out))
            if isinstance(out, dict) and out.get("status") == 200:
                done.append(bid)
                result[bid] = (True, "接口返回 200")

        # 用接口核对剩余时间真的变长（7 天 → 大约 +168h）
        if done:
            for _ in range(12):
                time.sleep(10)
                cur = {bid: _remaining(token, bid, proxy) for bid in bot_ids}
                for bid in done:
                    b, c = before.get(bid), cur.get(bid)
                    if c is not None and b is not None and float(c) > float(b) + 12:
                        result[bid] = (True, "剩余 {:.0f}h → {:.0f}h".format(float(b), float(c)))
                if all(result[bid][0] for bid in done):
                    break
        sb.save_screenshot("web-renew-final.png")
    return result
