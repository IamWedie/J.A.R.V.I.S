"""Source-level E2E: license server + app offline/online activation flow."""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

from core import config
from core import license as lic

SERVER = "http://127.0.0.1:8765"
passed = []
failed = []


def check(name, cond, extra=""):
    if cond:
        passed.append(name)
        print(f"  PASS  {name}")
    else:
        failed.append(name)
        print(f"  FAIL  {name}  {extra}")


def reset_data_dir():
    d = tempfile.mkdtemp(prefix="jarvis_e2e_")
    config.PROJECT_DIR = d
    config.LICENSE_KEY = ""
    return d


# --- 1. server-side activation limit reset by minting a fresh key ---
mint = requests.post(SERVER + "/admin/mint", json={"count": 1}, timeout=15).json()
key = mint["keys"][0]
print(f"minted via server: {key[:40]}...")

# --- 2. offline verify (app only, no server) ---
check("offline verify true", lic.validate_license(key) is True)
# tampered
parts = key.split("-")
seg = parts[1]
parts[1] = ("B" if seg[0] != "B" else "A") + seg[1:]
check("offline verify rejects tamper", lic.validate_license("-".join(parts)) is False)

# --- 3. app offline activate into a temp data dir ---
reset_data_dir()
ok, reason = lic.activate(key)
check("app activate offline ok", ok, reason)
check("app is_licensed after activate", lic.is_licensed() is True)

# --- 4. server validate sees the signature-valid key ---
v = requests.post(SERVER + "/license/validate", json={"license_key": key}, timeout=15).json()
check("server validate valid", v.get("valid") is True)

# --- 5. server activate + instance ---
a = requests.post(
    SERVER + "/license/activate",
    json={"license_key": key, "instance_name": "E2E-PC"},
    timeout=15,
).json()
check("server activate returns instance", bool(a.get("instance_id")), str(a))
inst = a.get("instance_id")

# --- 6. auto_activate with server_validate (online) ---
reset_data_dir()
config.LICENSE_KEY = key
d2 = config.PROJECT_DIR
activated, reason2 = lic.auto_activate(server_validate=lambda k: (True, ""))
check("auto_activate online ok", activated, reason2)
check("auto_activate wrote license", lic.is_licensed() is True)

# --- 7. auto_activate refuses when server rejects ---
reset_data_dir()
config.LICENSE_KEY = "JARV-NOPE"
ok3, reason3 = lic.auto_activate(server_validate=lambda k: (False, "server rejected"))
check("auto_activate rejects when server says no", ok3 is False, reason3)

# --- 8. server deactivate ---
d = requests.post(
    SERVER + "/license/deactivate",
    json={"license_key": key, "instance_id": inst},
    timeout=15,
).json()
check("server deactivate ok", d.get("ok") is True)

print("\n================ RESULT ================")
print(f"PASSED: {len(passed)}  FAILED: {len(failed)}")
if failed:
    print("FAILED:", failed)
    sys.exit(1)
print("ALL SOURCE-LEVEL E2E CHECKS PASSED")
