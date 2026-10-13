#!/usr/bin/env python3
import base64
import json
import time
import types
import unittest

from auth import (
    COOKIE_NAME,
    FRONTEND,
    _code_from_success_url,
    _exchange_code,
    _finish_callback,
    discord_token,
    extract_mws_token,
    jwt_seconds_left,
)
import os


class JwtTests(unittest.TestCase):
    def test_seconds_left(self):
        exp = int(time.time()) + 3600
        payload = base64.urlsafe_b64encode(
            json.dumps({"exp": exp}).encode()
        ).decode().rstrip("=")
        token = "eyJhbGciOiJub25lIn0.{}.x".format(payload)
        left = jwt_seconds_left(token)
        self.assertIsNotNone(left)
        self.assertTrue(3500 < left < 3700)

    def test_bad_token(self):
        self.assertIsNone(jwt_seconds_left(""))
        self.assertIsNone(jwt_seconds_left("not-a-jwt"))


class CookieTests(unittest.TestCase):
    def test_extract(self):
        header = (
            "__Host-mrtcloud_oauth_state=abc; Path=/; HttpOnly, "
            "__Host-mrtcloud_token=eyJhbGciOiJIUzI1NiJ9.aaa.bbb; Path=/; Secure"
        )
        self.assertEqual(
            extract_mws_token(header),
            "eyJhbGciOiJIUzI1NiJ9.aaa.bbb",
        )

    def test_missing(self):
        self.assertEqual(extract_mws_token("foo=bar"), "")


class DiscordTokenEnvTests(unittest.TestCase):
    def test_comma_form(self):
        os.environ["DISCORD_TOKEN"] = "备注,the-real-token"
        self.assertEqual(discord_token(), "the-real-token")
        del os.environ["DISCORD_TOKEN"]


class _Resp:
    def __init__(self, status_code=200, headers=None, payload=None):
        self.status_code = status_code
        self.headers = headers or {}
        self._payload = payload
        self.text = ""

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class _Session:
    """够用的假 session：只有 _finish_callback/_exchange_code 用到的那几个点。"""

    def __init__(self, gets=None, post_resp=None, post_cookie=None):
        self._gets = gets or {}
        self._post_resp = post_resp
        self._post_cookie = post_cookie
        self.cookies = []
        self.posted = None

    def get(self, url, **kw):
        return self._gets[url]

    def post(self, url, **kw):
        self.posted = (url, kw)
        if self._post_cookie:
            self.cookies.append(types.SimpleNamespace(name=COOKIE_NAME, value=self._post_cookie))
        return self._post_resp


class NewCallbackFlowTests(unittest.TestCase):
    """MWS 2026-10 起：后端 302 到前端 /auth/success?code=…，再 POST /api/auth/exchange。"""

    SUCCESS = "https://cloud.m-ws.cc/auth/success?code=abc123XYZ_-="

    def test_code_from_success_url(self):
        self.assertEqual(_code_from_success_url(self.SUCCESS), "abc123XYZ_-=")
        self.assertEqual(_code_from_success_url("https://cloud.m-ws.cc/auth/success/?code=abc123XYZ_-="),
                         "abc123XYZ_-=")
        # 后端 callback 上的 code 不能拿来用
        self.assertEqual(
            _code_from_success_url("https://cloud-api.m-ws.cc/auth/callback?code=x&state=y"), "")
        self.assertEqual(_code_from_success_url(""), "")
        self.assertEqual(_code_from_success_url("https://cloud.m-ws.cc/auth/success"), "")

    def test_finish_callback_uses_exchange(self):
        cb = "https://cloud-api.m-ws.cc/auth/callback?code=oauthcode&state=s"
        sess = _Session(
            gets={cb: _Resp(302, {"Location": self.SUCCESS}),
                  self.SUCCESS: _Resp(200, {})},
            post_resp=_Resp(200, {"Set-Cookie": "__Host-mrtcloud_token=jwt123; Path=/; Secure"}),
            post_cookie="jwt123",
        )
        token = _finish_callback(sess, cb)
        self.assertEqual(token, "jwt123")
        self.assertEqual(sess.posted[0], FRONTEND + "/api/auth/exchange")
        self.assertEqual(sess.posted[1]["json"], {"code": "abc123XYZ_-="})

    def test_exchange_reads_token_from_body(self):
        sess = _Session(post_resp=_Resp(200, {}, payload={"data": {"token": "jwt-from-body"}}))
        self.assertEqual(_exchange_code(sess, "c"), "jwt-from-body")

    def test_old_flow_still_works(self):
        cb = "https://cloud-api.m-ws.cc/auth/callback?code=oauthcode&state=s"
        sess = _Session(gets={cb: _Resp(200, {"Set-Cookie": "__Host-mrtcloud_token=oldjwt; Path=/"})})
        self.assertEqual(_finish_callback(sess, cb), "oldjwt")

    def test_no_token_raises(self):
        cb = "https://cloud-api.m-ws.cc/auth/callback?code=oauthcode&state=s"
        sess = _Session(gets={cb: _Resp(302, {"Location": "https://cloud.m-ws.cc/auth/success?error=1"}),
                              "https://cloud.m-ws.cc/auth/success?error=1": _Resp(200, {})})
        with self.assertRaises(RuntimeError):
            _finish_callback(sess, cb)


if __name__ == "__main__":
    unittest.main()
