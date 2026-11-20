"""Worker thread: microphone frames to partial text and final utterances.

Pausing clears the accept flag. The input stream stays open, the level meter
keeps moving, and paused audio is neither transcribed nor written to the wav.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

import numpy as np
from PySide6.QtCore import QThread, Signal

from app.core.asr_engine import ASREngine
from app.core.audio_capture import SAMPLE_RATE, AudioCapture, WavSink, frame_is_active, frame_rms
from app.core.punctuator import Punctuator
from app.core.vad import SpeechSegmenter

log = logging.getLogger(__name__)


class MeetingPipeline(QThread):
    audio_level = Signal(float)
    mic_ready = Signal(bool)
    partial_text = Signal(str, float)
    finalized = Signal(str, float, float, float, object)
    error = Signal(str)

    def __init__(
        self,
        asr: ASREngine,
        segmenter: SpeechSegmenter,
        embedder,
        punctuator: Punctuator,
        wav_path,
        start_offset_seconds: float = 0.0,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.asr = asr
        self.segmenter = segmenter
        self.embedder = embedder
        self.punctuator = punctuator
        self.wav_path = wav_path
        self.capture = AudioCapture()
        self._accepting = threading.Event()
        self._accepting.set()
        self._stop = threading.Event()
        self._active_samples = int(max(0.0, start_offset_seconds) * SAMPLE_RATE)
        self._utt_chunks: list[np.ndarray] = []
        self._last_partial = ""
        self._mic_ready = False
        self._silence_warned = False
        self._started_mono = 0.0
        self._wav: WavSink | None = None

    @property
    def active_seconds(self) -> float:
        return self._active_samples / SAMPLE_RATE

    def set_accepting(self, accepting: bool) -> None:
        if accepting:
            self._accepting.set()
        else:
            self._accepting.clear()

    def request_stop(self) -> None:
        self._stop.set()

    def run(self) -> None:
        try:
            self._run()
        except Exception as exc:  # pragma: no cover - hardware/model failures
            log.exception("pipeline failed")
            self.error.emit(str(exc))
        finally:
            self.capture.stop()
            if self._wav is not None:
                self._wav.close()

    def _run(self) -> None:
        try:
            self.capture.start()
        except Exception as exc:
            self.error.emit(str(exc))
            return
        try:
            append = self._active_samples > 0 and Path(self.wav_path).is_file()
            self._wav = WavSink(self.wav_path, append=append)
        except Exception as exc:
            self.error.emit(f"无法写入录音文件：{exc}")
            return
        self._started_mono = time.monotonic()
        while not self._stop.is_set():
            chunk = self.capture.read(timeout=0.1)
            if chunk is None:
                self._maybe_warn_silence()
                continue
            self.audio_level.emit(frame_rms(chunk))
            if not self._mic_ready and frame_is_active(chunk):
                self._mic_ready = True
                self.mic_ready.emit(True)
            if not self._mic_ready:
                self._maybe_warn_silence()
            if not self._accepting.is_set():
                continue
            self._handle_chunk(chunk)
        self._flush()

    def _maybe_warn_silence(self) -> None:
        if self._mic_ready or self._silence_warned:
            return
        if time.monotonic() - self._started_mono >= 1.0:
            self._silence_warned = True
            self.mic_ready.emit(False)

    def _handle_chunk(self, pcm: np.ndarray) -> None:
        if self._wav is not None:
            self._wav.write(pcm)
        self._active_samples += int(pcm.shape[0])
        self._utt_chunks.append(pcm)
        self.asr.accept_audio(pcm)
        partial = self.asr.get_partial()
        if partial != self._last_partial:
            self._last_partial = partial
            self.partial_text.emit(partial, self.active_seconds)
        ended, text = self.asr.pop_endpoint()
        if ended:
            self._finalize_utterance(text)

    def _flush(self) -> None:
        text = ""
        try:
            text = self.asr.flush() or ""
        except Exception:
            log.exception("asr flush failed")
            text = self._last_partial
        if text.strip() or self._utt_chunks:
            self._finalize_utterance(text)

    def _finalize_utterance(self, text: str) -> None:
        if self._utt_chunks:
            pcm = np.concatenate(self._utt_chunks)
        else:
            pcm = np.zeros(0, dtype=np.int16)
        self._utt_chunks.clear()
        end = self.active_seconds
        start = max(0.0, end - (len(pcm) / SAMPLE_RATE))
        cleaned = (text or "").strip()
        self._last_partial = ""
        if not cleaned:
            self.partial_text.emit("", end)
            return
        samples = pcm.astype(np.float32) / 32768.0 if pcm.size else np.zeros(0, dtype=np.float32)
        try:
            speech, speech_seconds = self.segmenter.measure(samples)
        except Exception:
            log.exception("vad failed")
            speech, speech_seconds = samples, len(samples) / SAMPLE_RATE
        embedding = None
        if (
            self.embedder is not None
            and speech_seconds >= 1.0
            and speech.size > 0
        ):
            try:
                embedding = self.embedder.compute(speech)
            except Exception:
                log.exception("speaker embedding failed")
                embedding = None
        try:
            punctuated = self.punctuator.punctuate(cleaned)
        except Exception:
            log.exception("punctuation failed")
            punctuated = cleaned
        emb_list = None if embedding is None else np.asarray(embedding, dtype=float).tolist()
        self.finalized.emit(punctuated, float(start), float(end), float(speech_seconds), emb_list)
        self.partial_text.emit("", end)
