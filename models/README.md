# 语音模型

这里的 ONNX 模型不进 git（约数百 MB）。打包或本地运行前执行：

```bash
bash build/download_models.sh
```

脚本会下载并整理成下面的布局（优先使用 int8，以便安装包落在大约 300–500 MB）：

- `models/asr/` — sherpa-onnx `streaming-paraformer-bilingual-zh-en`（encoder/decoder int8 + tokens.txt）
- `models/vad/silero_vad.onnx` — Silero VAD
- `models/speaker/eres2net.onnx` — 3D-Speaker ERes2Net 中文声纹
- `models/punct/model.onnx` — ct-transformer 中英标点

模型来自 [k2-fsa/sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx/releases) 的发布文件，许可证以上游为准。
