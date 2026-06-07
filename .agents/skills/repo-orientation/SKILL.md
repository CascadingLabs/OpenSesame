---
name: repo-orientation
description: Use when starting work in this repository, mapping the codebase, or deciding which committed project docs and modules to inspect first.
---

# Repo Orientation

Goal: build a correct repo map from committed project truth before making changes.

## Read first

1. `AGENTS.md`
2. relevant package or module README files
3. nested `AGENTS.md` files in the area being changed, if present
4. any deeper committed docs linked from `AGENTS.md`

## Method

1. Restate what the repo does and the area relevant to the task.
2. Identify the likely files, modules, commands, and invariants involved.
3. Call out uncertainty rather than guessing past missing project documentation.
4. Use checkout-local notes only when working in a checkout that explicitly provides them.

## Output

- repo summary
- relevant areas
- important commands
- constraints or hazards
- open questions before editing
