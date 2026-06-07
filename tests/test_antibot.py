from open_sesame.harness.antibot import classify_antibot_response


def test_classify_cloudflare_turnstile_managed_challenge() -> None:
    html = """
    <title>Just a moment...</title>
    <p>Performing security verification</p>
    <input type="hidden" name="cf-turnstile-response">
    <script src="https://challenges.cloudflare.com/turnstile/v0/api.js"></script>
    <script src="/cdn-cgi/challenge-platform/h/g/orchestrate/chl_page/v1"></script>
    """

    verdict = classify_antibot_response(html, status_code=200, headers={"cf-ray": "abc"})

    assert verdict.challenged
    assert verdict.vendor == "cloudflare"
    assert verdict.challenge_type == "turnstile_managed"
    assert verdict.confidence > 0.8


def test_classify_plain_page_as_not_challenged() -> None:
    verdict = classify_antibot_response("<html><title>Hello</title><p>Ready</p></html>")

    assert not verdict.challenged
    assert verdict.vendor is None
    assert verdict.challenge_type is None
