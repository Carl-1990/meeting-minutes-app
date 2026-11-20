# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the arm64 会议纪要.app bundle."""

import os
import platform

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
ICON = os.path.join(SPECPATH, "icon.icns")


def _safe_collect(package: str):
    binaries = []
    datas = []
    hidden = []
    try:
        binaries = collect_dynamic_libs(package)
    except Exception:
        binaries = []
    try:
        datas = collect_data_files(package)
    except Exception:
        datas = []
    try:
        hidden = collect_submodules(package)
    except Exception:
        hidden = []
    return binaries, datas, hidden


binaries = []
datas = [
    (os.path.join(ROOT, "app", "ui", "styles.qss"), os.path.join("app", "ui")),
    (os.path.join(ROOT, "models"), "models"),
    (os.path.join(ROOT, "build", "icon.png"), "build"),
]
hiddenimports = [
    "sherpa_onnx",
    "sounddevice",
    "docx",
    "lxml",
    "numpy",
    "PySide6.QtSvg",
    "PySide6.QtNetwork",
    "AVFoundation",
    "Cocoa",
    "objc",
]
for package in ("sherpa_onnx", "sounddevice"):
    extra_bins, extra_datas, extra_hidden = _safe_collect(package)
    binaries += extra_bins
    datas += extra_datas
    hiddenimports += extra_hidden

block_cipher = None
a = Analysis(
    [os.path.join(ROOT, "main.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "pytest"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
target_arch = "arm64" if platform.machine() == "arm64" else None
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="会议纪要",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=target_arch,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="会议纪要",
)
app = BUNDLE(
    coll,
    name="会议纪要.app",
    icon=ICON,
    bundle_identifier="com.meetingminutes.local",
    info_plist={
        "CFBundleName": "会议纪要",
        "CFBundleDisplayName": "会议纪要",
        "CFBundleIdentifier": "com.meetingminutes.local",
        "CFBundleShortVersionString": "0.3.0",
        "CFBundleVersion": "0.3.0",
        "CFBundlePackageType": "APPL",
        "LSMinimumSystemVersion": "12.0",
        "NSMicrophoneUsageDescription": "会议纪要需要使用麦克风录制会议并转为文字",
        "NSHighResolutionCapable": True,
        "LSApplicationCategoryType": "public.app-category.productivity",
        "CFBundleDevelopmentRegion": "zh_CN",
    },
)
