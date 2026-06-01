from dataclasses import dataclass


@dataclass
class TransformConfig:
    # ============ 基础变换 ============
    mirror_enabled: bool = False
    orientation: str = "keep"

    resolution_enabled: bool = True
    resolution_scale: float = 0.95

    frame_drop_enabled: bool = True
    frame_drop_interval: int = 30

    watermark_enabled: bool = True
    watermark_text: str = ""

    speed_enabled: bool = True
    speed_factor: float = 1.03

    audio_pitch_enabled: bool = True
    audio_pitch_factor: float = 1.02

    # ============ 业内激进去重变换（破坏平台指纹）============
    # 1. 边缘裁切 - 直接裁掉外圈像素，破坏 pHash 边缘特征
    crop_edge_enabled: bool = False
    crop_edge_percent: float = 0.08  # 裁掉总尺寸的 8%

    # 2. 加边框 - 在裁切后画面外加纯色边框
    border_enabled: bool = False
    border_width: int = 20
    border_color: str = "black"

    # 3. LUT 调色 - 改变色调，破坏 CLIP embedding
    lut_enabled: bool = False
    lut_preset: str = "warmer"  # warmer / cooler / vintage

    # 4. 首尾黑屏 - 添加片头/片尾 padding，改变关键帧序列
    intro_black_enabled: bool = False
    intro_black_duration: float = 0.5  # 秒
    outro_black_duration: float = 0.5

    # 5. 音频 EQ 频谱重映射 - 改变频段能量分布，破坏 ACR 指纹
    audio_eq_enabled: bool = False
    eq_low_gain: float = -3.0   # 200Hz 增益
    eq_mid_gain: float = 2.0    # 2kHz 增益
    eq_high_gain: float = -1.0  # 8kHz 增益

    # 6. 元数据清理 - 清除创建时间、设备信息等
    metadata_clean: bool = True

    # ============ 算法升级到位（破坏 videohash 类感知哈希算法）============
    # 7. 双重缩放 - 先缩小到 mid 比例，再放大到 final 比例
    # 经历两次 bicubic 插值，小波频率特征被破坏
    double_scale_enabled: bool = False
    double_scale_mid: float = 0.5      # 中间缩到 50%
    double_scale_final: float = 1.10   # 再放大到 110%

    # 8. 双码率重压缩 - 先 CRF=high 极烂压一次，再 CRF=normal 高质量压一次
    # 第一次有损压缩破坏 DCT 系数，第二次重新编码生成完全不同的像素分布
    double_compress_enabled: bool = False
    double_compress_pre_crf: int = 35    # 第一次（极烂）
    double_compress_final_crf: int = 20  # 第二次（高质）

    # 9. 3D LUT 电影级调色 - 加载 cube 文件做颜色直方图重映射
    lut3d_enabled: bool = False
    lut3d_preset: str = "cinematic_warm"  # cinematic_warm/cool/vintage_film/cross_process

    # 10. VFR 变量速度 - setpts 用周期函数破坏时间戳哈希
    # 速度按 sin(N/period) 波动，平均速度 ≈ 1.0
    vfr_enabled: bool = False
    vfr_intensity: float = 0.05   # 速度波动幅度（±5%）
    vfr_period_frames: float = 50  # 波动周期（帧）

    # 11. 容器层 hack（NoBlur 风格）- 二次重封装 + 改 movflags
    # 改 mp4 容器结构（不改音视频流），干扰平台容器层指纹
    container_hack_enabled: bool = False

    # ============ 算法升级 v2（针对 videohash 残留特征）============
    # 12. 模糊+锐化双重操作 - 破坏小波频率特征
    # gblur 软化高频，unsharp 重建（但频率特征已经被重组）
    blur_sharpen_enabled: bool = False
    blur_sigma: float = 0.5   # 高斯模糊强度

    # 13. HSL 大幅调整 - 破坏颜色直方图（videohash 64bit hash 一半看颜色）
    hsl_shift_enabled: bool = False
    hsl_hue_shift: float = 10.0      # 色相偏移（度，0-360）
    hsl_saturation: float = 0.88     # 饱和度倍率
    hsl_value: float = 1.05          # 亮度倍率

    # 14. 强制帧率转换 - 改变时间采样点
    # 30fps → 24fps → 30fps 这种双向转换会改变所有帧时间戳
    fps_convert_enabled: bool = False
    fps_intermediate: int = 24       # 中间转换的帧率
    fps_final: int = 30              # 最终帧率

    # ============ 编码参数 ============
    crf: int = 23
    preset: str = "fast"
