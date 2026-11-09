# crawl4ai (CoProcure fork)

A trimmed fork of [unclecode/crawl4ai](https://github.com/unclecode/crawl4ai) (Apache-2.0, see `LICENSE`) that keeps only what the Pavilion app ("gazebo") uses: a Playwright-driven `AsyncWebCrawler` that fetches pages, honors `robots.txt`, caches HTML, converts it to markdown, extracts links with optional link previews and scoring, and crawls many URLs with memory-aware throttling.

Everything else from upstream (the command line tools, deep crawling, extraction strategies, content filters, the HTTP crawler strategy, the docs site, and so on) has been removed.

## Usage

```python
from crawl4ai import AsyncLogger, AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig
from crawl4ai.async_logger import LogLevel

async with AsyncWebCrawler(
    config=BrowserConfig(user_agent="my-agent"),
    logger=AsyncLogger(log_level=LogLevel.WARNING),
) as crawler:
    result = await crawler.arun(
        "https://example.com",
        config=CrawlerRunConfig(cache_mode=CacheMode.BYPASS),
    )
    print(result.markdown)
```

Playwright needs a browser: run `playwright install chromium` once.

## Development

```bash
uv sync
uv run playwright install chromium
uv run pytest tests/
```
