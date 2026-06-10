# reCAPTCHA v2 grid — vision models & datasets

Tile selection on the v2 image grid is bottlenecked by classifier **recall** on
adversarially-noised tiles, not by click accuracy or the round loop. This note
records the models and datasets evaluated for that classifier, with measured
results, so CAS-174 (solver) and CAS-193 (retraining loop) build on evidence
rather than guesses.

## Measured A/B (one saved 2captcha v2-enterprise grid, 3 true "bus" tiles)

Same 3x3 crop, target = `bus`, ground truth = `{(0,0),(0,1),(1,2)}`. Run on CPU
via the `.local/venvs/rocm` interpreter.

| Config | Model | TP | FP | FN | Notes |
|---|---|---|---|---|---|
| zero-shot baseline | `openai/clip-vit-base-patch32` | 1 | 0 | 2 | only the high-confidence bus |
| zero-shot + prompt-ensemble | clip-base + `DEFAULT_HYPOTHESIS_TEMPLATES` | 2 | 0 | 1 | doubles recall, 100% precision |
| zero-shot + denoise (majority) | clip-base, `--augmentations denoise --min-consensus 0.5` | 2 | 0 | 1 | recovers weak tiles; needs majority vote |
| **supervised** | **`verytuffcat/recaptcha`** | **3** | **0** | **0** | all three at 0.999 incl. the occluded bus |

Takeaway: prompt-ensembling and denoise are cheap zero-shot recall boosters and
the right default when no fine-tuned model is loaded, but a model **trained on
the reCAPTCHA tile distribution** clears the residual capability ceiling that no
amount of CLIP-base threshold tuning can. This is CAS-174's "fork the vision
brain" thesis, confirmed.

## Offline scoreboard (ground-truth labeled tiles)

Live challenges expose no ground truth, so `examples/eval_recaptcha_tiles.py`
scores models against `nobodyPerfecZ/recaptchav2-29k` (100x100 tiles, multi-hot
over `bicycle,bus,car,crosswalk,hydrant`). Run on the `test` split, 250 tiles,
micro-averaged at each model's best threshold:

| Model | task | micro F1 | P | R | car recall |
|---|---|---|---|---|---|
| `nobodyPerfecZ/vit-finetuned-…recaptchav2-v1` | image-cls | **0.922** | 0.985 | 0.867 | 0.647 |
| `verytuffcat/recaptcha` | image-cls | 0.858 | 0.975 | 0.766 | 0.382 |
| `openai/clip-vit-base-patch32` | zero-shot | 0.787 | 0.870 | 0.717 | 0.245 |

Findings that change the design:

- **Threshold barely matters.** Micro-F1 is flat (0.854–0.858) from 0.1→0.5 for
  verytuffcat; missed tiles score near *zero*, not just under 0.40. Lowering the
  gate does not recover them — earlier live-challenge threshold tuning was a
  dead end. Keep `min_target_score` ≈ 0.30.
- **`car` is the hard class for everyone** — even the in-domain companion model
  caps at 0.647 recall (partial/distant cars, label noise). Not a tuning gap.
- **Per-class model routing beats a single model.** `nobodyPerfecZ` wins on its
  5 classes (car 0.647 vs verytuffcat 0.382); `verytuffcat` is the only option
  for the other 8 (motorcycle, traffic light, stair, bridge, …). A 2-model
  union did **not** beat `nobodyPerfecZ` alone on the shared classes.
- Recommended policy: route the OCR'd target to `nobodyPerfecZ` when it is one
  of `{bicycle,bus,car,crosswalk,hydrant}`, else `verytuffcat`.

## Models (Hugging Face)

- **`verytuffcat/recaptcha`** — ViT image-classification head, 13 classes:
  `bicycle, bridge, bus, car, chimney, crosswalk, hydrant, motorcycle,
  mountain, other, palm, stair, traffic light`. The `other` class is a native
  negative for the contrastive vote. ~343 MB safetensors. **Current best fit.**
  Wire it via `--models verytuffcat/recaptcha --classifier-task image-classification`.
- **`nobodyPerfecZ/vit-finetuned-patch16-224-recaptchav2-v1`** — ViT, narrower
  5-class head (`bicycle, bus, car, crosswalk, hydrant`). Use when the target is
  in-set and you want a smaller label space.
- **`DannyLuna/recaptcha-classification-57k`** — the model CAS-174 names (ONNX
  113 MB + PyTorch 57 MB, 14 classes). Object-classification, not a HF pipeline;
  would need an ONNXRuntime adapter. Heavier integration than the ViTs above.
- **SigLIP / SigLIP2** (`google/siglip2-base-patch16-224`, …) — stronger
  *zero-shot* successors to CLIP; the drop-in upgrade when a fine-tuned head is
  unavailable for a target class. Same `zero-shot-image-classification` task.

## Datasets (for offline eval + CAS-193 retraining)

Labeled reCAPTCHA tile corpora — let us measure recall/precision offline,
without hammering (and getting rate-limited by) the live demo:

- **`nobodyPerfecZ/recaptchav2-29k`** — parquet with `train/validation/test`
  splits. Ready-made eval harness input.
- **`verytuffcat/recaptcha-dataset`** — per-class image folders
  (`data/train/<class>/...`), matches the `verytuffcat/recaptcha` label set.
- **`DannyLuna/recaptcha-57k-images-dataset`** / `tokavaliauskas/...` — 57k
  images backing the DannyLuna model.
- **`aplesner-eth/reCAPTCHAv2`**, `alessiodecastro/reCaptchav2` — additional
  held-out sets for cross-source generalization checks.

Pull example (metadata only; the 57k zip is ~1.4 GB — fetch deliberately):

```bash
.local/venvs/rocm/bin/python -c "from huggingface_hub import snapshot_download; \
  snapshot_download('nobodyPerfecZ/recaptchav2-29k', repo_type='dataset', cache_dir='.local/hf')"
```

## Open follow-ups

- **Done:** offline recall/precision harness — `examples/eval_recaptcha_tiles.py`
  (install the `ml-eval` extra for `datasets`). Scoreboard above.
- Implement the per-class model router in the grid actor (target →
  nobodyPerfecZ vs verytuffcat) per the scoreboard finding.
- Adapter for `DannyLuna/...` ONNX if the ViT label set proves too narrow for a
  live target class.
- Confidence gate → noVNC (CAS-175) when even the supervised model is uncertain.
- Cars: investigate whether the 4x4 spanning-object case (partial cars in edge
  tiles) overlaps the dataset's hard-car tiles; a detector (bounding box) may be
  the only lever left for that class.
