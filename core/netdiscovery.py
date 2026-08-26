import asyncio
import ipaddress
import os
import re
import socket
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from core.logging_setup import get_logger

log = get_logger("netdiscovery")

OUI_VENDORS = {
    "00:1A:11": "Google", "F4:F5:D8": "Google", "30:FD:38": "Google",
    "A4:B3:05": "Honor", "8C:55:4A": "Honor", "5C:B3:95": "Huawei",
    "34:6B:D3": "Huawei", "78:1D:BA": "Huawei", "E8:CD:2D": "Huawei",
    "C8:69:CD": "Samsung", "40:B0:FA": "Samsung", "84:25:DB": "Samsung",
    "D0:07:A6": "Samsung", "F0:72:EA": "Samsung", "AC:5F:3E": "Samsung",
    "00:16:32": "Sony", "04:5D:4B": "Sony", "30:F9:ED": "Sony",
    "F8:8F:CA": "Sony", "00:26:BB": "Apple", "04:0C:CE": "Apple",
    "28:6A:BA": "Apple", "50:EA:D6": "Apple", "7C:6D:62": "Apple",
    "A4:83:E7": "Apple", "D0:03:4B": "Apple", "F0:18:98": "Apple",
    "64:A2:F9": "Xiaomi", "74:23:44": "Xiaomi", "8C:BE:BE": "Xiaomi",
    "AC:C1:EE": "Xiaomi", "F4:B8:5E": "Xiaomi", "18:B4:30": "TP-Link",
    "50:C7:BF": "TP-Link", "A0:F3:C1": "TP-Link", "C0:25:E9": "TP-Link",
    "A0:18:42": "TP-Link",
    "00:1B:44": "SanDisk", "00:50:56": "VMware", "08:00:27": "VirtualBox",
    "00:15:5D": "Microsoft Hyper-V", "3C:D9:2B": "HP", "48:0F:CF": "HP",
    "B4:99:BA": "HP", "00:26:2D": "Abbree", "44:85:00": "Intel",
    "7C:5C:F8": "Intel", "98:FA:E3": "Intel", "A0:A8:CD": "Intel",
    "C8:F7:50": "Intel", "E8:6A:64": "Intel", "18:03:73": "Wistron",
    "20:1A:06": "Wistron", "3C:97:0E": "Wistron", "00:0D:3A": "Microsoft",
    "60:45:CB": "AzureWave", "74:D0:2B": "AzureWave", "88:C9:D0": "AzureWave",
    "00:E0:4C": "Realtek", "52:54:00": "QEMU", "B8:27:EB": "Raspberry Pi",
    "DC:A6:32": "Raspberry Pi", "E4:5F:01": "Raspberry Pi", "0C:9D:A2": "ARRIS",
    "D8:3A:DD": "Raspberry Pi", "2C:CF:67": "Google", "3C:71:BF": "FFVV",
}

TV_HINTS = ("tv", "bravia", "samsung", "lg", "qled", "oled", "chromecast", "androidtv", "shield", "roku", "firetv", "tcl", "hisense", "vizio", "philips", "aoc")
PHONE_HINTS = ("iphone", "ipad", "android", "pixel", "galaxy", "mi ", "redmi", "phone", "honor", "huawei")
LAPTOP_HINTS = ("laptop", "macbook", "thinkpad", "notebook", "victus", "omen", "ideapad", "surface", "xps", "inspiron", "desk", "pc-")

_cache = {"devices": [], "ts": 0.0}
_cache_lock = threading.Lock()
CACHE_TTL = 240


def local_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def _ping_host(ip, timeout_ms=300):
    try:
        r = subprocess.run(
            ["ping", "-n", "1", "-w", str(timeout_ms), ip],
            capture_output=True, text=True, timeout=timeout_ms / 1000 + 3,
        )
        return ip if r.returncode == 0 else None
    except Exception:
        return None


