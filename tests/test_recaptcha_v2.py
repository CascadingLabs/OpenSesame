from __future__ import annotations

import io

import pytest

from open_sesame.harness.recaptcha_v2 import (
    BinaryTileVote,
    DENOISE_TILE_AUGMENTATIONS,
    HELPFUL_TILE_AUGMENTATIONS,
    WidgetRect,
    TileDecision,
    aggregate_binary_tile_votes,
    ax_nodes_mention_rate_limit,
    binary_vote_from_scores,
    build_recaptcha_research_report,
    click_tile_decisions,
    crop_page_screenshot,
    discover_recaptcha_failure_examples,
    export_recaptcha_failure_corpus,
    find_recaptcha_ax_control,
    human_mouse_path,
    infer_tile_grid_shape,
    inspect_tile_image_state,
    inspect_tile_visual_states,
    label_variants,
    parse_recaptcha_challenge_type,
    parse_widget_rect,
    parse_recaptcha_target_label,
    persist_recaptcha_attempt,
    recaptcha_audio_button_point,
    recaptcha_checkbox_point,
    recaptcha_challenge_rect_js,
    recaptcha_tile_grid_rect,
    resolve_local_hf_snapshot,
    rect_lookup_js,
    score_tiles_binary,
    text_mentions_rate_limit,
    click_like_human,
)


def test_parse_widget_rect_accepts_numeric_mapping() -> None:
    rect = parse_widget_rect({"x": 1, "y": "2", "width": 300.5, "height": 78})

    assert rect == WidgetRect(x=1.0, y=2.0, width=300.5, height=78.0)


def test_parse_widget_rect_rejects_missing_shape() -> None:
    assert parse_widget_rect({"x": 1, "y": 2}) is None
    assert parse_widget_rect(None) is None


def test_checkbox_point_is_inside_standard_anchor_rect() -> None:
    rect = WidgetRect(x=10, y=20, width=304, height=78)
    x, y = recaptcha_checkbox_point(rect, seed=1)

    assert 35 <= x <= 40
    assert 57 <= y <= 62


def test_recaptcha_tile_grid_rect_estimates_square_grid() -> None:
    grid = recaptcha_tile_grid_rect(WidgetRect(x=80, y=10, width=404, height=582))

    assert grid.x > 80
    assert 100 <= grid.y <= 140
    assert grid.width == grid.height
    assert grid.tile_center(0, 0)[0] < grid.tile_center(0, 1)[0]


def test_recaptcha_audio_button_point_targets_bottom_control_row() -> None:
    rect = WidgetRect(x=80, y=10, width=404, height=582)

    x, y = recaptcha_audio_button_point(rect)

    assert x == 156
    assert y == 561


def test_ax_nodes_mention_rate_limit_matches_block_message() -> None:
    nodes = [
        {"role": "button", "name": "Get an audio challenge"},
        {"role": "heading", "name": {"value": "Try again later"}},
    ]

    assert ax_nodes_mention_rate_limit(nodes)


def test_ax_nodes_mention_rate_limit_matches_automated_queries_text() -> None:
    nodes = [
        {
            "role": "text",
            "name": "Your computer or network may be sending automated queries.",
        }
    ]

    assert ax_nodes_mention_rate_limit(nodes)


def test_ax_nodes_mention_rate_limit_ignores_normal_challenge() -> None:
    nodes = [
        {"role": "button", "name": "Get an audio challenge"},
        {"role": "link", "name": "Alternatively, download audio as MP3"},
        "not-a-node",
    ]

    assert not ax_nodes_mention_rate_limit(nodes)
    assert not ax_nodes_mention_rate_limit(None)


def test_parse_recaptcha_challenge_type_detects_dynamic_refill() -> None:
    prompt = "Select all images with\nbuses\nClick verify once there are none left"

    assert parse_recaptcha_challenge_type(prompt) == "dynamic"


def test_parse_recaptcha_challenge_type_detects_skip_grid() -> None:
    prompt = "Select all squares with\nmotorcycles\nIf there are none, click skip"

    assert parse_recaptcha_challenge_type(prompt) == "skip"


def test_parse_recaptcha_challenge_type_detects_one_shot() -> None:
    assert parse_recaptcha_challenge_type("Select all images with traffic lights") == "one_shot"


def test_parse_recaptcha_challenge_type_handles_empty_and_unknown() -> None:
    assert parse_recaptcha_challenge_type("") == "unknown"
    assert parse_recaptcha_challenge_type("Please verify you are human") == "unknown"


