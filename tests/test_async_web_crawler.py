import pytest

from crawl4ai import AsyncWebCrawler, CrawlerRunConfig
from crawl4ai.async_dispatcher import MemoryAdaptiveDispatcher, RateLimiter


@pytest.mark.asyncio
async def test_arun_returns_markdown_for_the_requested_url(local_site):
    async with AsyncWebCrawler() as crawler:
        result = await crawler.arun(local_site.url("/"))

    assert result.status_code == 200
    assert result.url == local_site.url("/")
    assert "Welcome" in result.markdown


@pytest.mark.asyncio
async def test_arun_reports_failure_when_the_connection_is_refused():
    async with AsyncWebCrawler() as crawler:
        result = await crawler.arun("http://127.0.0.1:1/", config=CrawlerRunConfig(page_timeout=5000))

    assert not result.success
    assert result.error_message


@pytest.mark.asyncio
async def test_arun_extracts_content_images_with_their_alt_text(local_site):
    async with AsyncWebCrawler() as crawler:
        result = await crawler.arun(local_site.url("/"))

    images = {image["src"]: image for image in result.media["images"]}
    assert images[local_site.url("/team.png")]["alt"] == "Our team"


@pytest.mark.asyncio
async def test_arun_extracts_page_metadata(local_site):
    async with AsyncWebCrawler() as crawler:
        result = await crawler.arun(local_site.url("/"))

    assert result.metadata["title"] == "Home Page"
    assert result.metadata["description"] == "The home page"
    assert result.metadata["keywords"] == "home, welcome"


@pytest.mark.asyncio
async def test_arun_many_returns_a_result_for_each_url(local_site):
    urls = [local_site.url("/"), local_site.url("/about")]

    async with AsyncWebCrawler() as crawler:
        results = await crawler.arun_many(urls=urls)

    assert isinstance(results, list)
    assert sorted(result.url for result in results) == sorted(urls)
    assert all(result.success for result in results)
    assert all(result.dispatch_result is not None for result in results)


@pytest.mark.asyncio
async def test_arun_many_with_a_rate_limited_dispatcher_returns_each_url(local_site):
    urls = [local_site.url("/"), local_site.url("/about"), local_site.url("/contact")]
    dispatcher = MemoryAdaptiveDispatcher(
        rate_limiter=RateLimiter(base_delay=(0.01, 0.02), max_delay=0.1, max_retries=2),
        max_session_permit=2,
    )

    async with AsyncWebCrawler() as crawler:
        results = await crawler.arun_many(urls=urls, dispatcher=dispatcher)

    assert sorted(result.url for result in results) == sorted(urls)


@pytest.mark.asyncio
async def test_arun_returns_403_for_a_url_blocked_by_robots_txt(local_site):
    config = CrawlerRunConfig(check_robots_txt=True)

    async with AsyncWebCrawler() as crawler:
        blocked = await crawler.arun(local_site.url("/private/page"), config=config)

    assert not blocked.success
    assert blocked.status_code == 403


@pytest.mark.asyncio
async def test_arun_fetches_a_url_allowed_by_robots_txt(local_site):
    config = CrawlerRunConfig(check_robots_txt=True)

    async with AsyncWebCrawler() as crawler:
        allowed = await crawler.arun(local_site.url("/about"), config=config)

    assert allowed.success
    assert allowed.status_code == 200
