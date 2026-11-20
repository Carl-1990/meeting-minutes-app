"""Streaming ASR interface and the offline sherpa-onnx Paraformer engine."""

from __future__ import annotations

import inspect
from pathlib import Path

import numpy as np

from app.core.audio_capture import SAMPLE_RATE
from app.utils.paths import models_dir


class ASREngine:
    """Structural base matching the spec protocol, plus flush/endpoint helpers."""

    available: bool = False
    reason: str = ""

    def accept_audio(self, pcm: np.ndarray) -> None:
        raise NotImplementedError

    def get_partial(self) -> str:
        raise NotImplementedError

    def pop_final(self) -> str | None:
        raise NotImplementedError

    def reset(self) -> None:
        raise NotImplementedError

    def pop_endpoint(self) -> tuple[bool, str]:
        text = self.pop_final()
        if text:
            return True, text
        return False, ""

    def flush(self) -> str:
        return self.get_partial().strip()


def pcm_to_float(pcm: np.ndarray) -> np.ndarray:
    if pcm.dtype == np.float32:
        return np.ascontiguousarray(pcm.reshape(-1))
    if pcm.dtype == np.float64:
        return np.ascontiguousarray(pcm.reshape(-1), dtype=np.float32)
    return np.ascontiguousarray(pcm.astype(np.float32).reshape(-1) / 32768.0)


def _result_text(result) -> str:
    if result is None:
        return ""
    if isinstance(result, str):
        return result
    text = getattr(result, "text", None)
    if isinstance(text, str):
        return text
    return str(result)


def _first_existing(directory: Path, names: list[str]) -> Path | None:
    for name in names:
        candidate = directory / name
        if candidate.is_file():
            return candidate
    return None


class NullASR(ASREngine):
    available = False

    def __init__(self, reason: str) -> None:
        self.reason = reason

    def accept_audio(self, pcm: np.ndarray) -> None:
        return None

    def get_partial(self) -> str:
        return ""

    def pop_final(self) -> str | None:
        return None

    def reset(self) -> None:
        return None

    def pop_endpoint(self) -> tuple[bool, str]:
        return False, ""

    def flush(self) -> str:
        return ""


class SherpaStreamingASR(ASREngine):
    available = True

    def __init__(self, model_dir: Path, num_threads: int = 2) -> None:
        import sherpa_onnx

        encoder = _first_existing(model_dir, ["encoder.int8.onnx", "encoder.onnx"])
        decoder = _first_existing(model_dir, ["decoder.int8.onnx", "decoder.onnx"])
        tokens = model_dir / "tokens.txt"
        if encoder is None or decoder is None or not tokens.is_file():
            raise FileNotFoundError(f"流式识别模型不完整：{model_dir}")
        factory = sherpa_onnx.OnlineRecognizer.from_paraformer
        desired = {
            "tokens": str(tokens),
            "encoder": str(encoder),
            "decoder": str(decoder),
            "num_threads": num_threads,
            "sample_rate": SAMPLE_RATE,
            "feature_dim": 80,
            "decoding_method": "greedy_search",
            "enable_endpoint_detection": True,
            "rule1_min_trailing_silence": 2.4,
            "rule2_min_trailing_silence": 1.0,
            "rule3_min_utterance_length": 20.0,
            "provider": "cpu",
        }
        params = inspect.signature(factory).parameters
        kwargs = {key: value for key, value in desired.items() if key in params}
        self.recognizer = factory(**kwargs)
        self.stream = self.recognizer.create_stream()
        self.sample_rate = SAMPLE_RATE
        self.reason = ""

    def accept_audio(self, pcm: np.ndarray) -> None:
        samples = pcm_to_float(pcm)
        if samples.size == 0:
            return
        self.stream.accept_waveform(self.sample_rate, samples)
        while self.recognizer.is_ready(self.stream):
            self.recognizer.decode_stream(self.stream)

    def get_partial(self) -> str:
        return _result_text(self.recognizer.get_result(self.stream)).strip()

    def pop_final(self) -> str | None:
        ended, text = self.pop_endpoint()
        if ended and text:
            return text
        return None

    def pop_endpoint(self) -> tuple[bool, str]:
        if not self.recognizer.is_endpoint(self.stream):
            return False, ""
        text = self.get_partial()
        self.recognizer.reset(self.stream)
        return True, text

    def reset(self) -> None:
        self.recognizer.reset(self.stream)

    def flush(self) -> str:
        silence = np.zeros(int(self.sample_rate * 1.8), dtype=np.float32)
        self.accept_audio(silence)
        text = self.get_partial()
        self.reset()
        return text


def create_asr_engine(model_dir: Path | None = None) -> ASREngine:
    directory = Path(model_dir) if model_dir is not None else models_dir() / "asr"
    try:
        if not directory.is_dir():
            raise FileNotFoundError(f"缺少语音模型目录：{directory}")
        return SherpaStreamingASR(directory)
    except Exception as exc:
        return NullASR(str(exc))
