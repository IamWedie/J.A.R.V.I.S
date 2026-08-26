import json
import os
import socket
import subprocess
import threading
import time
import uuid
from http.server import HTTPServer, BaseHTTPRequestHandler

BROADCAST_PORT = 9999
HTTP_PORT = 9998
_message_log = []
_listeners = []
_server = None
_pc_ip = None

_TV_HINTS = ("tv", "bravia", "samsung", "lg", "qled", "oled", "chromecast",
             "androidtv", "shield", "roku", "firetv", "tcl", "hisense", "vizio")
_PHONE_HINTS = ("iphone", "ipad", "android", "pixel", "galaxy", "mi ", "redmi",
                "phone", "honor", "huawei")


def _get_pc_ip():
    global _pc_ip
    if _pc_ip:
        return _pc_ip
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        _pc_ip = s.getsockname()[0]
        s.close()
    except Exception:
        _pc_ip = "127.0.0.1"
    return _pc_ip


_MSG_PAGE = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>JARVIS</title><style>
*{margin:0;padding:0;box-sizing:border-box}
body{background:#0a0a0f;display:flex;align-items:center;justify-content:center;
min-height:100vh;font-family:'Segoe UI',system-ui,sans-serif;color:#fff;
background:radial-gradient(ellipse at 50%% 30%%,#0d1b2a 0%%,#060a10 70%%)}
.card{text-align:center;padding:60px;max-width:640px;width:90%%}
.arc{width:100px;height:100px;margin:0 auto 36px;border-radius:50%%;
background:conic-gradient(from 0deg,#00d4ff,#0099ff,#00d4ff);
box-shadow:0 0 60px rgba(0,180,255,.35);animation:pulse 2s ease-in-out infinite}
@keyframes pulse{0%%,100%%{box-shadow:0 0 60px rgba(0,180,255,.35)}
50%%{box-shadow:0 0 90px rgba(0,180,255,.55)}}
.msg{font-size:1.6rem;line-height:1.5;color:#e0e8f0;margin-top:20px}
.from{font-size:.9rem;color:#4da6ff;margin-top:16px;letter-spacing:2px;text-transform:uppercase}
.time{font-size:.75rem;color:#3a506b;margin-top:8px}
</style></head><body><div class="card">
<div class="arc"></div>
<div class="msg">%s</div>
<div class="from">From %s</div>
<div class="time">%s</div>
</div></body></html>"""


def _find_device(name):
    try:
        from core import memory
        devices = memory.known_devices()
        name_lower = name.lower()
        best = None
        best_score = 0
        for d in devices:
            score = 0
            hostname = d.get("hostname", "").lower()
            vendor = d.get("vendor", "").lower()
            dtype = d.get("type", "").lower()
            if name_lower == hostname or name_lower in hostname:
                score += 10
            if name_lower in vendor:
                score += 5
            if dtype != "unknown":
                score += 1
            if score > best_score:
                best_score = score
                best = d
        if best:
            return best
        for d in devices:
            vendor = d.get("vendor", "").lower()
            dtype = d.get("type", "").lower()
            for hint in _TV_HINTS + _PHONE_HINTS:
                if hint in name_lower and (hint in vendor or hint in dtype):
                    return d
        for d in devices:
            if name_lower in d.get("hostname", "").lower():
                return d
    except Exception:
        pass
    return None


def _route_send(name, message):
    results = []
    device = _find_device(name) if not _looks_like_ip(name) else {"ip": name, "type": "unknown"}
    if device and device.get("ip"):
        ip = device["ip"]
        dtype = device.get("type", "unknown")
        vendor = device.get("vendor", "")
        hostname = device.get("hostname", "")
        label = hostname or vendor or name
        is_phone = dtype == "phone" or any(h in vendor.lower() for h in _PHONE_HINTS)
        is_tv = dtype == "tv" or any(h in vendor.lower() for h in _TV_HINTS)
        if is_phone:
            try:
                from core.net import adb_controller
                if adb_controller._is_connected():
                    adb_controller.notify("JARVIS", message)
                    results.append(f"{label} (phone): notification sent")
                    return results
                else:
                    results.append(f"{label}: phone not connected via ADB, trying HTTP")
            except Exception as e:
                results.append(f"{label}: ADB notify failed ({e}), trying HTTP")
        if is_tv:
            try:
                from core.net import cast_controller
                cast = cast_controller._get_cast(label)
                if not cast:
                    cast = cast_controller._get_cast("TV")
                if cast:
                    msg_id = str(uuid.uuid4())[:8]
                    pc_ip = _get_pc_ip()
                    url = f"http://{pc_ip}:{HTTP_PORT}/msg/{msg_id}"
                    _pending_pages[msg_id] = {"message": message, "from": "JARVIS", "ts": time.time()}
                    cast.wait(timeout=10)
                    mc = cast.media_controller
                    mc.play_media(url, "text/html")
                    mc.block_until_active(timeout=10)
                    results.append(f"{label} (TV): message cast to screen")
                    return results
                else:
                    results.append(f"{label}: no Chromecast found, trying HTTP")
            except Exception as e:
                results.append(f"{label}: cast failed ({e}), trying HTTP")
        try:
            import urllib.request
            url = f"http://{ip}:{HTTP_PORT}/api/message"
            data = json.dumps({"from": "JARVIS", "message": message}).encode()
            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=3)
            results.append(f"{label} ({ip}): HTTP message delivered")
            return results
        except Exception:
            pass
    if not results and device:
        label = device.get("hostname") or device.get("vendor") or name
        results.append(f"{label}: no delivery channel available (not ADB-connected, not Cast-enabled)")
    elif not results:
        results.append(f"{name}: device not found in network cache, trying msg.exe")
    try:
        r = subprocess.run(
            ["msg", "*", "/timeout:10", f"JARVIS: {message}"],
            capture_output=True, timeout=5,
        )
        if r.returncode == 0:
            results.append("Windows msg broadcast sent to all PCs")
        else:
            results.append("msg.exe: sent (may be blocked on some PCs)")
    except FileNotFoundError:
        results.append("msg.exe: not available on this Windows edition")
    except Exception as e:
        results.append(f"msg.exe failed: {e}")
    return results


_pending_pages = {}


def _looks_like_ip(s):
    parts = s.split(".")
    return len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts)


def send_message(ip, message):
    return _route_send(ip, message)


def send_to_device(name, message):
    return _route_send(name, message)


def send_broadcast(message):
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        payload = json.dumps({"from": "JARVIS", "message": message, "ts": time.time()}).encode()
        sock.sendto(payload, ("<broadcast>", BROADCAST_PORT))
        sock.close()
        sent = []
        try:
            from core import memory
            for d in memory.known_devices():
                ip = d.get("ip")
                if not ip or ip == _get_pc_ip():
                    continue
                try:
                    import urllib.request
                    url = f"http://{ip}:{HTTP_PORT}/api/message"
                    data = json.dumps({"from": "JARVIS", "message": message}).encode()
                    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
                    urllib.request.urlopen(req, timeout=1)
                    label = d.get("hostname") or d.get("vendor") or ip
                    sent.append(label)
                except Exception:
                    pass
        except Exception:
            pass
        try:
            subprocess.run(
                ["msg", "*", "/timeout:10", f"JARVIS broadcast: {message}"],
                capture_output=True, timeout=5,
            )
            sent.append("all Windows PCs (msg.exe)")
        except Exception:
            pass
        return f"Broadcast to {len(sent)} devices: {message}" + (f" (reached: {', '.join(sent)})" if sent else "")
    except Exception as e:
        return f"Broadcast failed: {e}"


def ping_device(ip):
    try:
        r = subprocess.run(
            ["ping", "-n", "1", "-w", "1000", ip],
            capture_output=True, timeout=3,
        )
        return r.returncode == 0
    except Exception:
        return False


def on_message(callback):
    _listeners.append(callback)


def _notify_listeners(msg):
    for cb in _listeners:
        try:
            cb(msg)
        except Exception:
            pass


class _Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        try:
            data = json.loads(body)
            msg = data.get("message", "")
            sender = data.get("from", "unknown")
            entry = {"from": sender, "message": msg, "ts": time.time()}
            _message_log.append(entry)
            _notify_listeners(entry)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"ok":true}')
        except Exception:
            self.send_response(400)
            self.end_headers()

    def do_GET(self):
        if self.path == "/api/messages":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(_message_log[-50:]).encode())
            return
        if self.path.startswith("/msg/"):
            msg_id = self.path.split("/msg/", 1)[1]
            page = _pending_pages.get(msg_id)
            if not page:
                for entry in reversed(_message_log):
                    if entry.get("id") == msg_id:
                        page = entry
                        break
            if not page:
                self.send_response(404)
                self.end_headers()
                return
            from datetime import datetime
            ts_str = datetime.fromtimestamp(page["ts"]).strftime("%I:%M %p")
            html = _MSG_PAGE % (page["message"], page.get("from", "JARVIS"), ts_str)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html.encode())
            return
        self.send_response(404)
        self.end_headers()

    def log_message(self, *args):
        pass


def start_receiver(port=HTTP_PORT):
    global _server
    if _server:
        return
    try:
        _server = HTTPServer(("0.0.0.0", port), _Handler)
        t = threading.Thread(target=_server.serve_forever, daemon=True)
        t.start()
        print(f"[netmsg] receiver listening on port {port}")
    except Exception as e:
        print(f"[netmsg] receiver start failed: {e}")


def get_messages(limit=20):
    return _message_log[-limit:]
