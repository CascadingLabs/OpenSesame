"""A local login page gated by a visual challenge.

The fixture is the target half of the CAS-261 dogfooding loop: a page that a
headless caller cannot finish on its own, so the owning process must hand the
tab to a human through OpenSesame and resume it afterwards.

This is a *fixture*, not an anti-bot control. The challenge answer is rendered
from ordinary DOM text, so it is readable by anything that parses the HTML. It
is skewed and split across elements only so that a human reading it over noVNC
is the natural way to solve it; it does not resist scripted extraction, and it
must never be treated as a security boundary.
"""

from __future__ import annotations

import secrets
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import cast
from urllib import parse

FIXTURE_USERNAME = "operator"
FIXTURE_PASSWORD = "open-sesame"
SESSION_COOKIE = "opensesame_fixture"
SESSION_VALUE = "approved"

# Visually unambiguous over a lossy VNC transport: no O/0, I/1, S/5.
CHALLENGE_ALPHABET = "ABCDEFGHJKLMNPQRTUVWXYZ23479"
CHALLENGE_LENGTH = 5


@dataclass
class CaptchaLoginFixture:
    """Server-side state for one disposable fixture instance."""

    challenges: dict[str, str] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def issue(self) -> tuple[str, str]:
        """Mint a challenge and return its ``(id, answer)`` pair."""
        challenge_id = secrets.token_urlsafe(8)
        answer = "".join(
            secrets.choice(CHALLENGE_ALPHABET) for _ in range(CHALLENGE_LENGTH)
        )
        with self._lock:
            self.challenges[challenge_id] = answer
        return challenge_id, answer

    def consume(self, challenge_id: str, answer: str) -> bool:
        """Check *answer* and burn the challenge so it cannot be replayed."""
        with self._lock:
            expected = self.challenges.pop(challenge_id, None)
        return expected is not None and answer.strip().upper() == expected


class _FixtureServer(ThreadingHTTPServer):
    """Threading HTTP server carrying the fixture's challenge state."""

    daemon_threads = True

    def __init__(
        self,
        address: tuple[str, int],
        handler: type[BaseHTTPRequestHandler],
        fixture: CaptchaLoginFixture,
    ) -> None:
        super().__init__(address, handler)
        self.fixture = fixture


def _challenge_markup(answer: str) -> str:
    """Render *answer* as skewed per-character spans."""
    cells = []
    for index, character in enumerate(answer):
        rotation = -18 + (index * 9)
        offset = -4 + ((index * 5) % 9)
        cells.append(
            f'<span class="glyph" style="transform:rotate({rotation}deg)'
            f' translateY({offset}px)">{character}</span>'
        )
    return "".join(cells)