def test_text_mentions_rate_limit_matches_ocr_output() -> None:
    ocr_text = "Red Try again later\nsending automated queries. To"

    assert text_mentions_rate_limit(ocr_text)
    assert text_mentions_rate_limit("Your computer may be sending\n automated  queries")
    assert not text_mentions_rate_limit("Select all images with buses")


def test_infer_tile_grid_shape_detects_four_by_four(tmp_path) -> None:
    pillow = pytest.importorskip("PIL.Image")
    image_path = tmp_path / "grid.png"
    image = pillow.new("RGB", (400, 400), "green")
    for pos in (100, 200, 300):
        for offset in (-1, 0, 1):
            for index in range(400):
                image.putpixel((pos + offset, index), (255, 255, 255))
                image.putpixel((index, pos + offset), (255, 255, 255))
    image.save(image_path)

    assert infer_tile_grid_shape(image_path) == (4, 4)


def test_infer_tile_grid_shape_detects_three_by_three(tmp_path) -> None:
    pillow = pytest.importorskip("PIL.Image")
    image_path = tmp_path / "grid.png"
    image = pillow.new("RGB", (300, 300), "green")
    for pos in (100, 200):
        for offset in (-1, 0, 1):
            for index in range(300):
                image.putpixel((pos + offset, index), (255, 255, 255))
                image.putpixel((index, pos + offset), (255, 255, 255))
    image.save(image_path)

    assert infer_tile_grid_shape(image_path) == (3, 3)


def test_resolve_local_hf_snapshot_uses_refs_main(tmp_path) -> None:
    snapshot = tmp_path / "hub" / "models--openai--clip-vit-base-patch32" / "snapshots" / "abc"
    snapshot.mkdir(parents=True)
    (snapshot / "config.json").write_text("{}", encoding="utf-8")
    refs = tmp_path / "hub" / "models--openai--clip-vit-base-patch32" / "refs"
    refs.mkdir()
    (refs / "main").write_text("abc", encoding="utf-8")

    assert resolve_local_hf_snapshot("openai/clip-vit-base-patch32", cache_dir=tmp_path) == snapshot


def test_inspect_tile_image_state_marks_white_overlay_inactive() -> None:
    pillow = pytest.importorskip("PIL.Image")
    image = pillow.new("RGB", (40, 40), "white")

    state = inspect_tile_image_state(image, row=0, col=0)

    assert not state.active
    assert state.reason == "selected_or_blank"


def test_inspect_tile_image_state_marks_pale_selected_overlay_inactive() -> None:
    pillow = pytest.importorskip("PIL.Image")
    image = pillow.new("RGB", (40, 40), (236, 236, 236))
    for index in range(40):
        image.putpixel((index, index), (220, 220, 220))

    state = inspect_tile_image_state(image, row=0, col=0)

    assert not state.active
    assert state.reason == "selected_or_blank"


def test_inspect_tile_image_state_marks_checkmark_overlay_inactive() -> None:
    pillow = pytest.importorskip("PIL.Image")
    image = pillow.new("RGB", (40, 40), (70, 70, 70))
    for x in range(4, 36):
        for y in range(6, 34):
            if (x + y) % 2 == 0:
                image.putpixel((x, y), (255, 255, 255))

    state = inspect_tile_image_state(image, row=0, col=0)

    assert not state.active
    assert state.reason == "selected_or_blank"


def test_inspect_tile_visual_states_marks_active_and_inactive_tiles(tmp_path) -> None:
    pillow = pytest.importorskip("PIL.Image")
    image = pillow.new("RGB", (80, 40), "white")
    for x in range(40, 80):
        for y in range(40):
            image.putpixel((x, y), (0, (x * 5) % 255, (y * 7) % 255))
    path = tmp_path / "tiles.png"
    image.save(path)

    states = inspect_tile_visual_states(path, rows=1, cols=2)

    assert [state.active for state in states] == [False, True]


def test_parse_recaptcha_target_label_from_tesseract_text() -> None:
    prompt = "Select all squares with\nbicycles\nIf there are none, click skip"

    assert parse_recaptcha_target_label(prompt) == "bicycles"


def test_parse_recaptcha_target_label_from_images_prompt() -> None:
    prompt = "Select all images with traffic lights"

    assert parse_recaptcha_target_label(prompt) == "traffic lights"


