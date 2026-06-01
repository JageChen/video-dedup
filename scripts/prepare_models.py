"""把 ModelScope/HuggingFace 缓存里的模型复制到项目 models/ 目录，准备打包。

打包前先跑一次：python scripts/prepare_models.py

会从以下位置拉取：
  - ~/.cache/modelscope/hub/models/iic/SenseVoiceSmall
  - ~/.cache/modelscope/hub/models/iic/speech_fsmn_vad_zh-cn-16k-common-pytorch
  - ~/.cache/huggingface/hub/models--Systran--faster-whisper-tiny

如果本地没有，会自动下载（首次运行很慢，国内 HuggingFace 大文件可能要等很久）。
"""

import os
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.resolve()
MODELS_DIR = PROJECT_ROOT / "models"
MODELS_DIR.mkdir(exist_ok=True)


def copy_dir(src: Path, dst: Path, label: str) -> bool:
    if dst.exists() and any(dst.iterdir()):
        print(f"  ⏩ {label} 已存在，跳过：{dst}")
        return True
    if not src.exists():
        print(f"  ❌ 源不存在：{src}")
        return False
    print(f"  📋 复制 {label}：{src} → {dst}")
    shutil.copytree(src, dst, dirs_exist_ok=True)
    return True


def download_sensevoice():
    """优先从缓存复制，没缓存时从 ModelScope 下载"""
    target = MODELS_DIR / "SenseVoiceSmall"
    if target.exists() and any(target.iterdir()):
        print(f"  ⏩ SenseVoiceSmall 已存在，跳过")
        return True

    cache_path = Path.home() / ".cache" / "modelscope" / "hub" / "models" / "iic" / "SenseVoiceSmall"
    if cache_path.exists():
        return copy_dir(cache_path, target, "SenseVoiceSmall")

    print("  ⬇️ 缓存里没有，从 ModelScope 下载 SenseVoiceSmall...")
    from modelscope import snapshot_download
    path = snapshot_download("iic/SenseVoiceSmall", cache_dir=str(MODELS_DIR.parent / ".tmp_ms"))
    return copy_dir(Path(path), target, "SenseVoiceSmall")


def download_vad():
    target = MODELS_DIR / "fsmn-vad"
    if target.exists() and any(target.iterdir()):
        print(f"  ⏩ fsmn-vad 已存在，跳过")
        return True

    cache_path = (
        Path.home() / ".cache" / "modelscope" / "hub" / "models" / "iic"
        / "speech_fsmn_vad_zh-cn-16k-common-pytorch"
    )
    if cache_path.exists():
        return copy_dir(cache_path, target, "fsmn-vad")

    print("  ⬇️ 缓存里没有，从 ModelScope 下载 fsmn-vad...")
    from modelscope import snapshot_download
    path = snapshot_download(
        "iic/speech_fsmn_vad_zh-cn-16k-common-pytorch",
        cache_dir=str(MODELS_DIR.parent / ".tmp_ms"),
    )
    return copy_dir(Path(path), target, "fsmn-vad")


def download_whisper_tiny():
    """faster-whisper tiny 的 cache 路径用了 HuggingFace 格式，里面有 snapshots/<hash>/<files>"""
    target = MODELS_DIR / "faster-whisper-tiny"
    if target.exists() and any(target.iterdir()):
        print(f"  ⏩ faster-whisper-tiny 已存在，跳过")
        return True

    cache_root = (
        Path.home() / ".cache" / "huggingface" / "hub"
        / "models--Systran--faster-whisper-tiny"
    )
    snapshots = cache_root / "snapshots"
    if snapshots.exists():
        # 取第一个 snapshot 目录
        snap_dirs = list(snapshots.iterdir())
        if snap_dirs:
            return copy_dir(snap_dirs[0], target, "faster-whisper-tiny")

    print("  ⬇️ 缓存里没有，从 HuggingFace 下载 faster-whisper-tiny...")
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    from huggingface_hub import snapshot_download as hf_download
    path = hf_download("Systran/faster-whisper-tiny", cache_dir=str(MODELS_DIR.parent / ".tmp_hf"))
    return copy_dir(Path(path), target, "faster-whisper-tiny")


def main():
    print("=" * 60)
    print("准备模型文件 → models/")
    print("=" * 60)

    ok = True
    ok &= download_sensevoice()
    ok &= download_vad()
    ok &= download_whisper_tiny()

    print()
    print("=" * 60)
    if ok:
        print("✅ 所有模型准备完毕")
        total = sum(f.stat().st_size for f in MODELS_DIR.rglob("*") if f.is_file())
        print(f"📊 models/ 总大小: {total / 1024 / 1024:.1f} MB")
        # 列出每个模型大小
        for sub in sorted(MODELS_DIR.iterdir()):
            if sub.is_dir():
                size = sum(f.stat().st_size for f in sub.rglob("*") if f.is_file())
                print(f"   {sub.name}: {size / 1024 / 1024:.1f} MB")
    else:
        print("❌ 部分模型准备失败，请检查上面的错误")
        sys.exit(1)


if __name__ == "__main__":
    main()