def _arp_table():
    entries = {}
    try:
        out = subprocess.run(["arp", "-a"], capture_output=True, text=True, timeout=10).stdout
        for line in out.splitlines():
            m = re.match(r"\s*(\d+\.\d+\.\d+\.\d+)\s+([0-9a-fA-F:-]{14,17})\s+(\w+)", line)
            if m:
                ip, mac, kind = m.group(1), m.group(2).replace("-", ":").lower(), m.group(3)
                if mac != "ff:ff:ff:ff:ff:ff":
                    entries[ip] = (mac, kind)
    except Exception:
        pass
    return entries


def _hostname(ip, timeout=0.6):
    try:
        socket.setdefaulttimeout(timeout)
        return socket.gethostbyaddr(ip)[0]
    except Exception:
        return ""


def _is_multicast_or_reserved(ip):
    try:
        first = int(ip.split(".")[0])
        return first >= 224
    except Exception:
        return True


def _vendor(mac):
    if not mac:
        return ""
    prefix = mac[:8].upper()
    return OUI_VENDORS.get(prefix, "")


def _classify(name, vendor, mac):
    hay = f"{name} {vendor}".lower()
    if any(h in hay for h in TV_HINTS):
        return "tv"
    if any(h in hay for h in PHONE_HINTS) or (vendor in ("Apple", "Xiaomi", "Huawei", "Honor", "Google") and "pc" not in hay and "book" not in hay):
        return "phone"
    if any(h in hay for h in LAPTOP_HINTS) or vendor in ("HP", "Dell", "Lenovo", "Microsoft", "Intel", "Wistron"):
        return "laptop"
    if vendor in ("VMware", "VirtualBox", "QEMU", "Microsoft Hyper-V"):
        return "vm"
    return "other"


def _mdns_scan(duration=4.0):
    found = []
    try:
        from zeroconf import Zeroconf, ServiceBrowser

        services = [
            "_googlecast._tcp.local.", "_airplay._tcp.local.", "_raop._tcp.local.",
            "_adb-tls-connect._tcp.local.", "_device-info._tcp.local.",
            "_companion-link._tcp.local.", "_smb._tcp.local.",
            "_jarvis-agent._tcp.local.", "_http._tcp.local.",
        ]
        zc = Zeroconf()
        results = {}

        class Listener:
            def add_service(self, zc, type_, name):
                try:
                    info = zc.get_service_info(type_, name, 1500)
                    if info and info.addresses:
                        ip = socket.inet_ntoa(info.addresses[0])
                        server = (info.server or name or "").rstrip(".")
                        results[ip] = {"name": server.split(".")[0] if server else "", "mdns": type_.split(".")[0].lstrip("_")}
                except Exception:
                    pass

            def update_service(self, zc, type_, name):
                pass

            def remove_service(self, zc, type_, name):
                pass

        listeners = [ServiceBrowser(zc, s, Listener()) for s in services]
        time.sleep(duration)
        for l in listeners:
            try:
                l.cancel()
            except Exception:
                pass
        zc.close()
        found = results
    except Exception as e:
        log.warning("mdns scan failed: %s", e)
    return found


def _ssdp_scan(duration=3.0):
    found = {}
    try:
        msg = (
            "M-SEARCH * HTTP/1.1\r\n"
            "HOST: 239.255.255.250:1900\r\n"
            'MAN: "ssdp:discover"\r\n'
            "MX: 2\r\n"
            "ST: upnp:rootdevice\r\n\r\n"
        ).encode()
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        s.settimeout(0.5)
        s.sendto(msg, ("239.255.255.250", 1900))
        end = time.time() + duration
        while time.time() < end:
            try:
                data, addr = s.recvfrom(4096)
                text = data.decode("utf-8", "ignore")
                server = ""
                m = re.search(r"SERVER:\s*(.+)", text, re.I)
                if m:
                    server = m.group(1).strip()
                found[addr[0]] = server
            except socket.timeout:
                continue
        s.close()
    except Exception as e:
        log.warning("ssdp scan failed: %s", e)
    return found


