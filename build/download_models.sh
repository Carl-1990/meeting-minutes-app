#!/usr/bin/env bash
# Download offline sherpa-onnx models used by 会议纪要.
# Large files stay out of git. Safe to re-run: each file is skipped once present.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MODEL_ROOT="$ROOT/models"
mkdir -p "$MODEL_ROOT/asr" "$MODEL_ROOT/vad" "$MODEL_ROOT/speaker" "$MODEL_ROOT/punct"

download() {
  local dest="$1"
  shift
  local url
  for url in "$@"; do
    echo "Downloading $url"
    if curl -fL --retry 5 --retry-delay 2 --connect-timeout 30 -o "$dest" "$url"; then
      return 0
    fi
    rm -f "$dest"
    echo "failed: $url" >&2
  done
  echo "all download URLs failed for $dest" >&2
  return 1
}

min_size() {
  local file="$1"
  local min="$2"
  local size
  size="$(wc -c < "$file" | tr -d ' ')"
  if [[ "$size" -lt "$min" ]]; then
    echo "$file is only $size bytes (expected >= $min)" >&2
    exit 1
  fi
}

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

if [[ ! -f "$MODEL_ROOT/asr/tokens.txt" || ! -f "$MODEL_ROOT/asr/encoder.int8.onnx" || ! -f "$MODEL_ROOT/asr/decoder.int8.onnx" ]]; then
  download "$TMP/asr.tar.bz2" \
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/sherpa-onnx-streaming-paraformer-bilingual-zh-en.tar.bz2"
  min_size "$TMP/asr.tar.bz2" 100000000
  mkdir -p "$TMP/asr"
  tar -xjf "$TMP/asr.tar.bz2" -C "$TMP/asr"
  rm -f "$TMP/asr.tar.bz2"
  ENC="$(find "$TMP/asr" -name 'encoder.int8.onnx' -print -quit)"
  DEC="$(find "$TMP/asr" -name 'decoder.int8.onnx' -print -quit)"
  if [[ -z "$ENC" || -z "$DEC" ]]; then
    ENC="$(find "$TMP/asr" -name 'encoder.onnx' -print -quit)"
    DEC="$(find "$TMP/asr" -name 'decoder.onnx' -print -quit)"
  fi
  TOK="$(find "$TMP/asr" -name 'tokens.txt' -print -quit)"
  cp "$ENC" "$MODEL_ROOT/asr/encoder.int8.onnx"
  cp "$DEC" "$MODEL_ROOT/asr/decoder.int8.onnx"
  cp "$TOK" "$MODEL_ROOT/asr/tokens.txt"
  rm -rf "$TMP/asr"
fi
min_size "$MODEL_ROOT/asr/encoder.int8.onnx" 20000000
min_size "$MODEL_ROOT/asr/decoder.int8.onnx" 10000000
min_size "$MODEL_ROOT/asr/tokens.txt" 1000

if [[ ! -f "$MODEL_ROOT/vad/silero_vad.onnx" ]]; then
  download "$MODEL_ROOT/vad/silero_vad.onnx" \
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx" \
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad_v5.onnx"
fi
min_size "$MODEL_ROOT/vad/silero_vad.onnx" 100000

if [[ ! -f "$MODEL_ROOT/speaker/eres2net.onnx" ]]; then
  download "$MODEL_ROOT/speaker/eres2net.onnx" \
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models/3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx"
fi
min_size "$MODEL_ROOT/speaker/eres2net.onnx" 5000000

if [[ ! -f "$MODEL_ROOT/punct/model.onnx" ]]; then
  download "$TMP/punct.tar.bz2" \
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/punctuation-models/sherpa-onnx-punct-ct-transformer-zh-en-vocab272727-2024-04-12-int8.tar.bz2" \
    "https://github.com/k2-fsa/sherpa-onnx/releases/download/punctuation-models/sherpa-onnx-punct-ct-transformer-zh-en-vocab272727-2024-04-12.tar.bz2"
  mkdir -p "$TMP/punct"
  tar -xjf "$TMP/punct.tar.bz2" -C "$TMP/punct"
  PUN="$(find "$TMP/punct" -name '*.int8.onnx' -print -quit)"
  if [[ -z "$PUN" ]]; then
    PUN="$(find "$TMP/punct" -name '*.onnx' -print -quit)"
  fi
  cp "$PUN" "$MODEL_ROOT/punct/model.onnx"
  rm -rf "$TMP/punct"
fi
min_size "$MODEL_ROOT/punct/model.onnx" 5000000

MODEL_ROOT="$MODEL_ROOT" python3 - <<'PY'
import json
import os
from pathlib import Path
manifest = {
    "asr": "sherpa-onnx-streaming-paraformer-bilingual-zh-en (int8 preferred)",
    "vad": "silero_vad",
    "speaker": "3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k",
    "punctuation": "sherpa-onnx-punct-ct-transformer-zh-en-vocab272727-2024-04-12",
}
dest = Path(os.environ["MODEL_ROOT"]) / "manifest.json"
dest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY

echo "Models ready in $MODEL_ROOT"
