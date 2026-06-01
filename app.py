import os
# 国内访问 HuggingFace LFS CDN 经常被墙，切到 hf-mirror.com 镜像
# 必须在 import faster_whisper / huggingface_hub 之前设置
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

import shutil
import subprocess
from pathlib import Path

import gradio as gr

from core.config import TransformConfig
from core.processor import process_batch
from core.downloader import download_sensevoice, get_model_status
from core.paths import is_sensevoice_ready
from core.subtitle import (
    FONT_CHOICES,
    burn_subtitle,
    segments_to_srt,
    transcribe,
)
from core.tts import VOICE_CHOICES, replace_audio, synthesize_srt


BASE_DIR = Path(__file__).parent.resolve()
DEFAULT_OUTPUT = BASE_DIR / "output"
UPLOAD_DIR = BASE_DIR / "uploads"


ORIENTATION_CHOICES = [
    ("保持原样", "keep"),
    ("横屏 → 竖屏（裁切中间）", "h2v"),
    ("竖屏 → 横屏（模糊背景）", "v2h"),
]

LANGUAGE_CHOICES = [
    ("自动检测", "auto"),
    ("中文", "zh"),
    ("English", "en"),
    ("日本語", "ja"),
    ("한국어", "ko"),
    ("Español", "es"),
    ("Français", "fr"),
]

MODEL_CHOICES = [
    ("tiny (75MB, 已下好, 立即可用)", "tiny"),
    ("base (150MB, 国内下载较慢)", "base"),
    ("small (500MB, 推荐质量, 国内 50min)", "small"),
    ("medium (1.5GB, 国内 2-3 小时)", "medium"),
    ("large-v3 (3GB, 最准, 国内大半天)", "large-v3"),
]

ENGINE_CHOICES = [
    ("SenseVoice（阿里达摩院，中文场景推荐）", "sensevoice"),
    ("Whisper（OpenAI，多语言）", "whisper"),
]


def open_in_file_manager(dir_path: str) -> str:
    """跨平台在系统文件管理器中打开目录"""
    import platform
    p = Path(dir_path).expanduser().resolve() if dir_path else DEFAULT_OUTPUT
    p.mkdir(parents=True, exist_ok=True)
    system = platform.system()
    try:
        if system == "Darwin":
            subprocess.Popen(["open", str(p)])
        elif system == "Windows":
            subprocess.Popen(["explorer", str(p)])
        else:
            subprocess.Popen(["xdg-open", str(p)])
        return f"📂 已打开：`{p}`"
    except Exception as e:
        return f"❌ 打开失败: {e}\n路径：`{p}`"


def check_ffmpeg() -> str:
    if not shutil.which("ffmpeg"):
        return "⚠️ 未检测到 ffmpeg，请先执行：`brew install ffmpeg`"
    try:
        out = subprocess.run(
            ["ffmpeg", "-version"], capture_output=True, text=True, timeout=5
        )
        first_line = (out.stdout or "").splitlines()[0] if out.stdout else "ffmpeg"
        return f"✅ {first_line}"
    except Exception as e:
        return f"⚠️ ffmpeg 检测失败: {e}"


# ============== Tab 1: 视频去重 ==============

