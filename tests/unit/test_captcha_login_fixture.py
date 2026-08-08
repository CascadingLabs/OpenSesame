from __future__ import annotations

from urllib import error, parse, request

import pytest

from opensesame.fixtures import (
    FIXTURE_PASSWORD,
    FIXTURE_USERNAME,
    CaptchaLoginFixture,
    captcha_login_server,
)
from opensesame.fixtures.captcha_login import CHALLENGE_ALPHABET, CHALLENGE_LENGTH


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: request.Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> None:
        return None


def post_login(base_url: str, **fields: str) -> error.HTTPError:
    """POST the fixture login form and return its un-followed 303 response."""
    opener = request.build_opener(NoRedirect)
    body = parse.urlencode(fields).encode()
    try:
        opener.open(request.Request(f"{base_url}/login", data=body, method="POST"))
    except error.HTTPError as response:
        return response
    raise AssertionError("login unexpectedly followed its redirect")


def issued_challenge(fixture: CaptchaLoginFixture, base_url: str) -> tuple[str, str]:
    """Load /login and return the ``(challenge_id, answer)`` it rendered."""
    before = set(fixture.challenges)
    with request.urlopen(f"{base_url}/login") as response:
        assert response.status == 200
    minted = set(fixture.challenges) - before
    assert len(minted) == 1
    challenge_id = minted.pop()
    return challenge_id, fixture.challenges[challenge_id]


def test_issue_mints_unambiguous_single_use_challenges() -> None:
    fixture = CaptchaLoginFixture()

    challenge_id, answer = fixture.issue()

    assert len(answer) == CHALLENGE_LENGTH
    assert set(answer) <= set(CHALLENGE_ALPHABET)
    assert fixture.consume(challenge_id, answer) is True
    assert fixture.consume(challenge_id, answer) is False, "challenge must not replay"


def test_consume_is_case_insensitive_and_trims() -> None:
    fixture = CaptchaLoginFixture()
    challenge_id, answer = fixture.issue()

    assert fixture.consume(challenge_id, f"  {answer.lower()} ") is True


def test_consume_rejects_wrong_answer_and_unknown_id() -> None:
    fixture = CaptchaLoginFixture()
    challenge_id, answer = fixture.issue()

    assert fixture.consume("no-such-id", answer) is False
    assert fixture.consume(challenge_id, "WRONG") is False


def test_correct_credentials_and_challenge_grant_http_only_session() -> None:
    with captcha_login_server() as (base_url, fixture):
        challenge_id, answer = issued_challenge(fixture, base_url)

        response = post_login(
            base_url,
            username=FIXTURE_USERNAME,
            password=FIXTURE_PASSWORD,
            challenge=answer,
            challenge_id=challenge_id,
        )

        assert response.code == 303
        assert response.headers["Location"] == "/secure"
        cookie = response.headers["Set-Cookie"]
        assert "opensesame_fixture=approved" in cookie
        assert "HttpOnly" in cookie

        secure = request.Request(
            f"{base_url}/secure", headers={"Cookie": cookie.split(";", 1)[0]}
        )
        with request.urlopen(secure) as authenticated:
            assert "Fixture Secure Area" in authenticated.read().decode()


@pytest.mark.parametrize(
    ("field", "value", "expected_error"),
    [
        ("password", "wrong", "Incorrect"),
        ("challenge", "WRONG", "Challenge"),
    ],
)
def test_bad_credentials_or_challenge_return_to_login(
    field: str, value: str, expected_error: str
) -> None:
    with captcha_login_server() as (base_url, fixture):
        challenge_id, answer = issued_challenge(fixture, base_url)
        fields = {
            "username": FIXTURE_USERNAME,
            "password": FIXTURE_PASSWORD,
            "challenge": answer,
            "challenge_id": challenge_id,
        }
        fields[field] = value

        response = post_login(base_url, **fields)

        assert response.code == 303
        assert response.headers["Location"].startswith("/login?error=")
        assert expected_error in response.headers["Location"]


def test_secure_area_redirects_without_a_session_cookie() -> None:
    opener = request.build_opener(NoRedirect)
    with captcha_login_server() as (base_url, _fixture):
        try:
            opener.open(f"{base_url}/secure")
        except error.HTTPError as response:
            assert response.code == 303
            assert response.headers["Location"] == "/login"
        else:  # pragma: no cover - NoRedirect must preserve the 303 response
            raise AssertionError("/secure unexpectedly served without a session")


def test_login_page_renders_the_answer_for_a_human_to_read() -> None:
    with captcha_login_server() as (base_url, fixture):
        with request.urlopen(f"{base_url}/login") as response:
            body = response.read().decode()

        challenge_id, answer = next(iter(fixture.challenges.items()))
        assert challenge_id in body
        for character in answer:
            assert f">{character}</span>" in body
