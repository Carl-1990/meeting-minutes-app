from pathlib import Path

import numpy as np
import pytest

from app.core.audio_capture import WavSink, frame_is_active, frame_rms, resample_int16
from app.core.asr_engine import NullASR, create_asr_engine
from app.core.diarizer import create_speaker_embedder
from app.core.punctuator import create_punctuator
from app.core.vad import create_speech_segmenter
from app.utils.mic_permission import authorization_status, open_microphone_settings


def test_silence_detection_requires_a_nonzero_sample():
    assert frame_is_active(np.zeros(1600, dtype=np.int16)) is False
    assert frame_is_active(np.array([0, 0, 1], dtype=np.int16)) is True
    assert frame_rms(np.zeros(8, dtype=np.int16)) == 0
    tone = np.full(1600, 16000, dtype=np.int16)
    assert frame_rms(tone) == pytest.approx(16000 / 32768, rel=1e-3)


def test_resample_48k_to_16k_keeps_duration():
    pcm = np.zeros(48000, dtype=np.int16)
    pcm[::3] = 1000
    converted = resample_int16(pcm, 48000, 16000)
    assert abs(len(converted) - 16000) <= 1
    assert frame_is_active(converted)
    same = resample_int16(pcm[:160], 16000, 16000)
    assert np.array_equal(same, pcm[:160])


def test_wav_appends_without_dropping_earlier_audio(tmp_path: Path):
    import wave

    path = tmp_path / "meeting.wav"
    sink = WavSink(path)
    sink.write(np.array([1, 2, 3, 4], dtype=np.int16))
    sink.close()
    again = WavSink(path, append=True)
    again.write(np.array([5, 6], dtype=np.int16))
    again.close()
    with wave.open(str(path), "rb") as handle:
        assert handle.getnchannels() == 1
        assert handle.getframerate() == 16000
        payload = handle.readframes(handle.getnframes())
    assert payload == np.array([1, 2, 3, 4, 5, 6], dtype=np.int16).tobytes()


def test_speech_engines_degrade_without_models():
    engine = create_asr_engine()
    assert isinstance(engine, NullASR)
    assert engine.available is False
    assert engine.get_partial() == ""
    assert engine.pop_final() is None
    engine.accept_audio(np.zeros(160, dtype=np.int16))

    segmenter = create_speech_segmenter()
    audio, seconds = segmenter.measure(np.zeros(16000, dtype=np.float32))
    assert len(audio) == 16000
    assert seconds == pytest.approx(1.0)

    assert create_punctuator().punctuate(" 你好 ") == "你好"
    embedder, error = create_speaker_embedder(Path("/no/such/eres2net.onnx"))
    assert embedder is None
    assert error


def test_microphone_helper_does_not_crash_off_device():
    status = authorization_status()
    assert status in {"unknown", "not_determined", "restricted", "denied", "authorized"}
    open_microphone_settings()
