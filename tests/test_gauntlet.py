from open_sesame.harness.antibot import extract_page_links
from open_sesame.harness.gauntlet import (
    GauntletPageResult,
    extract_turnstile_sitekey,
    summarize_gauntlet,
)


def test_extract_page_links_keeps_same_origin_and_dedupes() -> None:
    html = """
    <a href="/one">one</a>
    <a href="/one#section">one again</a>
    <a href="https://fortress.theplumber.dev/two">two</a>
    <a href="https://example.com/offsite">offsite</a>
    """

    assert extract_page_links(html, "https://fortress.theplumber.dev/") == (
        "https://fortress.theplumber.dev/one",
        "https://fortress.theplumber.dev/two",
    )


def test_summarize_gauntlet_counts_blocked_and_errors() -> None:
    results = [
        GauntletPageResult(
            url="https://fortress.theplumber.dev/",
            engine="httpx",
            ok=True,
            status_code=403,
            elapsed_ms=1.0,
        ),
        GauntletPageResult(
            url="https://fortress.theplumber.dev/two",
            engine="httpx",
            ok=False,
            status_code=None,
            elapsed_ms=2.0,
            error="timeout",
        ),
    ]

    summary = summarize_gauntlet(
        "https://fortress.theplumber.dev/",
        "httpx",
        3.0,
        2,
        results,
    )

    assert summary.visited == 2
    assert summary.blocked == 1
    assert summary.errors == 1


def test_turnstile_metadata_blocks_pending_widget() -> None:
    result = GauntletPageResult(
        url="https://fortress.theplumber.dev/",
        engine="voidcrawl-profile",
        ok=True,
        status_code=200,
        elapsed_ms=1.0,
        metadata={
            "captcha_kind": "turnstile",
            "turnstile_token_present": True,
            "turnstile_token_length": 0,
        },
    )

    assert result.blocked is True
    assert result.as_dict()["metadata"]["turnstile_token_length"] == 0


def test_extract_turnstile_sitekey_from_div() -> None:
    html = '<div class="cf-turnstile" data-sitekey="0x4AAAAAAAGlwMzq_9z6S9Mh"></div>'

    assert extract_turnstile_sitekey(html) == "0x4AAAAAAAGlwMzq_9z6S9Mh"


def test_extract_turnstile_sitekey_from_script_property() -> None:
    html = "turnstile.render('#captcha', { sitekey: '0xSCRIPTKEY' })"

    assert extract_turnstile_sitekey(html) == "0xSCRIPTKEY"


def test_extract_turnstile_sitekey_from_iframe_url() -> None:
    html = '<iframe src="https://challenges.cloudflare.com/foo?k=0xIFRAMEKEY&amp;theme=light"></iframe>'

    assert extract_turnstile_sitekey(html) == "0xIFRAMEKEY"
