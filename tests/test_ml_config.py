from __future__ import annotations

import pytest

from open_sesame.solvers.local_ml import LocalMLCaptchaOCRSolver, _extract_prediction
from open_sesame.solvers.ml_config import (
    LocalOCRConfig,
    MODEL_OPTIONS,
    RUNNABLE_MODEL_OPTIONS,
    get_model_option,
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
