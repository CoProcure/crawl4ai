from __future__ import annotations

import asyncio
import os
from abc import ABC, abstractmethod
from collections.abc import Callable

from playwright.async_api import Error, Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from .async_configs import BrowserConfig, CrawlerRunConfig
from .async_logger import AsyncLogger
from .browser_manager import BrowserManager
from .js_snippet import load_js_script
from .models import AsyncCrawlResponse

BODY_VISIBLE_JS = """() => {
    const element = document.body;
    if (!element) return false;
    const style = window.getComputedStyle(element);
    const isVisible = style.display !== 'none' &&
                    style.visibility !== 'hidden' &&
                    style.opacity !== '0';
    return isVisible;
}"""


class AsyncCrawlerStrategy(ABC):
    @abstractmethod
    async def crawl(self, url: str, config: CrawlerRunConfig) -> AsyncCrawlResponse:
        pass


class AsyncPlaywrightCrawlerStrategy(AsyncCrawlerStrategy):
    def __init__(self, browser_config: BrowserConfig = None, logger: AsyncLogger = None):
        self.browser_config = browser_config or BrowserConfig()
        self.logger = logger

        self.hooks = {
            "on_browser_created": None,
            "on_page_context_created": None,
            "before_goto": None,
            "after_goto": None,
            "before_return_html": None,
            "before_retrieve_html": None,
        }

        self.browser_manager = BrowserManager(
            browser_config=self.browser_config,
            logger=self.logger,
        )

    async def __aenter__(self):
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def start(self):
        await self.browser_manager.start()
        await self.execute_hook(
            "on_browser_created",
            self.browser_manager.browser,
            context=self.browser_manager.default_context,
        )

    async def close(self):
        await self.browser_manager.close()

    def set_hook(self, hook_type: str, hook: Callable):
        if hook_type in self.hooks:
            self.hooks[hook_type] = hook
        else:
            raise ValueError(f"Invalid hook type: {hook_type}")

    async def execute_hook(self, hook_type: str, *args, **kwargs):
        hook = self.hooks.get(hook_type)
        if hook:
            if asyncio.iscoroutinefunction(hook):
                return await hook(*args, **kwargs)
            return hook(*args, **kwargs)
        return args[0] if args else None

    def set_custom_headers(self, headers: dict[str, str]):
        self.headers = headers

    async def csp_compliant_wait(
        self, page: Page, user_wait_function: str, timeout: float = 30000
    ):
        wrapper_js = f"""
        async () => {{
            const userFunction = {user_wait_function};
            const startTime = Date.now();
            try {{
                while (true) {{
                    if (await userFunction()) {{
                        return true;
                    }}
                    if (Date.now() - startTime > {timeout}) {{
                        return false;
                    }}
                    await new Promise(resolve => setTimeout(resolve, 100));
                }}
            }} catch (error) {{
                throw new Error(`Error evaluating condition: ${{error.message}}`);
            }}
        }}
        """

        try:
            return await page.evaluate(wrapper_js)
        except Exception as e:
            if "Error evaluating condition" in str(e):
                raise RuntimeError(f"Failed to evaluate wait condition: {str(e)}")
            return False

    async def crawl(self, url: str, config: CrawlerRunConfig) -> AsyncCrawlResponse:
        if url.startswith(("http://", "https://", "view-source:")):
            return await self._crawl_web(url, config)

        if url.startswith("file://"):
            local_file_path = url[7:]
            if not os.path.exists(local_file_path):
                raise FileNotFoundError(f"Local file not found: {local_file_path}")
            with open(local_file_path, encoding="utf-8") as f:
                html = f.read()
            return AsyncCrawlResponse(html=html, response_headers={}, status_code=200)

        if url.startswith("raw://") or url.startswith("raw:"):
            html = url[6:] if url.startswith("raw://") else url[4:]
            return AsyncCrawlResponse(html=html, response_headers={}, status_code=200)

        raise ValueError(
            "URL must start with 'http://', 'https://', 'file://', or 'raw:'"
        )

    async def _crawl_web(
        self, url: str, config: CrawlerRunConfig
    ) -> AsyncCrawlResponse:
        config.url = url
        redirected_url = url

        if config.user_agent:
            self.browser_config.user_agent = config.user_agent

        page, context = await self.browser_manager.get_page(crawlerRunConfig=config)

        await self.execute_hook("on_page_context_created", page, context=context, config=config)

        try:
            await self.execute_hook("before_goto", page, context=context, url=url, config=config)

            try:
                response = await page.goto(
                    url, wait_until=config.wait_until, timeout=config.page_timeout
                )
                redirected_url = page.url
            except Error as e:
                raise RuntimeError(f"Failed on navigating ACS-GOTO:\n{str(e)}")

            await self.execute_hook(
                "after_goto", page, context=context, url=url, response=response, config=config
            )

            if response is None:
                status_code = 200
                response_headers = {}
            else:
                first_resp = response
                req = response.request
                while req and req.redirected_from:
                    prev_req = req.redirected_from
                    prev_resp = await prev_req.response()
                    if prev_resp:
                        first_resp = prev_resp
                    req = prev_req

                status_code = first_resp.status
                response_headers = first_resp.headers

            try:
                await page.wait_for_selector("body", state="attached", timeout=30000)

                is_visible = await self.csp_compliant_wait(
                    page, BODY_VISIBLE_JS, timeout=30000
                )

                if not is_visible and not config.ignore_body_visibility:
                    visibility_info = await self.check_visibility(page)
                    raise Error(f"Body element is hidden: {visibility_info}")

            except Error:
                visibility_info = await self.check_visibility(page)

                if self.browser_config.verbose:
                    self.logger.debug(
                        message="Body visibility info: {info}",
                        tag="DEBUG",
                        params={"info": visibility_info},
                    )

                if not config.ignore_body_visibility:
                    raise Error(f"Body element is hidden: {visibility_info}")

            update_image_dimensions_js = load_js_script("update_image_dimensions")
            try:
                try:
                    await page.wait_for_load_state("domcontentloaded", timeout=5)
                except PlaywrightTimeoutError:
                    pass
                await page.evaluate(update_image_dimensions_js)
            except Exception as e:
                self.logger.error(
                    message="Error updating image dimensions: {error}",
                    tag="ERROR",
                    params={"error": str(e)},
                )

            await self.execute_hook("before_retrieve_html", page, context=context, config=config)
            if config.delay_before_return_html:
                await asyncio.sleep(config.delay_before_return_html)

            html = await page.content()

            await self.execute_hook(
                "before_return_html", page=page, html=html, context=context, config=config
            )

            return AsyncCrawlResponse(
                html=html,
                response_headers=response_headers,
                status_code=status_code,
                redirected_url=redirected_url,
            )

        finally:
            all_contexts = page.context.browser.contexts
            total_pages = sum(len(context.pages) for context in all_contexts)
            if total_pages <= 1 and self.browser_config.headless:
                pass
            else:
                await page.close()

    async def check_visibility(self, page):
        return await page.evaluate(BODY_VISIBLE_JS)
