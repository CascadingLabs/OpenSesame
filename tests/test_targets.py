from open_sesame.harness.targets import DEFAULT_TARGETS, targets_for_family


def test_default_target_ids_are_unique() -> None:
    target_ids = [target.id for target in DEFAULT_TARGETS]

    assert len(target_ids) == len(set(target_ids))


def test_normal_ocr_targets_include_live_holdout_candidates() -> None:
    targets = targets_for_family("normal_ocr")

    assert {target.id for target in targets} >= {
        "2captcha-normal",
        "azcaptcha-image-text",
    }
    assert any(target.holdout_candidate for target in targets)


def test_synthetic_targets_are_not_marked_as_holdout() -> None:
    targets = targets_for_family("synthetic_ocr")

    assert targets
    assert all(not target.holdout_candidate for target in targets)
