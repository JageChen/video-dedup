"""按需下载 SenseVoice 模型 — 首次启动时调用。

从 ModelScope（阿里达摩院官方源，国内访问快）下载到用户数据目录：
  Win:   %APPDATA%/VideoDedup/models/
  macOS: ~/Library/Application Support/VideoDedup/models/
  Linux: ~/.local/share/VideoDedup/models/

下完一次以后离线就能用。
"""

import shutil
from pathlib import Path
from typing import Callable, Optional

from .paths import get_user_models_dir, is_sensevoice_ready


SENSEVOICE_REPO = "iic/SenseVoiceSmall"
VAD_REPO = "iic/speech_fsmn_vad_zh-cn-16k-common-pytorch"


def download_sensevoice(
    on_progress: Optional[Callable[[float, str], None]] = None,
) -> tuple[bool, str]:
    """下载 SenseVoice 主模型 + VAD 子模型到用户数据目录。

    on_progress(pct, msg) 每个阶段会回调一次（0.0 ~ 1.0）。
    """
    target_dir = get_user_models_dir()
    tmp_cache = target_dir / ".download_cache"
    tmp_cache.mkdir(parents=True, exist_ok=True)

    def _say(pct, msg):
        if on_progress:
            on_progress(pct, msg)
        print(f"[{int(pct*100):3d}%] {msg}")

    try:
        from modelscope import snapshot_download
    except ImportError:
        return False, "缺少 modelscope 库，请先 pip install modelscope"

    # ===== Step 1: SenseVoiceSmall（~900MB）=====
    sv_target = target_dir / "SenseVoiceSmall"
    if (sv_target / "model.pt").exists():
        _say(0.5, "SenseVoiceSmall 已存在，跳过下载")
    else:
        _say(0.05, "开始下载 SenseVoiceSmall（约 900MB，从 ModelScope 镜像）...")
        try:
            sv_path = snapshot_download(SENSEVOICE_REPO, cache_dir=str(tmp_cache))
        except Exception as e:
            return False, f"下载 SenseVoice 失败: {e}"
        _say(0.45, "复制到本地目录...")
        if sv_target.exists():
            shutil.rmtree(sv_target)
        shutil.copytree(sv_path, sv_target)
        _say(0.55, "SenseVoice 下载完成")

    # ===== Step 2: fsmn-vad（~4MB）=====
    vad_target = target_dir / "fsmn-vad"
    if vad_target.exists() and any(vad_target.iterdir()):
        _say(0.95, "fsmn-vad 已存在，跳过下载")
    else:
        _say(0.6, "下载 fsmn-vad（约 4MB）...")
        try:
            vad_path = snapshot_download(VAD_REPO, cache_dir=str(tmp_cache))
        except Exception as e:
            return False, f"下载 VAD 失败: {e}"
        _say(0.9, "复制到本地目录...")
        if vad_target.exists():
            shutil.rmtree(vad_target)
        shutil.copytree(vad_path, vad_target)

    # ===== Step 3: 清理 =====
    try:
        shutil.rmtree(tmp_cache)
    except Exception:
        pass

    _say(1.0, "✅ 所有模型准备完毕")
    return True, f"模型已下载到：{target_dir}"


def get_model_status() -> str:
    """返回模型状态描述（用在 UI 顶部状态条）"""
    if is_sensevoice_ready():
        from .paths import get_model_path
        path = get_model_path("SenseVoiceSmall")
        return f"✅ 模型已就绪 (`{path}`)"
    return (
        "⚠️ **首次使用需要下载 SenseVoice 模型（约 900MB）**  \n"
        "点击下方「📥 下载模型」按钮开始。下载一次后离线可用。"
    )