def _login_page(challenge_id: str, answer: str, error: str | None) -> str:
    banner = (
        f'<p class="error" role="alert">{error}</p>'
        if error
        else '<p class="hint">Read the code above and type it below to continue.</p>'
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>OpenSesame Fixture Login</title>
<style>
  body {{ font-family: system-ui, sans-serif; background: #14161a; color: #e8e8ea;
         display: grid; place-items: center; min-height: 100vh; margin: 0; }}
  main {{ width: min(28rem, 90vw); background: #1d2027; padding: 2rem;
          border-radius: 12px; border: 1px solid #2f333d; }}
  h1 {{ margin-top: 0; font-size: 1.25rem; }}
  label {{ display: block; margin: 0.75rem 0 0.25rem; font-size: 0.85rem; }}
  input {{ width: 100%; padding: 0.5rem; border-radius: 6px; border: 1px solid #3a3f4b;
           background: #101217; color: inherit; box-sizing: border-box; }}
  button {{ margin-top: 1.25rem; width: 100%; padding: 0.6rem; border: 0;
            border-radius: 6px; background: #4f8cff; color: #fff; font-weight: 600; }}
  .challenge {{ margin-top: 1rem; padding: 1rem; text-align: center;
                background: repeating-linear-gradient(45deg, #202430, #202430 6px,
                #262b38 6px, #262b38 12px); border-radius: 8px; }}
  .glyph {{ display: inline-block; font-size: 2rem; font-weight: 700;
            letter-spacing: 0.2rem; color: #f2f4ff; }}
  .hint {{ font-size: 0.8rem; color: #9aa2b1; }}
  .error {{ font-size: 0.85rem; color: #ff8080; }}
</style>
</head>
<body>
<main>
  <h1>OpenSesame Fixture Login</h1>
  <div class="challenge">{_challenge_markup(answer)}</div>
  {banner}
  <form method="post" action="/login">
    <input type="hidden" name="challenge_id" value="{challenge_id}">
    <label for="username">Username</label>
    <input id="username" name="username" autocomplete="username">
    <label for="password">Password</label>
    <input id="password" name="password" type="password"
           autocomplete="current-password">
    <label for="challenge">Challenge code</label>
    <input id="challenge" name="challenge" autocomplete="off"
           autocapitalize="characters">
    <button type="submit">Sign in</button>
  </form>
</main>
</body>
</html>"""


SECURE_PAGE = """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>OpenSesame Fixture Secure Area</title></head>
<body style="font-family:system-ui,sans-serif;background:#14161a;color:#e8e8ea">
<main><h1>Fixture Secure Area</h1>
<p id="status">Authenticated locally.</p></main>
</body>
</html>"""


class CaptchaLoginHandler(BaseHTTPRequestHandler):
    server_version = "OpenSesameCaptchaLogin/1.0"

    @property
    def fixture(self) -> CaptchaLoginFixture:
        server = self.server
        assert isinstance(server, _FixtureServer)
        return server.fixture

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        path = parse.urlparse(self.path)
        if path.path in ("/", "/login"):
            error = parse.parse_qs(path.query).get("error", [None])[0]
            challenge_id, answer = self.fixture.issue()
            self._html(_login_page(challenge_id, answer, error))
            return
        if path.path == "/secure":
            cookies = self.headers.get("Cookie", "")
            if f"{SESSION_COOKIE}={SESSION_VALUE}" not in cookies:
                self._redirect("/login")
                return
            self._html(SECURE_PAGE)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        if parse.urlparse(self.path).path != "/login":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        length = int(self.headers.get("Content-Length", "0"))
        form = parse.parse_qs(self.rfile.read(length).decode("utf-8"))

        def field_value(name: str) -> str:
            return form.get(name, [""])[0]

        credentials_ok = (
            field_value("username") == FIXTURE_USERNAME
            and field_value("password") == FIXTURE_PASSWORD
        )
        challenge_ok = self.fixture.consume(
            field_value("challenge_id"), field_value("challenge")
        )
        if not credentials_ok:
            self._redirect("/login?error=Incorrect+username+or+password.")
            return
        if not challenge_ok:
            self._redirect("/login?error=Challenge+code+did+not+match.")
            return
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header(
            "Set-Cookie",
            f"{SESSION_COOKIE}={SESSION_VALUE}; HttpOnly; SameSite=Lax; Path=/",
        )
        self.send_header("Location", "/secure")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, _format: str, *_args: object) -> None:
        """Keep fixture request logs out of the caller's result output."""

    def _html(self, body: str) -> None:
        encoded = body.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def _redirect(self, location: str) -> None:
        self.send_response(HTTPStatus.SEE_OTHER)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()


@contextmanager
def captcha_login_server(
    host: str = "127.0.0.1", port: int = 0
) -> Iterator[tuple[str, CaptchaLoginFixture]]:
    """Serve the fixture on an ephemeral port for the duration of the block."""
    fixture = CaptchaLoginFixture()
    server = _FixtureServer((host, port), CaptchaLoginHandler, fixture)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    bound_host, bound_port = cast("tuple[str, int]", server.server_address)
    try:
        yield f"http://{bound_host}:{bound_port}", fixture
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
