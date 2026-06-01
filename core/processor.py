import random
import string
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Optional

from .config import TransformConfig
from .paths import FFMPEG, FFPROBE
from .lut_generator import generate_all_presets


# 项目根目录下的 LUT 文件夹
_LUTS_DIR = Path(__file__).parent.parent / "assets" / "luts"


def _ensure_luts():
    """懒加载 cube 文件（首次使用时生成）"""
    if not _LUTS_DIR.exists() or not any(_LUTS_DIR.glob("*.cube")):
        generate_all_presets(_LUTS_DIR)
    return _LUTS_DIR


def _rand_text(n: int = 4) -> str:
    return "".join(random.choices(string.ascii_letters + string.digits, k=n))


# LUT 调色预设（用 colorbalance 滤镜，参数范围 -1.0 ~ 1.0）
# 顺序：shadows(rs/gs/bs), midtones(rm/gm/bm), highlights(rh/gh/bh)
LUT_PRESETS = {
    "warmer": "colorbalance=rs=0.05:gs=0.0:bs=-0.05:rm=0.08:gm=0.02:bm=-0.08:rh=0.05:gh=0.0:bh=-0.03",
    "cooler": "colorbalance=rs=-0.05:gs=0.0:bs=0.05:rm=-0.08:gm=-0.02:bm=0.08:rh=-0.03:gh=0.0:bh=0.05",
    "vintage": "curves=preset=vintage",
    "warm_film": "eq=saturation=1.1:contrast=1.05,colorbalance=rm=0.1:bm=-0.08",
}


