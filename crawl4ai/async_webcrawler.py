from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from crawl4ai.cache_client import HTML_CACHE_KEY_PREFIX, CacheClient, NoCacheClient

from .__version__ import __version__ as crawl4ai_version
from .async_configs import BrowserConfig, CrawlerRunConfig
from .async_crawler_strategy import (
    AsyncCrawlerStrategy,
    AsyncPlaywrightCrawlerStrategy,
)
from .async_dispatcher import BaseDispatcher, MemoryAdaptiveDispatcher, RateLimiter
from .async_logger import AsyncLogger, AsyncLoggerBase
from .cache_context import CacheContext, CacheMode
from .markdown_generation_strategy import (
    DefaultMarkdownGenerator,
    MarkdownGenerationStrategy,
)
from .models import (
    AsyncCrawlResponse,
    CrawlResult,
    CrawlResultContainer,
    DispatchResult,
    MarkdownGenerationResult,
    ScrapingResult,
)
from .utils import (
    RobotsParser,
    get_error_context,
    sanitize_input_encode,
)


class AsyncWebCrawler:
    def __init__(
        self,
        cache_client: CacheClient = NoCacheClient(),
        crawler_strategy: AsyncCrawlerStrategy = None,
        config: BrowserConfig = None,
        base_directory: str = str(
            os.getenv("CRAWL4_AI_BASE_DIRECTORY", Path.home())),
        logger: AsyncLoggerBase = None,
    ):
        browser_config = config or BrowserConfig()

        self.browser_config = browser_config
        self.cache_client = cache_client

        self.logger = logger or AsyncLogger(
            log_file=os.path.join(base_directory, ".crawl4ai", "crawler.log"),
            verbose=self.browser_config.verbose,
            tag_width=10,
        )

        self.crawler_strategy = crawler_strategy or AsyncPlaywrightCrawlerStrategy(
            browser_config=browser_config,
            logger=self.logger,
        )

        self.crawl4ai_folder = os.path.join(base_directory, ".crawl4ai")
        os.makedirs(self.crawl4ai_folder, exist_ok=True)

        self.robots_parser = RobotsParser(cache_client=cache_client)

        self.ready = False

    async def start(self):
        await self.crawler_strategy.__aenter__()
        self.logger.info(f"Crawl4AI {crawl4ai_version}", tag="INIT")
        self.ready = True
        return self

    async def close(self):
        await self.crawler_strategy.__aexit__(None, None, None)

    async def __aenter__(self):
        return await self.start()

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def arun(
        self,
        url: str,
        config: CrawlerRunConfig = None,
    ) -> CrawlResultContainer[CrawlResult]:
        if not self.ready:
            await self.start()

        config = config or CrawlerRunConfig()
        if not isinstance(url, str) or not url:
            raise ValueError(
                "Invalid URL, make sure the URL is a non-empty string")

        try:
            self.logger.verbose = config.verbose

            if config.cache_mode is None:
                config.cache_mode = CacheMode.BYPASS

            cache_context = CacheContext(url, config.cache_mode, False)

            start_time = time.perf_counter()

            if cache_context.should_read():
                cached_html = self.cache_client.get(
                    key=self._get_cache_key(url)
                )
                html = sanitize_input_encode(cached_html)

                if html:
                    self.logger.url_status(
                        url=cache_context.display_url,
                        success=True,
                        timing=time.perf_counter() - start_time,
                        tag="COMPLETE"
                    )
                    crawl_result: CrawlResult = await self.aprocess_html(
                        url=url,
                        html=html,
                        config=config,
                        is_raw_html=url.startswith("raw:"),
                    )
                    return CrawlResultContainer(crawl_result)

            t1 = time.perf_counter()

            if config.user_agent:
                self.crawler_strategy.update_user_agent(
                    config.user_agent)

            if config and config.check_robots_txt:
                if not await self.robots_parser.can_fetch(
                    url, self.browser_config.user_agent
                ):
                    return CrawlResult(
                        url=url,
                        html="",
                        success=False,
                        status_code=403,
                        error_message="Access denied by robots.txt",
                        response_headers={
                            "X-Robots-Status": "Blocked by robots.txt"
                        },
                    )

            async_response: AsyncCrawlResponse = await self.crawler_strategy.crawl(
                url,
                config=config,
            )

            html = sanitize_input_encode(async_response.html)

            t2 = time.perf_counter()
            self.logger.url_status(
                url=cache_context.display_url,
                success=async_response.status_code == 200 and bool(html),
                timing=t2 - t1,
                tag="FETCH",
            )

            crawl_result: CrawlResult = await self.aprocess_html(
                url=url,
                html=html,
                config=config,
                is_raw_html=url.startswith("raw:"),
                redirected_url=async_response.redirected_url,
            )

            crawl_result.status_code = async_response.status_code
            crawl_result.redirected_url = async_response.redirected_url or url
            crawl_result.response_headers = async_response.response_headers

            self.logger.url_status(
                url=cache_context.display_url,
                success=crawl_result.success,
                timing=time.perf_counter() - start_time,
                tag="COMPLETE",
            )

            if cache_context.should_write():
                self.cache_client.set(
                    key=self._get_cache_key(url),
                    value=crawl_result.html,
                    ttl_seconds=config.cache_ttl_seconds,
                )

            return CrawlResultContainer(crawl_result)

        except Exception as e:
            error_context = get_error_context(sys.exc_info())

            error_message = (
                f"Unexpected error in _crawl_web at line {error_context['line_no']} "
                f"in {error_context['function']} ({error_context['filename']}):\n"
                f"Error: {str(e)}\n\n"
                f"Code context:\n{error_context['code_context']}"
            )

            self.logger.error_status(
                url=url,
                error=error_message,
                tag="ERROR",
            )

            return CrawlResultContainer(
                CrawlResult(
                    url=url, html="", success=False, error_message=error_message
                )
            )

    def _get_cache_key(self, url: str) -> str:
        return f"{HTML_CACHE_KEY_PREFIX}{url}"

    async def aprocess_html(
        self,
        url: str,
        html: str,
        config: CrawlerRunConfig,
        is_raw_html: bool = False,
        redirected_url: str | None = None,
    ) -> CrawlResult:
        try:
            _url = url if not is_raw_html else "Raw HTML"
            t1 = time.perf_counter()

            scraping_strategy = config.scraping_strategy
            if not scraping_strategy.logger:
                scraping_strategy.logger = self.logger

            params = config.__dict__.copy()
            params.pop("url", None)
            if redirected_url:
                params["redirected_url"] = redirected_url

            result: ScrapingResult = scraping_strategy.scrap(
                url, html, **params)

            if result is None:
                raise ValueError(
                    f"Process HTML, Failed to extract content from the website: {url}"
                )

        except Exception as e:
            raise ValueError(
                f"Process HTML, Failed to extract content from the website: {url}, error: {str(e)}"
            )

        cleaned_html = sanitize_input_encode(result.cleaned_html)
        media = result.media.model_dump()
        links = result.links.model_dump()
        metadata = result.metadata

        markdown_generator: MarkdownGenerationStrategy | None = (
            config.markdown_generator or DefaultMarkdownGenerator()
        )

        markdown_result: MarkdownGenerationResult = (
            markdown_generator.generate_markdown(
                input_html=cleaned_html,
                base_url=params.get("redirected_url", url) or url
            )
        )

        self.logger.url_status(
            url=_url,
            success=True,
            timing=int((time.perf_counter() - t1) * 1000) / 1000,
            tag="SCRAPE"
        )

        return CrawlResult(
            url=url,
            html=html,
            cleaned_html=cleaned_html,
            markdown=markdown_result,
            media=media,
            links=links,
            metadata=metadata,
            success=True,
            error_message="",
        )

    async def arun_many(
        self,
        urls: list[str],
        config: CrawlerRunConfig | None = None,
        dispatcher: BaseDispatcher | None = None,
    ) -> list[CrawlResultContainer[CrawlResult]]:
        config = config or CrawlerRunConfig()

        if dispatcher is None:
            dispatcher = MemoryAdaptiveDispatcher(
                rate_limiter=RateLimiter(
                    base_delay=(1.0, 3.0), max_delay=60.0, max_retries=3
                ),
            )

        def transform_result(task_result):
            return (
                setattr(
                    task_result.result,
                    "dispatch_result",
                    DispatchResult(
                        task_id=task_result.task_id,
                        memory_usage=task_result.memory_usage,
                        peak_memory=task_result.peak_memory,
                        start_time=task_result.start_time,
                        end_time=task_result.end_time,
                        error_message=task_result.error_message,
                    ),
                )
                or task_result.result
            )

        _results = await dispatcher.run_urls(crawler=self, urls=urls, config=config)
        return [transform_result(res) for res in _results]
