"""3D LUT 生成器 — 自动生成电影级 .cube 调色文件。

FFmpeg lut3d 滤镜直接吃 .cube 格式。这里用代码生成几个常用风格，
不用网上下载也能用上"业界电影调色"级别的颜色重映射。

调色风格参考好莱坞/Netflix 调色档：
- cinematic_warm  暖色电影感（橙色高光 + 蓝色阴影）
- cinematic_cool  冷色电影感（青色 + 蓝色调）
- vintage_film    复古胶片（褪色 + 黄绿偏色）
- cross_process   反差冲印（高饱和度 + 强对比）
"""

import math
from pathlib import Path


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


# ====== 调色映射函数 ======
# 每个函数接收 (r, g, b) ∈ [0,1]³，返回 (r', g', b') ∈ [0,1]³

def map_cinematic_warm(r: float, g: float, b: float):
    """好莱坞暖色调：橙色高光 + 青蓝阴影（电影调色经典手法 Teal & Orange）"""
    # 计算亮度
    luma = 0.299 * r + 0.587 * g + 0.114 * b
    # 阴影偏冷（B+），高光偏暖（R+）
    shadow_strength = (1.0 - luma) ** 2
    highlight_strength = luma ** 2
    r_out = r + highlight_strength * 0.10 - shadow_strength * 0.05
    g_out = g + highlight_strength * 0.02 - shadow_strength * 0.02
    b_out = b - highlight_strength * 0.05 + shadow_strength * 0.10
    return _clamp01(r_out), _clamp01(g_out), _clamp01(b_out)


def map_cinematic_cool(r: float, g: float, b: float):
    """冷色电影感（蓝青主调）"""
    luma = 0.299 * r + 0.587 * g + 0.114 * b
    r_out = r * 0.92 - 0.02
    g_out = g * 0.98 + 0.02
    b_out = b * 1.05 + 0.05
    # 阴影压暗
    if luma < 0.3:
        r_out *= 0.85
        g_out *= 0.90
    return _clamp01(r_out), _clamp01(g_out), _clamp01(b_out)


def map_vintage_film(r: float, g: float, b: float):
    """复古胶片：褪色 + 黄绿偏色 + 低对比"""
    # 压缩到中间灰阶（低对比）
    r2 = 0.15 + r * 0.7
    g2 = 0.18 + g * 0.7
    b2 = 0.10 + b * 0.65
    # 整体偏黄绿
    r_out = r2 + 0.05
    g_out = g2 + 0.05
    b_out = b2 - 0.05
    return _clamp01(r_out), _clamp01(g_out), _clamp01(b_out)


def map_cross_process(r: float, g: float, b: float):
    """反差冲印：高对比 + 鲜艳色彩（90 年代 MV 感）"""
    # S 曲线增加对比度
    def s_curve(x):
        return 0.5 * (1.0 + math.tanh(3.0 * (x - 0.5)))
    r_out = s_curve(r) * 1.10
    g_out = s_curve(g) * 1.00 - 0.05  # 压绿，让肤色偏品红
    b_out = s_curve(b) * 1.05
    return _clamp01(r_out), _clamp01(g_out), _clamp01(b_out)


PRESETS = {
    "cinematic_warm": (map_cinematic_warm, "好莱坞暖色（Teal & Orange）"),
    "cinematic_cool": (map_cinematic_cool, "冷色电影感"),
    "vintage_film": (map_vintage_film, "复古胶片"),
    "cross_process": (map_cross_process, "反差冲印（高对比鲜艳）"),
}


def generate_cube(preset_name: str, output_path: Path, size: int = 17) -> None:
    """生成 .cube 3D LUT 文件
    size=17 是工业标准（17³=4913 个采样点，跟 Resolve/Premiere 一致）
    """
    if preset_name not in PRESETS:
        raise ValueError(f"未知预设: {preset_name}，可选: {list(PRESETS.keys())}")
    func, desc = PRESETS[preset_name]

    lines = [
        f"# {preset_name} - {desc}",
        f"# Generated for video-dedup, size={size}",
        f"TITLE \"{preset_name}\"",
        f"LUT_3D_SIZE {size}",
        "DOMAIN_MIN 0.0 0.0 0.0",
        "DOMAIN_MAX 1.0 1.0 1.0",
        "",
    ]

    # .cube 格式: R 最快变化，G 中间，B 最慢
    for bi in range(size):
        for gi in range(size):
            for ri in range(size):
                r = ri / (size - 1)
                g = gi / (size - 1)
                b = bi / (size - 1)
                rr, gg, bb = func(r, g, b)
                lines.append(f"{rr:.6f} {gg:.6f} {bb:.6f}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def generate_all_presets(luts_dir: Path) -> dict[str, Path]:
    """生成所有预设到指定目录，返回 {preset_name: cube_path}"""
    luts_dir.mkdir(parents=True, exist_ok=True)
    paths = {}
    for name in PRESETS:
        out = luts_dir / f"{name}.cube"
        if not out.exists():
            generate_cube(name, out)
        paths[name] = out
    return paths


if __name__ == "__main__":
    # 命令行直接跑：python -m core.lut_generator
    luts_dir = Path(__file__).parent.parent / "assets" / "luts"
    paths = generate_all_presets(luts_dir)
    print(f"已生成 {len(paths)} 个 LUT 文件到 {luts_dir}:")
    for name, path in paths.items():
        size_kb = path.stat().st_size / 1024
        print(f"  {name}.cube  ({size_kb:.1f} KB)")
