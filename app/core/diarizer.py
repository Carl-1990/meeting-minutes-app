"""Online speaker clustering and sherpa-onnx speaker embeddings.

Short utterances (under ``min_speech_seconds``) keep the previous speaker and
do not update a centroid. A new speaker is opened when the best cosine
similarity is below the threshold, up to 10 speakers. After that, audio is
assigned to the closest existing speaker.
"""

from __future__ import annotations

from typing import Literal

import numpy as np

from app.core.session import MAX_SPEAKERS, Speaker

AssignSource = Literal["auto", "manual"]


def cosine_similarity(left: np.ndarray, right: np.ndarray) -> float:
    a = np.asarray(left, dtype=np.float64).reshape(-1)
    b = np.asarray(right, dtype=np.float64).reshape(-1)
    if a.shape != b.shape or a.size == 0:
        return 0.0
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0.0 or not np.isfinite(denom):
        return 0.0
    value = float(np.dot(a, b) / denom)
    if not np.isfinite(value):
        return 0.0
    return max(-1.0, min(1.0, value))


def _unit(embedding: np.ndarray | list[float]) -> np.ndarray | None:
    vec = np.asarray(embedding, dtype=np.float64).reshape(-1)
    if vec.size == 0 or not np.isfinite(vec).all():
        return None
    norm = float(np.linalg.norm(vec))
    if norm == 0.0:
        return None
    return vec / norm


class OnlineDiarizer:
    def __init__(
        self,
        threshold: float = 0.55,
        max_speakers: int = MAX_SPEAKERS,
        min_speech_seconds: float = 1.0,
    ) -> None:
        self.threshold = float(threshold)
        self.max_speakers = int(max_speakers)
        self.min_speech_seconds = float(min_speech_seconds)

    def absorb(self, speaker: Speaker, embedding: np.ndarray | list[float]) -> None:
        """Fold a corrected utterance embedding into ``speaker``."""
        self._update(speaker, embedding)

    def _update(self, speaker: Speaker, embedding: np.ndarray | list[float]) -> None:
        vec = _unit(embedding)
        if vec is None:
            return
        if speaker.centroid is None or speaker.count <= 0:
            speaker.centroid = vec
            speaker.count = 1
            return
        speaker.count += 1
        speaker.centroid = speaker.centroid + (vec - speaker.centroid) / speaker.count

    def _best(self, speakers: dict[int, Speaker], embedding: np.ndarray) -> tuple[int | None, float]:
        best_id: int | None = None
        best_sim = -1.0
        for speaker_id in sorted(speakers):
            speaker = speakers[speaker_id]
            if speaker.centroid is None:
                continue
            sim = cosine_similarity(embedding, speaker.centroid)
            if sim > best_sim:
                best_sim = sim
                best_id = speaker_id
        return best_id, best_sim

    def _new_speaker_id(self, speakers: dict[int, Speaker]) -> int | None:
        for speaker_id in range(1, self.max_speakers + 1):
            speaker = speakers.get(speaker_id)
            if speaker is not None and speaker.centroid is None:
                return speaker_id
        return None

    def assign_utterance(
        self,
        speakers: dict[int, Speaker],
        embedding: np.ndarray | list[float] | None,
        speech_seconds: float,
        previous_speaker_id: int | None,
        mode: str = "auto",
        manual_speaker_id: int | None = None,
        forced_speaker_id: int | None = None,
    ) -> tuple[int, AssignSource, int | None]:
        """Return ``(speaker_id, source, forced_speaker_id_after)``.

        Manual mode keeps ``forced_speaker_id`` unchanged. In auto mode a
        one-shot ``forced_speaker_id`` is consumed and cleared.
        """
        vec = None if embedding is None else _unit(embedding)
        long_enough = speech_seconds >= self.min_speech_seconds and vec is not None

        if mode == "manual":
            speaker_id = manual_speaker_id or 1
            if speaker_id not in speakers:
                speaker_id = 1
            if long_enough and vec is not None:
                self._update(speakers[speaker_id], vec)
            return speaker_id, "manual", forced_speaker_id

        if forced_speaker_id is not None and forced_speaker_id in speakers:
            if long_enough and vec is not None:
                self._update(speakers[forced_speaker_id], vec)
            return forced_speaker_id, "manual", None

        if not long_enough or vec is None:
            speaker_id = previous_speaker_id if previous_speaker_id in speakers else 1
            return speaker_id, "auto", None

        best_id, best_sim = self._best(speakers, vec)
        if best_id is not None and best_sim >= self.threshold:
            self._update(speakers[best_id], vec)
            return best_id, "auto", None

        fresh_id = self._new_speaker_id(speakers)
        if fresh_id is not None:
            self._update(speakers[fresh_id], vec)
            return fresh_id, "auto", None

        if best_id is None:
            best_id = 1
        self._update(speakers[best_id], vec)
        return best_id, "auto", None


class SpeakerEmbedder:
    """sherpa-onnx 3D-Speaker embedding. ``available`` is False without a model."""

    def __init__(self, model_path: str, num_threads: int = 1) -> None:
        import sherpa_onnx

        config = sherpa_onnx.SpeakerEmbeddingExtractorConfig(
            model=model_path,
            num_threads=num_threads,
            debug=False,
            provider="cpu",
        )
        if hasattr(config, "validate") and not config.validate():
            raise ValueError(f"说话人模型无效：{model_path}")
        self._extractor = sherpa_onnx.SpeakerEmbeddingExtractor(config)
        self.available = True

    def compute(self, samples_f32: np.ndarray, sample_rate: int = 16000) -> np.ndarray | None:
        samples = np.ascontiguousarray(samples_f32, dtype=np.float32).reshape(-1)
        if samples.size < int(sample_rate * 0.4):
            return None
        stream = self._extractor.create_stream()
        stream.accept_waveform(sample_rate=sample_rate, waveform=samples)
        stream.input_finished()
        if hasattr(self._extractor, "is_ready") and not self._extractor.is_ready(stream):
            return None
        embedding = np.asarray(self._extractor.compute(stream), dtype=np.float64)
        if embedding.size == 0:
            return None
        return embedding


def create_speaker_embedder(model_path) -> tuple[SpeakerEmbedder | None, str | None]:
    path = model_path
    if path is None or not getattr(path, "is_file", lambda: False)():
        return None, f"缺少说话人模型：{path}"
    try:
        return SpeakerEmbedder(str(path)), None
    except Exception as exc:  # pragma: no cover - depends on native model
        return None, str(exc)
