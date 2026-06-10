#!/usr/bin/env python3
"""Live reCAPTCHA-style image-grid solve through the OpenSesame public API.

Real reCAPTCHA grid token-mint is gated by session/IP reputation (a *correct*
tile solve can still be refused), so this example is self-contained and
deterministic: it serves a page with the *same DOM structure the grid engine
reads on Google* (a same-origin ``api2/bframe`` iframe, an
``#rc-imageselect-target`` table, ``#recaptcha-verify-button``, the parent
``#g-recaptcha-response``) and mints a local "token" only when the correct tiles
are selected. The engine runs unchanged: read the grid DOM, classify each tile
via a registry ``TileSelector``, **click the matching cells via the DOM**,
verify, harvest the token.

The classifier here keys on tile colour (a real, deterministic image read); a
CLIP/ViT object classifier (e.g. ``verytuffcat/recaptcha``) plugs into the same
``tiles`` provider seam for real object grids.

Run (needs the `live` extra; uses the unified solver venv):

    PYTHONPATH=src .../venvs/solver/bin/python examples/solve_vision_live.py
"""

from __future__ import annotations

import asyncio
import base64
import io
import random
import sys
import tempfile
from pathlib import Path

from OpenSesame import Challenge, SolverPolicy
from OpenSesame.api.defaults import default_solver

COLORS = {
    "blue": (40, 90, 230),
    "red": (220, 50, 50),
    "green": (40, 170, 70),
    "yellow": (235, 205, 40),
}
TARGET = "blue"
ROWS = COLS = 3


def tile_png(rgb: tuple[int, int, int]) -> str:
    from PIL import Image

    img = Image.new("RGB", (100, 100), rgb)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def build_grid(rng: random.Random) -> tuple[list[str], list[int]]:
    """Assign each of 9 cells a colour; return (cell colours, target indices)."""

    cells = [rng.choice(list(COLORS)) for _ in range(ROWS * COLS)]
    if TARGET not in cells:                       # guarantee at least one target
        cells[rng.randrange(len(cells))] = TARGET
    target_idx = [i for i, c in enumerate(cells) if c == TARGET]
    return cells, target_idx


def write_pages(work: Path, cells: list[str], target_idx: list[int]) -> Path:
    frame_dir = work / "api2"
    frame_dir.mkdir(parents=True, exist_ok=True)
    tds = "".join(
        f'<td role="button" aria-pressed="false" style="padding:0">'
        f'<img width="100" height="100" src="{tile_png(COLORS[c])}"></td>'
        + ("</tr><tr>" if i % COLS == COLS - 1 and i != len(cells) - 1 else "")
        for i, c in enumerate(cells)
    )
    (frame_dir / "bframe.html").write_text(
        f"""<!doctype html><html><body>
<div class="rc-imageselect-instructions">Select all images with {TARGET}</div>
<div id="rc-imageselect-target">
  <table class="rc-imageselect-table-{ROWS}{COLS}"><tr>{tds}</tr></table>
</div>
<button id="recaptcha-verify-button" onclick="verify()">Verify</button>
<script>
const TARGET = {target_idx};
document.querySelectorAll('#rc-imageselect-target td').forEach(td =>
  td.addEventListener('click', () =>
    td.setAttribute('aria-pressed', td.getAttribute('aria-pressed') === 'true' ? 'false' : 'true')));
function verify() {{
  const sel = [];
  document.querySelectorAll('#rc-imageselect-target td').forEach((td, i) => {{
    if (td.getAttribute('aria-pressed') === 'true') sel.push(i);
  }});
  const ok = sel.length === TARGET.length && TARGET.every(i => sel.includes(i));
  if (ok) parent.document.querySelector('#g-recaptcha-response').value = 'GRID-TOKEN-' + Date.now();
}}
</script></body></html>""",
        encoding="utf-8",
    )
    parent = work / "parent.html"
    parent.write_text(
        f"""<!doctype html><html><body>
<h3>Image-grid captcha demo</h3>
<textarea id="g-recaptcha-response" style="display:none"></textarea>
<iframe src="file://{frame_dir / 'bframe.html'}" width="320" height="430" frameborder="0"></iframe>
</body></html>""",
        encoding="utf-8",
    )
    return parent


# -- TileSelector provider: classify each cell by colour ------------------

class ColorTileSelector:
    """A :class:`~OpenSesame.api.providers.TileSelector` keyed on tile colour."""

    model_id = "color"
    device = "cpu"

    def select_tiles(self, image_path, *, rows, cols, target, candidate_labels=()):
        from PIL import Image

        img = Image.open(image_path).convert("RGB")
        tw, th = img.width // cols, img.height // rows
        want = COLORS.get(target.rstrip("s"), COLORS.get(target))
        picks = []
        for r in range(rows):
            for c in range(cols):
                # sample the cell centre to avoid borders/gridlines
                cx, cy = c * tw + tw // 2, r * th + th // 2
                patch = img.crop((cx - 12, cy - 12, cx + 12, cy + 12)).resize((1, 1))
                mean = patch.getpixel((0, 0))
                nearest = min(COLORS, key=lambda k: sum((a - b) ** 2 for a, b in zip(mean, COLORS[k])))
                if want is not None and COLORS[nearest] == want:
                    picks.append((r, c, 1.0))
        return picks


async def main() -> int:
    from voidcrawl import BrowserConfig, BrowserSession

    solver = default_solver(SolverPolicy.auto_only(
        allow_sites=[""],                          # file:// pages have empty host
        models={"recaptcha_v2_grid": "color", "recaptcha_v2_strategy": "grid"},
    ))
    solver.registry.register_factory("tiles", lambda key: ColorTileSelector())

    work = Path(tempfile.mkdtemp(prefix="os-vision-"))
    rng = random.Random()

    async with BrowserSession(BrowserConfig(headless=True, stealth=True)) as browser:
        page = await browser.new_page("about:blank")
        async with solver.engine():
            for attempt in range(1, 6):
                cells, target_idx = build_grid(rng)
                parent = write_pages(work, cells, target_idx)
                await page.goto(f"file://{parent}", timeout=15)

                challenge = Challenge.from_capture({"kind": "recaptcha", "page_url": f"file://{parent}"})
                result = await solver.solve(challenge, page=page, timeout=30)

                ok = result.ok and result.solution and result.solution.is_token
                print(f"  attempt {attempt}: target tiles {target_idx} -> "
                      f"{'token' if ok else result.status.value}")
                if ok:
                    print(f"\n✓ PASSED — correct tiles selected, token minted "
                          f"({result.token}, applied={result.applied}, "
                          f"{result.timing.elapsed_ms:.0f}ms)")
                    return 0

    print("\n✗ did not pass within attempt budget")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
