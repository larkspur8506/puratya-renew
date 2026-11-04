#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从节点链接起一个本地 sing-box（mixed 入站），供浏览器 / requests 走代理。

支持：tuic://、hysteria2://（含 hy2://）、vless://、vmess://、trojan://
用法：
    from singbox import start_singbox
    proxy = start_singbox(os.environ.get("NODE_LINK"))   # -> "socks5://127.0.0.1:7890" 或 None
解析与启动逻辑沿用 skymc_renew.py 的成熟实现，额外补了 tuic / hysteria2 / trojan。
"""

import atexit
import base64
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from urllib.parse import urlparse, parse_qs, unquote

PORT = int(os.environ.get("SINGBOX_PORT") or "7890")
_LOG_PATH = "/tmp/sing-box-mws.log"
_PROC = None


def _b64decode(data):
    data = (data or "").strip().replace("-", "+").replace("_", "/")
    return base64.b64decode(data + "=" * ((-len(data)) % 4))


def _truthy(v):
    return str(v or "").strip().lower() in ("1", "true", "yes", "on")


def _split_alpn(v):
    return [x.strip() for x in (v or "").split(",") if x.strip()] or None


def _parse_tuic(link):
    """tuic://uuid:password@host:port?congestion_control=bbr&udp_relay_mode=native&alpn=h3&sni=..&allow_insecure=1"""
    p = urlparse(link)
    q = {k: v[0] for k, v in parse_qs(p.query).items()}
    tls = {
        "enabled": True,
        "server_name": q.get("sni") or q.get("peer") or (p.hostname or ""),
    }
    alpn = _split_alpn(q.get("alpn"))
    if alpn:
        tls["alpn"] = alpn
    if _truthy(q.get("allow_insecure") or q.get("insecure") or q.get("skip_cert_verify")):
        tls["insecure"] = True
    out = {
        "type": "tuic",
        "tag": "proxy",
        "server": p.hostname or "",
        "server_port": int(p.port or 443),
        "uuid": unquote(p.username or ""),
        "password": unquote(p.password or ""),
        "tls": tls,
    }
    cc = q.get("congestion_control") or q.get("congestion")
    if cc:
        out["congestion_control"] = cc
    return out


def _parse_hysteria2(link):
    """hysteria2://password@host:port?sni=..&insecure=1&obfs=salamander&obfs-password=.."""
    p = urlparse(link)
    q = {k: v[0] for k, v in parse_qs(p.query).items()}
    tls = {
        "enabled": True,
        "server_name": q.get("sni") or q.get("peer") or (p.hostname or ""),
    }
    alpn = _split_alpn(q.get("alpn"))
    if alpn:
        tls["alpn"] = alpn
    if _truthy(q.get("insecure") or q.get("allow_insecure") or q.get("skip_cert_verify")):
        tls["insecure"] = True
    out = {
        "type": "hysteria2",
        "tag": "proxy",
        "server": p.hostname or "",
        "server_port": int(p.port or 443),
        "password": unquote(p.password or p.username or ""),
        "tls": tls,
    }
    if (q.get("obfs") or "").lower() == "salamander":
        out["obfs"] = {"type": "salamander", "password": q.get("obfs-password") or q.get("obfs_password") or ""}
    return out


def _parse_trojan(link):
    p = urlparse(link)
    q = {k: v[0] for k, v in parse_qs(p.query).items()}
    tls = {
        "enabled": True,
        "server_name": q.get("sni") or q.get("peer") or (p.hostname or ""),
    }
    alpn = _split_alpn(q.get("alpn"))
    if alpn:
        tls["alpn"] = alpn
    if _truthy(q.get("allowInsecure") or q.get("insecure") or q.get("skip-cert-verify")):
        tls["insecure"] = True
    out = {
        "type": "trojan",
        "tag": "proxy",
        "server": p.hostname or "",
        "server_port": int(p.port or 443),
        "password": unquote(p.password or p.username or ""),
        "tls": tls,
    }
    net = (q.get("type") or "tcp").lower()
    if net == "ws":
        out["transport"] = {"type": "ws", "path": q.get("path") or "/",
                            "headers": {"Host": q.get("host") or tls["server_name"]}}
    elif net == "grpc":
        out["transport"] = {"type": "grpc", "service_name": q.get("serviceName") or q.get("path") or ""}
    return out


def _parse_vmess(link):
    obj = json.loads(_b64decode(link[len("vmess://"):]).decode("utf-8"))
    host = obj.get("add") or obj.get("host") or ""
    net = (obj.get("net") or "tcp").lower()
    tls_on = str(obj.get("tls") or "").lower() in ("tls", "reality", "1", "true")
    sni = obj.get("sni") or obj.get("host") or host
    out = {
        "type": "vmess", "tag": "proxy", "server": host,
        "server_port": int(obj.get("port") or 443), "uuid": obj.get("id") or "",
        "security": obj.get("scy") or "auto", "alter_id": int(obj.get("aid") or 0),
    }
    if tls_on:
        out["tls"] = {"enabled": True, "server_name": sni,
                      "utls": {"enabled": True, "fingerprint": obj.get("fp") or "chrome"}}
    if net == "ws":
        out["transport"] = {"type": "ws", "path": obj.get("path") or "/",
                            "headers": {"Host": obj.get("host") or sni or host}}
    elif net == "grpc":
        out["transport"] = {"type": "grpc",
                            "service_name": obj.get("path") or obj.get("serviceName") or ""}
    return out