def run_dedup(
    files, output_dir_str,
    mirror, orientation,
    res_enabled, res_scale,
    frame_enabled, frame_interval,
    wm_enabled,
    speed_enabled, speed_factor,
    pitch_enabled, pitch_factor,
    # 激进变换
    crop_edge_enabled, crop_edge_percent,
    border_enabled, border_width,
    lut_enabled, lut_preset,
    intro_black_enabled, intro_black_duration, outro_black_duration,
    audio_eq_enabled, eq_low, eq_mid, eq_high,
    metadata_clean,
    # 算法升级到位（5 个）
    double_scale_enabled, double_scale_mid, double_scale_final,
    double_compress_enabled, dc_pre_crf, dc_final_crf,
    lut3d_enabled, lut3d_preset,
    vfr_enabled, vfr_intensity,
    container_hack_enabled,
    # v2 升级
    blur_sharpen_enabled, blur_sigma,
    hsl_shift_enabled, hsl_hue, hsl_sat, hsl_val,
    fps_convert_enabled, fps_inter, fps_final,
    crf, preset,
    progress=gr.Progress(),
):
    if not files:
        return "请先上传至少 1 个视频", None

    out_dir = Path(output_dir_str).expanduser().resolve() if output_dir_str else DEFAULT_OUTPUT
    out_dir.mkdir(parents=True, exist_ok=True)

    cfg = TransformConfig(
        mirror_enabled=mirror, orientation=orientation,
        resolution_enabled=res_enabled, resolution_scale=float(res_scale),
        frame_drop_enabled=frame_enabled, frame_drop_interval=int(frame_interval),
        watermark_enabled=wm_enabled,
        speed_enabled=speed_enabled, speed_factor=float(speed_factor),
        audio_pitch_enabled=pitch_enabled, audio_pitch_factor=float(pitch_factor),
        crop_edge_enabled=crop_edge_enabled, crop_edge_percent=float(crop_edge_percent),
        border_enabled=border_enabled, border_width=int(border_width),
        lut_enabled=lut_enabled, lut_preset=lut_preset,
        intro_black_enabled=intro_black_enabled,
        intro_black_duration=float(intro_black_duration),
        outro_black_duration=float(outro_black_duration),
        audio_eq_enabled=audio_eq_enabled,
        eq_low_gain=float(eq_low), eq_mid_gain=float(eq_mid), eq_high_gain=float(eq_high),
        metadata_clean=metadata_clean,
        # 算法升级到位
        double_scale_enabled=double_scale_enabled,
        double_scale_mid=float(double_scale_mid),
        double_scale_final=float(double_scale_final),
        double_compress_enabled=double_compress_enabled,
        double_compress_pre_crf=int(dc_pre_crf),
        double_compress_final_crf=int(dc_final_crf),
        lut3d_enabled=lut3d_enabled,
        lut3d_preset=lut3d_preset,
        vfr_enabled=vfr_enabled,
        vfr_intensity=float(vfr_intensity),
        container_hack_enabled=container_hack_enabled,
        # v2 升级
        blur_sharpen_enabled=blur_sharpen_enabled,
        blur_sigma=float(blur_sigma),
        hsl_shift_enabled=hsl_shift_enabled,
        hsl_hue_shift=float(hsl_hue),
        hsl_saturation=float(hsl_sat),
        hsl_value=float(hsl_val),
        fps_convert_enabled=fps_convert_enabled,
        fps_intermediate=int(fps_inter),
        fps_final=int(fps_final),
        crf=int(crf), preset=preset,
    )

    input_paths = [Path(f if isinstance(f, str) else f.name) for f in files]

    def _progress(pct: float, msg: str):
        progress(pct, desc=msg)

    results = process_batch(input_paths, out_dir, cfg, progress=_progress)

    rows = []
    ok_count = 0
    for r in results:
        if r["ok"]:
            ok_count += 1
        rows.append([
            Path(r["input"]).name,
            "✅" if r["ok"] else "❌",
            Path(r["output"]).name if r["output"] else "",
            r["msg"] if not r["ok"] else "",
        ])

    summary = f"完成 {ok_count}/{len(results)}，输出目录：{out_dir}"
    return summary, rows


# ============== Tab 2: 字幕生成 ==============

def run_transcribe(video_file, language, progress=gr.Progress()):
    if not video_file:
        return "❌ 请先上传视频", "", None

    if not is_sensevoice_ready():
        return (
            "❌ SenseVoice 模型未下载。请先点上方「📥 下载模型」按钮。",
            "", None,
        )

    video_path = Path(video_file if isinstance(video_file, str) else video_file.name)
    progress(0.05, desc="加载 SenseVoice 模型...")

    def _on_progress(pct: float):
        progress(0.1 + pct * 0.9, desc=f"转写中... {int(pct * 100)}%")

    try:
        segments, detected_lang = transcribe(
            video_path, language=language, on_progress=_on_progress,
        )
    except Exception as e:
        import traceback
        return f"❌ 转写失败: {e}\n```\n{traceback.format_exc()[-1500:]}\n```", "", None

    srt_text = segments_to_srt(segments)
    summary = f"✅ 转写完成（SenseVoice 阿里达摩院），识别语言：**{detected_lang}**，共 {len(segments)} 段字幕"
    return summary, srt_text, str(video_path)


def run_download_model(progress=gr.Progress()):
    """下载 SenseVoice + VAD 到用户目录"""
    if is_sensevoice_ready():
        return f"✅ 模型已就绪，无需下载"

    def _on_dl_progress(pct, msg):
        progress(pct, desc=msg)

    ok, msg = download_sensevoice(on_progress=_on_dl_progress)
    return f"{'✅' if ok else '❌'} {msg}"