def test_parse_recaptcha_target_label_handles_joined_ocr_words() -> None:
    prompt = "Select allimages with a\nbus\nClick verify once there are none left."

    assert parse_recaptcha_target_label(prompt) == "bus"


def test_label_variants_adds_simple_singular_plural_forms() -> None:
    assert label_variants("crosswalks") == ("crosswalks", "crosswalk")
    assert label_variants("bus") == ("bus", "buses")
    assert label_variants("bicycles") == ("bicycles", "bicycle")


def test_aggregate_binary_tile_votes_requires_consensus() -> None:
    votes = (
        BinaryTileVote(
            row=0,
            col=0,
            model_id="a",
            target_label="bus",
            target_score=0.8,
            non_target_label="road",
            non_target_score=0.2,
        ),
        BinaryTileVote(
            row=0,
            col=0,
            model_id="b",
            target_label="bus",
            target_score=0.7,
            non_target_label="road",
            non_target_score=0.3,
        ),
        BinaryTileVote(
            row=0,
            col=1,
            model_id="a",
            target_label="bus",
            target_score=0.4,
            non_target_label="road",
            non_target_score=0.6,
        ),
        BinaryTileVote(
            row=0,
            col=1,
            model_id="b",
            target_label="bus",
            target_score=0.7,
            non_target_label="road",
            non_target_score=0.3,
        ),
    )

    decisions = aggregate_binary_tile_votes(votes, rows=1, cols=2, min_consensus=1.0)

    assert len(decisions) == 1
    assert decisions[0].row == 0
    assert decisions[0].col == 0
    assert decisions[0].target_votes == 2
    assert decisions[0].total_votes == 2


def test_aggregate_binary_tile_votes_can_filter_to_active_tiles() -> None:
    votes = (
        BinaryTileVote(
            row=0,
            col=0,
            model_id="a",
            target_label="bus",
            target_score=0.8,
            non_target_label="road",
            non_target_score=0.2,
        ),
        BinaryTileVote(
            row=0,
            col=1,
            model_id="a",
            target_label="bus",
            target_score=0.8,
            non_target_label="road",
            non_target_score=0.2,
        ),
    )
    active_only = tuple(vote for vote in votes if (vote.row, vote.col) == (0, 1))

    decisions = aggregate_binary_tile_votes(active_only, rows=1, cols=2, min_consensus=1.0)

    assert [(decision.row, decision.col) for decision in decisions] == [(0, 1)]


def test_aggregate_binary_tile_votes_filters_weak_target_wins() -> None:
    votes = (
        BinaryTileVote(
            row=0,
            col=0,
            model_id="clip",
            target_label="traffic lights",
            target_score=0.34,
            non_target_label="crosswalk",
            non_target_score=0.22,
        ),
        BinaryTileVote(
            row=0,
            col=1,
            model_id="clip",
            target_label="traffic lights",
            target_score=0.72,
            non_target_label="crosswalk",
            non_target_score=0.03,
        ),
    )

    decisions = aggregate_binary_tile_votes(votes, rows=1, cols=2)

    assert [(decision.row, decision.col) for decision in decisions] == [(0, 1)]


