from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from crawl4ai.utils import RobotsParser
from tests.helpers import EXAMPLE_URL, TestCacheClient

ALLOW_ALL_ROBOTS_TXT = "User-agent: *\nAllow: /\n"

WILDCARD_ROBOTS_TXT = """User-agent: *
Allow: /***************************************.css
Allow: /***************************************.js
Allow: /sitemap*.xml
Allow: /*/default*
Allow: /s/default
Allow: /*default*/s/
Disallow: /_user_controls
Disallow: /admin
"""


@pytest.fixture
def cache_client():
    client = TestCacheClient()
    yield client
    client.cleanup()


def serve_robots_txt(text: str):
    response = MagicMock(status=200)
    response.text = AsyncMock(return_value=text)
    session = MagicMock()
    session.get.return_value.__aenter__.return_value = response
    session.__aenter__.return_value = session
    return patch("crawl4ai.utils.aiohttp.ClientSession", return_value=session)


@pytest.mark.asyncio
async def test_can_fetch_caches_robots_rules_per_domain(cache_client):
    parser = RobotsParser(cache_client=cache_client)

    with serve_robots_txt(ALLOW_ALL_ROBOTS_TXT):
        await parser.can_fetch(EXAMPLE_URL, "MyBot/1.0")
        entries_after_first_fetch = cache_client.count()
        await parser.can_fetch(EXAMPLE_URL, "MyBot/1.0")
        entries_after_repeat_fetch = cache_client.count()
        await parser.can_fetch("https://docs.crawl4ai.com", "MyBot/1.0")

    assert entries_after_first_fetch == 1
    assert entries_after_repeat_fetch == 1
    assert cache_client.count() == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://testing.com/static/style.css", True),
        ("https://testing.com/style.css", True),
        ("https://testing.com/blah/default", True),
        ("https://testing.com/admin", False),
        ("https://testing.com/admin/something", False),
    ],
)
async def test_can_fetch_applies_wildcard_allow_and_disallow_rules(cache_client, url, expected):
    parser = RobotsParser(cache_client=cache_client)

    with serve_robots_txt(WILDCARD_ROBOTS_TXT):
        allowed = await parser.can_fetch(url, "MyBot/1.0")

    assert allowed is expected
