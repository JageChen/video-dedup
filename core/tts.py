"""TTS 配音模块 — 用 Microsoft Edge TTS 把 SRT 字幕变成新音轨替换视频原音。

核心招式（业内矩阵号工具的标配）：
  1. 把 SRT 每段字幕送给 Edge TTS 生成对应音频
  2. 用 ffmpeg adelay + amix 按字幕时间戳定位每段
  3. 用新音轨替换原视频音轨 → 平台 ACR 指纹直接失效

可用音色见 VOICE_CHOICES。
"""

import asyncio
import re
import subprocess
import tempfile
from pathlib import Path

from .paths import FFMPEG, FFPROBE
from .subtitle import parse_srt


# 常用中文 Edge TTS 音色（按使用率排序）
VOICE_CHOICES = [
    ("晓晓 - 女声 · 温柔（默认推荐）", "zh-CN-XiaoxiaoNeural"),
    ("云扬 - 男声 · 活力", "zh-CN-YunyangNeural"),
    ("云健 - 男声 · 沉稳", "zh-CN-YunjianNeural"),
    ("云希 - 男声 · 年轻", "zh-CN-YunxiNeural"),
    ("晓伊 - 女声 · 活泼", "zh-CN-XiaoyiNeural"),
    ("辽宁晓北 - 女声 · 东北腔", "zh-CN-liaoning-XiaobeiNeural"),
    ("陕西晓妮 - 女声 · 陕西腔", "zh-CN-shaanxi-XiaoniNeural"),
    ("Aria - English Female", "en-US-AriaNeural"),
    ("Guy - English Male", "en-US-GuyNeural"),
]


async def _tts_one_async(text: str, voice: str, output: Path, rate: str = "+0%"):
    """生成一段 TTS 到指定 mp3 文件"""
    import edge_tts
    communicate = edge_tts.Communicate(text, voice, rate=rate)
    await communicate.save(str(output))


def tts_one(text: str, voice: str, output: Path, rate: str = "+0%"):
    """同步包装"""
    asyncio.run(_tts_one_async(text, voice, output, rate))


def _get_audio_duration(audio_path: Path) -> float:
    """用 ffprobe 拿音频时长（秒）"""
    result = subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(audio_path)],
        capture_output=True, text=True,
    )
    try:
        return float(result.stdout.strip())
    except (ValueError, AttributeError):
        return 0.0


def synthesize_srt(
    srt_text: str,
    voice: str,
    output_mp3: Path,
    rate: str = "+0%",
    on_progress=None,
) -> tuple[bool, str]:
    """把 SRT 整段合成成一个 mp3，按字幕时间戳精确定位每段。

    实现：每段 SRT 单独 TTS → ffmpeg adelay 定位 → amix 混合成一条音轨
    """
    segments = parse_srt(srt_text)
    if not segments:
        return False, "SRT 字幕解析后为空"

    workdir = output_mp3.parent / f".tts_tmp_{output_mp3.stem}"
    workdir.mkdir(parents=True, exist_ok=True)

    try:
        # 1. 每段 TTS
        clips: list[tuple[float, Path]] = []  # (start_sec, mp3_path)
        total = len(segments)
        for i, seg in enumerate(segments):
            if on_progress:
                on_progress(0.1 + 0.7 * i / total, f"TTS {i+1}/{total}")
            text = seg["text"].strip()
            if not text:
                continue
            clip_path = workdir / f"clip_{i:04d}.mp3"
            try:
                tts_one(text, voice, clip_path, rate)
                clips.append((seg["start"], clip_path))
            except Exception as e:
                # 单段失败不影响整体，跳过
                print(f"TTS 段 {i} 失败: {e}")
                continue

        if not clips:
            return False, "所有 TTS 段都生成失败（检查网络）"

        # 2. 用 ffmpeg adelay 定位 + amix 混合
        # 总时长 = 最后一段的 start + 它的实际时长（+ 1 秒余量）
        last_start, last_clip = clips[-1]
        last_dur = _get_audio_duration(last_clip)
        total_duration = last_start + last_dur + 1.0

        if on_progress:
            on_progress(0.85, "合并所有 TTS 片段")

        # 构造 ffmpeg 命令：N 个输入 + filter_complex + amix
        cmd = [FFMPEG, "-y"]
        for _, clip in clips:
            cmd.extend(["-i", str(clip)])

        # filter: 每个输入 adelay 到对应时间，然后全部 amix
        filter_parts = []
        amix_inputs = []
        for i, (start, _) in enumerate(clips):
            delay_ms = int(start * 1000)
            filter_parts.append(
                f"[{i}:a]adelay={delay_ms}|{delay_ms},apad[a{i}]"
            )
            amix_inputs.append(f"[a{i}]")
        amix_count = len(clips)
        filter_parts.append(
            f"{''.join(amix_inputs)}amix=inputs={amix_count}:duration=first:"
            f"dropout_transition=0,atrim=end={total_duration:.3f}[out]"
        )
        filter_complex = ";".join(filter_parts)

        cmd.extend([
            "-filter_complex", filter_complex,
            "-map", "[out]",
            "-c:a", "libmp3lame", "-b:a", "192k",
            str(output_mp3),
        ])

        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            return False, f"ffmpeg 合并失败:\n{result.stderr[-1500:]}"

        if on_progress:
            on_progress(1.0, "完成")
        return True, "OK"

    finally:
        # 清理临时文件
        try:
            for f in workdir.glob("*.mp3"):
                f.unlink()
            workdir.rmdir()
        except Exception:
            pass


def replace_audio(
    video_path: Path,
    new_audio_path: Path,
    output_path: Path,
    keep_original_volume: float = 0.0,
) -> tuple[bool, str]:
    """把视频的音轨换成新音频。
    keep_original_volume: 0.0 = 完全替换；0.1 = 保留 10% 原音当背景
    """
    if keep_original_volume <= 0.001:
        # 直接替换音轨
        cmd = [
            FFMPEG, "-y",
            "-i", str(video_path),
            "-i", str(new_audio_path),
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k",
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-shortest",
            str(output_path),
        ]
    else:
        # 混合原音 + 新音
        v = keep_original_volume
        cmd = [
            FFMPEG, "-y",
            "-i", str(video_path),
            "-i", str(new_audio_path),
            "-filter_complex",
            f"[0:a]volume={v:.3f}[orig];[1:a]volume=1.0[new];[orig][new]amix=inputs=2:duration=longest[out]",
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k",
            "-map", "0:v:0",
            "-map", "[out]",
            str(output_path),
        ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
        if result.returncode != 0:
            return False, f"替换音轨失败:\n{result.stderr[-1500:]}"
        return True, "OK"
    except subprocess.TimeoutExpired:
        return False, "替换音轨超时"
