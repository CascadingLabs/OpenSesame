"""Disposable, fully local web fixtures for OpenSesame handoff demos and tests.

These fixtures exist so an operator handoff can be exercised end to end without
depending on a third-party challenge page. Nothing here is a security control:
the credentials and the visual challenge are deliberately trivial and local.
"""

from opensesame.fixtures.captcha_login import (
    FIXTURE_PASSWORD,
    FIXTURE_USERNAME,
    CaptchaLoginFixture,
    captcha_login_server,
)

__all__ = [
    "FIXTURE_PASSWORD",
    "FIXTURE_USERNAME",
    "CaptchaLoginFixture",
    "captcha_login_server",
]