def build_video_filter(cfg: TransformConfig) -> str:
    filters: list[str] = []

    if cfg.mirror_enabled:
        filters.append("hflip")

    # ── 横竖屏切换 ──
    if cfg.orientation == "h2v":
        filters.append(
            "scale=720:1280:force_original_aspect_ratio=increase,"
            "crop=720:1280,setsar=1"
        )
    elif cfg.orientation == "v2h":
        filters.append(
            "split=2[bg][fg];"
            "[bg]scale=1280:720:force_original_aspect_ratio=increase,"
            "crop=1280:720,boxblur=20:5[bgblur];"
            "[fg]scale=-2:720[fgs];"
            "[bgblur][fgs]overlay=(W-w)/2:0,setsar=1"
        )

    # ── 【新】边缘裁切（破坏 pHash 边缘特征）──
    if cfg.crop_edge_enabled:
        p = max(0.01, min(0.25, cfg.crop_edge_percent))
        keep = 1.0 - p
        # 居中裁切，保留中间 (1-p) 比例
        filters.append(
            f"crop='trunc(iw*{keep}/2)*2:trunc(ih*{keep}/2)*2:trunc(iw*{p/2}):trunc(ih*{p/2})'"
        )

    # ── 【新】LUT 调色（破坏 CLIP embedding）──
    if cfg.lut_enabled:
        preset = LUT_PRESETS.get(cfg.lut_preset, LUT_PRESETS["warmer"])
        filters.append(preset)

    # ── 【算法升级 #1】3D LUT 电影级调色（破坏颜色直方图）──
    if cfg.lut3d_enabled:
        _ensure_luts()
        cube_path = _LUTS_DIR / f"{cfg.lut3d_preset}.cube"
        if cube_path.exists():
            # cube 文件路径在 filter 表达式中要转义特殊字符
            escaped = str(cube_path).replace("\\", "/").replace(":", "\\:")
            filters.append(f"lut3d=file='{escaped}'")

    # ── 分辨率缩放 ──
    if cfg.resolution_enabled:
        s = cfg.resolution_scale
        filters.append(f"scale='trunc(iw*{s}/2)*2:trunc(ih*{s}/2)*2'")

    # ── 【算法升级 #2】双重缩放（破坏小波频率特征）──
    # 经历两次 bicubic 插值，videohash 等感知哈希算法会失效
    if cfg.double_scale_enabled:
        mid = cfg.double_scale_mid
        fin = cfg.double_scale_final
        filters.append(
            f"scale='trunc(iw*{mid}/2)*2:trunc(ih*{mid}/2)*2':flags=bicubic"
        )
        filters.append(
            f"scale='trunc(iw*{fin}/2)*2:trunc(ih*{fin}/2)*2':flags=bicubic"
        )

    # ── 【新】加边框（在缩放后画面外）──
    if cfg.border_enabled:
        bw = max(1, int(cfg.border_width))
        color = cfg.border_color or "black"
        filters.append(
            f"pad=iw+{bw*2}:ih+{bw*2}:{bw}:{bw}:color={color}"
        )

    # ── 抽帧 ──
    if cfg.frame_drop_enabled:
        n = max(2, cfg.frame_drop_interval)
        filters.append(f"select='gt(mod(n\\,{n})\\,0)',setpts=N/FRAME_RATE/TB")

    # ── 水印色块 + 噪点 ──
    if cfg.watermark_enabled:
        x = random.randint(5, 30)
        y = random.randint(5, 30)
        w = random.randint(8, 16)
        h = random.randint(8, 16)
        colors = ["white", "black", "gray", "red", "blue"]
        color = random.choice(colors)
        alpha = random.uniform(0.03, 0.06)
        filters.append(
            f"drawbox=x={x}:y={y}:w={w}:h={h}:color={color}@{alpha:.3f}:t=fill"
        )
        filters.append("noise=alls=2:allf=t")

    # ── 变速 ──
    if cfg.speed_enabled and abs(cfg.speed_factor - 1.0) > 1e-3:
        pts = 1.0 / cfg.speed_factor
        filters.append(f"setpts={pts:.6f}*PTS")

    # ── 【算法升级 #3】VFR 变量速度（破坏时间戳哈希）──
    # 用 setpts 的正弦波动让每一帧的时间戳都不规则，
    # 平台抽帧采样到的时间点完全对不上原视频
    if cfg.vfr_enabled:
        intensity = max(0.01, min(0.15, cfg.vfr_intensity))
        period = max(10.0, cfg.vfr_period_frames)
        # 因子 = 1 + intensity * sin(2π * N / period)
        # 平均速度 ≈ 1.0，瞬时速度在 [1-intensity, 1+intensity] 之间
        filters.append(
            f"setpts='PTS / (1.0 + {intensity:.4f} * sin(2*PI*N/{period:.2f}))'"
        )

    # ── 【算法升级 v2 #1】模糊+锐化双重操作（破坏小波频率特征）──
    # 高斯模糊把高频信息抹掉，unsharp 重建出新的高频特征
    # videohash 的小波变换看到的频率分布完全不同
    if cfg.blur_sharpen_enabled:
        sigma = max(0.2, min(2.0, cfg.blur_sigma))
        filters.append(f"gblur=sigma={sigma:.2f}")
        # unsharp 参数：奇数 luma matrix size，强度，奇数 chroma matrix size，chroma 强度
        filters.append("unsharp=5:5:1.0:5:5:0.5")

    # ── 【算法升级 v2 #2】HSL 大幅调整（破坏颜色直方图）──
    # videohash 64bit 哈希有 32bit 来自颜色直方图，必须从根上动颜色分布
    if cfg.hsl_shift_enabled:
        hue = cfg.hsl_hue_shift
        sat = cfg.hsl_saturation
        val = cfg.hsl_value
        # hue 滤镜：h 是色相偏移角度，s 是饱和度倍率，v 是亮度倍率
        filters.append(f"hue=h={hue:.2f}:s={sat:.3f}:b={(val-1)*0.5:.3f}")

    # ── 【算法升级 v2 #3】强制帧率双向转换（破坏时间采样点）──
    # 30fps → 24fps（丢帧）→ 30fps（插帧），所有时间戳重新计算
    if cfg.fps_convert_enabled:
        inter = max(15, min(60, cfg.fps_intermediate))
        final = max(15, min(60, cfg.fps_final))
        filters.append(f"fps=fps={inter}")
        filters.append(f"fps=fps={final}")

    # ── 【新】首尾黑屏（破坏关键帧序列）──
    # tpad 必须在变速之后，否则黑屏时长会被错误缩放
    if cfg.intro_black_enabled:
        intro = max(0.0, cfg.intro_black_duration)
        outro = max(0.0, cfg.outro_black_duration)
        if intro > 0 or outro > 0:
            parts = []
            if intro > 0:
                parts.append(f"start_duration={intro:.3f}")
                parts.append("start_mode=add")
            if outro > 0:
                parts.append(f"stop_duration={outro:.3f}")
                parts.append("stop_mode=add")
            parts.append("color=black")
            filters.append("tpad=" + ":".join(parts))

    return ",".join(filters) if filters else "null"


def build_audio_filter(cfg: TransformConfig) -> str:
    filters: list[str] = []

    # ── 变速（音频要同步）──
    if cfg.speed_enabled and abs(cfg.speed_factor - 1.0) > 1e-3:
        filters.append(f"atempo={cfg.speed_factor:.6f}")

    # ── 音调微调 ──
    if cfg.audio_pitch_enabled and abs(cfg.audio_pitch_factor - 1.0) > 1e-3:
        rate = int(44100 * cfg.audio_pitch_factor)
        comp = 1.0 / cfg.audio_pitch_factor
        filters.append(f"asetrate={rate},aresample=44100,atempo={comp:.6f}")

    # ── 【新】EQ 频谱重映射（破坏 ACR 音频指纹）──
    # 低音 200Hz、中音 2kHz、高音 8kHz 各做增益调整
    if cfg.audio_eq_enabled:
        filters.append(f"equalizer=f=200:t=q:w=1:g={cfg.eq_low_gain:.1f}")
        filters.append(f"equalizer=f=2000:t=q:w=1:g={cfg.eq_mid_gain:.1f}")
        filters.append(f"equalizer=f=8000:t=q:w=1:g={cfg.eq_high_gain:.1f}")

    # ── 【新】首尾静音（配合视频黑屏）──
    if cfg.intro_black_enabled:
        intro = max(0.0, cfg.intro_black_duration)
        outro = max(0.0, cfg.outro_black_duration)
        if intro > 0:
            filters.append(f"adelay={int(intro*1000)}|{int(intro*1000)}")
        if outro > 0:
            filters.append(f"apad=pad_dur={outro:.3f}")

    return ",".join(filters) if filters else "anull"


