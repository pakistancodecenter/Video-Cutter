# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

project_dir = Path(SPECPATH)

datas = []
hiddenimports = []

# MoviePy / imageio-ffmpeg may load modules and binary data dynamically.
datas += collect_data_files("imageio_ffmpeg")
hiddenimports += collect_submodules("moviepy")
hiddenimports += collect_submodules("imageio_ffmpeg")

# Optional project-local FFmpeg folder.
ffmpeg_dir = project_dir / "ffmpeg"
if ffmpeg_dir.exists():
    datas.append((str(ffmpeg_dir), "ffmpeg"))

a = Analysis(
    ["Video_Cutter.py"],
    pathex=[str(project_dir)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="VideoCutter",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="VideoCutter",
)