def _sweep_subnet():
    local = local_ip()
    net = ipaddress.ip_network(f"{local}/24", strict=False)
    hosts = [str(h) for h in net.hosts()]
    alive = []
    lock = threading.Lock()

    def probe(ip):
        result = _ping_host(ip)
        if result:
            with lock:
                alive.append(result)

    threads = []
    for ip in hosts:
        th = threading.Thread(target=probe, args=(ip,))
        th.daemon = True
        th.start()
        threads.append(th)
        if len(threads) >= 128:
            for t in threads:
                t.join(timeout=4)
            threads = []
    for t in threads:
        t.join(timeout=4)
    alive.append(local)
    return local, alive


def scan_now():
    local, alive = _sweep_subnet()
    arp = _arp_table()
    mdns = _mdns_scan(3.5)
    ssdp = _ssdp_scan(2.5)

    devices = {}
    all_ips = set(alive) | set(arp.keys()) | set(mdns.keys()) | set(ssdp.keys())
    need_name = set()
    for ip in all_ips:
        if _is_multicast_or_reserved(ip):
            continue
        mac, kind = arp.get(ip, ("", ""))
        name = mdns.get(ip, {}).get("name", "")
        vendor = _vendor(mac) or ("This PC" if ip == local else "")
        online = ip in set(alive)
        dtype = "this-pc" if ip == local else "router" if ip.endswith(".1") else _classify(name, vendor, mac)
        if ip in mdns:
            dtype = dtype if dtype not in ("other", "router") else _classify(mdns[ip].get("mdns", ""), vendor, mac)
        if not name:
            if dtype == "router":
                name = "Router/Gateway"
            else:
                name = f"device-{ip.split('.')[-1]}"
                need_name.add(ip)
        devices[ip] = {
            "ip": ip, "mac": mac, "name": name,
            "vendor": vendor, "type": dtype, "online": online,
            "mdns": mdns.get(ip, {}).get("mdns", ""), "ssdp": ssdp.get(ip, ""),
        }
    if need_name:
        def _get_h(ip):
            h = _hostname(ip, timeout=0.5)
            if h:
                return ip, h.split(".")[0]
            return ip, None
        with ThreadPoolExecutor(max_workers=20) as ex:
            results = list(ex.map(_get_h, need_name))
        for ip, h in results:
            if h and ip in devices:
                devices[ip]["name"] = h
    for ip, server in ssdp.items():
        if ip not in devices and not _is_multicast_or_reserved(ip):
            devices[ip] = {
                "ip": ip, "mac": "", "name": (server.split(";")[0] or f"ssdp-{ip}")[:40],
                "vendor": "", "type": "tv", "online": True, "mdns": "", "ssdp": server,
            }

    dev_list = sorted(devices.values(), key=lambda d: tuple(int(x) for x in d["ip"].split(".")))
    with _cache_lock:
        _cache["devices"] = dev_list
        _cache["ts"] = time.time()
    try:
        from core import memory
        memory.save_devices(dev_list)
    except Exception as e:
        log.error("device cache save failed: %s", e)
    return dev_list


def get_devices(force=False):
    with _cache_lock:
        fresh = (time.time() - _cache["ts"]) < CACHE_TTL and _cache["devices"]
    if fresh and not force:
        return _cache["devices"]
    return scan_now()


def find_device(name):
    needle = str(name).lower()
    devices = get_devices()
    matches = [
        d for d in devices
        if needle in d["name"].lower() or needle in d["vendor"].lower() or needle in d["type"].lower()
    ]
    return matches


def format_devices(devices):
    if not devices:
        return "No devices found on the network."
    lines = []
    for d in devices:
        tag = " (this PC)" if d["type"] == "this-pc" else ""
        extra = f" via {d['mdns']}" if d.get("mdns") else ""
        extra += f" [media]" if d.get("ssdp") else ""
        lines.append(f"- {d['name']} | {d['type']}{tag} | {d['ip']}"
                     + (f" | {d['vendor']}" if d["vendor"] else "") + extra)
    return f"Found {len(devices)} devices on the network:\n" + "\n".join(lines)