def test_score_tiles_binary_passes_device_to_classifier(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pillow = pytest.importorskip("PIL.Image")
    image_path = tmp_path / "grid.png"
    pillow.new("RGB", (20, 20), "green").save(image_path)
    captured_devices: list[str] = []

    class FakeClassifier:
        def __init__(self, config) -> None:
            captured_devices.append(config.device)

        def classify(self, image_path) -> list[dict[str, object]]:
            return [
                {"label": "bus", "score": 0.8},
                {"label": "road", "score": 0.2},
            ]

    monkeypatch.setattr(
        "open_sesame.harness.recaptcha_v2.HuggingFaceImageClassifier",
        FakeClassifier,
    )

    votes = score_tiles_binary(
        image_path,
        target_label="bus",
        candidate_labels=("bus", "road"),
        model_id="fake-model",
        rows=1,
        cols=1,
        device="cuda:0",
        cache_dir=tmp_path,
    )

    assert captured_devices == ["cuda:0"]
    assert votes[0].votes_target is True


def test_score_tiles_binary_without_augmentations_scores_one_vote_per_active_tile(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pillow = pytest.importorskip("PIL.Image")
    image_path = tmp_path / "grid.png"
    pillow.new("RGB", (20, 10), "white").save(image_path)
    seen_paths: list[str] = []

    class FakeClassifier:
        def __init__(self, _config: object) -> None:
            pass

        def classify(self, path: object) -> list[dict[str, object]]:
            seen_paths.append(str(path))
            return [
                {"label": "bus", "score": 0.82},
                {"label": "road", "score": 0.18},
            ]

    monkeypatch.setattr(
        "open_sesame.harness.recaptcha_v2.HuggingFaceImageClassifier",
        FakeClassifier,
    )

    votes = score_tiles_binary(
        image_path,
        target_label="bus",
        candidate_labels=("bus", "road"),
        model_id="clip",
        rows=1,
        cols=2,
        cache_dir=tmp_path / "hf",
        active_tiles=((0, 1),),
        augmentation_preset="none",
    )

    assert len(votes) == 1
    assert len(seen_paths) == 1
    assert votes[0].row == 0
    assert votes[0].col == 1
    assert votes[0].augmentation_id == "identity"
    assert votes[0].source_tile_path == votes[0].augmented_tile_path


def test_score_tiles_binary_helpful_augmentations_are_votes_on_active_tiles(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pillow = pytest.importorskip("PIL.Image")
    image_path = tmp_path / "grid.png"
    pillow.new("RGB", (20, 10), "gray").save(image_path)
    seen_paths: list[str] = []

    class FakeClassifier:
        def __init__(self, _config: object) -> None:
            pass

        def classify(self, path: object) -> list[dict[str, object]]:
            seen_paths.append(str(path))
            return [
                {"label": "bus", "score": 0.82},
                {"label": "road", "score": 0.18},
            ]

    monkeypatch.setattr(
        "open_sesame.harness.recaptcha_v2.HuggingFaceImageClassifier",
        FakeClassifier,
    )

    votes = score_tiles_binary(
        image_path,
        target_label="bus",
        candidate_labels=("bus", "road"),
        model_id="clip",
        rows=1,
        cols=2,
        cache_dir=tmp_path / "hf",
        active_tiles=((0, 1),),
        augmentation_preset="helpful",
    )

    assert len(votes) == len(HELPFUL_TILE_AUGMENTATIONS)
    assert len(seen_paths) == len(HELPFUL_TILE_AUGMENTATIONS)
    assert [vote.augmentation_id for vote in votes] == list(HELPFUL_TILE_AUGMENTATIONS)
    assert {(vote.row, vote.col) for vote in votes} == {(0, 1)}
    assert all(vote.source_tile_path for vote in votes)
    assert all(vote.augmented_tile_path for vote in votes)


def test_score_tiles_binary_denoise_augmentations_emit_median_votes(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pillow = pytest.importorskip("PIL.Image")
    image_path = tmp_path / "grid.png"
    pillow.new("RGB", (20, 10), "gray").save(image_path)

    class FakeClassifier:
        def __init__(self, _config: object) -> None:
            pass

        def classify(self, _path: object) -> list[dict[str, object]]:
            return [
                {"label": "bus", "score": 0.7},
                {"label": "road", "score": 0.3},
            ]

    monkeypatch.setattr(
        "open_sesame.harness.recaptcha_v2.HuggingFaceImageClassifier",
        FakeClassifier,
    )

    votes = score_tiles_binary(
        image_path,
        target_label="bus",
        candidate_labels=("bus", "road"),
        model_id="clip",
        rows=1,
        cols=2,
        cache_dir=tmp_path / "hf",
        active_tiles=((0, 1),),
        augmentation_preset="denoise",
    )

    assert [vote.augmentation_id for vote in votes] == list(DENOISE_TILE_AUGMENTATIONS)


def test_score_tiles_binary_supervised_task_uses_model_distribution(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pillow = pytest.importorskip("PIL.Image")
    image_path = tmp_path / "grid.png"
    pillow.new("RGB", (10, 10), "green").save(image_path)
    captured: dict[str, object] = {}

    class FakeClassifier:
        def __init__(self, config) -> None:
            captured["task"] = config.task
            captured["candidate_labels"] = config.candidate_labels

        def classify(self, _path: object) -> list[dict[str, object]]:
            # A supervised reCAPTCHA head: full label distribution, no candidates.
            return [
                {"label": "bus", "score": 0.97},
                {"label": "car", "score": 0.02},
                {"label": "other", "score": 0.01},
            ]

    monkeypatch.setattr(
        "open_sesame.harness.recaptcha_v2.HuggingFaceImageClassifier",
        FakeClassifier,
    )

    votes = score_tiles_binary(
        image_path,
        target_label="bus",
        candidate_labels=(),
        model_id="verytuffcat/recaptcha",
        rows=1,
        cols=1,
        cache_dir=tmp_path / "hf",
        task="image-classification",
    )

    assert captured["task"] == "image-classification"
    assert captured["candidate_labels"] == ()  # supervised head: no candidates
    assert len(votes) == 1
    vote = votes[0]
    assert vote.votes_target is True
    assert vote.target_label == "bus"
    assert vote.target_score == pytest.approx(0.97)
    assert vote.non_target_label == "car"  # best competing class
    assert vote.non_target_score == pytest.approx(0.02)


def test_binary_vote_from_scores_handles_only_target_label() -> None:
    vote = binary_vote_from_scores(
        {"bus": 0.6},
        row=2,
        col=1,
        model_id="m",
        target_labels=("bus", "buses"),
        augmentation_id="identity",
        source_tile_path="s.png",
        augmented_tile_path="a.png",
    )

    assert vote.target_score == pytest.approx(0.6)
    assert vote.non_target_label == "not bus"
    assert vote.non_target_score == 0.0
    assert vote.votes_target is True


def test_score_tiles_binary_passes_hypothesis_templates_to_classifier(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pillow = pytest.importorskip("PIL.Image")
    image_path = tmp_path / "grid.png"
    pillow.new("RGB", (10, 10), "green").save(image_path)
    captured: list[tuple[str, ...]] = []

    class FakeClassifier:
        def __init__(self, config) -> None:
            captured.append(config.hypothesis_templates)

        def classify(self, _path: object) -> list[dict[str, object]]:
            return [{"label": "bus", "score": 0.9}, {"label": "road", "score": 0.1}]

    monkeypatch.setattr(
        "open_sesame.harness.recaptcha_v2.HuggingFaceImageClassifier",
        FakeClassifier,
    )

    score_tiles_binary(
        image_path,
        target_label="bus",
        candidate_labels=("bus", "road"),
        model_id="clip",
        rows=1,
        cols=1,
        cache_dir=tmp_path / "hf",
        hypothesis_templates=("a photo of a {}.", "a blurry photo of a {}."),
    )

    assert captured == [("a photo of a {}.", "a blurry photo of a {}.")]


def test_aggregate_binary_tile_votes_groups_augmented_votes_by_tile() -> None:
    votes = (
        BinaryTileVote(
            row=0,
            col=0,
            model_id="clip",
            target_label="bus",
            target_score=0.8,
            non_target_label="road",
            non_target_score=0.2,
            augmentation_id="identity",
        ),
        BinaryTileVote(
            row=0,
            col=0,
            model_id="clip",
            target_label="bus",
            target_score=0.7,
            non_target_label="road",
            non_target_score=0.3,
            augmentation_id="contrast_1_25",
        ),
    )

    decisions = aggregate_binary_tile_votes(votes, rows=1, cols=1)

    assert len(decisions) == 1
    assert decisions[0].target_votes == 2
    assert decisions[0].total_votes == 2
    assert [vote.augmentation_id for vote in decisions[0].votes] == [
        "identity",
        "contrast_1_25",
    ]


def test_binary_tile_vote_serializes_augmentation_metadata() -> None:
    vote = BinaryTileVote(
        row=1,
        col=2,
        model_id="clip",
        target_label="bus",
        target_score=0.8,
        non_target_label="road",
        non_target_score=0.2,
        augmentation_id="sharpness_1_5",
        source_tile_path="tile.png",
        augmented_tile_path="tile--sharpness_1_5.png",
    )

    payload = vote.as_dict()

    assert payload["augmentation_id"] == "sharpness_1_5"
    assert payload["source_tile_path"] == "tile.png"
    assert payload["augmented_tile_path"] == "tile--sharpness_1_5.png"


def test_persist_recaptcha_attempt_copies_artifacts_and_writes_index(tmp_path) -> None:
    image = tmp_path / "challenge.png"
    image.write_bytes(b"fake")
    round_image = tmp_path / "round.png"
    round_image.write_bytes(b"round")
    payload = {
        "state": {
            "challenge_image_path": str(image),
            "token_present": False,
        },
        "rounds": [
            {
                "round": 1,
                "challenge_image_path": str(round_image),
            }
        ],
        "post_verify": {
            "token_present": False,
        },
    }

    metadata = persist_recaptcha_attempt(payload, audit_dir=tmp_path / "runs")
    record = __import__("json").loads(metadata.read_text(encoding="utf-8"))

    assert metadata.exists()
    assert metadata.parent.name.endswith("-failure")
    assert (metadata.parent / "challenge_image.png").exists()
    assert (metadata.parent / "round_1_challenge.png").exists()
    assert record["review"]["status"] == "unreviewed"
    assert (tmp_path / "runs" / "attempts.jsonl").exists()


def test_persist_recaptcha_attempt_copies_audio_artifacts(tmp_path) -> None:
    screenshot = tmp_path / "audio.png"
    screenshot.write_bytes(b"audio screenshot")
    audio = tmp_path / "challenge.mp3"
    audio.write_bytes(b"mp3")
    payload = {
        "state": {
            "token_present": False,
        },
        "audio_challenge": {
            "clicked": True,
            "click_method": "ax",
            "screenshot_path": str(screenshot),
            "download_path": str(audio),
        },
        "post_verify": {
            "token_present": False,
        },
    }

    metadata = persist_recaptcha_attempt(payload, audit_dir=tmp_path / "runs")
    record = __import__("json").loads(metadata.read_text(encoding="utf-8"))

    assert (metadata.parent / "audio_challenge_screenshot.png").read_bytes() == b"audio screenshot"
    assert (metadata.parent / "audio_challenge_download.mp3").read_bytes() == b"mp3"
    assert record["artifacts"]["audio_challenge_screenshot"].endswith("audio_challenge_screenshot.png")
    assert record["artifacts"]["audio_challenge_download"].endswith("audio_challenge_download.mp3")


def test_discover_recaptcha_failure_examples_reads_audit_index(tmp_path) -> None:
    run_dir = tmp_path / "runs" / "20260609T010101.000000Z-failure"
    run_dir.mkdir(parents=True)
    challenge = run_dir / "challenge_image.png"
    challenge.write_bytes(b"challenge")
    prompt = run_dir / "prompt_image.png"
    prompt.write_bytes(b"prompt")
    metadata = run_dir / "metadata.json"
    metadata.write_text(
        __import__("json").dumps(
            {
                "created_at": "20260609T010101.000000Z",
                "outcome": "failure",
                "artifacts": {
                    "challenge_image": str(challenge),
                    "prompt_image": str(prompt),
                },
                "review": {"notes": "missed the bus tile"},
                "payload": {
                    "state": {
                        "target_label": "bus",
                        "prompt_text": "Select all images with buses",
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "runs" / "attempts.jsonl").write_text(
        __import__("json").dumps({"path": str(metadata)}) + "\n",
        encoding="utf-8",
    )

    examples = discover_recaptcha_failure_examples(tmp_path / "runs")

    assert len(examples) == 1
    assert examples[0].example_id == "20260609T010101.000000Z"
    assert examples[0].challenge_image_path == challenge.resolve()
    assert examples[0].prompt_image_path == prompt.resolve()
    assert examples[0].target_label == "bus"
    assert examples[0].notes == "missed the bus tile"


def test_export_recaptcha_failure_corpus_copies_artifacts_and_writes_index(tmp_path) -> None:
    image = tmp_path / "source-challenge.png"
    image.write_bytes(b"challenge")
    metadata = tmp_path / "runs" / "attempt-failure" / "metadata.json"
    metadata.parent.mkdir(parents=True)
    metadata.write_text(
        __import__("json").dumps(
            {
                "created_at": "20260609T020202.000000Z",
                "outcome": "failure",
                "artifacts": {"challenge_image": str(image)},
                "payload": {"state": {"target_label": "crosswalk"}},
            }
        ),
        encoding="utf-8",
    )

    exported = export_recaptcha_failure_corpus(
        audit_dir=tmp_path / "runs",
        corpus_dir=tmp_path / "corpus",
    )

    assert len(exported) == 1
    assert exported[0].challenge_image_path is not None
    assert exported[0].challenge_image_path.read_bytes() == b"challenge"
    assert exported[0].metadata_path.exists()
    assert (tmp_path / "corpus" / "corpus.jsonl").exists()


def test_build_recaptcha_research_report_summarizes_live_attempt() -> None:
    payload = {
        "query": "weather new york",
        "target_url": "https://www.google.com/search?q=weather+new+york",
        "state": {
            "kind": "recaptcha_v2",
            "signals": ["captcha-recaptcha_v2", "challenge-frame-visible"],
            "click_trace": {"target_x": 42, "target_y": 50},
            "challenge_rect": {"x": 10, "y": 20, "width": 400, "height": 580},
            "prompt_text": "Select all images with buses",
            "target_label": "buses",
            "screenshot_path": ".local/recaptcha/page.png",
            "challenge_image_path": ".local/recaptcha/grid.png",
            "prompt_image_path": ".local/recaptcha/prompt.png",
        },
        "page_metadata_before_tiles": {
            "url": "https://www.google.com/sorry/index",
            "title": "Sorry",
            "sitekey": "site-key",
            "anchor_src": "https://www.google.com/recaptcha/api2/anchor?k=site-key",
            "response_field_present": True,
            "token_present": False,
            "token_length": 0,
        },
        "rounds": [
            {
                "round": 1,
                "challenge_image_path": ".local/recaptcha/grid.png",
                "tile_states": [
                    {"row": 0, "col": 0, "active": True},
                    {"row": 0, "col": 1, "active": False},
                ],
                "ensemble_plan": [
                    {
                        "row": 0,
                        "col": 0,
                        "votes": [
                            {
                                "model_id": "openai/clip-vit-base-patch32",
                                "target_score": 0.72,
                            }
                        ],
                    }
                ],
                "tile_plan": [{"row": 0, "col": 0, "label": "bus"}],
                "tile_click_traces": [{"target_x": 100, "target_y": 120}],
            }
        ],
        "verify_click_trace": {"target_x": 300, "target_y": 500},
        "post_verify": {
            "token_present": True,
            "token_length": 128,
            "captcha_kind": None,
            "screenshot_path": ".local/recaptcha/post.png",
        },
        "page_metadata_after_verify": {
            "url": "https://www.google.com/search?q=weather+new+york",
            "title": "weather new york - Google Search",
            "sitekey": "site-key",
            "response_field_present": True,
            "token_present": True,
            "token_length": 128,
        },
        "audit_record_path": ".local/recaptcha-runs/metadata.json",
    }

    report = build_recaptcha_research_report(payload).as_dict()

    assert report["observed_system"]["captcha_kind"] == "recaptcha_v2"
    assert report["observed_system"]["sitekey"] == "site-key"
    assert report["observed_system"]["target_label"] == "buses"
    assert report["live_session"]["session_bound"] is True
    assert report["live_session"]["token_present_after"] is True
    assert report["vision"]["model_ids"] == ["openai/clip-vit-base-patch32"]
    assert report["vision"]["active_tiles"] == 1
    assert report["vision"]["planned_tiles"] == 1
    assert report["vision"]["clicked_tiles"] == 1
    assert report["actions"]["checkbox_clicked"] is True
    assert report["actions"]["verify_clicked"] is True
    assert report["artifacts"]["audit_record_path"] == ".local/recaptcha-runs/metadata.json"
    assert any("Session-bound invariant" in note for note in report["notes"])


def test_build_recaptcha_research_report_marks_inspection_without_token() -> None:
    report = build_recaptcha_research_report(
        {
            "state": {
                "kind": "recaptcha_v2",
                "signals": ["challenge-frame-visible"],
                "challenge_rect": {"x": 0, "y": 0, "width": 400, "height": 500},
            },
            "post_verify": {"token_present": False, "token_length": 0},
        }
    ).as_dict()

    assert report["live_session"]["token_present_after"] is False
    assert report["vision"]["rounds"] == []
    assert any("inspection/training evidence" in note for note in report["notes"])


def test_build_recaptcha_research_report_summarizes_audio_attempt() -> None:
    report = build_recaptcha_research_report(
        {
            "state": {
                "kind": "recaptcha_v2",
                "signals": ["challenge-frame-visible"],
                "challenge_rect": {"x": 0, "y": 0, "width": 400, "height": 500},
            },
            "audio_challenge": {
                "clicked": True,
                "click_method": "ax",
                "screenshot_path": ".local/recaptcha/audio.png",
                "download_path": ".local/recaptcha/audio.mp3",
            },
            "post_verify": {"token_present": False, "token_length": 0},
        }
    ).as_dict()

    assert report["actions"]["audio_clicked"] is True
    assert report["actions"]["audio_click_method"] == "ax"
    assert report["actions"]["audio_download_captured"] is True
    assert report["artifacts"]["audio_challenge_download_path"] == ".local/recaptcha/audio.mp3"
    assert any("audio challenge path" in note for note in report["notes"])


def test_human_mouse_path_is_deterministic_and_hits_endpoint() -> None:
    points = human_mouse_path((0, 0), (100, 50), steps=8, seed=4)

    assert len(points) == 8
    assert points == human_mouse_path((0, 0), (100, 50), steps=8, seed=4)
    assert points[0] == (0, 0)
    assert points[-1] == (100, 50)


def test_human_mouse_path_rejects_too_few_steps() -> None:
    with pytest.raises(ValueError, match="at least 2"):
        human_mouse_path((0, 0), (1, 1), steps=1)


def test_rect_lookup_js_contains_all_selectors() -> None:
    script = rect_lookup_js(("iframe[src*=recaptcha]", ".g-recaptcha"))

    assert "iframe[src*=recaptcha]" in script
    assert ".g-recaptcha" in script


def test_recaptcha_challenge_rect_js_scans_visible_iframes() -> None:
    script = recaptcha_challenge_rect_js()

    assert "querySelectorAll('iframe')" in script
    assert "recaptcha|challenge" in script
    assert "width * b.rect.height" in script


@pytest.mark.asyncio
async def test_find_recaptcha_ax_control_matches_wrapped_names() -> None:
    class FakePage:
        async def get_full_ax_tree(self) -> list[dict[str, object]]:
            return [
                {"role": {"value": "button"}, "name": {"value": "Get an audio challenge"}},
                {"role": {"value": "link"}, "name": {"value": "Download audio as MP3"}},
            ]

    button = await find_recaptcha_ax_control(FakePage(), roles=("button",), includes=("audio",))
    link = await find_recaptcha_ax_control(FakePage(), roles=("link",), includes=("download", "audio"))

    assert button == ("button", "Get an audio challenge")
    assert link == ("link", "Download audio as MP3")


@pytest.mark.asyncio
async def test_click_like_human_dispatches_move_press_release() -> None:
    class FakePage:
        def __init__(self) -> None:
            self.events: list[tuple[str, float, float]] = []

        async def dispatch_mouse_event(
            self,
            event_type: str,
            x: float,
            y: float,
            **_kwargs: object,
        ) -> None:
            self.events.append((event_type, x, y))

    page = FakePage()
    trace = await click_like_human(
        page,
        100,
        80,
        start=(0, 0),
        steps=4,
        hold_ms=0,
        move_delay=0,
    )

    assert trace.target_x == 100
    assert trace.target_y == 80
    assert [event[0] for event in page.events] == [
        "mouseMoved",
        "mouseMoved",
        "mouseMoved",
        "mouseMoved",
        "mousePressed",
        "mouseReleased",
    ]


@pytest.mark.asyncio
async def test_click_tile_decisions_dispatches_one_click_per_decision() -> None:
    class FakePage:
        def __init__(self) -> None:
            self.events: list[str] = []

        async def dispatch_mouse_event(self, event_type: str, *_args: object, **_kwargs: object) -> None:
            self.events.append(event_type)

    grid = recaptcha_tile_grid_rect(WidgetRect(x=80, y=10, width=404, height=582))
    decisions = (
        TileDecision(row=0, col=0, label="bicycle", score=0.9, click_x=0.1, click_y=0.1),
        TileDecision(row=1, col=2, label="bicycle", score=0.8, click_x=0.8, click_y=0.5),
    )
    traces = await click_tile_decisions(FakePage(), grid, decisions)

    assert len(traces) == 2
    assert sum(1 for trace in traces if trace.hold_ms == 55) == 2


@pytest.mark.asyncio
async def test_crop_page_screenshot_uses_device_pixel_ratio(tmp_path) -> None:
    pillow = pytest.importorskip("PIL.Image")

    class FakePage:
        async def eval_js(self, _script: str) -> float:
            return 2.0

        async def screenshot_png(self) -> bytes:
            image = pillow.new("RGB", (200, 160), "white")
            buf = io.BytesIO()
            image.save(buf, format="PNG")
            return buf.getvalue()

    output = await crop_page_screenshot(
        FakePage(),
        WidgetRect(x=10, y=20, width=30, height=40),
        tmp_path / "crop.png",
    )

    cropped = pillow.open(output)
    assert cropped.size == (60, 80)
