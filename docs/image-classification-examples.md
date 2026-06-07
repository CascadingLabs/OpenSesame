# Hugging Face Image Classification Examples

These examples are for image-group and coordinate-style CAPTCHA experiments, not
for standard distorted-text OCR. Standard image text CAPTCHAs should keep using
the local OCR model path.

## Single Image

Use zero-shot image classification when the target label comes from the CAPTCHA
prompt:

```bash
PYTHONPATH=src python examples/classify_image.py image.jpg --labels "bus,crosswalk,traffic light" --model openai/clip-vit-base-patch32
```

Use normal image classification when the model already has the right fixed label
space:

```bash
PYTHONPATH=src python examples/classify_image.py image.jpg --task image-classification --model google/vit-base-patch16-224
```

## Image Grid

For image-group CAPTCHAs, split the challenge grid into tiles and classify each
tile against the prompt labels:

```bash
PYTHONPATH=src python examples/classify_image_grid.py grid.jpg --rows 3 --cols 3 --labels "bus,crosswalk,traffic light"
```

The command prints row/column, best label, and score. A later session actor can
map those row/column indexes back to VoidCrawl click coordinates in the live
browser session.

## Sources

- Hugging Face image classification task docs:
  https://huggingface.co/docs/transformers/main/tasks/image_classification
- Hugging Face zero-shot image classification task docs:
  https://huggingface.co/docs/transformers/main/tasks/zero_shot_image_classification
- Hugging Face pipeline API docs:
  https://huggingface.co/docs/transformers/main_classes/pipelines
