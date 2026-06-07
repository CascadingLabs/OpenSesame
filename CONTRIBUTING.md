# Contributing to OpenSesame

Thanks for your interest! This guide covers how to get set up and what we expect from pull requests.

## Clone & Setup

```bash
git clone https://github.com/CascadingLabs/OpenSesame.git
cd OpenSesame
# <project-specific install steps go here>
```

## Install pre-commit hooks

```bash
uvx prek install
```

[Prek](https://github.com/thesuperzapper/prek) is a Rust-based pre-commit runner. Hooks run on every `git commit` and block the commit if anything fails. To run them manually:

```bash
uvx prek run --all-files
```

## Issues

Pick the [template](https://github.com/CascadingLabs/OpenSesame/issues/new/choose) that fits: Bug Report, Feature Request, Question, or Ticket. Blank issues are disabled.

## Pull Request Rules

1. **Branch from `main`** — use `feat/...`, `fix/...`, `docs/...`.
2. **Keep PRs focused** — one logical change per PR.
3. **Pass CI** — lint and tests must succeed.
4. **Fill in the PR template** — Intent, Changes, GenAI Usage, Risks.
5. **Link an issue** — reference it with `Closes #<number>`.

### Commit Conventions

Follow [Conventional Commits](https://www.conventionalcommits.org/):

```
feat: add X
fix: correct Y
docs: explain Z
```

## License

Contributions are licensed under Apache-2.0, matching the project.
