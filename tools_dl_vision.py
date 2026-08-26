import os
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

sys.stdout.reconfigure(encoding='utf-8')

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", "vision")
os.makedirs(OUT, exist_ok=True)
LOG = os.path.join(OUT, "download.log")

FILES = [
    ("https://github.com/ggml-org/llama.cpp/releases/download/b10635/llama-b10635-bin-win-cuda-12.4-x64.zip", 250_468_618),
    ("https://github.com/ggml-org/llama.cpp/releases/download/b10635/cudart-llama-bin-win-cuda-12.4-x64.zip", 391_443_627),
    ("https://huggingface.co/ggml-org/moondream2-20250414-GGUF/resolve/main/moondream2-text-model-f16.gguf", 2_839_535_072),
    ("https://huggingface.co/ggml-org/moondream2-20250414-GGUF/resolve/main/moondream2-mmproj-f16-20250414.gguf", 909_777_984),
]

CHUNK = 8 * 1024 * 1024
THREADS = 6


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def fetch_range(url, start, want, path, retries=8):
    """Download exactly `want` bytes into a standalone part file (local offset 0)."""
    end = start + want - 1
    for attempt in range(retries):
        try:
            req = urllib.request.Request(
                url,
                headers={"Range": f"bytes={start}-{end}", "User-Agent": "jarvis-setup",
                         "Accept-Encoding": "identity"},
            )
            tmp = path + ".tmp"
            with urllib.request.urlopen(req, timeout=45) as r:
                if r.getcode() != 206:
                    raise IOError(f"server ignored Range (status {r.getcode()})")
                remaining = want
                with open(tmp, "wb") as f:
                    while remaining > 0:
                        data = r.read(min(1024 * 256, remaining))
                        if not data:
                            raise IOError("stream ended early")
                        f.write(data)
                        remaining -= len(data)
            if os.path.getsize(tmp) != want:
                os.remove(tmp)
                raise IOError("short write")
            if os.path.exists(path):
                os.remove(path)
            os.replace(tmp, path)
            return True
        except Exception as e:
            try:
                if os.path.exists(path + ".tmp"):
                    os.remove(path + ".tmp")
            except OSError:
                pass
            if attempt == retries - 1:
                log(f"    chunk@{start}: giving up ({str(e)[:70]})")
                return False
            time.sleep(1.5 * (attempt + 1))


def download(url, size):
    name = url.split("/")[-1].split("?")[0]
    part_dir = os.path.join(OUT, f"_parts_{name}")
    os.makedirs(part_dir, exist_ok=True)
    n_chunks = (size + CHUNK - 1) // CHUNK

    def ppath(i):
        return os.path.join(part_dir, f"{i:05d}.part")

    todo = [i for i in range(n_chunks)
            if not os.path.exists(ppath(i)) or os.path.getsize(ppath(i)) != min(CHUNK, size - i * CHUNK)]

    if todo:
        log(f"{name}: fetching {len(todo)}/{n_chunks} chunks ({size/1e9:.2f} GB)")
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=THREADS) as ex:
            results = list(ex.map(lambda i: fetch_range(url, i * CHUNK, min(CHUNK, size - i * CHUNK), ppath(i)), todo))
        if not all(results):
            bad = sum(1 for r in results if not r)
            log(f"{name}: FAILED {bad} chunks")
            return False
        dt = time.time() - t0
        log(f"{name}: chunks complete in {dt:.0f}s ({size/1e6/dt:.1f} MB/s)")
    else:
        log(f"{name}: all chunks already present")

    final = os.path.join(OUT, name)
    ok_parts = all(os.path.exists(ppath(i)) and os.path.getsize(ppath(i)) == min(CHUNK, size - i * CHUNK)
                   for i in range(n_chunks))
    if not ok_parts:
        log(f"{name}: part validation failed")
        return False
    with open(final, "wb") as out:
        for i in range(n_chunks):
            with open(ppath(i), "rb") as pf:
                while True:
                    buf = pf.read(1024 * 1024)
                    if not buf:
                        break
                    out.write(buf)
    if os.path.getsize(final) != size:
        log(f"{name}: SIZE MISMATCH after concat")
        os.remove(final)
        return False
    for i in range(n_chunks):
        try:
            os.remove(ppath(i))
        except OSError:
            pass
    try:
        os.rmdir(part_dir)
    except OSError:
        pass
    log(f"DONE {name} ({size/1e9:.2f} GB verified)")
    return True


def main():
    log("=== vision downloader v2 started ===")
    all_ok = True
    for url, size in FILES:
        if not download(url, size):
            all_ok = False
    log("=== ALL DONE ===" if all_ok else "=== FINISHED WITH ERRORS ===")


if __name__ == "__main__":
    main()
