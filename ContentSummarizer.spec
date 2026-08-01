# PyInstaller recipe for the Windows desktop application.
from PyInstaller.utils.hooks import collect_all

yt_datas, yt_binaries, yt_hidden = collect_all("yt_dlp")

# The desktop app hosts the LAN web dashboard, so the web stack must ship too.
# uvicorn resolves its loop/protocol implementations by string at runtime, which
# static analysis cannot see — collect_all is what keeps those importable.
uv_datas, uv_binaries, uv_hidden = collect_all("uvicorn")

a = Analysis(
    ["desktop_gui.py"],
    pathex=[],
    binaries=yt_binaries + uv_binaries,
    datas=yt_datas + uv_datas,
    hiddenimports=yt_hidden + uv_hidden + [
        "anyio",
        "h11",
        "markdown",
        "multipart",
        "python_multipart",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["faster_whisper", "torch"],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="ContentSummarizer",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
