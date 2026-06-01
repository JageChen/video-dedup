# 打包成 Windows EXE

## 🎯 推荐方案：GitHub Actions 自动打包

不需要 Windows 机器，**完全免费**。

### 步骤

1. **推到 GitHub**

   ```bash
   cd /Users/jage/dev/tools/video-dedup
   git init
   git add .
   git commit -m "init"
   git remote add origin git@github.com:你的用户名/video-dedup.git
   git push -u origin main
   ```

2. **触发打包**（两种方式）

   方式 A — 推 tag 自动打包 + 发 Release：
   ```bash
   git tag v1.0.0
   git push --tags
   ```

   方式 B — 在 GitHub 网页 Actions 页面手动点 "Run workflow"

3. **下载 EXE**

   - Actions 页面进入跑完的构建 → 底部 Artifacts → 下载 `视频工具箱-windows.zip`
   - 或者从 Releases 页面下载

4. **发给朋友**
   - 朋友解压 zip
   - 双击 `视频工具箱.exe`
   - 浏览器自动打开 `http://127.0.0.1:7860`
   - 即开即用，不需要装 Python / ffmpeg / 模型

⏱️ 整个流程从 push 到拿到 exe 大约 **30-40 分钟**（Actions 跑构建时间）。

---

## 🛠️ 备选方案：本地 Windows 打包

如果有 Windows 机器：

```cmd
:: 装 Python 3.11
:: 装依赖
pip install -r requirements.txt
pip install pyinstaller

:: 下载 ffmpeg.exe 到项目根
:: https://github.com/BtbN/FFmpeg-Builds/releases

:: 准备模型（首次约 1GB 下载）
python scripts\prepare_models.py

:: 打包
pyinstaller build.spec --clean --noconfirm

:: 输出在 dist\视频工具箱\
```

---

## 📊 体积预估

| 组件 | 大小 |
|---|---|
| Python runtime + 依赖（torch / gradio / funasr 等）| ~400 MB |
| SenseVoiceSmall | 896 MB |
| fsmn-vad | 4 MB |
| faster-whisper-tiny | 75 MB |
| ffmpeg.exe + ffprobe.exe | ~150 MB |
| **总计（解压后）** | **~1.5 GB** |
| **zip 压缩后** | **~700-900 MB** |

---

## 🐛 常见问题

### 1. PyInstaller 打包后启动报 "ModuleNotFoundError"

在 `build.spec` 的 `hiddenimports` 加上缺的模块名再重新打包。

### 2. Gradio 静态资源 404

确保 `datas` 里有：
```python
datas += collect_data_files("gradio")
datas += collect_data_files("gradio_client")
```

### 3. SenseVoice 加载失败

检查 `models/SenseVoiceSmall/` 下要有 `model.pt` `config.yaml` `tokens.json` 等。

### 4. exe 被 Windows Defender 拦截

未签名 exe 会触发警告。让朋友点 "更多信息" → "仍要运行"。
或者你买代码签名证书（约 ¥500/年）做签名。

### 5. 模型路径找不到

代码已经做了兼容：本地 `models/` 找不到时会从云端下载（首次启动联网）。
