#!/usr/bin/env bash
# Build dist/会议纪要.app (ad-hoc, Hardened Runtime) and dist/会议纪要.dmg.
# Must run on an Apple Silicon Mac. Models are downloaded if missing.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export LANG="${LANG:-en_US.UTF-8}"
export LC_ALL="${LC_ALL:-en_US.UTF-8}"

if [[ "$(uname -s)" != "Darwin" ]]; then
  echo "会议纪要.dmg 只能在 macOS 上构建。请使用 GitHub Actions 的 macos-14 任务，或在 Apple Silicon Mac 上运行本脚本。" >&2
  exit 1
fi
if [[ "$(uname -m)" != "arm64" ]]; then
  echo "本安装包只构建 arm64（Apple Silicon，M1–M5）。当前架构：$(uname -m)" >&2
  exit 1
fi

bash "$ROOT/build/download_models.sh"
python3 -m pip install -r "$ROOT/requirements.txt" pyinstaller
python3 - <<'PY'
import sherpa_onnx
assert hasattr(sherpa_onnx.OnlineRecognizer, "from_paraformer")
assert hasattr(sherpa_onnx, "VoiceActivityDetector")
assert hasattr(sherpa_onnx, "SpeakerEmbeddingExtractor")
assert hasattr(sherpa_onnx, "OfflinePunctuation")
print("sherpa-onnx API ok", getattr(sherpa_onnx, "__version__", "unknown"))
PY

if [[ ! -f "$ROOT/build/icon.icns" || ! -f "$ROOT/build/icon.png" ]]; then
  python3 "$ROOT/build/make_icon.py"
fi

rm -rf "$ROOT/dist" "$ROOT/build/pyinstaller-work"
python3 -m PyInstaller --noconfirm --clean \
  --workpath "$ROOT/build/pyinstaller-work" \
  --distpath "$ROOT/dist" \
  "$ROOT/build/meeting_minutes.spec"

APP="$ROOT/dist/会议纪要.app"
if [[ ! -d "$APP" ]]; then
  echo "未生成 $APP" >&2
  exit 1
fi

xattr -cr "$APP" || true

echo "Ad-hoc codesign with Hardened Runtime"
while IFS= read -r -d '' file; do
  if file "$file" | grep -q "Mach-O"; then
    codesign --force --sign - --options runtime --timestamp=none "$file" || true
  fi
done < <(find "$APP" -type f -print0)

codesign --force --deep --options runtime --timestamp=none \
  --entitlements "$ROOT/build/entitlements.plist" \
  --sign - \
  "$APP"
codesign --verify --verbose=2 "$APP"
codesign -d --entitlements :- "$APP" 2>/dev/null | grep -q "com.apple.security.device.audio-input"

INFO="$APP/Contents/Info.plist"
/usr/libexec/PlistBuddy -c 'Print :NSMicrophoneUsageDescription' "$INFO" | grep -q "麦克风"
/usr/libexec/PlistBuddy -c 'Print :LSMinimumSystemVersion' "$INFO"

BIN="$APP/Contents/MacOS/会议纪要"
file "$BIN"
lipo -archs "$BIN" | grep -q arm64

if ! command -v create-dmg >/dev/null 2>&1; then
  echo "需要 create-dmg：brew install create-dmg" >&2
  exit 1
fi

STAGE="$ROOT/dist/dmg-src"
rm -rf "$STAGE"
mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
# The readme is added by create-dmg --add-file so it can sit under the icons.

DMG="$ROOT/dist/会议纪要.dmg"
rm -f "$DMG"
if [[ -d "/Volumes/会议纪要" ]]; then
  hdiutil detach "/Volumes/会议纪要" || true
fi

EXTRA=()
if create-dmg --help 2>&1 | grep -q -- "--no-internet-enable"; then
  EXTRA+=(--no-internet-enable)
fi

ok=0
for attempt in 1 2 3; do
  echo "create-dmg attempt ${attempt}"
  if create-dmg \
    --volname "会议纪要" \
    --volicon "$ROOT/build/icon.icns" \
    --window-pos 200 120 \
    --window-size 700 460 \
    --icon-size 128 \
    --icon "会议纪要.app" 180 170 \
    --hide-extension "会议纪要.app" \
    --app-drop-link 520 170 \
    --add-file "首次打开说明.txt" "$ROOT/build/首次打开说明.txt" 350 340 \
    "${EXTRA[@]}" \
    "$DMG" \
    "$STAGE"
  then
    ok=1
    break
  fi
  rm -f "$DMG"
  if [[ -d "/Volumes/会议纪要" ]]; then
    hdiutil detach "/Volumes/会议纪要" || true
  fi
  sleep 5
done

if [[ "$ok" -ne 1 || ! -f "$DMG" ]]; then
  echo "create-dmg 失败，没有生成拖拽安装布局的 dmg" >&2
  exit 1
fi

echo "DMG ready: $DMG"
ls -lh "$DMG"
