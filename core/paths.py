"""路径解析 — 兼容开发模式和 PyInstaller 打包后运行。

PyInstaller 打包后所有资源会被解压到一个临时目录 sys._MEIPASS。
开发模式下从项目根目录读取。
"""

import os
import shutil
import sys
from pathlib import Path


def get_resource_root() -> Path:
    """资源根目录：打包后是 _MEIPASS，开发时是项目根"""
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).parent.parent.resolve()


def get_user_data_dir() -> Path:
    """用户数据目录 — 按需下载的大模型放这里，跨平台标准位置。

    Win:   %APPDATA%/VideoDedup
    macOS: ~/Library/Application Support/VideoDedup
    Linux: ~/.local/share/VideoDedup
    """
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share"))
    d = base / "VideoDedup"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_user_models_dir() -> Path:
    d = get_user_data_dir() / "models"
    d.mkdir(parents=True, exist_ok=True)
    return d


def get_model_path(model_id: str) -> str | None:
    """按优先级找本地模型：
       1) 打包内置 models/  (开发模式或随程序打包的小模型)
       2) 用户数据目录的 models/  (首次启动下载到这里)
       3) VIDEO_DEDUP_MODELS 环境变量
       找不到返回 None。
    """
    candidates = [
        get_resource_root() / "models" / model_id,
        get_user_models_dir() / model_id,
        Path(os.environ.get("VIDEO_DEDUP_MODELS", "/nonexistent")) / model_id,
    ]
    for c in candidates:
        if c.exists() and c.is_dir() and any(c.iterdir()):
            return str(c.resolve())
    return None


def is_sensevoice_ready() -> bool:
    """SenseVoice 主模型是否就绪（用来决定首次启动是否要弹下载界面）"""
    p = get_model_path("SenseVoiceSmall")
    if not p:
        return False
    # 必须有 model.pt（893MB 那个权重文件）
    return (Path(p) / "model.pt").exists()


def get_ffmpeg_binary() -> str:
    """打包后用内置 ffmpeg.exe，开发时用系统 PATH"""
    if hasattr(sys, "_MEIPASS"):
        suffix = ".exe" if sys.platform == "win32" else ""
        candidate = get_resource_root() / f"ffmpeg{suffix}"
        if candidate.exists():
            return str(candidate)
    # 系统 PATH
    found = shutil.which("ffmpeg")
    return found or "ffmpeg"


def get_ffprobe_binary() -> str:
    if hasattr(sys, "_MEIPASS"):
        suffix = ".exe" if sys.platform == "win32" else ""
        candidate = get_resource_root() / f"ffprobe{suffix}"
        if candidate.exists():
            return str(candidate)
    found = shutil.which("ffprobe")
    return found or "ffprobe"


def is_frozen() -> bool:
    """是否运行在 PyInstaller 打包后的环境中"""
    return hasattr(sys, "_MEIPASS")


# 模块加载时即解析，全局复用（PyInstaller 下也只会算一次）
FFMPEG = get_ffmpeg_binary()
FFPROBE = get_ffprobe_binary()
