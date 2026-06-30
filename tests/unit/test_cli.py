from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from opensesame import cli, demo


def test_requested_demo_targets_are_registered():
    assert demo.DEMO_TARGETS["mtcaptcha"] == "https://2captcha.com/demo/mtcaptcha"
    assert demo.DEMO_TARGETS["recaptcha-v3-enterprise"].endswith(
        "/recaptcha-v3-enterprise"
    )
    assert demo.DEMO_TARGETS["cloudflare-managed"].endswith(
        "/cloudflare-turnstile-challenge"
    )
    assert demo.DEMO_TARGETS["xcaptcha-moving"] == "https://xcaptcha.com/demo"
    assert demo.DEMO_ARM_TARGETS == (
        demo.RECAPTCHA_DEMO_TARGETS + demo.CLOUDFLARE_DEMO_TARGETS
    )
    assert [n for n in demo.DEMO_ARM_TARGETS if n.startswith("xcaptcha-")] == []


@pytest.mark.parametrize(
    ("variant", "target"),
    [
        ("turnstile", "cloudflare-turnstile"),
        ("widget", "cloudflare-turnstile"),
        ("managed", "cloudflare-managed"),
        ("challenge", "cloudflare-managed"),
    ],
)
def test_cloudflare_demo_variants_route_to_focused_targets(
    monkeypatch, variant: str, target: str
):
    calls: list[dict[str, object]] = []

    async def fake_run_demo(**kwargs: object) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(cli, "run_demo", fake_run_demo)

    result = CliRunner().invoke(
        cli.main,
        ["demo", "cloudflare", variant, "--no-serve-ui", "--no-ui-prompt"],
    )

    assert result.exit_code == 0, result.output
    assert calls[-1]["challenge_type"] == target


@pytest.mark.parametrize(
    ("variant", "target"), sorted(demo.RECAPTCHA_TYPE_TARGETS.items())
)
def test_recaptcha_demo_variants_route_to_focused_targets(
    monkeypatch, variant: str, target: str
):
    calls: list[dict[str, object]] = []

    async def fake_run_demo(**kwargs: object) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(cli, "run_demo", fake_run_demo)

    result = CliRunner().invoke(
        cli.main,
        ["demo", "recaptcha", variant, "--no-serve-ui", "--no-ui-prompt"],
    )

    assert result.exit_code == 0, result.output
    assert calls[-1]["challenge_type"] == target


def test_family_demo_defaults_route_to_primary_targets(monkeypatch):
    calls: list[dict[str, object]] = []

    async def fake_run_demo(**kwargs: object) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(cli, "run_demo", fake_run_demo)

    cloudflare = CliRunner().invoke(
        cli.main, ["demo", "cloudflare", "--no-serve-ui", "--no-ui-prompt"]
    )
    recaptcha = CliRunner().invoke(
        cli.main, ["demo", "recaptcha", "--no-serve-ui", "--no-ui-prompt"]
    )

    assert cloudflare.exit_code == 0, cloudflare.output
    assert recaptcha.exit_code == 0, recaptcha.output
    assert [call["challenge_type"] for call in calls] == [
        "cloudflare-turnstile",
        "recaptcha-v2",
    ]


def test_datadome_demo_is_explicitly_not_registered():
    result = CliRunner().invoke(cli.main, ["demo", "datadome", "-A"])

    assert result.exit_code != 0
    assert "No durable DataDome demo target is registered yet" in result.output


@pytest.mark.parametrize(
    ("family", "expected_targets"),
    [
        ("cloudflare", demo.CLOUDFLARE_DEMO_TARGETS),
        ("recaptcha", demo.RECAPTCHA_DEMO_TARGETS),
    ],
)
def test_family_demo_all_queues_only_family_targets(
    monkeypatch, family: str, expected_targets: tuple[str, ...]
):
    calls: list[dict[str, object]] = []

    async def fake_arm_all_demo_events(**kwargs: object) -> None:
        calls.append(kwargs)

    monkeypatch.setattr(cli, "arm_all_demo_events", fake_arm_all_demo_events)

    result = CliRunner().invoke(
        cli.main,
        [
            "demo",
            family,
            "-A",
            "--exit-after-all",
            "--no-serve-ui",
            "--concurrency",
            "2",
        ],
    )

    assert result.exit_code == 0, result.output
    assert calls[-1]["target_names"] == expected_targets
    assert calls[-1]["concurrency"] == 2
    assert calls[-1]["keep_ui"] is False


def test_xcaptcha_demo_targets_have_prepare_clicks():
    text_click_v1_sitekey = "".join(["5b4fc1a2", "21c3e79c", "9bac1903", "63808884"])
    no_captcha_sitekey = "".join(["a537c95d", "43097aed", "9cd8a295", "ecdc2a79"])

    assert demo.DEMO_PREPARE_SELECTORS[
        "xcaptcha-text-click-v1"
    ] == demo.xcaptcha_button(text_click_v1_sitekey)
    assert demo.DEMO_PREPARE_SELECTORS["xcaptcha-no-captcha"] == demo.xcaptcha_button(
        no_captcha_sitekey
    )


def test_open_url_prompt_loop_allows_repeated_opens(monkeypatch):
    url = "http://127.0.0.1:8765"
    answers = iter(["o", " O ", "no", "", "o"])
    opened: list[str] = []

    def fake_input(prompt: str) -> str:
        try:
            return next(answers)
        except StopIteration as exc:
            raise EOFError from exc

    monkeypatch.setattr(cli.console, "input", fake_input)
    monkeypatch.setattr(cli.webbrowser, "open", lambda value: opened.append(value))

    cli._open_url_prompt_loop(url)

    assert opened == [url, url, url]


def test_demo_server_spawn_disables_hidden_open_prompt(monkeypatch):
    readiness = iter([False, True])
    spawned: dict[str, object] = {}

    class FakeProcess:
        def poll(self) -> None:
            return None

    def fake_popen(command: list[str], **kwargs: object) -> FakeProcess:
        spawned["command"] = command
        spawned["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(demo, "url_is_ready", lambda _url: next(readiness))
    monkeypatch.setattr(demo.subprocess, "Popen", fake_popen)

    process = demo.start_opensesame_server(
        opensesame_url="http://127.0.0.1:8765",
        db_path=Path("test.sqlite3"),
        notify=False,
    )

    assert process is not None
    assert "--no-open-prompt" in spawned["command"]
    assert spawned["kwargs"]["stdin"] is subprocess.DEVNULL


@pytest.mark.asyncio
async def test_demo_uses_voidcrawl_capture_challenge_contract():
    class FakeBrowser:
        async def websocket_url(self) -> str:
            return "ws://127.0.0.1/devtools/browser/demo"

    class FakePage:
        def __init__(self) -> None:
            self.kwargs: dict[str, object] | None = None

        async def capture_challenge(self, **kwargs: object) -> dict[str, object]:
            self.kwargs = kwargs
            return {"challenge": {"event_id": "event-1", "blocking": True}}

    page = FakePage()

    capture = await demo.capture_voidcrawl_challenge(
        browser=FakeBrowser(),
        page=page,
        session_id="opensesame-demo",
        vnc_url="vnc://127.0.0.1:5900",
        novnc_url="http://127.0.0.1:6080",
    )

    assert capture["challenge"]["event_id"] == "event-1"
    assert page.kwargs == {
        "websocket_url": "ws://127.0.0.1/devtools/browser/demo",
        "session_id": "opensesame-demo",
        "vnc_url": "vnc://127.0.0.1:5900",
        "novnc_url": "http://127.0.0.1:6080",
    }