def build_style_preview(font, font_size, primary_color, outline_color,
                        outline_width, position):
    """用 CSS 模拟最终字幕效果，让用户调参数时实时看到。"""
    # 字号近似换算：ASS 在视频里渲染的字号 ≈ 预览的 CSS px 大小
    css_size = int(font_size * 1.6)

    # text-shadow 多方向偏移模拟描边
    ow = int(outline_width)
    shadows = []
    if ow > 0:
        for dx in range(-ow, ow + 1):
            for dy in range(-ow, ow + 1):
                if dx == 0 and dy == 0:
                    continue
                shadows.append(f"{dx}px {dy}px 0 {outline_color}")
    shadow_css = ", ".join(shadows) if shadows else "none"

    align_map = {"底部": "flex-end", "中间": "center", "顶部": "flex-start"}
    align = align_map.get(position, "flex-end")
    padding_v = "30px 16px" if position != "中间" else "16px"

    # 两个比例的预览：横屏 16:9 + 竖屏 9:16，让用户两种都能看
    preview_text = "朋友们这个视频真的太赞了"
    common_span = f"""
        <span style="
            font-family: '{font}', 'PingFang SC', 'Hiragino Sans GB', sans-serif;
            font-size: {css_size}px;
            color: {primary_color};
            text-shadow: {shadow_css};
            font-weight: 600;
            text-align: center;
            line-height: 1.3;
            display: inline-block;
            max-width: 90%;
        ">{preview_text}</span>
    """

    landscape = f"""
        <div style="
            background: linear-gradient(135deg, #4a5568 0%, #2d3748 50%, #1a202c 100%);
            background-image:
                linear-gradient(135deg, #4a5568 0%, #2d3748 50%, #1a202c 100%),
                radial-gradient(circle at 30% 50%, rgba(255,255,255,0.05), transparent);
            aspect-ratio: 16/9;
            border-radius: 8px;
            display: flex;
            justify-content: center;
            align-items: {align};
            padding: {padding_v};
            position: relative;
            overflow: hidden;
            min-height: 180px;
        ">
            <div style="position:absolute; top:8px; left:12px; color:#888; font-size:11px; font-family:monospace;">
                📺 横屏 16:9
            </div>
            {common_span}
        </div>
    """

    portrait = f"""
        <div style="
            background: linear-gradient(135deg, #4a5568 0%, #2d3748 50%, #1a202c 100%);
            aspect-ratio: 9/16;
            border-radius: 8px;
            display: flex;
            justify-content: center;
            align-items: {align};
            padding: {padding_v};
            position: relative;
            overflow: hidden;
            max-width: 200px;
            margin: 0 auto;
        ">
            <div style="position:absolute; top:8px; left:12px; color:#888; font-size:11px; font-family:monospace;">
                📱 9:16
            </div>
            {common_span}
        </div>
    """

    return f"""
    <div style="display: grid; grid-template-columns: 2fr 1fr; gap: 12px; margin-top: 8px;">
        {landscape}
        {portrait}
    </div>
    <div style="text-align:center; color:#888; font-size:11px; margin-top:6px;">
        ↑ 实时预览（CSS 模拟，最终效果以烧字幕后的视频为准）
    </div>
    """


def run_burn(video_path_str, srt_text, font, font_size, primary_color,
             outline_color, outline_width, position,
             # TTS 参数
             tts_enabled, tts_voice, tts_keep_orig_vol,
             output_dir_str, progress=gr.Progress()):
    if not video_path_str:
        return "❌ 请先转写视频", None
    if not srt_text or not srt_text.strip():
        return "❌ SRT 字幕为空", None

    video_path = Path(video_path_str)
    out_dir = Path(output_dir_str).expanduser().resolve() if output_dir_str else DEFAULT_OUTPUT
    out_dir.mkdir(parents=True, exist_ok=True)

    # 工作视频源：如果启用 TTS，先生成替换音轨的中间版本
    working_video = video_path

    if tts_enabled:
        progress(0.05, desc="🎙️ 生成 TTS 配音...")
        tts_mp3 = out_dir / f"{video_path.stem}_tts.mp3"

        def _tts_progress(pct: float, msg: str):
            progress(0.05 + pct * 0.5, desc=f"🎙️ {msg}")

        ok, msg = synthesize_srt(
            srt_text=srt_text,
            voice=tts_voice,
            output_mp3=tts_mp3,
            on_progress=_tts_progress,
        )
        if not ok:
            return f"❌ TTS 生成失败:\n```\n{msg}\n```", None

        progress(0.6, desc="🔄 替换视频音轨...")
        replaced_video = out_dir / f"{video_path.stem}_tts_video.mp4"
        ok, msg = replace_audio(
            video_path=video_path,
            new_audio_path=tts_mp3,
            output_path=replaced_video,
            keep_original_volume=float(tts_keep_orig_vol),
        )
        if not ok:
            return f"❌ 替换音轨失败:\n```\n{msg}\n```", None

        working_video = replaced_video

    output_path = out_dir / f"{video_path.stem}_final{video_path.suffix or '.mp4'}"

    progress(0.75, desc="🔥 烧字幕到视频...")

    ok, msg = burn_subtitle(
        video_path=working_video,
        srt_text=srt_text,
        output_path=output_path,
        font=font,
        font_size=int(font_size),
        primary_color=primary_color,
        outline_color=outline_color,
        outline_width=int(outline_width),
        position=position,
    )

    progress(1.0)

    if ok:
        tts_note = "（✅ TTS 已替换原音轨）" if tts_enabled else ""
        return f"✅ 完成！{tts_note}\n输出：{output_path}", str(output_path)
    else:
        return f"❌ 烧字幕失败:\n```\n{msg}\n```", None


# ============== UI ==============

