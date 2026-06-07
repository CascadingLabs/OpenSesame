# OpenSesame Agent Guide

## Project overview

`OpenSesame` — Self-hosted captcha/token-solving microservice with no paid solver APIs.

TODO: Replace this section with the repo's actual purpose, major responsibilities, and domain vocabulary.

## Read first

- this file
- relevant package or module README files
- nested `AGENTS.md` files in the area being changed, if present

## Working rules

- Keep diffs focused.
- Do not rewrite unrelated code.
- Add or update tests for behavior changes.
- Prefer the smallest relevant validation loop first.
- Do not touch secrets, deployment config, or migrations unless the task explicitly requires it.
- Put durable repo truth in committed project docs; keep checkout-local experiments in `.local/` until they are ready to promote.

## Key commands

TODO: Add the canonical commands for setup, formatting, linting, type checking, tests, and build/release workflows.

## Repository map

TODO: Replace with the actual top-level structure and important directories.

## Architecture and constraints

TODO: Record the important boundaries, invariants, integrations, and change-sensitive areas agents must preserve.

If the repo grows complex enough, split deeper architecture notes into dedicated committed docs and link them from here.

## Validation

TODO: Add the smallest useful checks for routine changes and the broader checks expected before merge or release.

If the repo develops a substantial testing strategy, split the details into a dedicated committed testing document and link it from here.

## Checkout-local capability

Private per-clone capabilities belong in ignored checkout files such as:

```text
.local/
AGENTS.local.md
CLAUDE.local.md
```

Use checkout-local files for private tools, skills, prompts, and experiments. Promote them into committed project files when they become durable repo truth useful to collaborators.
