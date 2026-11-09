from unittest.mock import Mock, patch

import pytest
import pytest_asyncio

from crawl4ai.async_configs import CrawlerRunConfig
from crawl4ai.async_webcrawler import AsyncWebCrawler
from crawl4ai.cache_context import CacheMode
from crawl4ai.models import AsyncCrawlResponse
from tests.helpers import EXAMPLE_RAW_HTML, EXAMPLE_URL, TestCacheClient


@pytest.fixture
def cache_client():
    client = TestCacheClient()
    yield client
    client.cleanup()


@pytest_asyncio.fixture
async def example_page_response(monkeypatch):
    response = AsyncCrawlResponse(
        html=EXAMPLE_RAW_HTML,
        response_headers={"content-type": "text/html"},
        status_code=200,
        redirected_url=EXAMPLE_URL,
    )

    async def crawl(self, url, **kwargs):
        return response

    monkeypatch.setattr(
        "crawl4ai.async_crawler_strategy.AsyncPlaywrightCrawlerStrategy.crawl", crawl
    )


@pytest.mark.asyncio
async def test_second_enabled_crawl_is_served_from_the_cache(local_site, cache_client):
    config = CrawlerRunConfig(cache_mode=CacheMode.ENABLED)

    async with AsyncWebCrawler(cache_client=cache_client) as crawler:
        first = await crawler.arun(url=local_site.url("/"), config=config)
        entries_after_first = cache_client.count()
        second = await crawler.arun(url=local_site.url("/"), config=config)

    assert entries_after_first == 1
    assert cache_client.count() == 1
    assert second.html == first.html


@pytest.mark.asyncio
async def test_bypass_mode_skips_reading_the_cache(local_site, cache_client):
    get_spy = Mock(wraps=cache_client.get)

    with patch.object(TestCacheClient, "get", get_spy):
        async with AsyncWebCrawler(cache_client=cache_client) as crawler:
            await crawler.arun(
                url=local_site.url("/"),
                config=CrawlerRunConfig(cache_mode=CacheMode.ENABLED),
            )
            reads_after_enabled_crawl = get_spy.call_count
            await crawler.arun(
                url=local_site.url("/"),
                config=CrawlerRunConfig(cache_mode=CacheMode.BYPASS),
            )

    assert reads_after_enabled_crawl == 1
    assert get_spy.call_count == reads_after_enabled_crawl


@pytest.mark.asyncio
async def test_cached_html_is_reprocessed_with_each_crawls_excluded_tags(
    example_page_response, cache_client
):
    async def markdown_excluding(excluded_tags):
        config = CrawlerRunConfig(cache_mode=CacheMode.ENABLED, excluded_tags=excluded_tags)
        result = await crawler.arun(url=EXAMPLE_URL, config=config)
        return result.markdown

    async with AsyncWebCrawler(cache_client=cache_client) as crawler:
        without_paragraphs = await markdown_excluding(["p"])
        without_heading = await markdown_excluding(["h1"])
        unfiltered = await markdown_excluding([])

    assert without_paragraphs == "# Example Domain\n"
    assert "# Example Domain" not in without_heading
    assert "This domain is for use" in without_heading
    assert unfiltered.startswith("# Example Domain\nThis domain is for use")
    assert cache_client.count() == 1
