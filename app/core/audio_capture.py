"""Microphone capture via sounddevice.

The PortAudio callback only copies samples into a queue. Resampling to
16 kHz mono int16 happens there so the processing thread always sees the
rate the speech models expect.
"""

from __future__ import annotations

import queue
import struct
import threading
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16000
BLOCK_MS = 100


class AudioCaptureError(RuntimeError):
    """The default input device could not be opened."""


def frame_is_active(pcm: np.ndarray) -> bool:
    """True when an int16 frame contains a non-zero sample.

    A denied macOS microphone often opens successfully and then delivers
    exact silence. Green status must be based on real samples.
    """
    if pcm is None or np.size(pcm) == 0:
        return False
    return int(np.max(np.abs(pcm))) >= 1


def frame_rms(pcm: np.ndarray) -> float:
    if pcm is None or np.size(pcm) == 0:
        return 0.0
    samples = pcm.astype(np.float64) / 32768.0
    return float(np.sqrt(np.mean(samples * samples)))


def resample_int16(pcm: np.ndarray, src_rate: int, dst_rate: int = SAMPLE_RATE) -> np.ndarray:
    """Linear resample. Exact integer ratios keep every Nth neighborhood."""
    samples = np.asarray(pcm, dtype=np.float32).reshape(-1)
    if samples.size == 0 or src_rate == dst_rate:
        return np.asarray(pcm, dtype=np.int16).reshape(-1)
    if src_rate <= 0 or dst_rate <= 0:
        return np.zeros(0, dtype=np.int16)
    n_out = int(round(samples.size * dst_rate / src_rate))
    if n_out <= 0:
        return np.zeros(0, dtype=np.int16)
    positions = np.linspace(0, samples.size - 1, num=n_out)
    left = np.floor(positions).astype(np.int64)
    right = np.minimum(left + 1, samples.size - 1)
    frac = (positions - left).astype(np.float32)
    mixed = samples[left] * (1.0 - frac) + samples[right] * frac
    return np.clip(np.rint(mixed), -32768, 32767).astype(np.int16)


class WavSink:
    """16 kHz mono PCM wav. ``append=True`` keeps samples already on disk."""

    def __init__(self, path: Path, sample_rate: int = SAMPLE_RATE, append: bool = False) -> None:
        self.path = Path(path)
        self.sample_rate = sample_rate
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._closed = False
        existing = 0
        if append and self.path.is_file() and self.path.stat().st_size >= 44:
            self._fh = self.path.open("r+b")
            self._fh.seek(40)
            existing = struct.unpack("<I", self._fh.read(4))[0]
            self._fh.seek(44 + existing)
        else:
            self._fh = self.path.open("w+b")
            self._write_header(0)
        self._data_bytes = existing

    def _write_header(self, data_bytes: int) -> None:
        byte_rate = self.sample_rate * 2
        self._fh.seek(0)
        self._fh.write(b"RIFF")
        self._fh.write(struct.pack("<I", 36 + data_bytes))
        self._fh.write(b"WAVEfmt ")
        self._fh.write(struct.pack("<IHHIIHH", 16, 1, 1, self.sample_rate, byte_rate, 2, 16))
        self._fh.write(b"data")
        self._fh.write(struct.pack("<I", data_bytes))

    def write(self, pcm: np.ndarray) -> None:
        payload = np.ascontiguousarray(pcm, dtype=np.int16).reshape(-1).tobytes()
        if not payload:
            return
        with self._lock:
            if self._closed:
                return
            self._fh.write(payload)
            self._data_bytes += len(payload)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._write_header(self._data_bytes)
            self._fh.flush()
            self._fh.close()


class AudioCapture:
    def __init__(self, sample_rate: int = SAMPLE_RATE, block_ms: int = BLOCK_MS) -> None:
        self.sample_rate = sample_rate
        self.block_ms = block_ms
        self.queue: queue.Queue[np.ndarray] = queue.Queue(maxsize=200)
        self.native_rate = sample_rate
        self.device_name = ""
        self._stream = None

    def start(self) -> None:
        try:
            import sounddevice as sd
        except Exception as exc:
            raise AudioCaptureError("未检测到麦克风，请检查连接或系统隐私设置") from exc
        try:
            info = sd.query_devices(kind="input")
        except Exception as exc:
            raise AudioCaptureError("未检测到麦克风，请检查连接或系统隐私设置") from exc
        if not info:
            raise AudioCaptureError("未检测到麦克风，请检查连接或系统隐私设置")
        self.device_name = str(info.get("name") or "")
        self.native_rate = int(info.get("default_samplerate") or self.sample_rate)
        block = max(1, int(self.native_rate * self.block_ms / 1000))
        try:
            self._stream = sd.InputStream(
                samplerate=self.native_rate,
                channels=1,
                dtype="int16",
                blocksize=block,
                callback=self._on_audio,
            )
            self._stream.start()
        except Exception as exc:
            self._stream = None
            raise AudioCaptureError("未检测到麦克风，请检查连接或系统隐私设置") from exc

    def _on_audio(self, indata, frames, time_info, status) -> None:
        del frames, time_info, status
        pcm = np.array(indata[:, 0], dtype=np.int16, copy=True)
        resampled = resample_int16(pcm, self.native_rate, self.sample_rate)
        if resampled.size == 0:
            return
        try:
            self.queue.put_nowait(resampled)
        except queue.Full:
            try:
                self.queue.get_nowait()
            except queue.Empty:
                pass
            try:
                self.queue.put_nowait(resampled)
            except queue.Full:
                pass

    def read(self, timeout: float = 0.1) -> np.ndarray | None:
        try:
            return self.queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def stop(self) -> None:
        stream = self._stream
        self._stream = None
        if stream is None:
            return
        try:
            stream.stop()
            stream.close()
        except Exception:
            return
