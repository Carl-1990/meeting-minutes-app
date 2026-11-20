import numpy as np
import pytest

from app.core.diarizer import OnlineDiarizer, cosine_similarity
from app.core.session import default_speakers


def _bases(count: int, dim: int, seed: int = 0) -> list[np.ndarray]:
    rng = np.random.default_rng(seed)
    matrix = rng.normal(size=(dim, count))
    orthogonal, _ = np.linalg.qr(matrix)
    return [orthogonal[:, index].copy() for index in range(count)]


def _with_cosine(base: np.ndarray, cosine: float, seed: int = 1) -> np.ndarray:
    rng = np.random.default_rng(seed)
    ortho = rng.normal(size=base.shape)
    ortho = ortho - base * np.dot(base, ortho)
    ortho = ortho / np.linalg.norm(ortho)
    vec = cosine * base + np.sqrt(max(0.0, 1.0 - cosine * cosine)) * ortho
    return vec / np.linalg.norm(vec)


def _assign(diarizer, speakers, embedding, seconds, previous, **kwargs):
    return diarizer.assign_utterance(
        speakers,
        embedding,
        seconds,
        previous,
        kwargs.get("mode", "auto"),
        kwargs.get("manual_speaker_id"),
        kwargs.get("forced_speaker_id"),
    )


def test_similar_embeddings_share_a_speaker_and_different_ones_do_not():
    diarizer = OnlineDiarizer(threshold=0.55)
    speakers = default_speakers()
    bases = _bases(2, 32)
    first, source, forced = _assign(diarizer, speakers, bases[0], 2.0, None)
    assert (first, source, forced) == (1, "auto", None)
    again, _, _ = _assign(diarizer, speakers, _with_cosine(bases[0], 0.96, seed=2), 2.0, first)
    assert again == 1
    assert speakers[1].count == 2
    second, _, _ = _assign(diarizer, speakers, bases[1], 2.0, again)
    assert second == 2
    assert speakers[2].centroid is not None
    assert cosine_similarity(bases[0], bases[1]) < 0.2


def test_threshold_is_inclusive():
    base = _bases(1, 16)[0]
    near = _with_cosine(base, 0.70, seed=3)
    similarity = cosine_similarity(base, near)
    speakers = default_speakers()
    inclusive = OnlineDiarizer(threshold=similarity)
    _assign(inclusive, speakers, base, 1.5, None)
    matched, _, _ = _assign(inclusive, speakers, near, 1.5, 1)
    assert matched == 1

    speakers = default_speakers()
    stricter = OnlineDiarizer(threshold=min(0.89, similarity + 0.05))
    _assign(stricter, speakers, base, 1.5, None)
    opened, _, _ = _assign(stricter, speakers, near, 1.5, 1)
    assert opened == 2


def test_short_utterance_reuses_previous_speaker_without_new_centroid():
    diarizer = OnlineDiarizer(threshold=0.55, min_speech_seconds=1.0)
    speakers = default_speakers()
    base = _bases(1, 16)[0]
    other = _bases(2, 16, seed=4)[1]
    _assign(diarizer, speakers, base, 2.0, None)
    speaker_id, source, _ = _assign(diarizer, speakers, other, 0.4, 1)
    assert speaker_id == 1
    assert source == "auto"
    assert speakers[2].centroid is None
    assert speakers[2].count == 0


def test_manual_mode_and_one_shot_override():
    diarizer = OnlineDiarizer()
    speakers = default_speakers()
    vector = _bases(1, 16)[0]
    speaker_id, source, forced = _assign(
        diarizer,
        speakers,
        vector,
        2.0,
        None,
        mode="manual",
        manual_speaker_id=3,
        forced_speaker_id=9,
    )
    assert (speaker_id, source, forced) == (3, "manual", 9)
    assert speakers[3].count == 1

    speaker_id, source, forced = _assign(
        diarizer,
        speakers,
        vector,
        0.2,
        3,
        mode="auto",
        forced_speaker_id=4,
    )
    assert (speaker_id, source, forced) == (4, "manual", None)


def test_eleventh_voice_falls_back_to_the_closest_speaker():
    diarizer = OnlineDiarizer(threshold=0.55)
    speakers = default_speakers()
    bases = _bases(10, 32, seed=5)
    previous = None
    for index, base in enumerate(bases, start=1):
        speaker_id, _, _ = _assign(diarizer, speakers, base, 2.0, previous)
        assert speaker_id == index
        previous = speaker_id
    assert all(speakers[i].centroid is not None for i in range(1, 11))
    extra = bases[2] * 0.95 + bases[3] * 0.05
    extra = extra / np.linalg.norm(extra)
    before = speakers[3].count
    chosen, source, _ = _assign(diarizer, speakers, extra, 2.0, previous)
    assert chosen == 3
    assert source == "auto"
    assert speakers[3].count == before + 1


def test_correction_absorb_pulls_later_audio_to_that_speaker():
    diarizer = OnlineDiarizer(threshold=0.55)
    speakers = default_speakers()
    left, right = _bases(2, 32, seed=6)
    diarizer.absorb(speakers[1], left)
    diarizer.absorb(speakers[2], right)
    probe = _with_cosine(right, 0.97, seed=7)
    speaker_id, _, _ = _assign(diarizer, speakers, probe, 2.0, 1)
    assert speaker_id == 2
