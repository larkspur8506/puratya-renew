#!/usr/bin/env python3
"""通知模板（renew.format_report / _fmt_left / _fmt_expiry）的离线单测。"""
import unittest
from datetime import datetime, timedelta

import renew


class FmtLeftTests(unittest.TestCase):
    def test_sample_value(self):
        self.assertEqual(renew._fmt_left(23.98), "23h59min后")

    def test_days(self):
        self.assertEqual(renew._fmt_left(167), "167h后（约 6天23小时）")

    def test_unknown(self):
        self.assertEqual(renew._fmt_left(None), "未知")


class FmtExpiryTests(unittest.TestCase):
    def test_iso(self):
        self.assertEqual(renew._fmt_expiry("2026-10-20T21:53:13Z", None), "2026/10/20")

    def test_epoch_seconds(self):
        ts = datetime(2026, 10, 20, 12, 0).timestamp()
        self.assertEqual(renew._fmt_expiry(ts, None), "2026/10/20")

    def test_from_remaining_hours(self):
        got = renew._fmt_expiry(None, 24)
        expect = (datetime.now().astimezone() + timedelta(hours=24)).strftime("%Y/%m/%d")
        self.assertEqual(got, expect)

    def test_unknown(self):
        self.assertEqual(renew._fmt_expiry(None, None), "未知")
        self.assertEqual(renew._fmt_expiry("not-a-date", None), "未知")


class FormatReportTests(unittest.TestCase):
    ITEMS = [("Bot", 11973, "larkspur", 167, "2026-10-20T16:00:00Z")]

    def test_template_shape(self):
        text = renew.format_report("✅ 续期成功", items=self.ITEMS, account="larkspur8506")
        lines = text.split("\n")
        self.assertEqual(lines[0], "{} {} 续期通知".format(renew.FLAG, renew.SERVICE))
        self.assertEqual(lines[1], "")
        self.assertEqual(lines[2], "✅ 续期成功")
        self.assertEqual(lines[3], "👤 登录账户: larkspur8506")
        self.assertEqual(lines[4], "📅 到期时间: 2026/10/20")
        self.assertEqual(lines[5], "⏱️ 可续期时间: 167h后（约 6天23小时）")
        self.assertTrue(lines[6].startswith("⏱️ 登录时间: "))
        # 时间戳格式 YYYY-MM-DD HH:MM:SS
        stamp = lines[6].split(": ", 1)[1]
        datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S")

    def test_account_masked_when_missing(self):
        text = renew.format_report("✅ 续期成功")
        self.assertIn("👤 登录账户: ***", text)

    def test_multiple_items_get_headers(self):
        items = [("Bot", 1, "a", 100, None), ("Bot", 2, "b", 50, None)]
        text = renew.format_report("✅ 续期成功", items=items, account="u")
        self.assertIn("—  Bot a（id:1）", text)
        self.assertIn("—  Bot b（id:2）", text)

    def test_note_appended(self):
        text = renew.format_report("❌ 续期失败", items=self.ITEMS, account="u", note="详情见日志")
        self.assertIn("详情见日志", text)


if __name__ == "__main__":
    unittest.main()
