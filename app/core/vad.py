"""Silero VAD wrapper used to measure speech inside one ASR utterance."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from app.core.audio_capture import SAMPLE_RATE
from app.utils.paths import models_dir


class SpeechSegmenter:
    available = False
    reason = ""

    def measure(self, samples_f32: np.ndarray) -> tuple[np.ndarray, float]:
        samples = np.asarray(samples_f32, dtype=np.float32).reshape(-1)
        return samples, (len(samples) / SAMPLE_RATE if samples.size else 0.0)


class SileroSpeechSegmenter(SpeechSegmenter):
    available = True

    def __init__(self, model_path: Path) -> None:
        import sherpa_onnx

        config = sherpa_onnx.VadModelConfig()
        silero = config.silero_vad
        silero.model = str(model_path)
        for attr, value in (
            ("threshold", 0.5),
            ("min_silence_duration", 0.4),
            ("min_speech_duration", 0.25),
            ("max_speech_duration", 30.0),
        ):
            if hasattr(silero, attr):
                setattr(silero, attr, value)
        config.sample_rate = SAMPLE_RATE
        window = int(getattr(silero, "window_size", 0) or 512)
        self._window = window
        self._vad = sherpa_onnx.VoiceActivityDetector(config, buffer_size_in_seconds=60)
        self.reason = ""

    def measure(self, samples_f32: np.ndarray) -> tuple[np.ndarray, float]:
        samples = np.asarray(samples_f32, dtype=np.float32).reshape(-1)
        if samples.size == 0:
            return samples, 0.0
        if hasattr(self._vad, "reset"):
            self._vad.reset()
        padded = np.concatenate(
            [samples, np.zeros(int(SAMPLE_RATE * 0.6), dtype=np.float32)]
        )
        offset = 0
        window = self._window
        while offset + window <= len(padded):
            self._vad.accept_waveform(padded[offset : offset + window])
            offset += window
        chunks: list[np.ndarray] = []
        while not self._vad.empty():
            chunks.append(np.array(self._vad.front.samples, dtype=np.float32))
            self._vad.pop()
        if not chunks:
            return samples, len(samples) / SAMPLE_RATE
        speech = np.concatenate(chunks)
        return speech, len(speech) / SAMPLE_RATE


def create_speech_segmenter(model_path: Path | None = None) -> SpeechSegmenter:
    path = Path(model_path) if model_path is not None else models_dir() / "vad" / "silero_vad.onnx"
    if not path.is_file():
        segmenter = SpeechSegmenter()
        segmenter.reason = f"缺少 VAD 模型：{path}"
        return segmenter
    try:
        return SileroSpeechSegmenter(path)
    except Exception as exc:  # pragma: no cover - native model
        segmenter = SpeechSegmenter()
        segmenter.reason = str(exc)
        return segmenter
