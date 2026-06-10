from open_sesame.harness.google_search import (
    build_google_search_url,
    classify_google_search_page,
)


def test_build_google_search_url_encodes_query_and_locale() -> None:
    url = build_google_search_url("open sesame captcha", hl="en", gl="us")

    assert url == "https://www.google.com/search?q=open+sesame+captcha&hl=en&gl=us&pws=0"


def test_classify_google_sorry_recaptcha_as_blocked() -> None:
    blocked, signals = classify_google_search_page(
        html='<script src="https://www.google.com/recaptcha/api.js"></script>',
        text="Our systems have detected unusual traffic from your computer network.",
        title="Sorry",
        final_url="https://www.google.com/sorry/index?continue=https://www.google.com/search",
        result_count=0,
    )

    assert blocked
    assert "google-sorry-url" in signals
    assert "google-unusual-traffic-copy" in signals
    assert "google-recaptcha-script" in signals


def test_classify_google_results_as_not_blocked() -> None:
    blocked, signals = classify_google_search_page(
        html="<html><h3>OpenSesame</h3></html>",
        text="OpenSesame result",
        title="open sesame - Google Search",
        final_url="https://www.google.com/search?q=open+sesame",
        result_count=4,
    )

    assert not blocked
    assert signals == ()


def test_classify_consent_wall_as_signal_not_recaptcha_block() -> None:
    blocked, signals = classify_google_search_page(
        html="<html></html>",
        text="Before you continue to Google Search",
        title="Before you continue",
        final_url="https://consent.google.com/",
        result_count=0,
    )

    assert blocked
    assert signals == ("google-consent-wall",)