def _parse_vless(link):
    p = urlparse(link)
    q = {k: v[0] for k, v in parse_qs(p.query).items()}
    host = p.hostname or ""
    security = (q.get("security") or "none").lower()
    net = (q.get("type") or "tcp").lower()
    out = {
        "type": "vless", "tag": "proxy", "server": host,
        "server_port": int(p.port or 443), "uuid": unquote(p.username or ""),
        "packet_encoding": "xudp",
    }
    flow = q.get("flow")
    if flow:
        out["flow"] = flow
    if security in ("tls", "reality"):
        tls = {"enabled": True, "server_name": q.get("sni") or host,
               "utls": {"enabled": True, "fingerprint": q.get("fp") or "chrome"}}
        alpn = _split_alpn(q.get("alpn"))
        if alpn:
            tls["alpn"] = alpn
        if security == "reality":
            tls["reality"] = {"enabled": True, "public_key": q.get("pbk") or "",
                              "short_id": q.get("sid") or ""}
        out["tls"] = tls
    if net == "ws":
        out["transport"] = {"type": "ws", "path": q.get("path") or "/",
                            "headers": {"Host": q.get("host") or q.get("sni") or host}}
    elif net == "grpc":
        out["transport"] = {"type": "grpc",
                            "service_name": q.get("serviceName") or q.get("path") or ""}
    elif net == "httpupgrade":
        out["transport"] = {"type": "httpupgrade", "path": q.get("path") or "/",
                            "headers": {"Host": q.get("host") or q.get("sni") or host}}
    return out


PARSERS = (
    ("tuic://", _parse_tuic),
    ("hysteria2://", _parse_hysteria2),
    ("hy2://", _parse_hysteria2),
    ("vless://", _parse_vless),
    ("vmess://", _parse_vmess),
    ("trojan://", _parse_trojan),
)


def parse_node(link):
    """节点链接 -> sing-box outbound dict。"""
    link = (link or "").strip()
    for prefix, fn in PARSERS:
        if link.lower().startswith(prefix):
            out = fn(link)
            if not out.get("server") or not out.get("server_port"):
                raise ValueError("{} 链接缺少 server/port".format(prefix))
            return out
    raise ValueError("不支持的节点协议（支持 tuic/hysteria2/vless/vmess/trojan）")


def build_config(link, listen_port=PORT):
    return {
        "log": {"level": "info", "timestamp": True},
        "inbounds": [{
            "type": "mixed", "tag": "mixed-in",
            "listen": "127.0.0.1", "listen_port": int(listen_port),
        }],
        "outbounds": [parse_node(link), {"type": "direct", "tag": "direct"}],
    }


def _port_open(host, port):
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def start_singbox(link, listen_port=PORT):
    """起 sing-box；返回 proxy URL（socks5://127.0.0.1:PORT），没配链接返回 None。"""
    global _PROC
    link = (link or "").strip()
    if not link:
        return None
    binary = shutil.which("sing-box")
    if not binary:
        print("[!] 已设 NODE_LINK 但找不到 sing-box 可执行文件")
        sys.exit(1)
    cfg = build_config(link, listen_port)
    cfg_path = "/tmp/sing-box-mws.json"
    with open(cfg_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    print("[i] sing-box 节点协议: {}://".format(link.split("://", 1)[0]))

    logf = open(_LOG_PATH, "ab")
    _PROC = subprocess.Popen([binary, "run", "-c", cfg_path], stdout=logf, stderr=logf)

    def _stop():
        if _PROC and _PROC.poll() is None:
            _PROC.terminate()
    atexit.register(_stop)

    for _ in range(40):
        if _PROC.poll() is not None:
            break
        if _port_open("127.0.0.1", listen_port):
            # mixed 入站同时支持 http 与 socks5；用 http 让 requests / Chrome 都能直接吃
            proxy = "{}://127.0.0.1:{}".format(
                os.environ.get("SINGBOX_PROXY_SCHEME", "http"), listen_port)
            print("[✓] sing-box 已启动: {}".format(proxy))
            return proxy
        time.sleep(0.4)

    print("[✗] sing-box 启动失败，日志尾部：")
    try:
        print(open(_LOG_PATH, errors="ignore").read()[-2000:])
    except OSError:
        pass
    sys.exit(1)


def stop_singbox():
    global _PROC
    if _PROC and _PROC.poll() is None:
        _PROC.terminate()
