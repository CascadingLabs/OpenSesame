"""Regression tests for generated API source links."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import generate_api_docs


REPO_URL = "https://github.com/CascadingLabs/OpenSesame"


def _current_ref() -> str:
    return generate_api_docs._current_git_ref()


def test_validate_source_links_rejects_existing_file_with_bad_line() -> None:
    ref = _current_ref()
    content = (
        f'## `CandidateAnswer` <a href="{REPO_URL}/blob/{ref}/'
        'src/open_sesame/contracts.py#L1">bad</a>'
    )

    with pytest.raises(SystemExit, match="Source link line validation failed"):
        generate_api_docs._validate_source_links(content, REPO_URL, ref)


def test_validate_source_links_rejects_wrong_symbol_on_declaration_line() -> None:
    ref = _current_ref()
    content = (
        f'## `WrongName` <a href="{REPO_URL}/blob/{ref}/'
        'src/open_sesame/contracts.py#L10">bad</a>'
    )

    with pytest.raises(SystemExit, match="Source link line validation failed"):
        generate_api_docs._validate_source_links(content, REPO_URL, ref)


def test_generated_source_links_target_declaration_lines() -> None:
    ref = _current_ref()
    content = generate_api_docs.generate("test", REPO_URL, ref)

    generate_api_docs._validate_source_links(content, REPO_URL, ref)
    assert f"{REPO_URL}/blob/{ref}/src/open_sesame/contracts.py#L10" in content
    assert f"{REPO_URL}/blob/{ref}/src/open_sesame/contracts.py#L26" in content
