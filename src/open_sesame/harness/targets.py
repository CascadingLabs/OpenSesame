"""Seed target registry for captcha and anti-bot evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

CaptchaFamily = Literal["normal_ocr", "synthetic_ocr", "cloudflare_managed"]
TargetKind = Literal["live_demo", "live_antibot", "self_host", "synthetic"]


@dataclass(frozen=True)
class Target:
    id: str
    title: str
    family: CaptchaFamily
    kind: TargetKind
    url: str
    source: str
    notes: str
    requires_account: bool = False
    holdout_candidate: bool = False
    throughput_candidate: bool = False


DEFAULT_TARGETS: tuple[Target, ...] = (
    Target(
        id="2captcha-normal",
        title="2Captcha normal captcha demo",
        family="normal_ocr",
        kind="live_demo",
        url="https://2captcha.com/demo/normal",
        source="CAS-170 / CAS-181",
        notes="Real form target named in CAS-170; useful for end-to-end OCR form smoke.",
        holdout_candidate=True,
    ),
    Target(
        id="azcaptcha-image-text",
        title="AZcaptcha image/text captcha demo",
        family="normal_ocr",
        kind="live_demo",
        url="https://azcaptcha.com/demo",
        source="web research",
        notes="Public image-to-text demo page; may require an API key for solver-network actions.",
        requires_account=True,
        holdout_candidate=True,
    ),
    Target(
        id="captchasonic-normal",
        title="CaptchaSonic normal captcha demo",
        family="normal_ocr",
        kind="live_demo",
        url="https://captchasonic.com/en/demo/normal-captcha",
        source="web research",
        notes="Candidate live normal-captcha route from CaptchaSonic demo listing.",
        requires_account=True,
        holdout_candidate=True,
    ),
    Target(
        id="fortress-cloudflare-managed",
        title="Fortress Cloudflare managed challenge",
        family="cloudflare_managed",
        kind="live_antibot",
        url="https://fortress.theplumber.dev/",
        source="CAS-181 throughput target",
        notes=(
            "Hard live Cloudflare managed challenge with Turnstile assets. Use as "
            "a throughput/routing benchmark, not an OCR or image-classification target."
        ),
        holdout_candidate=True,
        throughput_candidate=True,
    ),
    Target(
        id="securimage-selfhost",
        title="Self-hosted Securimage fixture",
        family="synthetic_ocr",
        kind="self_host",
        url="https://github.com/dapphp/securimage",
        source="CAS-170",
        notes="Use for unlimited labeled PHP CAPTCHA generation; not a held-out real target.",
    ),
    Target(
        id="python-captcha-synthetic",
        title="Python captcha synthetic generator",
        family="synthetic_ocr",
        kind="synthetic",
        url="https://pypi.org/project/captcha/",
        source="CAS-170",
        notes="Use for local labeled synthetic training/eval data.",
    ),
)


def targets_for_family(family: CaptchaFamily) -> tuple[Target, ...]:
    return tuple(target for target in DEFAULT_TARGETS if target.family == family)
