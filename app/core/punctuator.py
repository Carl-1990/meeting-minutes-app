"""Offline punctuation restoration (ct-transformer zh-en)."""

from __future__ import annotations

from pathlib import Path

from app.utils.paths import models_dir


class Punctuator:
    available = False
    reason = ""

    def punctuate(self, text: str) -> str:
        return (text or "").strip()


class SherpaPunctuator(Punctuator):
    available = True

    def __init__(self, model_path: Path) -> None:
        import sherpa_onnx

        config = sherpa_onnx.OfflinePunctuationConfig(
            model=sherpa_onnx.OfflinePunctuationModelConfig(ct_transformer=str(model_path)),
        )
        self._punct = sherpa_onnx.OfflinePunctuation(config)
        self.reason = ""

    def punctuate(self, text: str) -> str:
        cleaned = (text or "").strip()
        if not cleaned:
            return ""
        try:
            result = self._punct.add_punctuation(cleaned)
        except Exception:
            return cleaned
        return (result or cleaned).strip()


def _find_punct_model(directory: Path) -> Path | None:
    for name in ("model.int8.onnx", "model.onnx"):
        candidate = directory / name
        if candidate.is_file():
            return candidate
    if not directory.is_dir():
        return None
    matches = sorted(directory.glob("*.onnx"))
    return matches[0] if matches else None


def create_punctuator(model_dir: Path | None = None) -> Punctuator:
    directory = Path(model_dir) if model_dir is not None else models_dir() / "punct"
    model = _find_punct_model(directory)
    if model is None:
        engine = Punctuator()
        engine.reason = f"缺少标点模型：{directory}"
        return engine
    try:
        return SherpaPunctuator(model)
    except Exception as exc:  # pragma: no cover - native model
        engine = Punctuator()
        engine.reason = str(exc)
        return engine
