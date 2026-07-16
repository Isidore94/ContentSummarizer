# PyInstaller recipe for the Windows desktop application.
from PyInstaller.utils.hooks import collect_all

yt_datas, yt_binaries, yt_hidden = collect_all("yt_dlp")

a = Analysis(
    ["desktop_gui.py"],
    pathex=[],
    binaries=yt_binaries,
    datas=yt_datas,
    hiddenimports=yt_hidden,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["faster_whisper", "torch", "fastapi", "uvicorn", "markdown"],
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
