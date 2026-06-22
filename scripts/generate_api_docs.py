"""Generate API reference markdown from the OpenSesame public API.

Uses griffe for static analysis, so the package does not need to be imported.
Only symbols listed in ``open_sesame.__all__`` are included.
"""

from __future__ import annotations

import argparse
import difflib
import re
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, cast

import griffe

if TYPE_CHECKING:
    from griffe import Class, Function, Object

_REPO_ROOT = Path(__file__).parent.parent
_SRC_ROOT = _REPO_ROOT / "src"
_GITHUB_ICON = (
    '<svg aria-hidden="true" height="14" viewBox="0 0 16 16"'
    ' version="1.1" width="14"'
    ' xmlns="http://www.w3.org/2000/svg"'
    ' style="vertical-align:-2px;display:inline-block">'
    '<path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53'
    " 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49"
    "-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13"
    "-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72"
    " 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2"
    "-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2"
    "-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27"
    " 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82"
    ".44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0"
    " 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0"
    " 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013"
    ' 8.013 0 0016 8c0-4.42-3.58-8-8-8z"/>'
    "</svg>"
)


def _current_git_ref() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=_REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _source_path_exists_at_ref(ref: str, rel_path: str) -> bool:
    result = subprocess.run(
        ["git", "cat-file", "-e", f"{ref}:{rel_path}"],
        cwd=_REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def _source_text_at_ref(ref: str, rel_path: str) -> str:
    result = subprocess.run(
        ["git", "show", f"{ref}:{rel_path}"],
        cwd=_REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout


def _validate_source_links(content: str, repo_url: str, ref: str) -> None:
    link_pattern = re.compile(
        rf'href="{re.escape(repo_url)}/blob/{re.escape(ref)}/([^"#]+)(?:#L(\d+))?"'
    )
    heading_pattern = re.compile(
        rf'^\#\#\#? `([^`]+)`.*?href="{re.escape(repo_url)}/blob/{re.escape(ref)}/([^"#]+)(?:#L(\d+))?"',
        re.MULTILINE,
    )
    missing = sorted(
        {
            path
            for path, _line in link_pattern.findall(content)
            if not _source_path_exists_at_ref(ref, path)
        }
    )
    if missing:
        joined = "\n".join(f"  - {path}" for path in missing)
        raise SystemExit(f"Source link validation failed for ref {ref}:\n{joined}")
    bad_lines: list[str] = []
    for name, path, line in heading_pattern.findall(content):
        if not line:
            continue
        lines = _source_text_at_ref(ref, path).splitlines()
        lineno = int(line)
        source_line = lines[lineno - 1].strip() if 0 < lineno <= len(lines) else ""
        if not re.match(rf"(async\s+def|def|class)\s+{re.escape(name)}\b", source_line):
            bad_lines.append(f"  - {path}#L{lineno}: {source_line}")
    if bad_lines:
        joined = "\n".join(bad_lines)
        raise SystemExit(f"Source link line validation failed for ref {ref}:\n{joined}")


def _declaration_lineno(obj: Object, rel: Path) -> int | None:
    lineno = getattr(obj, "lineno", None)
    if not lineno:
        return None
    source_path = _REPO_ROOT / rel
    if not source_path.exists():
        return lineno
    name = getattr(obj, "name", "")
    keyword = "class" if isinstance(obj, griffe.Class) else r"(?:async\s+def|def)"
    pattern = re.compile(rf"^\s*{keyword}\s+{re.escape(name)}\b")
    lines = source_path.read_text().splitlines()
    start = max(lineno - 8, 0)
    stop = min((getattr(obj, "endlineno", None) or lineno) + 8, len(lines))
    for index in range(start, stop):
        if pattern.match(lines[index]):
            return index + 1
    return None


def _line_is_declaration_at_ref(ref: str, rel: Path, lineno: int, name: str) -> bool:
    rel_path = rel.as_posix()
    if not _source_path_exists_at_ref(ref, rel_path):
        return False
    lines = _source_text_at_ref(ref, rel_path).splitlines()
    source_line = lines[lineno - 1].strip() if 0 < lineno <= len(lines) else ""
    return bool(re.match(rf"(async\s+def|def|class)\s+{re.escape(name)}\b", source_line))


def _check_file(path: Path, expected: str) -> None:
    if not path.exists():
        raise SystemExit(f"Generated API reference is missing: {path}")
    actual = path.read_text()
    if actual == expected:
        print(f"OK: {path}")
        return
    diff = "".join(
        difflib.unified_diff(
            actual.splitlines(keepends=True),
            expected.splitlines(keepends=True),
            fromfile=str(path),
            tofile=f"{path} (generated)",
        )
    )
    sys.stderr.write(diff)
    raise SystemExit(f"Generated API reference is out of date: {path}")


def _gh_link(obj: Object, repo_url: str, ref: str) -> str:
    filepath = getattr(obj, "filepath", None)
    if not filepath:
        return ""
    try:
        rel = Path(filepath).relative_to(_REPO_ROOT)
    except ValueError:
        return ""
    lineno = _declaration_lineno(obj, rel)
    if not lineno:
        return ""
    name = getattr(obj, "name", "")
    if not _line_is_declaration_at_ref(ref, rel, lineno, name):
        return ""
    url = f"{repo_url}/blob/{ref}/{rel.as_posix()}#L{lineno}"
    return (
        f' <a href="{url}" target="_blank" rel="noopener noreferrer"'
        f' title="View source on GitHub">{_GITHUB_ICON}</a>'
    )


def _render_docstring(obj: Object) -> str:
    if not obj.docstring:
        return ""

    parsed = obj.docstring.parse("google")
    parts: list[str] = []
    for section in parsed:
        kind = section.kind.value
        if kind == "text":
            parts.append(str(section.value).strip())
        elif kind == "parameters":
            parts.append("**Args:**\n")
            for param in section.value:
                ann = f"`{param.annotation}`" if param.annotation else ""
                desc = param.description.strip() if param.description else ""
                parts.append(f"- `{param.name}` {ann} - {desc}")
            parts.append("")
        elif kind == "attributes":
            parts.append("**Attributes:**\n")
            for attr in section.value:
                ann = f"`{attr.annotation}`" if attr.annotation else ""
                desc = attr.description.strip() if attr.description else ""
                parts.append(f"- `{attr.name}` {ann} - {desc}")
            parts.append("")
        elif kind in ("returns", "yields"):
            label = "Returns" if kind == "returns" else "Yields"
            items = section.value if isinstance(section.value, list) else [section.value]
            descs = [
                (f"`{item.annotation}` - " if item.annotation else "") + (item.description or "")
                for item in items
            ]
            parts.append(f"**{label}:** {' '.join(descs)}".strip())
            parts.append("")
    return "\n".join(parts).strip()


def _render_params(fn: Function) -> str:
    params = [p for p in fn.parameters if p.name not in ("self", "cls")]
    parts: list[str] = []
    for p in params:
        ann = f": {p.annotation}" if p.annotation else ""
        default = f" = {p.default}" if p.default is not None else ""
        parts.append(f"{p.name}{ann}{default}")
    ret = f" -> {fn.returns}" if fn.returns else ""
    return f"({', '.join(parts)}){ret}"


def _format_function(name: str, obj: Function, link: str) -> list[str]:
    lines = [f"## `{name}`{link}\n", f"`{name}{_render_params(obj)}`\n"]
    doc = _render_docstring(obj)
    if doc:
        lines.append(doc)
        lines.append("")
    return lines


def _format_class(name: str, obj: Class, link: str, repo_url: str, ref: str) -> list[str]:
    lines = [f"## `{name}`{link}\n"]
    doc = _render_docstring(obj)
    if doc:
        lines.append(doc)
        lines.append("")

    for mname, member in sorted(obj.members.items()):
        if mname.startswith("_") or member.is_alias or not isinstance(member, griffe.Function):
            continue
        mlink = _gh_link(member, repo_url, ref)
        lines.append(f"### `{mname}`{mlink}\n")
        lines.append(f"`{mname}{_render_params(member)}`\n")
        mdoc = _render_docstring(member)
        if mdoc:
            lines.append(mdoc)
            lines.append("")
    return lines


def _extract_all_names(pkg: griffe.Module) -> list[str]:
    if "__all__" in pkg.members:
        all_obj = pkg.members["__all__"]
        raw = str(all_obj.value) if hasattr(all_obj, "value") else ""
        names = re.findall(r'"([^"]+)"|\'([^\']+)\'', raw)
        flattened = [double or single for double, single in names]
        if flattened:
            return flattened
    return [name for name in pkg.members if not name.startswith("_")]


def generate(version: str, repo_url: str, ref: str) -> str:
    pkg = cast("griffe.Module", griffe.load("open_sesame", search_paths=[str(_SRC_ROOT)]))
    parts: list[str] = [
        "---",
        "title: API Reference",
        f"description: Full API reference for OpenSesame {version}",
        f"version: {version}",
        "---",
        "",
        "# API Reference",
        "",
        f"> Generated from OpenSesame `{version}`. Only symbols in `__all__` are listed.",
        "",
    ]

    for name in sorted(_extract_all_names(pkg)):
        obj = pkg.members.get(name)
        if obj is None:
            continue
        target = cast("Object", obj.final_target) if isinstance(obj, griffe.Alias) else cast("Object", obj)
        link = _gh_link(target, repo_url, ref)
        if isinstance(target, griffe.Class):
            parts.extend(_format_class(name, target, link, repo_url, ref))
        elif isinstance(target, griffe.Function):
            parts.extend(_format_function(name, target, link))

    return "\n".join(parts).rstrip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate OpenSesame API reference markdown.")
    parser.add_argument(
        "--output",
        default="../OpenSesameDocs/opensesame/reference/api-reference.md",
        help="Output markdown path",
    )
    parser.add_argument("--version", default="", help="Version string (e.g. 0.1.0)")
    parser.add_argument("--check", action="store_true", help="Check committed docs output without writing files")
    parser.add_argument(
        "--github-repo",
        default="https://github.com/CascadingLabs/OpenSesame",
        help="GitHub repository base URL",
    )
    parser.add_argument(
        "--ref",
        default="",
        help="Git ref (tag/branch/commit) for source links; defaults to the current commit SHA",
    )
    args = parser.parse_args()

    if args.version:
        version = args.version
    else:
        text = (_REPO_ROOT / "pyproject.toml").read_text()
        match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
        version = match.group(1) if match else "unknown"
    ref = args.ref or _current_git_ref()

    content = generate(version, args.github_repo, ref)
    _validate_source_links(content, args.github_repo, ref)
    out = Path(args.output)
    if args.check:
        _check_file(out, content)
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content)
    print(f"Wrote {len(content):,} bytes to {out}")


if __name__ == "__main__":
    main()
