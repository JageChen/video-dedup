# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置 — Windows / macOS 通用

打包前必须：
  1. 运行 python scripts/prepare_models.py 准备模型
  2. 准备 ffmpeg.exe / ffmpeg 二进制放到项目根
       Windows: 下载 ffmpeg-release-full.7z 解压取 bin/ffmpeg.exe
       macOS:   /opt/homebrew/bin/ffmpeg 复制过来

打包命令：
  pyinstaller build.spec --clean
输出：dist/视频工具箱/
"""

import sys
import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules

block_cipher = None
PROJECT_ROOT = Path(SPECPATH).resolve()
IS_WINDOWS = sys.platform == "win32"
FFMPEG_NAME = "ffmpeg.exe" if IS_WINDOWS else "ffmpeg"
FFPROBE_NAME = "ffprobe.exe" if IS_WINDOWS else "ffprobe"


# ============== 资源文件 ==============
datas = [
    # 项目自带资源
    ("assets/luts", "assets/luts"),
    # 只打包 4MB 的 VAD 模型；SenseVoice (~900MB) 首次启动按需下载
    ("models/fsmn-vad", "models/fsmn-vad"),
]

# Gradio 的前端静态资源（必须，否则页面 404）
datas += collect_data_files("gradio")
datas += collect_data_files("gradio_client")
# funasr 的资源
datas += collect_data_files("funasr", include_py_files=False)
# modelscope 资源
datas += collect_data_files("modelscope", include_py_files=False)
# edge-tts
datas += collect_data_files("edge_tts")


# ============== 二进制 ==============
binaries = []
ffmpeg_path = PROJECT_ROOT / FFMPEG_NAME
ffprobe_path = PROJECT_ROOT / FFPROBE_NAME
if ffmpeg_path.exists():
    binaries.append((str(ffmpeg_path), "."))
if ffprobe_path.exists():
    binaries.append((str(ffprobe_path), "."))


# ============== 隐藏依赖 ==============
# PyInstaller 静态分析抓不到的动态导入
hiddenimports = [
    # 我们自己的
    "core.config",
    "core.paths",
    "core.processor",
    "core.subtitle",
    "core.tts",
    "core.lut_generator",
]

# Gradio 全套
hiddenimports += collect_submodules("gradio")
hiddenimports += collect_submodules("gradio_client")

# ASR / TTS — 只保留 SenseVoice 全家桶（funasr + modelscope + torch）
# 删 faster_whisper / ctranslate2（不用 Whisper 了）
hiddenimports += collect_submodules("funasr")
hiddenimports += collect_submodules("modelscope")
hiddenimports += collect_submodules("edge_tts")
hiddenimports += collect_submodules("torch")
hiddenimports += collect_submodules("torchaudio")

# Pillow / videohash 等小依赖
hiddenimports += [
    "PIL",
    "PIL.Image",
    "numpy",
    "scipy",
    "scipy.signal",
    "soundfile",
    "librosa",
    "websockets",
    "uvicorn",
    "fastapi",
    "starlette",
    "pydantic",
    "huggingface_hub",
]


# ============== 排除（缩小体积）==============
excludes = [
    "matplotlib",      # 不用
    "PyQt5",
    "PyQt6",
    "PySide2",
    "PySide6",
    "tkinter",
    "tcl",
    "tk",
    "test",
    "tests",
    "unittest",
    "pytest",
    "IPython",
    "jupyter",
    "notebook",
]


a = Analysis(
    ["app.py"],
    pathex=[str(PROJECT_ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="视频工具箱",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,             # UPX 会让 Windows Defender 误报，关掉
    console=True,          # 保留控制台窗口（看日志）；要无窗口改 False
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(PROJECT_ROOT / "assets" / "icon.ico") if (PROJECT_ROOT / "assets" / "icon.ico").exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="视频工具箱",
)
