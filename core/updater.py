"""JARVIS Auto-Update — downloads and installs updates from GitHub releases."""
import json
import os
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from datetime import datetime

import core.config as config
from core.logging_setup import get_logger

log = get_logger("updater")

GITHUB_REPO = "IamWedie/J.A.R.V.I.S"
GITHUB_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
FROZEN = getattr(sys, "frozen", False)


def _current_version():
    return config.VERSION


def check_for_update():
    try:
        req = urllib.request.Request(
            GITHUB_API,
            headers={"Accept": "application/vnd.github.v3+json"},
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
            latest = data.get("tag_name", "").lstrip("v")
            if not latest:
                return None
            if latest == _current_version():
                return None
            assets = data.get("assets", [])
            exe_asset = None
            for a in assets:
                name = a.get("name", "")
                if name.endswith(".exe") and "setup" in name.lower():
                    exe_asset = a
                    break
            if not exe_asset and assets:
                exe_asset = assets[0]
            return {
                "current": _current_version(),
                "latest": latest,
                "url": data.get("html_url", ""),
                "download_url": exe_asset.get("browser_download_url", "") if exe_asset else "",
                "asset_name": exe_asset.get("name", "") if exe_asset else "",
                "size": exe_asset.get("size", 0) if exe_asset else 0,
                "body": data.get("body", ""),
            }
    except Exception as e:
        log.warning("update check failed: %s", e)
        return None


def download_update(info, progress_callback=None):
    url = info.get("download_url", "")
    if not url:
        log.warning("no download URL for update")
        return None
    try:
        tmp_dir = tempfile.mkdtemp(prefix="jarvis_update_")
        ext = os.path.splitext(info.get("asset_name", ""))[1] or ".exe"
        dest = os.path.join(tmp_dir, f"JARVIS_update{ext}")
        log.info("downloading update from %s", url)
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=120) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            with open(dest, "wb") as f:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress_callback and total:
                        progress_callback(downloaded, total)
        log.info("downloaded %s (%d bytes)", dest, downloaded)
        return dest
    except Exception as e:
        log.error("download failed: %s", e)
        return None


def install_update(filepath):
    if not filepath or not os.path.exists(filepath):
        return False
    log.info("installing update from %s", filepath)
    try:
        if FROZEN:
            subprocess.Popen(
                [filepath, "/SILENT", "/NORESTART"],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        else:
            subprocess.Popen(
                [filepath, "/SILENT", "/NORESTART"],
            )
        return True
    except Exception as e:
        log.error("install failed: %s", e)
        return False


def apply_zip_update(info):
    url = info.get("download_url", "")
    if not url or not url.endswith(".zip"):
        return False
    try:
        tmp_dir = tempfile.mkdtemp(prefix="jarvis_update_")
        zip_path = os.path.join(tmp_dir, "update.zip")
        log.info("downloading zip update from %s", url)
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=120) as resp:
            with open(zip_path, "wb") as f:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
        if FROZEN:
            target = os.path.dirname(sys.executable)
        else:
            target = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        log.info("extracting to %s", target)
        with zipfile.ZipFile(zip_path, "r") as zf:
            for member in zf.namelist():
                if member.startswith("/") or ".." in member:
                    continue
                target_path = os.path.join(target, member)
                os.makedirs(os.path.dirname(target_path), exist_ok=True)
                if not member.endswith("/"):
                    with zf.open(member) as src, open(target_path, "wb") as dst:
                        dst.write(src.read())
        log.info("zip update applied, restart required")
        return True
    except Exception as e:
        log.error("zip update failed: %s", e)
        return False
