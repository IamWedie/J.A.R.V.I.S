import json
import socket
import subprocess
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler

BROADCAST_PORT = 9999
HTTP_PORT = 9998
_message_log = []
_listeners = []
_server = None


def send_message(ip, message):
    import urllib.request
    try:
        url = f"http://{ip}:{HTTP_PORT}/api/message"
        data = json.dumps({"from": "JARVIS", "message": message}).encode()
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=3)
        return f"Message sent to {ip}: {message}"
    except Exception as e:
        return f"Send to {ip} failed: {e}"


def send_broadcast(message):
    import urllib.request
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        payload = json.dumps({"from": "JARVIS", "message": message, "ts": time.time()}).encode()
        sock.sendto(payload, ("<broadcast>", BROADCAST_PORT))
        sock.close()
        results = []
        try:
            net = subprocess.run(
                ["ipconfig"], capture_output=True, text=True, timeout=3
            )
            for line in net.stdout.splitlines():
                if "IPv4" in line and "192.168" in line:
                    ip = line.split(":")[-1].strip()
                    base = ip.rsplit(".", 1)[0]
                    for i in range(1, 255):
                        target = f"{base}.{i}"
                        if target == ip:
                            continue
                        try:
                            url = f"http://{target}:{HTTP_PORT}/api/message"
                            data = json.dumps({"from": "JARVIS", "message": message}).encode()
                            req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
                            urllib.request.urlopen(req, timeout=1)
                            results.append(target)
                        except Exception:
                            pass
        except Exception:
            pass
        return f"Broadcast sent to {len(results)} devices: {message}"
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
        else:
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
