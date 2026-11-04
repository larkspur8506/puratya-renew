#!/usr/bin/env python3
"""节点链接 -> sing-box 配置的离线单测（tuic/hysteria2/vless/vmess/trojan）。"""
import base64
import json
import unittest

import singbox

TUIC = ("tuic://11111111-2222-3333-4444-555555555555:pass123@example.com:443"
        "?congestion_control=bbr&udp_relay_mode=native&alpn=h3&sni=example.com&allow_insecure=1#n")
HY2 = ("hysteria2://pass123@example.com:8443?sni=example.com&insecure=1"
       "&obfs=salamander&obfs-password=abc#n")


class TuicTests(unittest.TestCase):
    def test_fields(self):
        out = singbox.parse_node(TUIC)
        self.assertEqual(out["type"], "tuic")
        self.assertEqual(out["server"], "example.com")
        self.assertEqual(out["server_port"], 443)
        self.assertEqual(out["uuid"], "11111111-2222-3333-4444-555555555555")
        self.assertEqual(out["password"], "pass123")
        self.assertEqual(out["congestion_control"], "bbr")
        self.assertEqual(out["tls"]["alpn"], ["h3"])
        self.assertTrue(out["tls"]["insecure"])

    def test_no_insecure_flag(self):
        out = singbox.parse_node("tuic://u:p@h.example:443?sni=h.example#n")
        self.assertNotIn("insecure", out["tls"])
        self.assertEqual(out["tls"]["server_name"], "h.example")


class Hysteria2Tests(unittest.TestCase):
    def test_alias_and_obfs(self):
        out = singbox.parse_node(HY2)
        self.assertEqual(out["type"], "hysteria2")
        self.assertEqual(out["server_port"], 8443)
        self.assertEqual(out["password"], "pass123")
        self.assertEqual(out["obfs"], {"type": "salamander", "password": "abc"})

    def test_hy2_alias(self):
        out = singbox.parse_node(HY2.replace("hysteria2://", "hy2://"))
        self.assertEqual(out["type"], "hysteria2")


class OtherProtocolsTests(unittest.TestCase):
    def test_trojan_ws(self):
        out = singbox.parse_node("trojan://p@e.example:443?sni=e.example&type=ws&path=%2Ftr#n")
        self.assertEqual(out["type"], "trojan")
        self.assertEqual(out["transport"]["type"], "ws")
        self.assertEqual(out["transport"]["path"], "/tr")

    def test_vless_reality(self):
        out = singbox.parse_node(
            "vless://11111111-2222-3333-4444-555555555555@e.example:443?security=reality"
            "&sni=e.example&pbk=KEY&sid=ab12&type=tcp&flow=xtls-rprx-vision&fp=chrome#n")
        self.assertEqual(out["flow"], "xtls-rprx-vision")
        self.assertTrue(out["tls"]["reality"]["enabled"])
        self.assertEqual(out["tls"]["reality"]["short_id"], "ab12")

    def test_vmess_b64(self):
        payload = {"add": "e.example", "port": "443", "id": "uuid-1", "net": "ws",
                   "path": "/ws", "host": "e.example", "tls": "tls"}
        link = "vmess://" + base64.b64encode(json.dumps(payload).encode()).decode()
        out = singbox.parse_node(link)
        self.assertEqual(out["type"], "vmess")
        self.assertEqual(out["transport"]["type"], "ws")

    def test_unsupported(self):
        with self.assertRaises(ValueError):
            singbox.parse_node("ss://whatever")


class ConfigShapeTests(unittest.TestCase):
    def test_mixed_inbound_and_tags(self):
        cfg = singbox.build_config(TUIC, listen_port=7890)
        inb = cfg["inbounds"][0]
        self.assertEqual((inb["type"], inb["listen"], inb["listen_port"]), ("mixed", "127.0.0.1", 7890))
        self.assertEqual(cfg["outbounds"][0]["tag"], "proxy")
        self.assertEqual(cfg["outbounds"][1]["type"], "direct")
