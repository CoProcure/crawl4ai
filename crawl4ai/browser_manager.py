import asyncio
import hashlib
import json

from playwright.async_api import BrowserContext, ProxySettings, async_playwright

from .async_configs import BrowserConfig, CrawlerRunConfig


class BrowserManager:
    def __init__(self, browser_config: BrowserConfig, logger=None):
        self.config: BrowserConfig = browser_config
        self.logger = logger

        self.browser = None
        self.default_context = None
        self.playwright = None

        self.contexts_by_config = {}
        self._contexts_lock = asyncio.Lock()

    async def start(self):
        if self.playwright is not None:
            await self.close()

        self.playwright = await async_playwright().start()

        browser_args = self._build_browser_args()

        if self.config.browser_type == "firefox":
            self.browser = await self.playwright.firefox.launch(**browser_args)
        elif self.config.browser_type == "webkit":
            self.browser = await self.playwright.webkit.launch(**browser_args)
        else:
            self.browser = await self.playwright.chromium.launch(**browser_args)

        self.default_context = self.browser

    def _build_browser_args(self) -> dict:
        args = [
            "--disable-gpu",
            "--disable-gpu-compositing",
            "--disable-software-rasterizer",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-infobars",
            "--window-position=0,0",
            "--ignore-certificate-errors",
            "--ignore-certificate-errors-spki-list",
            "--disable-blink-features=AutomationControlled",
            "--window-position=400,0",
            "--disable-renderer-backgrounding",
            "--disable-ipc-flooding-protection",
            "--force-color-profile=srgb",
            "--mute-audio",
            "--disable-background-timer-throttling",
            f"--window-size={self.config.viewport_width},{self.config.viewport_height}",
        ]

        if self.config.extra_args:
            args.extend(self.config.extra_args)

        args = list(dict.fromkeys(args))

        browser_args = {"headless": self.config.headless, "args": args}

        if self.config.chrome_channel:
            browser_args["channel"] = self.config.chrome_channel

        if self.config.proxy or self.config.proxy_config:
            proxy_settings = (
                ProxySettings(server=self.config.proxy)
                if self.config.proxy
                else ProxySettings(
                    server=self.config.proxy_config.server,
                    username=self.config.proxy_config.username,
                    password=self.config.proxy_config.password,
                )
            )
            browser_args["proxy"] = proxy_settings

        return browser_args

    async def setup_context(
        self,
        context: BrowserContext,
        crawlerRunConfig: CrawlerRunConfig = None,
    ):
        if self.config.headers:
            await context.set_extra_http_headers(self.config.headers)

        if self.config.cookies:
            await context.add_cookies(self.config.cookies)

        if self.config.user_agent:
            combined_headers = {
                "User-Agent": self.config.user_agent,
                "sec-ch-ua": self.config.browser_hint,
            }
            combined_headers.update(self.config.headers)
            await context.set_extra_http_headers(combined_headers)

        await context.add_cookies(
            [
                {
                    "name": "cookiesEnabled",
                    "value": "true",
                    "url": crawlerRunConfig.url
                    if crawlerRunConfig and crawlerRunConfig.url
                    else "https://crawl4ai.com/",
                }
            ]
        )

    async def create_browser_context(self, crawlerRunConfig: CrawlerRunConfig = None):
        user_agent = self.config.headers.get("User-Agent", self.config.user_agent)
        viewport_settings = {
            "width": self.config.viewport_width,
            "height": self.config.viewport_height,
        }
        proxy_settings = {"server": self.config.proxy} if self.config.proxy else None

        context_settings = {
            "user_agent": user_agent,
            "viewport": viewport_settings,
            "proxy": proxy_settings,
            "accept_downloads": False,
            "ignore_https_errors": self.config.ignore_https_errors,
            "device_scale_factor": 1.0,
            "java_script_enabled": self.config.java_script_enabled,
        }

        if crawlerRunConfig:
            if crawlerRunConfig.proxy_config:
                proxy_settings = {
                    "server": crawlerRunConfig.proxy_config.server,
                }
                if crawlerRunConfig.proxy_config.username:
                    proxy_settings.update({
                        "username": crawlerRunConfig.proxy_config.username,
                        "password": crawlerRunConfig.proxy_config.password,
                    })
                context_settings["proxy"] = proxy_settings

            if crawlerRunConfig.locale:
                context_settings["locale"] = crawlerRunConfig.locale
            if crawlerRunConfig.timezone_id:
                context_settings["timezone_id"] = crawlerRunConfig.timezone_id

        return await self.browser.new_context(**context_settings)

    def _make_config_signature(self, crawlerRunConfig: CrawlerRunConfig) -> str:
        config_dict = crawlerRunConfig.__dict__.copy()
        for key in ["scraping_strategy", "cache_mode", "url"]:
            config_dict.pop(key, None)

        signature_json = json.dumps(config_dict, sort_keys=True, default=str)
        return hashlib.sha256(signature_json.encode("utf-8")).hexdigest()

    async def get_page(self, crawlerRunConfig: CrawlerRunConfig):
        config_signature = self._make_config_signature(crawlerRunConfig)

        async with self._contexts_lock:
            if config_signature in self.contexts_by_config:
                context = self.contexts_by_config[config_signature]
            else:
                context = await self.create_browser_context(crawlerRunConfig)
                await self.setup_context(context, crawlerRunConfig)
                self.contexts_by_config[config_signature] = context

        page = await context.new_page()
        return page, context

    async def close(self):
        if self.config.sleep_on_close:
            await asyncio.sleep(0.5)

        for ctx in self.contexts_by_config.values():
            try:
                await ctx.close()
            except Exception as e:
                self.logger.error(
                    message="Error closing context: {error}",
                    tag="ERROR",
                    params={"error": str(e)}
                )
        self.contexts_by_config.clear()

        if self.browser:
            await self.browser.close()
            self.browser = None

        if self.playwright:
            await self.playwright.stop()
            self.playwright = None
