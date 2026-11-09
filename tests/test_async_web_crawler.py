import pytest

from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig
from crawl4ai.async_dispatcher import MemoryAdaptiveDispatcher, RateLimiter
from tests.helpers import EXAMPLE_URL


@pytest.mark.asyncio
async def test_arun():
    async with AsyncWebCrawler() as crawler:
        result = await crawler.arun(
            url=EXAMPLE_URL
        )
        assert result.status_code == 200
        assert result.url == EXAMPLE_URL
        assert result.markdown is not None

@pytest.mark.asyncio
async def test_arun_many():
    test_urls = [
        "https://www.python.org/",
        EXAMPLE_URL,
    ]
    
    async with AsyncWebCrawler() as crawler:
        results = await crawler.arun_many(
            urls=test_urls[:2],
        )
        assert len(results) == len(test_urls)
        for item in results:
            assert item.status_code == 200
            assert item.markdown is not None
            assert item.url in test_urls

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
async def test_robots_parser_allows_public_and_blocks_private_paths(local_site):
    async with AsyncWebCrawler() as crawler:
        public_allowed = await crawler.robots_parser.can_fetch(
            local_site.url("/about"), "bot"
        )
        private_allowed = await crawler.robots_parser.can_fetch(
            local_site.url("/private/page"), "bot"
        )

    assert public_allowed
    assert not private_allowed


@pytest.mark.asyncio
async def test_arun_returns_403_for_a_url_blocked_by_robots_txt(local_site):
    config = CrawlerRunConfig(check_robots_txt=True)

    async with AsyncWebCrawler() as crawler:
        blocked = await crawler.arun(local_site.url("/private/page"), config=config)
        allowed = await crawler.arun(local_site.url("/about"), config=config)

    assert not blocked.success
    assert blocked.status_code == 403
    assert allowed.success
    assert allowed.status_code == 200


@pytest.mark.asyncio
@pytest.mark.parametrize("viewport", [
    (800, 600),
    (1024, 768),
    (1920, 1080)
])
async def test_viewport_config(viewport):
    """Test different viewport configurations"""
    width, height = viewport
    browser_config = BrowserConfig(
        browser_type="chromium",
        headless=True,
        viewport_width=width,
        viewport_height=height
    )
    
    async with AsyncWebCrawler(config=browser_config) as crawler:
        result = await crawler.arun(
            url=EXAMPLE_URL,
            config=CrawlerRunConfig(
                page_timeout=30000  # 30 seconds
            )
        )
        assert result.success

@pytest.mark.asyncio
async def test_memory_management():
    """Test memory-adaptive dispatching"""
    browser_config = BrowserConfig(
        browser_type="chromium",
        headless=True,
        viewport_width=1024,
        viewport_height=768
    )
    
    dispatcher = MemoryAdaptiveDispatcher(
        memory_threshold_percent=70.0,
        check_interval=1.0,
        max_session_permit=5
    )
    
    urls = [EXAMPLE_URL] * 3  # Test with multiple identical URLs
    
    async with AsyncWebCrawler(config=browser_config) as crawler:
        results = await crawler.arun_many(
            urls=urls,
            config=CrawlerRunConfig(page_timeout=30000),
            dispatcher=dispatcher
        )
        assert len(results) == len(urls)

@pytest.mark.asyncio
async def test_rate_limiting():
    """Test rate limiting functionality"""
    browser_config = BrowserConfig(
        browser_type="chromium",
        headless=True
    )
    
    dispatcher = MemoryAdaptiveDispatcher(
        rate_limiter=RateLimiter(
            base_delay=(1.0, 2.0),
            max_delay=5.0,
            max_retries=2
        ),
        memory_threshold_percent=70.0
    )
    
    urls = [
        EXAMPLE_URL,
        "https://example.org",
        "https://example.net"
    ]
    
    async with AsyncWebCrawler(config=browser_config) as crawler:
        results = await crawler.arun_many(
            urls=urls,
            config=CrawlerRunConfig(page_timeout=30000),
            dispatcher=dispatcher
        )
        assert len(results) == len(urls)

@pytest.mark.asyncio
@pytest.mark.parametrize("error_url", [
    "https://invalid.domain.test",
    "https://httpbin.org/status/404",
    "https://httpbin.org/status/503",
    "https://httpbin.org/status/403"
])
async def test_error_handling(error_url):
    """Test error handling for various failure scenarios"""
    browser_config = BrowserConfig(
        browser_type="chromium",
        headless=True
    )
    
    async with AsyncWebCrawler(config=browser_config) as crawler:
        result = await crawler.arun(
            url=error_url,
            config=CrawlerRunConfig(
                page_timeout=10000
            )
        )

    assert not result.success
    assert result.error_message is not None


@pytest.mark.asyncio
async def test_extract_media():
    async with AsyncWebCrawler() as crawler:
        url = "https://www.nbcnews.com/business"
        result = await crawler.arun(url=url)

        assert result.success
        assert result.media
        assert result.media["images"]
        assert any(img["src"] for img in result.media["images"])
        assert any(img["alt"] for img in result.media["images"])
        assert any(img["score"] for img in result.media["images"])

@pytest.mark.asyncio
async def test_extract_metadata():
    async with AsyncWebCrawler() as crawler:
        url = "https://www.nbcnews.com/business"
        result = await crawler.arun(url=url)

        assert result.success
        assert result.metadata
        assert all(
            key in result.metadata for key in ["title", "description", "keywords"]
        )
