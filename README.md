# 会议纪要

离线 macOS 会议记录：麦克风录音、实时中英转写、自动/手动发言人、结束时把 Word 和录音保存到桌面。

规格见 [docs/FRAMEWORK.md](docs/FRAMEWORK.md)，使用说明见 [docs/使用说明.md](docs/使用说明.md)。

## 运行（开发）

需要 Python 3.11（3.12 也可跑界面和单元测试）以及本机麦克风。

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
bash build/download_models.sh
python main.py
```

没有模型时仍能打开界面并录音，但不会转写。语音模型不放进 git。

## 测试

```bash
pip install -r requirements-dev.txt
QT_QPA_PLATFORM=offscreen python -m pytest -q
```

覆盖会议状态机、改名同步、声纹聚类、Word 导出、自动保存，以及无显示环境下窗口能否创建。

## 安装包

只做 Apple Silicon（arm64，macOS 12+）的 ad-hoc 签名，不开公证。Hardened Runtime 带上麦克风 entitlement。

在 Mac 上：

```bash
brew install create-dmg
bash build/make_dmg.sh
```

产物是 `dist/会议纪要.dmg`。双击后把「会议纪要」拖进「应用程序」。第一次请右键 → 打开；若提示已损坏，执行 `xattr -cr /Applications/会议纪要.app`。说明也在 dmg 里的「首次打开说明.txt」。

GitHub Actions（`.github/workflows/macos-dmg.yml`）在 `macos-14`（arm64）上同样构建，上传 artifact `meeting-minutes-dmg`。推送 `v*` tag 时还会把 dmg 挂到对应的 GitHub Release。也可以手动 `workflow_dispatch`。
