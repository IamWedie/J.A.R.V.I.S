import os
import subprocess
import sys
import zipfile

sys.stdout.reconfigure(encoding='utf-8')

BASE = os.path.dirname(os.path.abspath(__file__))
VIS = os.path.join(BASE, "models", "vision")
LLAMA_DIR = os.path.join(VIS, "llama-bin")

TEXT_F16 = os.path.join(VIS, "moondream2-text-model-f16_ct-vicuna.gguf")
MMPROJ = os.path.join(VIS, "moondream2-mmproj-f16-20250414.gguf")
TEXT_Q4 = os.path.join(VIS, "moondream2-text-q4_k_m.gguf")
SERVER = os.path.join(LLAMA_DIR, "llama-server.exe")
PORT = 8755


def step(msg):
    print(f"\n=== {msg} ===", flush=True)


def extract_zips():
    step("extracting llama.cpp binaries")
    for z in ("llama-b10635-bin-win-cuda-12.4-x64.zip", "cudart-llama-bin-win-cuda-12.4-x64.zip"):
        src = os.path.join(VIS, z)
        if not os.path.exists(src):
            print(f"  missing: {z}")
            return False
        print(f"  extracting {z} ...")
        with zipfile.ZipFile(src) as zf:
            for member in zf.namelist():
                target = os.path.join(LLAMA_DIR, member)
                if member.endswith("/"):
                    os.makedirs(target, exist_ok=True)
                    continue
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with zf.open(member) as s, open(target, "wb") as d:
                    while True:
                        buf = s.read(1024 * 1024)
                        if not buf:
                            break
                        d.write(buf)
    exe = SERVER
    ok = os.path.exists(exe)
    print(f"  llama-server.exe present: {ok}")
    return ok


def quantize():
    step("quantizing text model to Q4_K_M (fits 4GB VRAM)")
    if os.path.exists(TEXT_Q4) and os.path.getsize(TEXT_Q4) > 500_000_000:
        print("  already quantized")
        return True
    qexe = os.path.join(LLAMA_DIR, "llama-quantize.exe")
    if not os.path.exists(qexe):
        print("  llama-quantize.exe missing")
        return False
    r = subprocess.run([qexe, TEXT_F16, TEXT_Q4, "Q4_K_M"],
                       capture_output=True, text=True, timeout=1800)
    if r.returncode != 0 or not os.path.exists(TEXT_Q4):
        print(f"  quantize failed: {r.stderr[-400:]}")
        return False
    print(f"  wrote {TEXT_Q4} ({os.path.getsize(TEXT_Q4)/1e9:.2f} GB)")
    return True


def start_server(detached=True):
    step(f"starting llama-server on port {PORT}")
    if not os.path.exists(SERVER):
        print("  server binary missing")
        return False
    args = [SERVER,
            "-m", TEXT_Q4,
            "--mmproj", MMPROJ,
            "--port", str(PORT),
            "-ngl", "99",
            "-c", "2048",
            "--host", "127.0.0.1"]
    if detached:
        subprocess.Popen(args, cwd=VIS,
                         creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS)
    else:
        subprocess.run(args, cwd=VIS)
    return True


def health(timeout=120):
    import time
    import urllib.request
    step("waiting for /health")
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=3) as r:
                if r.getcode() == 200:
                    print(f"  healthy after {time.time()-t0:.0f}s")
                    return True
        except Exception:
            pass
        time.sleep(2)
    print("  server did not become healthy")
    return False


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    if mode in ("all", "extract"):
        if not extract_zips():
            sys.exit(1)
    if mode in ("all", "quantize"):
        if not quantize():
            sys.exit(1)
    if mode in ("all", "start"):
        if not start_server():
            sys.exit(1)
        sys.exit(0 if health() else 1)
