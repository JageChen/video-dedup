import re
import subprocess
import tempfile
from pathlib import Path
from typing import Iterator

from .paths import FFMPEG, FFPROBE


_MODEL_CACHE: dict[str, object] = {}


def _get_sensevoice_asr():
    """SenseVoice ASR 模型 — 优先本地路径，找不到从 ModelScope 下载"""
    key = "sensevoice_asr"
    if key not in _MODEL_CACHE:
        from funasr import AutoModel
        from .paths import get_model_path
        local = get_model_path("SenseVoiceSmall")
        _MODEL_CACHE[key] = AutoModel(
            model=local or "iic/SenseVoiceSmall",
            device="cpu",
            disable_update=True,
        )
    return _MODEL_CACHE[key]


def _get_vad():
    """VAD 模型 — 优先本地路径"""
    key = "vad"
    if key not in _MODEL_CACHE:
        from funasr import AutoModel
        from .paths import get_model_path
        local = get_model_path("fsmn-vad")
        _MODEL_CACHE[key] = AutoModel(
            model=local or "fsmn-vad",
            device="cpu",
            disable_update=True,
            max_single_segment_time=8000,
        )
    return _MODEL_CACHE[key]


# 按标点优先级切分（强 → 弱）
_SPLIT_STRONG = re.compile(r"(?<=[。！？!?])")
_SPLIT_WEAK = re.compile(r"(?<=[，,；;、])")


def _split_long_text(
    text: str, start: float, end: float, max_chars: int = 18,
) -> list[dict]:
    """长段按标点 + 字数二次切分，时间戳按字符比例线性分配"""
    text = text.strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [{"start": start, "end": end, "text": text}]

    # 第一刀：按强标点切句子
    sents = [s.strip() for s in _SPLIT_STRONG.split(text) if s.strip()]
    # 第二刀：长句继续按弱标点切
    parts: list[str] = []
    for s in sents:
        if len(s) <= max_chars:
            parts.append(s)
        else:
            sub = [p.strip() for p in _SPLIT_WEAK.split(s) if p.strip()]
            if not sub:
                parts.append(s)
                continue
            # 弱标点切完仍超长 → 硬切
            for p in sub:
                if len(p) <= max_chars:
                    parts.append(p)
                else:
                    for i in range(0, len(p), max_chars):
                        parts.append(p[i:i + max_chars])

    if not parts:
        return [{"start": start, "end": end, "text": text}]

    total_chars = sum(len(p) for p in parts)
    if total_chars == 0:
        return [{"start": start, "end": end, "text": text}]

    duration = max(end - start, 0.1)
    result = []
    cursor = start
    for p in parts:
        seg_dur = duration * len(p) / total_chars
        seg_end = cursor + seg_dur
        result.append({"start": cursor, "end": seg_end, "text": p})
        cursor = seg_end
    return result


# SenseVoice 输出里的特殊标记，要清理掉才能作为字幕
_SENSEVOICE_TAG = re.compile(r"<\|[^|]+\|>")


