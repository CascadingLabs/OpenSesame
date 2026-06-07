from open_sesame.harness.twocaptcha import (
    parse_demo_expected_answer,
    parse_normal_demo_image_url,
)


def test_parse_demo_expected_answer() -> None:
    page_text = "Upload returns OK|2122988149. Solved answer returns OK|W9H5K"

    assert parse_demo_expected_answer(page_text) == "W9H5K"


def test_parse_demo_expected_answer_returns_none_when_absent() -> None:
    assert parse_demo_expected_answer("no answer here") is None


def test_parse_normal_demo_image_url_resolves_relative_image() -> None:
    html = '<img src="/dist/web/assets/captcha.jpg" alt="normal captcha example"/>'

    assert (
        parse_normal_demo_image_url(html, "https://2captcha.com/demo/normal")
        == "https://2captcha.com/dist/web/assets/captcha.jpg"
    )


def test_parse_normal_demo_image_url_returns_none_when_absent() -> None:
    assert parse_normal_demo_image_url("<html></html>", "https://2captcha.com") is None
