import pytest

from src.api.routes.live_sessions import live_platform


@pytest.mark.parametrize("url, platform", [
    ("https://www.twitch.tv/my_channel", "twitch"),
    ("https://kick.com/my-channel", "kick"),
    ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "youtube"),
])
def test_supported_live_urls(url, platform):
    assert live_platform(url) == platform


@pytest.mark.parametrize("url", [
    "http://twitch.tv/my_channel", "https://twitch.tv/videos/123",
    "https://kick.com/video/123", "https://example.com/channel",
    "https://evil.example.com:bad/channel",
])
def test_rejects_nonlive_or_untrusted_urls(url):
    with pytest.raises(ValueError):
        live_platform(url)