def _format_timestamp(seconds: float) -> str:
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int((seconds - int(seconds)) * 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _parse_timestamp(ts: str) -> float:
    h, m, rest = ts.split(":")
    s, ms = rest.split(",")
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000


def transcribe(
    video_path: Path,
    language: str = "auto",
    on_progress=None,
) -> tuple[list[dict], str]:
    """SenseVoice ASR 转写视频，返回 (segments, detected_language)"""
    return _transcribe_sensevoice(video_path, language, on_progress)


def _transcribe_sensevoice(video_path, language, on_progress):
    """SenseVoice 中文 ASR + VAD 切分。
    流程：ffmpeg 抽 16kHz wav → VAD 拿 segments → 每段单独 SenseVoice → 拼时间戳
    """
    # 1. 抽 16kHz 单声道 wav（SenseVoice 要求）
    audio_wav = video_path.parent / f".{video_path.stem}_16k.wav"
    subprocess.run(
        [FFMPEG, "-y", "-i", str(video_path),
         "-ac", "1", "-ar", "16000", "-loglevel", "error",
         str(audio_wav)],
        check=True,
    )

    if on_progress:
        on_progress(0.15)

    try:
        # 2. VAD 切分
        vad = _get_vad()
        vad_res = vad.generate(input=str(audio_wav), disable_pbar=True)
        vad_segments = vad_res[0]["value"] if vad_res else []
        # 形如 [[start_ms, end_ms], ...]

        if on_progress:
            on_progress(0.25)

        # 3. 对每段独立跑 SenseVoice
        asr = _get_sensevoice_asr()
        segments = []
        detected_lang = "zh" if language == "auto" else language
        total = max(len(vad_segments), 1)

        for i, seg in enumerate(vad_segments):
            start_ms, end_ms = seg[0], seg[1]
            if on_progress:
                on_progress(0.25 + 0.7 * (i + 1) / total)

            # 切音频片段为临时文件（用 ffmpeg copy stream，无重编码很快）
            slice_path = audio_wav.parent / f".slice_{i}.wav"
            subprocess.run(
                [FFMPEG, "-y", "-i", str(audio_wav),
                 "-ss", f"{start_ms/1000:.3f}", "-to", f"{end_ms/1000:.3f}",
                 "-loglevel", "error", str(slice_path)],
                check=True,
            )

            try:
                res = asr.generate(
                    input=str(slice_path),
                    cache={},
                    language=language if language != "auto" else "auto",
                    use_itn=True,
                    disable_pbar=True,
                )
                if not res:
                    continue
                raw = res[0].get("text", "")
                # 提取检测语言
                m = re.search(r"<\|(zh|en|ja|ko|yue)\|>", raw)
                if m:
                    detected_lang = m.group(1)
                text = _SENSEVOICE_TAG.sub("", raw).strip()
                if text:
                    # 长文本按标点二次切分，避免一段几十秒铺满屏幕
                    sub_segs = _split_long_text(
                        text,
                        start_ms / 1000.0,
                        end_ms / 1000.0,
                        max_chars=18,
                    )
                    segments.extend(sub_segs)
            finally:
                slice_path.unlink(missing_ok=True)

        return segments, detected_lang
    finally:
        audio_wav.unlink(missing_ok=True)


def segments_to_srt(segments: list[dict]) -> str:
    lines = []
    for i, seg in enumerate(segments, 1):
        start = _format_timestamp(seg["start"])
        end = _format_timestamp(seg["end"])
        lines.append(f"{i}\n{start} --> {end}\n{seg['text']}\n")
    return "\n".join(lines)


def parse_srt(srt_text: str) -> list[dict]:
    segments = []
    blocks = re.split(r"\n\s*\n", srt_text.strip())
    for block in blocks:
        lines = block.strip().split("\n")
        if len(lines) < 3:
            continue
        timing = lines[1]
        m = re.match(r"([\d:,]+)\s*-->\s*([\d:,]+)", timing)
        if not m:
            continue
        start = _parse_timestamp(m.group(1))
        end = _parse_timestamp(m.group(2))
        text = "\n".join(lines[2:]).strip()
        segments.append({"start": start, "end": end, "text": text})
    return segments


# ASS Alignment: 1=左下 2=中下 3=右下 5=左上 6=中上 7=右上
ALIGNMENT_MAP = {
    "底部": 2,
    "中间": 10,
    "顶部": 6,
}

# macOS 自带的中文字体
FONT_CHOICES = [
    "PingFang SC",
    "Hiragino Sans GB",
    "STHeiti",
    "Songti SC",
    "Arial",
]


def _rgb_to_ass_color(rgb_hex: str) -> str:
    """#RRGGBB → &H00BBGGRR（ASS 用 BGR 顺序，前两位是透明度反转值 00=不透明）"""
    rgb_hex = rgb_hex.lstrip("#")
    if len(rgb_hex) != 6:
        rgb_hex = "FFFFFF"
    r, g, b = rgb_hex[0:2], rgb_hex[2:4], rgb_hex[4:6]
    return f"&H00{b}{g}{r}".upper()


def burn_subtitle(
    video_path: Path,
    srt_text: str,
    output_path: Path,
    font: str = "PingFang SC",
    font_size: int = 22,
    primary_color: str = "#FFFFFF",
    outline_color: str = "#000000",
    outline_width: int = 2,
    position: str = "底部",
) -> tuple[bool, str]:
    """把 SRT 字幕烧到视频上"""
    alignment = ALIGNMENT_MAP.get(position, 2)
    primary = _rgb_to_ass_color(primary_color)
    outline = _rgb_to_ass_color(outline_color)

    # 把 SRT 放在输出目录下（短路径无特殊字符），避免 FFmpeg filter 转义陷阱
    output_path.parent.mkdir(parents=True, exist_ok=True)
    srt_path = output_path.with_suffix(".srt")
    srt_path.write_text(srt_text, encoding="utf-8")

    # 注意 ASS style 内部用 `,` 分隔 key=value，必须用反斜杠转义，
    # 不然 FFmpeg filtergraph parser 会把 `,` 当成滤镜分隔符
    style = (
        f"FontName={font}\\,"
        f"FontSize={font_size}\\,"
        f"PrimaryColour={primary}\\,"
        f"OutlineColour={outline}\\,"
        f"Outline={outline_width}\\,"
        f"Alignment={alignment}\\,"
        f"BorderStyle=1\\,"
        f"MarginV=30"
    )

    vf = f"subtitles={srt_path}:force_style={style}"

    cmd = [
        FFMPEG, "-y",
        "-i", str(video_path),
        "-vf", vf,
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "20",
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(output_path),
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if result.returncode != 0:
            return False, (result.stderr or "")[-1500:]
        return True, "OK"
    except subprocess.TimeoutExpired:
        return False, "烧字幕超时（>30 分钟）"
