# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

hiddenimports = [
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.wsproto_impl",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "websockets",
    "pystray._win32",
    "comtypes.stream",
]

import os

_VISION_INC = os.getenv("JARVIS_BUILD_VISION", "").strip().lower() in ("1", "true", "yes")


def _model_datas():
    """Bundle only the models the shipped app needs at runtime.

    - models/tiny.en       : whisper STT (config.json, model.bin, tokenizer.json, vocabulary.txt)
    - models/redimnet*.onnx: voice-lock fingerprint model
    Excludes models/vision/* (multi-GB local-vision moondream2/llama assets) unless
    JARVIS_BUILD_VISION=1, because local vision is Phase-D disabled and the app
    falls back to cloud vision.
    """
    out = []
    if os.path.isdir("models/tiny.en"):
        out.append(("models/tiny.en", "models/tiny.en"))
    if os.path.isfile(os.path.join("models", "redimnet_b2_vox2.onnx")):
        out.append(("models/redimnet_b2_vox2.onnx", "models"))
    if _VISION_INC and os.path.isdir("models/vision"):
        out.append(("models/vision", "models/vision"))
    return out


datas = [
    ("ui", "ui"),
    ("jarvis.ico", "."),
    ("TERMS.md", "."),
    ("CHANGELOG.md", "."),
    ("VERSION", "."),
]
datas += _model_datas()
datas += collect_data_files("openwakeword")
datas += collect_data_files("faster_whisper")
datas += [
    (src, os.path.join("ctranslate2", dst))
    for src, dst in [
    ]
]

if os.path.isdir("platform-tools"):
    datas.append(("platform-tools", "platform-tools"))

binaries = []
binaries += collect_dynamic_libs("ctranslate2")

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "pandas"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="JARVIS",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon="jarvis.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="JARVIS",
)
