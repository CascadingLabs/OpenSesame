"""Same-origin DOM reader for Google's reCAPTCHA demo (api2/bframe).

On a third-party site the reCAPTCHA challenge iframe is cross-origin and opaque,
so the solver must screenshot the widget and estimate tile geometry. On Google's
own demo (``www.google.com/recaptcha/api2/demo``) the challenge ``bframe`` is
*same origin*, so the page can read the challenge DOM directly: exact
instructions (no OCR), the exact grid shape (``rc-imageselect-table-33`` /
``-44``), and every tile cell's true bounding rect (no geometry estimate). This
module returns that structured state for a precise DOM-driven solve.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# Reads the same-origin challenge bframe and returns structured challenge state
# in TOP-LEVEL viewport CSS coordinates (cell rect + iframe offset), ready for
# CDP coordinate clicks.
RECAPTCHA_DOM_STATE_JS = r"""
(() => {
  const frame = document.querySelector('iframe[src*="api2/bframe"]');
  if (!frame) return {ok: false, reason: 'no-bframe'};
  let doc;
  try { doc = frame.contentDocument; } catch (e) { return {ok: false, reason: 'cross-origin'}; }
  if (!doc) return {ok: false, reason: 'no-document'};

  const fr = frame.getBoundingClientRect();
  const instrEl = doc.querySelector('.rc-imageselect-instructions');
  const instructions = instrEl ? instrEl.innerText.replace(/\s+/g, ' ').trim() : '';
  const table = doc.querySelector('#rc-imageselect-target table');
  if (!table) {
    // Challenge may be present but between states (verifying / loading).
    return {ok: true, present: false, instructions};
  }
  const cls = table.className || '';
  const m = cls.match(/rc-imageselect-table-(\d)(\d)/);
  const rows = m ? parseInt(m[1], 10) : 3;
  const cols = m ? parseInt(m[2], 10) : 3;

  const cells = [];
  const tds = Array.from(table.querySelectorAll('td'));
  tds.forEach((td, index) => {
    const r = td.getBoundingClientRect();
    const img = td.querySelector('img');
    const selected = td.getAttribute('aria-pressed') === 'true'
      || td.className.indexOf('rc-imageselect-tileselected') !== -1;
    cells.push({
      index,
      row: Math.floor(index / cols),
      col: index % cols,
      // Top-level viewport coordinates: iframe offset + in-frame cell rect.
      x: fr.left + r.left,
      y: fr.top + r.top,
      width: r.width,
      height: r.height,
      selected: !!selected,
      img_src: img ? img.src : null,
      img_natural: img ? [img.naturalWidth, img.naturalHeight] : null,
    });
  });

  // The grid image element (first tile's <img>), so we can read its full src.
  const gridImg = table.querySelector('img');
  // Verify button lives in the same bframe.
  const verify = doc.querySelector('#recaptcha-verify-button');
  const vr = verify ? verify.getBoundingClientRect() : null;
  // Final response token lands in the PARENT textarea after a good solve.
  const tokenEl = document.querySelector('#g-recaptcha-response, textarea[name="g-recaptcha-response"]');
  const token = tokenEl ? (tokenEl.value || '') : '';

  return {
    ok: true,
    present: true,
    instructions,
    table_class: cls,
    rows,
    cols,
    cells,
    grid_img_src: gridImg ? gridImg.src : null,
    grid_img_natural: gridImg ? [gridImg.naturalWidth, gridImg.naturalHeight] : null,
    verify: vr ? {x: fr.left + vr.left, y: fr.top + vr.top, width: vr.width, height: vr.height} : null,
    token,
  };
})()
"""


@dataclass(frozen=True)
class DomTile:
    index: int
    row: int
    col: int
    x: float
    y: float
    width: float
    height: float
    selected: bool
    img_src: str | None = None

    @property
    def center(self) -> tuple[float, float]:
        return (self.x + self.width / 2.0, self.y + self.height / 2.0)


@dataclass(frozen=True)
class DomChallengeState:
    ok: bool
    present: bool
    instructions: str = ""
    table_class: str = ""
    rows: int = 0
    cols: int = 0
    tiles: tuple[DomTile, ...] = field(default_factory=tuple)
    grid_img_src: str | None = None
    grid_img_natural: tuple[int, int] | None = None
    verify: dict[str, float] | None = None
    token: str = ""
    reason: str = ""

    @property
    def grid_rect(self) -> tuple[float, float, float, float] | None:
        """Union bounding rect of all tile cells, in top-level CSS coords."""

        if not self.tiles:
            return None
        left = min(t.x for t in self.tiles)
        top = min(t.y for t in self.tiles)
        right = max(t.x + t.width for t in self.tiles)
        bottom = max(t.y + t.height for t in self.tiles)
        return (left, top, right - left, bottom - top)


def parse_dom_state(raw: Any) -> DomChallengeState:
    if not isinstance(raw, dict) or not raw.get("ok"):
        reason = raw.get("reason", "unreadable") if isinstance(raw, dict) else "unreadable"
        return DomChallengeState(ok=False, present=False, reason=str(reason))
    if not raw.get("present"):
        return DomChallengeState(
            ok=True,
            present=False,
            instructions=str(raw.get("instructions") or ""),
        )
    tiles = tuple(
        DomTile(
            index=int(c["index"]),
            row=int(c["row"]),
            col=int(c["col"]),
            x=float(c["x"]),
            y=float(c["y"]),
            width=float(c["width"]),
            height=float(c["height"]),
            selected=bool(c.get("selected")),
            img_src=c.get("img_src"),
        )
        for c in raw.get("cells", [])
        if isinstance(c, dict)
    )
    natural = raw.get("grid_img_natural")
    return DomChallengeState(
        ok=True,
        present=True,
        instructions=str(raw.get("instructions") or ""),
        table_class=str(raw.get("table_class") or ""),
        rows=int(raw.get("rows") or 0),
        cols=int(raw.get("cols") or 0),
        tiles=tiles,
        grid_img_src=raw.get("grid_img_src"),
        grid_img_natural=tuple(natural) if isinstance(natural, list) and len(natural) == 2 else None,
        verify=raw.get("verify") if isinstance(raw.get("verify"), dict) else None,
        token=str(raw.get("token") or ""),
    )


async def read_dom_challenge_state(page: object) -> DomChallengeState:
    return parse_dom_state(await page.eval_js(RECAPTCHA_DOM_STATE_JS))