_CUSTOM_CSS = """
/* ===== 整体容器 ===== */
.gradio-container {
    max-width: 1280px !important;
    font-family: -apple-system, BlinkMacSystemFont, 'Inter', 'PingFang SC',
                 'Hiragino Sans GB', 'Microsoft YaHei', sans-serif !important;
}

/* ===== 强度预设 - 去掉刺眼紫蓝 ===== */
#dedup-preset {
    background: var(--block-background-fill) !important;
    border: 1px solid var(--border-color-primary) !important;
    border-radius: 12px !important;
    padding: 16px 20px !important;
    margin-bottom: 16px !important;
}

/* ===== 段落标题 - 大写小字 + 中性灰 ===== */
.section-title {
    font-size: 12px !important;
    font-weight: 600 !important;
    color: #71717a !important;
    text-transform: uppercase !important;
    letter-spacing: 0.08em !important;
    margin: 20px 0 10px !important;
    padding: 0 !important;
    border: none !important;
}
.dark .section-title { color: #a1a1aa !important; }

/* ===== Accordion 卡片 ===== */
.gradio-container details {
    border-radius: 10px !important;
    margin-bottom: 8px !important;
}

/* ===== 主按钮 - 干净蓝色 ===== */
button.primary {
    background: #2563eb !important;
    border: none !important;
    color: white !important;
    font-weight: 600 !important;
    letter-spacing: 0.02em !important;
    transition: all 0.15s !important;
}
button.primary:hover {
    background: #1d4ed8 !important;
    transform: translateY(-1px) !important;
    box-shadow: 0 4px 12px rgba(37, 99, 235, 0.25) !important;
}

/* ===== 隐藏 footer ===== */
footer { display: none !important; }
"""

# Monochrome 主题 - 黑白灰为主，按钮处用蓝色点缀
_theme = gr.themes.Monochrome(
    primary_hue="blue",
    secondary_hue="gray",
    neutral_hue="zinc",
    font=[
        gr.themes.GoogleFont("Inter"),
        "ui-sans-serif", "system-ui", "PingFang SC", "sans-serif",
    ],
).set(
    body_background_fill="#fafafa",
    body_background_fill_dark="#0a0a0a",
    block_background_fill="white",
    block_background_fill_dark="#18181b",
    block_border_color="#e4e4e7",
    block_border_color_dark="#27272a",
    block_radius="10px",
    block_shadow="0 1px 2px rgba(0,0,0,0.04)",
    button_primary_background_fill="#2563eb",
    button_primary_background_fill_hover="#1d4ed8",
    button_primary_text_color="white",
    button_secondary_background_fill="white",
    button_secondary_background_fill_dark="#27272a",
    color_accent="#2563eb",
)

