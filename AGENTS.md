# OpenSesame Agent Guide

## Project overview

`OpenSesame` is an async-native self-hosted captcha/token-solving microservice
with no paid solver APIs. It handles direct-answer captchas by producing answer
candidates and browser/session-bound captchas by acting inside the live session
that hit the challenge.

## Read first

- this file
- `docs/ocr-test-sites.md` when working on OCR targets
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

```bash
PYTHONPATH=src python -m pytest
```

## Repository map

- `src/open_sesame/contracts.py` — shared solver result contracts
- `src/open_sesame/solvers/` — solver implementations
- `src/open_sesame/harness/` — target registry and future runner/scoreboard
- `docs/` — durable implementation notes and target research
- `tests/` — focused unit tests

## Architecture and constraints

- Direct-answer solvers return `SolveResult(kind="answer")`; they do not mint
  or fake browser-bound tokens.
- Session-bound captcha work must preserve the live browser/session invariant:
  the useful token is minted in the same session that encountered the wall.
- Browser/session harnesses should use `VoidCrawl`; HTTP fetches should use
  async `httpx` clients.
- `CAS-170` should stay a generalist OCR path. Do not create one model per site
  unless an active-learning loop explicitly gates that exception.

If the repo grows complex enough, split deeper architecture notes into dedicated committed docs and link them from here.

## Validation

Run `PYTHONPATH=src python -m pytest` for routine Python changes.

If the repo develops a substantial testing strategy, split the details into a dedicated committed testing document and link it from here.

## Checkout-local capability

Private per-clone capabilities belong in ignored checkout files such as:

```text
.local/
AGENTS.local.md
CLAUDE.local.md
```

Use checkout-local files for private tools, skills, prompts, and experiments. Promote them into committed project files when they become durable repo truth useful to collaborators.
