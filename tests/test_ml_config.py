from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from open_sesame.solvers.local_ml import LocalMLCaptchaOCRSolver, _extract_prediction
from open_sesame.solvers.ml_config import (
    LocalOCRConfig,
    MODEL_OPTIONS,
    RUNNABLE_MODEL_OPTIONS,
    get_model_option,
    resolve_torch_device_info,
    resolve_torch_device,
)


def test_model_options_include_recommended_downloadable_default() -> None:
    option = get_model_option("grafj-conv-transformer-base")

    assert option.recommended is True
    assert option.repo_id == "Graf-J/captcha-conv-transformer-base"
    assert option.backend == "transformers_pipeline"
    assert option.trust_remote_code is True
    assert option.revision == "1896f25517e3e9c2905db37863bc18e774759646"


def test_model_options_include_heavier_fallback_candidate() -> None:
    option = get_model_option("anuashok-trocr-v3")

    assert option.backend == "trocr"
    assert option.license == "apache-2.0"


def test_unknown_model_option_lists_available_options() -> None:
    with pytest.raises(ValueError, match="available:"):
        get_model_option("missing")


def test_resolve_torch_device_accepts_cpu() -> None:
    assert resolve_torch_device("cpu") == ("cpu", -1)


def test_resolve_torch_device_auto_detects_rocm(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_torch = SimpleNamespace(
        __version__="2.9.1+rocm7.2",
        version=SimpleNamespace(hip="7.2.0", cuda=None),
        cuda=SimpleNamespace(
            is_available=lambda: True,
            get_device_name=lambda index: f"AMD Radeon {index}",
        ),
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)

    info = resolve_torch_device_info("auto")

    assert resolve_torch_device("auto") == ("cuda:0", 0)
    assert info.torch_device == "cuda:0"
    assert info.pipeline_device == 0
    assert info.accelerator == "rocm"
    assert info.hip_version == "7.2.0"
    assert info.device_name == "AMD Radeon 0"


def test_resolve_torch_device_auto_falls_back_to_cpu(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_torch = SimpleNamespace(
        __version__="2.9.1+cu128",
        version=SimpleNamespace(hip=None, cuda="12.8"),
        cuda=SimpleNamespace(is_available=lambda: False),
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)

    info = resolve_torch_device_info("auto")

    assert info.torch_device == "cpu"
    assert info.pipeline_device == -1
    assert info.accelerator == "cpu"
    assert info.cuda_version == "12.8"


def test_extract_prediction_accepts_pipeline_prediction_shape() -> None:
    text, confidence = _extract_prediction({"prediction": "W9H5K", "score": 0.91})

    assert text == "W9H5K"
    assert confidence == 0.91


def test_extract_prediction_accepts_trocr_shape() -> None:
    text, confidence = _extract_prediction([{"generated_text": " W9H5K "}])

    assert text == " W9H5K "
    assert confidence == 0.0


def test_only_one_recommended_model_for_default_local_dev() -> None:
    recommended = [option for option in MODEL_OPTIONS.values() if option.recommended]

    assert [option.id for option in recommended] == ["grafj-conv-transformer-base"]


def test_research_only_model_is_excluded_from_runnable_options() -> None:
    assert "xiaolv-ocr-captcha" in MODEL_OPTIONS
    assert "xiaolv-ocr-captcha" not in RUNNABLE_MODEL_OPTIONS


def test_remote_code_model_requires_explicit_opt_in() -> None:
    with pytest.raises(RuntimeError, match="requires trusted remote code"):
        LocalMLCaptchaOCRSolver(LocalOCRConfig(model_id="grafj-crnn-base"))


def test_research_only_model_cannot_be_instantiated() -> None:
    with pytest.raises(NotImplementedError, match="research"):
        LocalMLCaptchaOCRSolver(LocalOCRConfig(model_id="xiaolv-ocr-captcha"))