with gr.Blocks(title="视频工具箱", theme=_theme, css=_CUSTOM_CSS) as demo:
    with gr.Row():
        gr.Markdown("# 🎬 视频工具箱")
    gr.Markdown(check_ffmpeg())

    with gr.Tabs():
        # ---------- Tab 1: 视频去重 ----------
        with gr.Tab("🔧 批量去重"):
            with gr.Group(elem_id="dedup-preset"):
                gr.Markdown("**🎚️ 强度预设** — 一键调整所有参数")
                preset_strength = gr.Radio(
                    choices=["温和", "平衡", "激进"],
                    value="平衡",
                    show_label=False,
                    container=False,
                )

            with gr.Row():
                with gr.Column(scale=1):
                    gr.Markdown("### 📥 输入")
                    files_input = gr.File(
                        label="上传视频（支持多选）",
                        file_count="multiple",
                        file_types=["video"],
                    )
                    output_dir_input = gr.Textbox(
                        label="输出目录", value=str(DEFAULT_OUTPUT),
                    )
                    open_dedup_dir_btn = gr.Button(
                        "📂 打开输出目录", size="sm", variant="secondary",
                    )
                    open_dedup_dir_status = gr.Markdown()

                    gr.Markdown("### ⚙️ 编码参数")
                    crf_input = gr.Slider(
                        label="CRF 质量（越小质量越好，文件越大）",
                        minimum=18, maximum=30, step=1, value=23,
                    )
                    preset_input = gr.Dropdown(
                        label="编码 preset",
                        choices=["ultrafast", "fast", "medium", "slow"],
                        value="fast",
                    )

                with gr.Column(scale=1):
                    gr.Markdown("### 🔧 去重变换")

                    with gr.Group():
                        gr.Markdown("**画面变换**")
                        mirror_input = gr.Checkbox(label="水平镜像翻转（左右颠倒，效果最强但内容反向）", value=False)
                        orientation_input = gr.Dropdown(
                            label="横竖屏切换",
                            choices=ORIENTATION_CHOICES, value="keep",
                        )

                    with gr.Group():
                        gr.Markdown("**分辨率**")
                        res_enabled_input = gr.Checkbox(label="启用分辨率缩放", value=True)
                        res_scale_input = gr.Slider(
                            label="缩放比例（0.9 表示缩到 90%）",
                            minimum=0.5, maximum=1.5, step=0.01, value=0.95,
                        )

                    with gr.Group():
                        gr.Markdown("**抽帧**")
                        frame_enabled_input = gr.Checkbox(label="启用抽帧", value=True)
                        frame_interval_input = gr.Slider(
                            label="每 N 帧丢 1 帧（N 越小丢得越多）",
                            minimum=10, maximum=120, step=1, value=30,
                        )

                    with gr.Group():
                        gr.Markdown("**随机色块 + 噪点**")
                        wm_enabled_input = gr.Checkbox(
                            label="启用随机色块水印 + 轻微噪点", value=True,
                        )

                    with gr.Group():
                        gr.Markdown("**变速**")
                        speed_enabled_input = gr.Checkbox(label="启用变速", value=True)
                        speed_factor_input = gr.Slider(
                            label="变速倍率",
                            minimum=0.9, maximum=1.1, step=0.01, value=1.03,
                        )

                    with gr.Group():
                        gr.Markdown("**音调**")
                        pitch_enabled_input = gr.Checkbox(label="启用音调微调", value=True)
                        pitch_factor_input = gr.Slider(
                            label="音调倍率",
                            minimum=0.95, maximum=1.05, step=0.01, value=1.02,
                        )

            # ============ 业内激进去重变换 ============
            with gr.Accordion("🔥 强力变换  ·  裁切 / 边框 / LUT / 黑屏 / EQ / 元数据  ·  6 项", open=False):

                with gr.Row():
                    with gr.Column(scale=1):
                        with gr.Group():
                            gr.Markdown("**🔲 边缘裁切** <small>· 破坏 pHash 边缘特征</small>")
                            crop_edge_enabled_input = gr.Checkbox(label="启用边缘裁切", value=False)
                            crop_edge_percent_input = gr.Slider(
                                label="裁掉总尺寸百分比", minimum=0.02, maximum=0.20,
                                step=0.01, value=0.08,
                            )

                        with gr.Group():
                            gr.Markdown("**🖼️ 边框** <small>· 改变完整画面结构</small>")
                            border_enabled_input = gr.Checkbox(label="启用边框", value=False)
                            border_width_input = gr.Slider(
                                label="边框宽度（像素）",
                                minimum=5, maximum=80, step=1, value=20,
                            )

                        with gr.Group():
                            gr.Markdown("**🎨 LUT 调色** <small>· 破坏 CLIP 语义 embedding</small>")
                            lut_enabled_input = gr.Checkbox(label="启用 LUT 调色", value=False)
                            lut_preset_input = gr.Dropdown(
                                label="调色预设",
                                choices=["warmer", "cooler", "vintage", "warm_film"],
                                value="warmer",
                            )

                    with gr.Column(scale=1):
                        with gr.Group():
                            gr.Markdown("**⏯️ 首尾黑屏** <small>· 破坏关键帧序列</small>")
                            intro_black_enabled_input = gr.Checkbox(
                                label="启用首尾黑屏 padding", value=False,
                            )
                            intro_black_duration_input = gr.Slider(
                                label="片头黑屏时长（秒）",
                                minimum=0.0, maximum=3.0, step=0.1, value=0.5,
                            )
                            outro_black_duration_input = gr.Slider(
                                label="片尾黑屏时长（秒）",
                                minimum=0.0, maximum=3.0, step=0.1, value=0.5,
                            )

                        with gr.Group():
                            gr.Markdown("**🎵 音频 EQ 重映射** <small>· 破坏 ACR 音频指纹</small>")
                            audio_eq_enabled_input = gr.Checkbox(
                                label="启用 EQ 频谱重映射", value=False,
                            )
                            eq_low_input = gr.Slider(
                                label="低音增益 (200Hz, dB)",
                                minimum=-10, maximum=10, step=0.5, value=-3,
                            )
                            eq_mid_input = gr.Slider(
                                label="中音增益 (2kHz, dB)",
                                minimum=-10, maximum=10, step=0.5, value=2,
                            )
                            eq_high_input = gr.Slider(
                                label="高音增益 (8kHz, dB)",
                                minimum=-10, maximum=10, step=0.5, value=-1,
                            )

                        with gr.Group():
                            gr.Markdown("**🧹 元数据清理**")
                            metadata_clean_input = gr.Checkbox(
                                label="清除创建时间/设备信息/GPS（推荐开启）",
                                value=True,
                            )

            # ============ 算法升级 v1（实测有效，破解 videohash 感知哈希）============
            with gr.Accordion("🚀 算法升级 v1  ·  双重缩放 / 双码率 / 3D LUT / VFR / 容器  ·  实测距离 4 → 13", open=False):

                with gr.Row():
                    with gr.Column(scale=1):
                        with gr.Group():
                            gr.Markdown("**🔁 双重缩放**（破坏小波频率特征）")
                            double_scale_enabled_input = gr.Checkbox(
                                label="启用：先缩小再放大，破坏频率特征",
                                value=False,
                            )
                            double_scale_mid_input = gr.Slider(
                                label="中间缩小到（0.5 = 50%）",
                                minimum=0.3, maximum=0.8, step=0.05, value=0.5,
                            )
                            double_scale_final_input = gr.Slider(
                                label="再放大到（1.1 = 110%）",
                                minimum=0.9, maximum=1.5, step=0.05, value=1.10,
                            )

                        with gr.Group():
                            gr.Markdown("**🎞️ 双码率重压缩**（破坏 DCT 系数）")
                            double_compress_enabled_input = gr.Checkbox(
                                label="启用：极烂 → 高质，两次编码",
                                value=False,
                            )
                            dc_pre_crf_input = gr.Slider(
                                label="第 1 次 CRF（极烂，35 推荐）",
                                minimum=28, maximum=45, step=1, value=35,
                            )
                            dc_final_crf_input = gr.Slider(
                                label="第 2 次 CRF（高质，20 推荐）",
                                minimum=18, maximum=26, step=1, value=20,
                            )

                    with gr.Column(scale=1):
                        with gr.Group():
                            gr.Markdown("**🎬 3D LUT 电影调色**（破坏颜色直方图）")
                            lut3d_enabled_input = gr.Checkbox(
                                label="启用：加载真正的 .cube 调色档",
                                value=False,
                            )
                            lut3d_preset_input = gr.Dropdown(
                                label="调色风格",
                                choices=[
                                    ("好莱坞暖色 Teal & Orange", "cinematic_warm"),
                                    ("冷色电影感", "cinematic_cool"),
                                    ("复古胶片", "vintage_film"),
                                    ("反差冲印（高对比鲜艳）", "cross_process"),
                                ],
                                value="cinematic_warm",
                            )

                        with gr.Group():
                            gr.Markdown("**⏱️ VFR 变量速度**（破坏时间戳哈希）")
                            vfr_enabled_input = gr.Checkbox(
                                label="启用：速度按正弦波动，平均不变",
                                value=False,
                            )
                            vfr_intensity_input = gr.Slider(
                                label="波动强度（0.05 = ±5%）",
                                minimum=0.02, maximum=0.10, step=0.01, value=0.05,
                            )

                        with gr.Group():
                            gr.Markdown("**📦 容器层 hack**（NoBlur 风格）")
                            container_hack_enabled_input = gr.Checkbox(
                                label="启用：二次重封装 + 改 mp4 atom 结构",
                                value=False,
                            )

            # ============ 算法升级 v2（实测把 videohash 距离从 13 推到 18）============
            with gr.Accordion("🔬 算法升级 v2  ·  模糊锐化 / HSL / 帧率转换  ·  实测距离 13 → 18  ✅", open=False):

                with gr.Row():
                    with gr.Column(scale=1):
                        with gr.Group():
                            gr.Markdown("**🌀 模糊+锐化双重操作**（破坏小波频率特征）")
                            blur_sharpen_enabled_input = gr.Checkbox(
                                label="启用：gblur 软化 + unsharp 重建高频",
                                value=False,
                            )
                            blur_sigma_input = gr.Slider(
                                label="模糊强度 sigma",
                                minimum=0.2, maximum=2.0, step=0.1, value=0.5,
                            )

                        with gr.Group():
                            gr.Markdown("**🎨 HSL 大幅调整**（破坏颜色直方图）")
                            hsl_shift_enabled_input = gr.Checkbox(
                                label="启用：hue/saturation/value 全谱调整",
                                value=False,
                            )
                            hsl_hue_input = gr.Slider(
                                label="色相偏移（度）",
                                minimum=-30, maximum=30, step=1, value=10,
                            )
                            hsl_sat_input = gr.Slider(
                                label="饱和度倍率",
                                minimum=0.5, maximum=1.5, step=0.05, value=0.88,
                            )
                            hsl_val_input = gr.Slider(
                                label="亮度倍率",
                                minimum=0.7, maximum=1.3, step=0.05, value=1.05,
                            )

                    with gr.Column(scale=1):
                        with gr.Group():
                            gr.Markdown("**🎞️ 帧率双向转换**（破坏时间采样点）")
                            fps_convert_enabled_input = gr.Checkbox(
                                label="启用：30fps → 中间 → 最终 fps",
                                value=False,
                            )
                            fps_inter_input = gr.Slider(
                                label="中间帧率",
                                minimum=15, maximum=60, step=1, value=24,
                            )
                            fps_final_input = gr.Slider(
                                label="最终帧率",
                                minimum=15, maximum=60, step=1, value=30,
                            )

            run_btn = gr.Button("🚀 开始批量处理", variant="primary", size="lg")

            open_dedup_dir_btn.click(
                fn=open_in_file_manager,
                inputs=[output_dir_input],
                outputs=[open_dedup_dir_status],
            )
            summary_output = gr.Markdown()
            results_output = gr.Dataframe(
                headers=["输入文件", "状态", "输出文件", "错误信息"],
                label="处理结果", wrap=True,
            )

            run_btn.click(
                fn=run_dedup,
                inputs=[
                    files_input, output_dir_input,
                    mirror_input, orientation_input,
                    res_enabled_input, res_scale_input,
                    frame_enabled_input, frame_interval_input,
                    wm_enabled_input,
                    speed_enabled_input, speed_factor_input,
                    pitch_enabled_input, pitch_factor_input,
                    crop_edge_enabled_input, crop_edge_percent_input,
                    border_enabled_input, border_width_input,
                    lut_enabled_input, lut_preset_input,
                    intro_black_enabled_input,
                    intro_black_duration_input, outro_black_duration_input,
                    audio_eq_enabled_input,
                    eq_low_input, eq_mid_input, eq_high_input,
                    metadata_clean_input,
                    # 算法升级到位
                    double_scale_enabled_input, double_scale_mid_input, double_scale_final_input,
                    double_compress_enabled_input, dc_pre_crf_input, dc_final_crf_input,
                    lut3d_enabled_input, lut3d_preset_input,
                    vfr_enabled_input, vfr_intensity_input,
                    container_hack_enabled_input,
                    # v2 升级
                    blur_sharpen_enabled_input, blur_sigma_input,
                    hsl_shift_enabled_input, hsl_hue_input, hsl_sat_input, hsl_val_input,
                    fps_convert_enabled_input, fps_inter_input, fps_final_input,
                    crf_input, preset_input,
                ],
                outputs=[summary_output, results_output],
            )

            # ============ 强度预设：点击一键调整所有参数 ============
            def apply_preset(p):
                # 返回顺序：基础(11) + 激进(14) + 算法升级 v1(11) + 算法升级 v2(9)
                if p == "温和":
                    return (
                        False, "keep",
                        True, 0.95, True, 30, True,
                        True, 1.03, True, 1.02,
                        False, 0.05, False, 15,
                        False, "warmer",
                        False, 0.3, 0.3,
                        False, -2, 1, -1, True,
                        # 算法升级 v1 — 全关
                        False, 0.5, 1.10,
                        False, 35, 20,
                        False, "cinematic_warm",
                        False, 0.05,
                        False,
                        # 算法升级 v2 — 全关
                        False, 0.5,
                        False, 10, 0.88, 1.05,
                        False, 24, 30,
                    )
                elif p == "平衡":
                    return (
                        False, "keep",
                        True, 0.92, True, 20, True,
                        True, 1.04, True, 1.03,
                        True, 0.06, False, 20,
                        True, "warmer",
                        True, 0.3, 0.3,
                        True, -3, 2, -1, True,
                        # 算法升级 v1 — 部分开启
                        False, 0.5, 1.10,
                        False, 35, 20,
                        True, "cinematic_warm",
                        False, 0.05,
                        True,
                        # 算法升级 v2 — HSL 轻量
                        False, 0.5,
                        True, 8, 0.92, 1.03,
                        False, 24, 30,
                    )
                elif p == "激进":
                    # 实测哈希距离 18 的 8 算法全开配方
                    return (
                        False, "keep",      # 默认不开镜像（避免左右反向）
                        True, 0.95, True, 30, True,
                        True, 1.03, True, 1.02,
                        True, 0.08, False, 20,
                        False, "warmer",
                        False, 0.3, 0.3,
                        False, -3, 2, -1, True,
                        # 算法升级 v1 — 全开
                        True, 0.5, 1.10,
                        True, 35, 20,
                        True, "cinematic_warm",
                        True, 0.05,
                        True,
                        # 算法升级 v2 — 全开（关键 3 招把距离推到 18）
                        True, 0.5,
                        True, 10, 0.88, 1.05,
                        True, 24, 30,
                    )
                return (gr.update(),) * 44

            preset_strength.change(
                fn=apply_preset,
                inputs=[preset_strength],
                outputs=[
                    mirror_input, orientation_input,
                    res_enabled_input, res_scale_input,
                    frame_enabled_input, frame_interval_input,
                    wm_enabled_input,
                    speed_enabled_input, speed_factor_input,
                    pitch_enabled_input, pitch_factor_input,
                    crop_edge_enabled_input, crop_edge_percent_input,
                    border_enabled_input, border_width_input,
                    lut_enabled_input, lut_preset_input,
                    intro_black_enabled_input,
                    intro_black_duration_input, outro_black_duration_input,
                    audio_eq_enabled_input,
                    eq_low_input, eq_mid_input, eq_high_input,
                    metadata_clean_input,
                    # 算法升级 v1
                    double_scale_enabled_input, double_scale_mid_input, double_scale_final_input,
                    double_compress_enabled_input, dc_pre_crf_input, dc_final_crf_input,
                    lut3d_enabled_input, lut3d_preset_input,
                    vfr_enabled_input, vfr_intensity_input,
                    container_hack_enabled_input,
                    # 算法升级 v2
                    blur_sharpen_enabled_input, blur_sigma_input,
                    hsl_shift_enabled_input, hsl_hue_input, hsl_sat_input, hsl_val_input,
                    fps_convert_enabled_input, fps_inter_input, fps_final_input,
                ],
            )

        # ---------- Tab 2: 字幕生成 ----------
        with gr.Tab("📝 字幕生成"):
            gr.Markdown(
                "**两步走**：1️⃣ 上传视频 → 自动转写出 SRT 字幕（可手动修改）  "
                "2️⃣ 选择字幕样式 → 一键烧到视频上"
            )

            video_path_state = gr.State()

            # === 模型下载状态条（顶部）===
            with gr.Group():
                model_status = gr.Markdown(get_model_status())
                with gr.Row():
                    download_model_btn = gr.Button(
                        "📥 下载 SenseVoice 模型（首次使用，~900MB）",
                        variant="primary",
                        visible=not is_sensevoice_ready(),
                    )
                download_status = gr.Markdown()

            with gr.Row():
                with gr.Column(scale=1):
                    gr.Markdown("### 1️⃣ 转写设置")
                    sub_video_input = gr.File(
                        label="上传视频（单个）",
                        file_count="single",
                        file_types=["video"],
                    )
                    sub_language = gr.Dropdown(
                        label="语言",
                        choices=LANGUAGE_CHOICES, value="zh",
                        info="SenseVoice 支持中/英/日/韩/粤，明确选语言识别更准",
                    )
                    transcribe_btn = gr.Button("🎤 开始转写", variant="primary")
                    transcribe_status = gr.Markdown()

                with gr.Column(scale=2):
                    gr.Markdown("### 📝 字幕内容（可手动修改）")
                    srt_editor = gr.Textbox(
                        label="SRT 字幕",
                        lines=18, max_lines=30,
                        placeholder="转写完成后字幕会显示在这里，标准 SRT 格式，可以手动修改",
                    )

            gr.Markdown("---")

            with gr.Row():
                with gr.Column(scale=1):
                    gr.Markdown("### 2️⃣ 字幕样式")
                    font_input = gr.Dropdown(
                        label="字体（中文推荐 PingFang SC）",
                        choices=FONT_CHOICES, value="PingFang SC",
                    )
                    font_size_input = gr.Slider(
                        label="字号（竖屏建议 16-20，横屏建议 22-28）",
                        minimum=12, maximum=48, step=1, value=18,
                    )
                    position_input = gr.Radio(
                        label="位置",
                        choices=["底部", "中间", "顶部"], value="底部",
                    )

                with gr.Column(scale=1):
                    gr.Markdown("### 🎨 颜色")
                    primary_color_input = gr.ColorPicker(
                        label="字体颜色", value="#FFFFFF",
                    )
                    outline_color_input = gr.ColorPicker(
                        label="描边颜色", value="#000000",
                    )
                    outline_width_input = gr.Slider(
                        label="描边宽度", minimum=0, maximum=5, step=1, value=2,
                    )

                with gr.Column(scale=1):
                    gr.Markdown("### 🎙️ TTS 配音（破坏 ACR 音频指纹）")
                    tts_enabled_input = gr.Checkbox(
                        label="启用：用 TTS 替换原音轨（推荐！这是过审最强的招）",
                        value=False,
                    )
                    tts_voice_input = gr.Dropdown(
                        label="音色",
                        choices=VOICE_CHOICES,
                        value="zh-CN-XiaoxiaoNeural",
                    )
                    tts_keep_orig_input = gr.Slider(
                        label="保留原音音量（0 = 完全替换，0.2 = 留一点背景）",
                        minimum=0.0, maximum=0.5, step=0.05, value=0.0,
                    )

                    gr.Markdown("### 📤 输出")
                    sub_output_dir = gr.Textbox(
                        label="输出目录", value=str(DEFAULT_OUTPUT),
                    )
                    open_sub_dir_btn = gr.Button(
                        "📂 打开输出目录", size="sm", variant="secondary",
                    )
                    open_sub_dir_status = gr.Markdown()
                    burn_btn = gr.Button(
                        "🔥 一键生成（TTS + 烧字幕）", variant="primary",
                    )
                    burn_status = gr.Markdown()
                    burn_video_output = gr.Video(label="输出视频预览", height=300)

            # 实时样式预览
            gr.Markdown("### 👁️ 样式预览（实时）")
            style_preview = gr.HTML(
                value=build_style_preview(
                    "PingFang SC", 18, "#FFFFFF", "#000000", 2, "底部",
                ),
            )

            # 任意一个样式控件变化都重渲染预览
            _style_inputs = [
                font_input, font_size_input, primary_color_input,
                outline_color_input, outline_width_input, position_input,
            ]
            for ctrl in _style_inputs:
                ctrl.change(
                    fn=build_style_preview,
                    inputs=_style_inputs,
                    outputs=style_preview,
                )

            transcribe_btn.click(
                fn=run_transcribe,
                inputs=[sub_video_input, sub_language],
                outputs=[transcribe_status, srt_editor, video_path_state],
            )

            # 下载模型按钮 - 下载完成后刷新状态、隐藏自己
            def _after_download(status_text):
                # 重新读模型状态
                return (
                    gr.update(value=get_model_status()),
                    gr.update(visible=not is_sensevoice_ready()),
                    status_text,
                )

            download_model_btn.click(
                fn=run_download_model,
                inputs=[],
                outputs=[download_status],
            ).then(
                fn=_after_download,
                inputs=[download_status],
                outputs=[model_status, download_model_btn, download_status],
            )

            burn_btn.click(
                fn=run_burn,
                inputs=[
                    video_path_state, srt_editor,
                    font_input, font_size_input,
                    primary_color_input, outline_color_input,
                    outline_width_input, position_input,
                    tts_enabled_input, tts_voice_input, tts_keep_orig_input,
                    sub_output_dir,
                ],
                outputs=[burn_status, burn_video_output],
            )

            open_sub_dir_btn.click(
                fn=open_in_file_manager,
                inputs=[sub_output_dir],
                outputs=[open_sub_dir_status],
            )


if __name__ == "__main__":
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    DEFAULT_OUTPUT.mkdir(parents=True, exist_ok=True)
    demo.queue().launch(
        server_name="127.0.0.1",
        server_port=7860,
        inbrowser=False,
        show_error=True,
    )
