from __future__ import annotations

import pytest
from pydantic import ValidationError

from open_sesame.api.policy import SolverPolicy, load_policy


def test_extra_forbid_rejects_typoed_key() -> None:
    with pytest.raises(ValidationError):
        SolverPolicy(allow_sties=["x"])  # typo: allow_sties


def test_auto_only_defaults() -> None:
    policy = SolverPolicy.auto_only(allow_sites=["www.google.com"])
    assert policy.escalate_on_fail is False
    assert policy.allows("www.google.com")
    assert not policy.allows("evil.test")


def test_default_deny_empty_allow_sites() -> None:
    assert SolverPolicy().allows("anything.com") is False


def test_merged_lists_replace_dicts_deep_merge() -> None:
    base = SolverPolicy(models={"recaptcha_v2": "a", "ocr": "b"}, allow_sites=["x"])
    merged = base.merged(models={"recaptcha_v2": "c"}, allow_sites=["y"])
    assert merged.models == {"recaptcha_v2": "c", "ocr": "b"}  # deep-merge
    assert merged.allow_sites == ("y",)                         # list replaces
    assert base.models == {"recaptcha_v2": "a", "ocr": "b"}     # frozen: original intact


def test_from_toml_and_host_precedence(tmp_path) -> None:
    (tmp_path / "global.toml").write_text(
        'allow_sites = ["a.com"]\nauto_timeout_s = 20\npolicy_id = "g"\n', encoding="utf-8"
    )
    host_dir = tmp_path / "hosts"
    host_dir.mkdir()
    (host_dir / "a.com.toml").write_text("auto_timeout_s = 5\n", encoding="utf-8")

    policy = load_policy(
        tmp_path / "global.toml", host_dir=host_dir, host="a.com", min_confidence=0.9
    )
    assert policy.allow_sites == ("a.com",)
    assert policy.auto_timeout_s == 5.0        # per-host file overrode global
    assert policy.min_confidence == 0.9        # per-call override wins
    assert policy.policy_id == "g"
