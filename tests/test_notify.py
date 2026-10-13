#!/usr/bin/env python3
"""notify.py 的离线单测（不联网）。"""
import json
import unittest
from unittest import mock

import notify


class RenderTests(unittest.TestCase):
    def test_basic(self):
        txt = notify.render_text("MWS 续期报告", "  · Bot A：续期成功",
                                 level="success",
                                 details={"total": 2, "success": 2, "failed": 0})
        self.assertIn("✅ MWS 续期报告", txt)
        self.assertIn("Bot A", txt)
        self.assertIn("共 2 · 成功 2 · 失败 0", txt)

    def test_failed_emoji(self):
        self.assertTrue(notify.render_text("x", level="failed").startswith("❌"))
        self.assertTrue(notify.render_text("x", level="partial").startswith("⚠️"))

    def test_chunks_keep_all_lines(self):
        text = "\n".join("line %03d" % i for i in range(2000))
        chunks = notify._chunks(text, limit=500)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(c) <= 500 for c in chunks))
        joined = "\n".join(chunks)
        for i in (0, 999, 1999):
            self.assertIn("line %03d" % i, joined)

    def test_chunks_single_long_line(self):
        chunks = notify._chunks("a" * 1200, limit=500)
        self.assertEqual(len(chunks), 3)
        self.assertEqual(sum(len(c) for c in chunks), 1200)


class ChannelTests(unittest.TestCase):
    def setUp(self):
        self._saved = (notify.TG_BOT_TOKEN, notify.TG_CHAT_ID, notify.NOTIFY_URL)

    def tearDown(self):
        notify.TG_BOT_TOKEN, notify.TG_CHAT_ID, notify.NOTIFY_URL = self._saved

    def test_no_channel_raises(self):
        notify.TG_BOT_TOKEN = ""
        notify.TG_CHAT_ID = ""
        notify.NOTIFY_URL = ""
        with self.assertRaises(RuntimeError):
            notify.notify("t", "c")

    def test_telegram_preferred(self):
        notify.TG_BOT_TOKEN = "123:ABC"
        notify.TG_CHAT_ID = "-100999"
        notify.NOTIFY_URL = "https://gateway.invalid/api/notify"
        captured = {}

        class FakeResp:
            def read(self):
                return json.dumps({"ok": True}).encode()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["body"] = req.data.decode()
            return FakeResp()

        with mock.patch.object(notify.urllib.request, "urlopen", fake_urlopen):
            res = notify.notify("标题", "正文", level="success")
        self.assertEqual(res["via"], "telegram")
        self.assertIn("/bot123:ABC/sendMessage", captured["url"])
        self.assertIn("chat_id=-100999", captured["body"])

    def test_gateway_fallback_body_shape(self):
        notify.TG_BOT_TOKEN = ""
        notify.TG_CHAT_ID = ""
        notify.NOTIFY_URL = "https://gateway.invalid/api/notify"
        notify.NOTIFY_TOKEN = "key"
        captured = {}

        class FakeResp:
            def read(self):
                return json.dumps({"ok": True}).encode()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["payload"] = json.loads(req.data.decode())
            captured["auth"] = req.headers.get("Authorization")
            return FakeResp()

        with mock.patch.object(notify.urllib.request, "urlopen", fake_urlopen):
            res = notify.notify("标题", "正文", level="partial",
                                details={"total": 1, "success": 0, "failed": 1})
        self.assertEqual(res["via"], "gateway")
        self.assertEqual(captured["payload"]["title"], "标题")
        self.assertEqual(captured["payload"]["level"], "partial")
        self.assertEqual(captured["payload"]["source"], "puratya-renew")
        self.assertEqual(captured["auth"], "Bearer key")


if __name__ == "__main__":
    unittest.main()