def _run_ffmpeg(cmd: list[str], timeout: int = 1800) -> tuple[bool, str]:
    """统一 ffmpeg 执行入口"""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            return False, (result.stderr or "")[-1500:]
        return True, "OK"
    except subprocess.TimeoutExpired:
        return False, f"ffmpeg 超时（>{timeout/60:.0f} 分钟）"
    except FileNotFoundError:
        return False, "找不到 ffmpeg，请先 brew install ffmpeg"
    except Exception as e:
        return False, f"未知错误: {e}"


def _pre_compress(input_path: Path, output_path: Path, pre_crf: int) -> tuple[bool, str]:
    """【算法升级 #4】双码率重压第一步：极烂压缩破坏 DCT 系数"""
    cmd = [
        FFMPEG, "-y", "-i", str(input_path),
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", str(pre_crf),
        "-c:a", "aac", "-b:a", "96k",
        str(output_path),
    ]
    return _run_ffmpeg(cmd, timeout=600)


def _container_hack(input_path: Path, output_path: Path) -> tuple[bool, str]:
    """【算法升级 #5】容器层 hack（NoBlur 风格）：
    二次重封装 + 强制改变 mp4 atom 顺序，干扰容器层指纹检测。
    用 ffmpeg -movflags + 改变 brand 等技巧实现（不改音视频流，纯容器层）。
    """
    cmd = [
        FFMPEG, "-y", "-i", str(input_path),
        "-c", "copy",                       # 不重编码，纯重封装
        "-movflags", "+faststart+frag_keyframe+empty_moov",
        "-brand", "mp42",                   # 改变 ftyp brand
        "-write_tmcd", "0",                 # 不写时间码 track
        "-fflags", "+bitexact",
        "-map_metadata", "-1",
        "-map_chapters", "-1",
        str(output_path),
    ]
    return _run_ffmpeg(cmd, timeout=300)


def process_one(
    input_path: Path,
    output_path: Path,
    cfg: TransformConfig,
) -> tuple[bool, str]:
    vf = build_video_filter(cfg)
    af = build_audio_filter(cfg)

    # 输入源：如果启用双码率重压，先做一次"极烂压缩"
    source_path = input_path
    temp_files: list[Path] = []

    try:
        if cfg.double_compress_enabled:
            pre_path = output_path.parent / f".precompress_{output_path.stem}.mp4"
            ok, msg = _pre_compress(input_path, pre_path, cfg.double_compress_pre_crf)
            if not ok:
                return False, f"双码率第 1 步失败:\n{msg}"
            source_path = pre_path
            temp_files.append(pre_path)

        # 主处理 — 应用所有滤镜 + 最终编码
        final_crf = cfg.double_compress_final_crf if cfg.double_compress_enabled else cfg.crf

        # 如果还要做容器 hack，先输出到临时文件，最后再重封装
        main_output = output_path
        if cfg.container_hack_enabled:
            main_output = output_path.parent / f".prehack_{output_path.stem}.mp4"
            temp_files.append(main_output)

        cmd = [
            FFMPEG, "-y",
            "-i", str(source_path),
            "-vf", vf,
            "-af", af,
            "-c:v", "libx264",
            "-preset", cfg.preset,
            "-crf", str(final_crf),
            "-c:a", "aac",
            "-b:a", "128k",
            "-movflags", "+faststart",
        ]

        if cfg.metadata_clean:
            cmd.extend([
                "-map_metadata", "-1",
                "-map_chapters", "-1",
                "-metadata", "encoder=",
                "-metadata", "comment=",
                "-fflags", "+bitexact",
            ])

        cmd.append(str(main_output))

        ok, msg = _run_ffmpeg(cmd)
        if not ok:
            return False, f"主处理失败:\n{msg}"

        # 最后一步：容器 hack（如果启用）
        if cfg.container_hack_enabled:
            ok, msg = _container_hack(main_output, output_path)
            if not ok:
                return False, f"容器 hack 失败:\n{msg}"

        return True, "OK"

    finally:
        # 清理临时文件
        for tmp in temp_files:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass


def process_batch(
    input_paths: list[Path],
    output_dir: Path,
    cfg: TransformConfig,
    progress: Optional[Callable[[float, str], None]] = None,
) -> list[dict]:
    output_dir.mkdir(parents=True, exist_ok=True)
    results: list[dict] = []
    total = len(input_paths)

    for idx, src in enumerate(input_paths):
        if progress:
            progress(idx / total, f"处理中 ({idx + 1}/{total}): {src.name}")

        # 每个视频用新的随机种子（保证不同视频水印位置不一样）
        random.seed()

        dst = output_dir / f"{src.stem}_dedup{src.suffix or '.mp4'}"
        ok, msg = process_one(src, dst, cfg)

        results.append({
            "input": str(src),
            "output": str(dst) if ok else "",
            "ok": ok,
            "msg": msg,
        })

    if progress:
        progress(1.0, f"完成 {total} 个视频")

    return results
